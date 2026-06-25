import shutil
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from accessories.models import Accessory
from assets.models import Asset, AssetModel, DepreciationProfile
from catalogue.models import Category, Manufacturer, StatusLabel
from components.models import Component
from consumables.models import Consumable
from licences.models import License
from organisations.models import Company
from suppliers.models import Supplier

from .learning import find_similarity_matches, get_review_analytics
from .models import AIIntakeClassificationSignal, AIIntakeDocument, AIIntakeDraft, AIIntakeInvoiceReview, AIIntakeJob, AIIntakeLineItem
from .policy import (
    build_legacy_asset_classification,
    enrich_legacy_extracted_data,
    get_inventory_classification_policy,
    normalize_inventory_classification,
)
from .provider import AIProviderError, AIProviderSchemaError, AzureOpenAIIntakeClient, ExtractionResult
from .schemas import AssetIntakeExtraction, InvoiceIntakeExtraction, InvoiceLineItemClassification
from .services import approve_line_item, evaluate_duplicate_risk, process_document, process_document_line_items

TEST_MEDIA_ROOT = Path(__file__).resolve().parent.parent / ".test-media-ai-intake"
shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)
(TEST_MEDIA_ROOT / "ai-intake").mkdir(parents=True, exist_ok=True)


@override_settings(
    MEDIA_ROOT=str(TEST_MEDIA_ROOT),
    AZURE_OPENAI={
        "endpoint": "",
        "api_key": "",
        "deployment": "",
        "api_version": "",
        "timeout_seconds": 30,
        "max_retries": 1,
    },
)
class AIIntakeTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.staff = get_user_model().objects.create_user(username="ai-admin", password="testpass123", is_staff=True)
        self.company = Company.objects.create(name="AI Co")
        self.staff.company = self.company
        self.staff.save(update_fields=["company"])
        self.asset_category = Category.objects.create(name="Laptops AI", category_type="asset")
        self.manufacturer = Manufacturer.objects.create(name="Dell")
        self.supplier = Supplier.objects.create(name="AI Supplier")
        self.status = StatusLabel.objects.create(name="Deployable AI", deployable=True, default_label=True)
        self.depreciation = DepreciationProfile.objects.create(name="36M AI", months=36)
        self.model = AssetModel.objects.create(
            name="Latitude",
            model_number="7400",
            category=self.asset_category,
            manufacturer=self.manufacturer,
            depreciation=self.depreciation,
        )

    def _provider_success(self, serial="SER-NEW-1", order_number="PO-NEW-1"):
        provider = Mock()
        provider.extract_asset_draft.return_value = ExtractionResult(
            payload=AssetIntakeExtraction(
                asset_name="Imported Laptop",
                manufacturer_name="Dell",
                model_name="Latitude",
                model_number="7400",
                supplier_name="AI Supplier",
                category_name="Laptops AI",
                serial=serial,
                order_number=order_number,
                purchase_date="2026-06-23",
                purchase_cost="1250.00",
                notes="Imported from invoice",
                quantity=1,
            ),
            raw_response={"ok": True},
            latency_ms=123,
            provider_request_id="req-1",
        )
        return provider

    def _invoice_provider_success(self):
        provider = Mock()
        provider.extract_invoice_payload.return_value = ExtractionResult(
            payload=InvoiceIntakeExtraction.model_validate(
                {
                    "invoice_header": {
                        "supplier_name": "AI Supplier",
                        "invoice_number": "INV-2001",
                        "order_number": "PO-2001",
                        "invoice_date": "2026-06-24",
                        "currency": "INR",
                        "merchandise_row_count": 2,
                        "subtotal_amount": "1500.00",
                        "tax_amount": "270.00",
                        "total_amount": "1770.00",
                        "notes": "Phase 2 extraction test",
                    },
                    "line_items": [
                        {
                            "raw_description": "Dell Latitude 7400 Laptop",
                            "normalized_description": "Dell Latitude 7400 Laptop",
                            "quantity": "1",
                            "unit_price": "1250.00",
                            "line_total": "1250.00",
                            "manufacturer_hint": "Dell",
                            "model_hint": "Latitude 7400",
                            "serial_hint": "SER-NEW-1",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                        {
                            "raw_description": "Printer Ink Cartridge",
                            "normalized_description": "Printer Ink Cartridge",
                            "quantity": "2",
                            "unit_price": "125.00",
                            "line_total": "250.00",
                            "manufacturer_hint": "",
                            "model_hint": "",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                    ],
                }
            ),
            raw_response={"ok": True, "pipeline": "invoice"},
            latency_ms=145,
            provider_request_id="req-invoice-1",
        )
        provider.classify_invoice_line_items.return_value = [
            InvoiceLineItemClassification.model_validate(
                {
                    "line_number": 1,
                    "classification": {
                        "inventory_type": "asset",
                        "inventory_confidence": 0.97,
                        "classification_rationale": "Laptop row has serial and model identity.",
                        "requires_review": True,
                        "normalized_item_name": "Dell Latitude 7400 Laptop",
                        "suggested_category_name": "Laptops AI",
                        "unsupported_for_approval": False,
                    },
                }
            ),
            InvoiceLineItemClassification.model_validate(
                {
                    "line_number": 2,
                    "classification": {
                        "inventory_type": "consumable",
                        "inventory_confidence": 0.84,
                        "classification_rationale": "Quantity-based stock item without trackable identity.",
                        "requires_review": True,
                        "normalized_item_name": "Printer Ink Cartridge",
                        "suggested_category_name": "Ink",
                        "unsupported_for_approval": False,
                    },
                }
            ),
        ]
        return provider

    @patch("ai_intake.views.process_document_line_items")
    def test_upload_processes_document_synchronously(self, mocked_process):
        self.client.force_login(self.staff)
        upload = SimpleUploadedFile("invoice.pdf", b"invoice-bytes", content_type="application/pdf")
        response = self.client.post(reverse("ai_intake:upload"), {"file": upload})
        self.assertEqual(response.status_code, 302)
        document = AIIntakeDocument.objects.get()
        self.assertEqual(response.url, reverse("ai_intake:detail", kwargs={"pk": document.pk}))
        mocked_process.assert_called_once_with(document=document, actor=self.staff)

    def test_upload_page_renders_compact_processing_overlay(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("ai_intake:upload"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ai-upload-overlay")
        self.assertContains(response, "AI magic in progress")
        self.assertContains(response, "The assistant is reading your document")
        self.assertContains(response, "ai-upload-overlay-percent")
        self.assertContains(response, "AI magic is extracting fields and values")
        self.assertContains(response, "ai-upload-center__progress")

    def test_duplicate_upload_redirects_to_existing_document(self):
        existing = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-bytes", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=13,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-bytes"),
            uploaded_by=self.staff,
        )
        self.client.force_login(self.staff)
        upload = SimpleUploadedFile("invoice-copy.pdf", b"invoice-bytes", content_type="application/pdf")
        response = self.client.post(reverse("ai_intake:upload"), {"file": upload})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("ai_intake:detail", kwargs={"pk": existing.pk}))
        self.assertEqual(AIIntakeDocument.objects.count(), 1)

    def test_processing_detail_renders_background_status_card(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-processing", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=len(b"invoice-processing"),
            sha256=AIIntakeDocument.hash_bytes(b"invoice-processing"),
            uploaded_by=self.staff,
            status=AIIntakeDocument.Status.PROCESSING,
        )
        AIIntakeJob.objects.create(document=document, status=AIIntakeJob.Status.RUNNING)
        self.client.force_login(self.staff)

        response = self.client.get(reverse("ai_intake:detail", kwargs={"pk": document.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AI is still working on this document")
        self.assertContains(response, "This page refreshes automatically")
        self.assertContains(response, reverse("ai_intake:processing-status", kwargs={"pk": document.pk}))
        self.assertContains(response, "window.setInterval(poll, refreshMs)")
        self.assertContains(response, "AI is still working on this document")
        self.assertContains(response, "Checking for updates")
        self.assertNotContains(response, 'data-processing-step="validating_document"')
        self.assertNotContains(response, "stepNodes.forEach")
        self.assertNotContains(response, "window.location.reload()")

    def test_processing_list_shows_background_activity_summary(self):
        processing_document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-processing-list", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=len(b"invoice-processing-list"),
            sha256=AIIntakeDocument.hash_bytes(b"invoice-processing-list"),
            uploaded_by=self.staff,
            status=AIIntakeDocument.Status.PROCESSING,
        )
        AIIntakeJob.objects.create(document=processing_document, status=AIIntakeJob.Status.RUNNING)
        self.client.force_login(self.staff)

        response = self.client.get(reverse("ai_intake:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "currently processing in the background")
        self.assertContains(response, "AI extraction is running in the background")

    def test_processing_status_endpoint_returns_live_job_state(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-status", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=len(b"invoice-status"),
            sha256=AIIntakeDocument.hash_bytes(b"invoice-status"),
            uploaded_by=self.staff,
            status=AIIntakeDocument.Status.PROCESSING,
        )
        AIIntakeJob.objects.create(document=document, status=AIIntakeJob.Status.RUNNING)
        self.client.force_login(self.staff)

        response = self.client.get(reverse("ai_intake:processing-status", kwargs={"pk": document.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["is_processing"], True)
        self.assertEqual(response.json()["document_status"], AIIntakeDocument.Status.PROCESSING)
        self.assertEqual(response.json()["job_status"], AIIntakeJob.Status.RUNNING)
        self.assertIn("processing_state", response.json())
        self.assertEqual(response.json()["processing_state"]["steps"][0]["key"], "upload_received")
        self.assertEqual(response.json()["processing_state"]["steps"][1]["state"], "completed")
        self.assertEqual(response.json()["processing_state"]["steps"][2]["state"], "active")

    def test_processing_status_endpoint_marks_queue_waiting_state(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-status-pending", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=len(b"invoice-status-pending"),
            sha256=AIIntakeDocument.hash_bytes(b"invoice-status-pending"),
            uploaded_by=self.staff,
            status=AIIntakeDocument.Status.PROCESSING,
        )
        AIIntakeJob.objects.create(document=document, status=AIIntakeJob.Status.PENDING)
        self.client.force_login(self.staff)

        response = self.client.get(reverse("ai_intake:processing-status", kwargs={"pk": document.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["processing_state"]["steps"][1]["state"], "active")
        self.assertEqual(response.json()["processing_state"]["steps"][2]["state"], "pending")
        self.assertIn("queued for AI intake processing", response.json()["processing_state"]["message"])

    def test_processing_status_endpoint_reports_completion_without_processing_payload(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-status-complete", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=len(b"invoice-status-complete"),
            sha256=AIIntakeDocument.hash_bytes(b"invoice-status-complete"),
            uploaded_by=self.staff,
            status=AIIntakeDocument.Status.REVIEW,
        )
        AIIntakeJob.objects.create(document=document, status=AIIntakeJob.Status.SUCCEEDED)
        self.client.force_login(self.staff)

        response = self.client.get(reverse("ai_intake:processing-status", kwargs={"pk": document.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["is_processing"], False)
        self.assertNotIn("processing_state", response.json())
        self.assertEqual(response.json()["job_status"], AIIntakeJob.Status.SUCCEEDED)

    def test_process_document_success_creates_draft(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-bytes", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=13,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-bytes"),
            uploaded_by=self.staff,
        )
        job = process_document(document=document, actor=self.staff, provider=self._provider_success())
        draft = job.draft
        self.assertEqual(job.status, job.Status.SUCCEEDED)
        self.assertEqual(draft.status, draft.Status.PENDING_REVIEW)
        self.assertEqual(draft.duplicate_risk_level, draft.RiskLevel.LOW)

    def test_process_document_enriches_phase0_policy_metadata(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-policy-bytes", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=20,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-policy-bytes"),
            uploaded_by=self.staff,
        )
        job = process_document(document=document, actor=self.staff, provider=self._provider_success())
        extracted = job.draft.extracted_data

        self.assertIn("inventory_policy", extracted)
        self.assertIn("inventory_classification", extracted)
        self.assertIn("reviewer_feedback", extracted)
        self.assertEqual(extracted["inventory_policy"]["policy_version"], "phase6-v1")
        self.assertEqual(extracted["inventory_policy"]["approval_mode"], "human_review_required")
        self.assertEqual(extracted["inventory_classification"]["inventory_type"], "asset")
        self.assertEqual(extracted["reviewer_feedback"]["predicted_type"], "asset")

    def test_phase0_policy_exposes_supported_and_reserved_types(self):
        policy = get_inventory_classification_policy()
        self.assertEqual(policy.policy_version, "phase6-v1")
        self.assertEqual(policy.approval_mode, "human_review_required")
        self.assertEqual(policy.supported_approval_types, ["accessory", "asset", "component", "consumable", "license"])
        self.assertEqual(policy.unsupported_rollout_types, [])

    def test_duplicate_risk_blocks_known_serial(self):
        Asset.objects.create(asset_tag="AST-1", model=self.model, status_label=self.status, company=self.company, serial="SER-DUP-1")
        risk_level, reasons, action = evaluate_duplicate_risk({"serial": "SER-DUP-1", "order_number": "", "model_name": "", "supplier_name": ""})
        self.assertEqual(risk_level, AIIntakeDraft.RiskLevel.HIGH)
        self.assertEqual(action, AIIntakeDraft.RecommendedAction.BLOCK)
        self.assertTrue(reasons)

    def test_provider_failure_creates_retry_required_draft(self):
        provider = Mock()
        provider.extract_asset_draft.side_effect = AIProviderError("provider down")
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-bytes", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=13,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-bytes"),
            uploaded_by=self.staff,
        )
        job = process_document(document=document, actor=self.staff, provider=provider)
        self.assertEqual(job.status, job.Status.FAILED)
        self.assertEqual(job.draft.status, AIIntakeDraft.Status.RETRY_REQUIRED)

    def test_approve_flow_bootstraps_default_status_when_missing(self):
        self.status.delete()
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-bytes", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=13,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-bytes-missing-status"),
            uploaded_by=self.staff,
        )
        job = process_document(document=document, actor=self.staff, provider=self._provider_success())
        self.client.force_login(self.staff)
        response = self.client.post(reverse("ai_intake:approve", kwargs={"pk": job.draft.pk}), {"company": self.company.pk})
        self.assertEqual(response.status_code, 302)
        job.draft.refresh_from_db()
        self.assertEqual(job.draft.status, AIIntakeDraft.Status.APPROVED)
        self.assertIsNotNone(job.draft.approved_asset)
        self.assertEqual(job.draft.approved_asset.status_label.name, "Ready for Deployment")
        self.assertTrue(StatusLabel.objects.filter(name="Ready for Deployment", default_label=True, deployable=True).exists())

    def test_approve_flow_creates_asset_only_after_explicit_review(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-bytes", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=13,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-bytes-2"),
            uploaded_by=self.staff,
        )
        job = process_document(document=document, actor=self.staff, provider=self._provider_success())
        self.client.force_login(self.staff)
        response = self.client.post(reverse("ai_intake:approve", kwargs={"pk": job.draft.pk}), {"company": self.company.pk, "review_notes": "looks good"})
        self.assertEqual(response.status_code, 302)
        job.draft.refresh_from_db()
        self.assertEqual(job.draft.status, AIIntakeDraft.Status.APPROVED)
        self.assertIsNotNone(job.draft.approved_asset)
        self.assertEqual(job.draft.extracted_data["reviewer_feedback"]["final_approved_type"], "asset")
        self.assertEqual(job.draft.extracted_data["reviewer_feedback"]["final_category"], "Laptops AI")

    def test_phase6_component_classification_is_first_class(self):
        classification = normalize_inventory_classification(
            {
                "inventory_type": "component",
                "inventory_confidence": 0.83,
                "classification_rationale": "Part-level hardware likely used inside a tracked asset.",
                "requires_review": False,
                "normalized_item_name": "Replacement SSD",
                "suggested_category_name": "Storage Parts",
            }
        )
        self.assertFalse(classification.unsupported_for_approval)
        self.assertFalse(classification.requires_review)
        self.assertEqual(classification.inventory_type.value, "component")

    def test_non_asset_classification_is_blocked_in_legacy_approval_flow(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-bytes-legacy-mismatch", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=29,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-bytes-legacy-mismatch"),
            uploaded_by=self.staff,
        )
        job = process_document(document=document, actor=self.staff, provider=self._provider_success())
        extracted = enrich_legacy_extracted_data(job.draft.extracted_data)
        extracted["inventory_classification"] = normalize_inventory_classification(
            {
                "inventory_type": "accessory",
                "inventory_confidence": 0.88,
                "classification_rationale": "Supporting issued gear rather than an independently tracked asset.",
                "requires_review": True,
                "normalized_item_name": "Laptop Bag",
                "suggested_category_name": "Bags",
            }
        ).model_dump(mode="json")
        job.draft.extracted_data = extracted
        job.draft.save(update_fields=["extracted_data", "updated_at"])

        self.client.force_login(self.staff)
        response = self.client.post(reverse("ai_intake:approve", kwargs={"pk": job.draft.pk}), {"company": self.company.pk})
        self.assertEqual(response.status_code, 302)
        job.draft.refresh_from_db()
        self.assertEqual(job.draft.status, AIIntakeDraft.Status.PENDING_REVIEW)
        self.assertIsNone(job.draft.approved_asset)

    def test_duplicate_draft_approval_is_blocked(self):
        Asset.objects.create(asset_tag="AST-2", model=self.model, status_label=self.status, company=self.company, serial="SER-DUP-2")
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-bytes", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=13,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-bytes-3"),
            uploaded_by=self.staff,
        )
        job = process_document(document=document, actor=self.staff, provider=self._provider_success(serial="SER-DUP-2"))
        self.client.force_login(self.staff)
        response = self.client.post(reverse("ai_intake:approve", kwargs={"pk": job.draft.pk}), {"company": self.company.pk})
        self.assertEqual(response.status_code, 302)
        job.draft.refresh_from_db()
        self.assertEqual(job.draft.status, AIIntakeDraft.Status.PENDING_REVIEW)
        self.assertIsNone(job.draft.approved_asset)

    def test_delete_upload_removes_document_and_file(self):
        self.client.force_login(self.staff)
        upload = SimpleUploadedFile("wrong-invoice.pdf", b"wrong-bytes", content_type="application/pdf")
        document = AIIntakeDocument.objects.create(
            file=upload,
            original_filename="wrong-invoice.pdf",
            content_type="application/pdf",
            size_bytes=len(b"wrong-bytes"),
            sha256=AIIntakeDocument.hash_bytes(b"wrong-bytes"),
            uploaded_by=self.staff,
        )
        stored_path = document.file.path
        self.assertTrue(Path(stored_path).exists())

        response = self.client.post(reverse("ai_intake:delete", kwargs={"pk": document.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("ai_intake:list"))
        self.assertFalse(AIIntakeDocument.objects.filter(pk=document.pk).exists())
        self.assertFalse(Path(stored_path).exists())

        replacement = SimpleUploadedFile("wrong-invoice.pdf", b"wrong-bytes", content_type="application/pdf")
        response = self.client.post(reverse("ai_intake:upload"), {"file": replacement})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AIIntakeDocument.objects.filter(sha256=AIIntakeDocument.hash_bytes(b"wrong-bytes")).count(), 1)

    def test_delete_upload_after_processing_removes_document_and_file(self):
        self.client.force_login(self.staff)
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("processed-invoice.pdf", b"processed-bytes", content_type="application/pdf"),
            original_filename="processed-invoice.pdf",
            content_type="application/pdf",
            size_bytes=len(b"processed-bytes"),
            sha256=AIIntakeDocument.hash_bytes(b"processed-bytes"),
            uploaded_by=self.staff,
        )
        process_document(document=document, actor=self.staff, provider=self._provider_success())
        stored_path = document.file.path
        self.assertTrue(Path(stored_path).exists())

        response = self.client.post(reverse("ai_intake:delete", kwargs={"pk": document.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AIIntakeDocument.objects.filter(pk=document.pk).exists())
        self.assertFalse(Path(stored_path).exists())

    def test_pdf_preview_view_allows_same_origin_framing(self):
        self.client.force_login(self.staff)
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("preview.pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF", content_type="application/pdf"),
            original_filename="preview.pdf",
            content_type="application/pdf",
            size_bytes=len(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"),
            sha256=AIIntakeDocument.hash_bytes(b"preview-pdf-bytes"),
            uploaded_by=self.staff,
        )
        response = self.client.get(reverse("ai_intake:preview", kwargs={"pk": document.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get("Content-Type"), "application/pdf")
        self.assertEqual(response.get("X-Frame-Options"), "SAMEORIGIN")

    def test_preview_view_streams_uploaded_document_inline(self):
        self.client.force_login(self.staff)
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("preview.jpg", b"image-bytes", content_type="image/jpeg"),
            original_filename="preview.jpg",
            content_type="image/jpeg",
            size_bytes=len(b"image-bytes"),
            sha256=AIIntakeDocument.hash_bytes(b"image-bytes"),
            uploaded_by=self.staff,
        )
        response = self.client.get(reverse("ai_intake:preview", kwargs={"pk": document.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get("Content-Type"), "image/jpeg")
        self.assertEqual(response.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertIn('inline; filename="preview.jpg"', response.get("Content-Disposition"))

    def test_legacy_asset_classification_uses_trackable_identity_signals(self):
        classification = build_legacy_asset_classification(
            {
                "asset_name": "Latitude 7400",
                "manufacturer_name": "Dell",
                "model_name": "Latitude",
                "serial": "SER-TRACK-1",
                "category_name": "Laptops AI",
                "quantity": 1,
            }
        )
        self.assertEqual(classification.inventory_type.value, "asset")
        self.assertGreaterEqual(classification.inventory_confidence, 0.9)
        self.assertEqual(classification.suggested_category_name, "Laptops AI")

    def test_phase1_invoice_review_models_can_coexist_with_legacy_draft(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase1-bytes", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=20,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase1-bytes"),
            uploaded_by=self.staff,
        )
        job = process_document(document=document, actor=self.staff, provider=self._provider_success())

        review = AIIntakeInvoiceReview.objects.create(
            job=job,
            legacy_draft=job.draft,
            status=AIIntakeInvoiceReview.Status.EXTRACTED,
            extracted_invoice_data={"invoice_number": "INV-1001", "supplier_name": "AI Supplier"},
            invoice_metadata={"currency": "INR"},
            review_summary={"line_item_count": 1},
        )
        line_item = AIIntakeLineItem.objects.create(
            invoice_review=review,
            line_number=1,
            raw_description="Dell Latitude 7400 Laptop",
            normalized_description="Dell Latitude 7400 Laptop",
            quantity="1.00",
            unit_price="1250.00",
            line_total="1250.00",
            manufacturer_hint="Dell",
            model_hint="Latitude 7400",
            serial_hint="SER-NEW-1",
            predicted_inventory_type="asset",
            predicted_category_name="Laptops AI",
            classification_confidence="0.960",
            classification_rationale="Trackable hardware with serial identity.",
            review_status=AIIntakeLineItem.ReviewStatus.PENDING_REVIEW,
            final_inventory_type="asset",
            final_category_name="Laptops AI",
            extraction_payload={"source": "phase1-test"},
            reviewer_feedback={"predicted_type": "asset"},
        )

        self.assertEqual(job.draft.status, AIIntakeDraft.Status.PENDING_REVIEW)
        self.assertEqual(review.legacy_draft_id, job.draft.id)
        self.assertEqual(review.line_items.count(), 1)
        self.assertEqual(line_item.invoice_review_id, review.id)

    def test_phase1_line_item_supports_generic_created_record_reference(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase1-link-bytes", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=25,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase1-link-bytes"),
            uploaded_by=self.staff,
        )
        job = process_document(document=document, actor=self.staff, provider=self._provider_success())
        review = AIIntakeInvoiceReview.objects.create(job=job, legacy_draft=job.draft)
        asset = Asset.objects.create(
            asset_tag="AST-PHASE1-1",
            name="Linked Asset",
            serial="SER-LINK-1",
            model=self.model,
            status_label=self.status,
            company=self.company,
        )

        line_item = AIIntakeLineItem.objects.create(
            invoice_review=review,
            line_number=1,
            raw_description="Linked asset row",
            review_status=AIIntakeLineItem.ReviewStatus.APPROVED,
            final_inventory_type="asset",
            created_record=asset,
        )

        self.assertEqual(line_item.created_record, asset)
        self.assertEqual(line_item.created_record_object_id, asset.id)
        self.assertEqual(line_item.created_record_content_type.model, "asset")

    def test_phase2_process_document_line_items_creates_invoice_review_and_line_items(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase2-bytes", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=20,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase2-bytes"),
            uploaded_by=self.staff,
        )

        job = process_document_line_items(document=document, actor=self.staff, provider=self._invoice_provider_success())

        self.assertEqual(job.status, AIIntakeJob.Status.SUCCEEDED)
        self.assertFalse(hasattr(job, "draft"))
        review = job.invoice_review
        self.assertEqual(review.status, AIIntakeInvoiceReview.Status.EXTRACTED)
        self.assertEqual(review.invoice_metadata["invoice_number"], "INV-2001")
        self.assertEqual(review.line_items.count(), 2)

        first_item = review.line_items.order_by("line_number").first()
        second_item = review.line_items.order_by("line_number")[1]
        self.assertEqual(first_item.predicted_inventory_type, "asset")
        self.assertEqual(second_item.predicted_inventory_type, "consumable")
        self.assertEqual(first_item.review_status, AIIntakeLineItem.ReviewStatus.PENDING_REVIEW)
        self.assertEqual(second_item.review_status, AIIntakeLineItem.ReviewStatus.PENDING_REVIEW)
        self.assertEqual(job.raw_response["line_item_classifications"][0]["classification"]["inventory_type"], "asset")

    def test_phase2_process_document_line_items_filters_non_inventory_charge_rows(self):
        provider = Mock()
        provider.extract_invoice_payload.return_value = ExtractionResult(
            payload=InvoiceIntakeExtraction.model_validate(
                {
                    "invoice_header": {
                        "supplier_name": "AI Supplier",
                        "invoice_number": "INV-CHARGE-1",
                        "order_number": "PO-CHARGE-1",
                        "invoice_date": "2026-06-24",
                        "currency": "INR",
                        "total_amount": "1299.00",
                    },
                    "line_items": [
                        {
                            "raw_description": "Laptop Backpack",
                            "normalized_description": "Laptop Backpack",
                            "quantity": "1",
                            "unit_price": "1299.00",
                            "line_total": "1299.00",
                            "manufacturer_hint": "HEROZ",
                            "model_hint": "Hammer Nylon 45L",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                        {
                            "raw_description": "Shipping Charges",
                            "normalized_description": "Shipping Charges",
                            "quantity": "1",
                            "unit_price": "0.00",
                            "line_total": "0.00",
                            "manufacturer_hint": "",
                            "model_hint": "",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                    ],
                }
            ),
            raw_response={"ok": True, "pipeline": "invoice"},
            latency_ms=144,
            provider_request_id="req-phase2-charge-filter",
        )
        provider.classify_invoice_line_items.return_value = [
            InvoiceLineItemClassification.model_validate(
                {
                    "line_number": 1,
                    "classification": {
                        "inventory_type": "accessory",
                        "inventory_confidence": 0.82,
                        "classification_rationale": "Backpack is issued support gear.",
                        "requires_review": True,
                        "normalized_item_name": "Laptop Backpack",
                        "suggested_category_name": "Bags",
                        "unsupported_for_approval": False,
                    },
                }
            )
        ]
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-charge-filter", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=24,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-charge-filter"),
            uploaded_by=self.staff,
        )

        job = process_document_line_items(document=document, actor=self.staff, provider=provider)

        review = job.invoice_review
        self.assertEqual(review.line_items.count(), 1)
        self.assertEqual(review.review_summary["line_item_count"], 1)
        kept_item = review.line_items.get()
        self.assertEqual(kept_item.raw_description, "Laptop Backpack")

    def test_phase2_process_document_line_items_corrects_backpack_component_prediction_to_accessory(self):
        provider = Mock()
        provider.extract_invoice_payload.return_value = ExtractionResult(
            payload=InvoiceIntakeExtraction.model_validate(
                {
                    "invoice_header": {
                        "supplier_name": "AI Supplier",
                        "invoice_number": "INV-BACKPACK-1",
                        "order_number": "PO-BACKPACK-1",
                        "invoice_date": "2026-06-24",
                        "currency": "INR",
                        "total_amount": "1299.00",
                    },
                    "line_items": [
                        {
                            "raw_description": "HEROZ Hammer Nylon 45 L Travel Laptop Backpack",
                            "normalized_description": "HEROZ Hammer Nylon 45 L Travel Laptop Backpack",
                            "quantity": "1",
                            "unit_price": "1299.00",
                            "line_total": "1299.00",
                            "manufacturer_hint": "HEROZ",
                            "model_hint": "Hammer Nylon 45 L",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        }
                    ],
                }
            ),
            raw_response={"ok": True, "pipeline": "invoice"},
            latency_ms=146,
            provider_request_id="req-phase2-backpack-fix",
        )
        provider.classify_invoice_line_items.return_value = [
            InvoiceLineItemClassification.model_validate(
                {
                    "line_number": 1,
                    "classification": {
                        "inventory_type": "component",
                        "inventory_confidence": 0.84,
                        "classification_rationale": "Part-level hardware signals suggest quantity-tracked component inventory.",
                        "requires_review": True,
                        "normalized_item_name": "HEROZ Hammer Nylon 45 L Travel Laptop Backpack",
                        "suggested_category_name": "Storage Parts",
                        "unsupported_for_approval": False,
                    },
                }
            )
        ]
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.jpg", b"invoice-backpack-fix", content_type="image/jpeg"),
            original_filename="invoice.jpg",
            content_type="image/jpeg",
            size_bytes=25,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-backpack-fix"),
            uploaded_by=self.staff,
        )

        job = process_document_line_items(document=document, actor=self.staff, provider=provider)

        line_item = job.invoice_review.line_items.get()
        self.assertEqual(line_item.predicted_inventory_type, "accessory")
        self.assertIn("issued accessory", line_item.classification_rationale.lower())

    def test_phase2_process_document_line_items_keeps_component_predictions_reviewable(self):
        provider = self._invoice_provider_success()
        provider.classify_invoice_line_items.return_value = [
            InvoiceLineItemClassification.model_validate(
                {
                    "line_number": 1,
                    "classification": {
                        "inventory_type": "component",
                        "inventory_confidence": 0.81,
                        "classification_rationale": "Spare part style row.",
                        "requires_review": False,
                        "normalized_item_name": "Replacement SSD",
                        "suggested_category_name": "Storage Parts",
                        "unsupported_for_approval": False,
                    },
                }
            ),
            InvoiceLineItemClassification.model_validate(
                {
                    "line_number": 2,
                    "classification": {
                        "inventory_type": "consumable",
                        "inventory_confidence": 0.84,
                        "classification_rationale": "Quantity-based stock item without trackable identity.",
                        "requires_review": True,
                        "normalized_item_name": "Printer Ink Cartridge",
                        "suggested_category_name": "Ink",
                        "unsupported_for_approval": False,
                    },
                }
            ),
        ]
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase2-component", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=26,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase2-component"),
            uploaded_by=self.staff,
        )

        job = process_document_line_items(document=document, actor=self.staff, provider=provider)

        review = job.invoice_review
        component_item = review.line_items.order_by("line_number").first()
        self.assertEqual(review.status, AIIntakeInvoiceReview.Status.EXTRACTED)
        self.assertEqual(component_item.review_status, AIIntakeLineItem.ReviewStatus.PENDING_REVIEW)
        self.assertFalse(component_item.unsupported_for_approval)
        self.assertEqual(component_item.predicted_inventory_type, "component")

    def test_phase3_line_item_workspace_renders_without_affecting_legacy_detail(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase3-bytes", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=20,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase3-bytes"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=self._invoice_provider_success())
        self.client.force_login(self.staff)

        legacy_response = self.client.get(reverse("ai_intake:detail", kwargs={"pk": document.pk}))
        workspace_response = self.client.get(reverse("ai_intake:line-item-workspace", kwargs={"pk": document.pk}))

        self.assertEqual(legacy_response.status_code, 200)
        self.assertNotContains(legacy_response, "Open Line-Item Workspace")
        self.assertContains(legacy_response, "Retry Extraction")
        self.assertContains(legacy_response, "Invoice extraction ready")
        self.assertContains(legacy_response, "Continue Line-Item Review")
        self.assertNotContains(legacy_response, "No draft exists for this document yet")
        self.assertEqual(workspace_response.status_code, 200)
        self.assertContains(workspace_response, "Line 1 Review")
        self.assertEqual(job.invoice_review.line_items.count(), 2)

    @patch("ai_intake.views.retry_invoice_review")
    def test_phase3_invoice_detail_retry_action_retries_invoice_extraction(self, retry_invoice_review_mock):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase3-retry", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=20,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase3-retry"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=self._invoice_provider_success())
        self.client.force_login(self.staff)

        response = self.client.post(reverse("ai_intake:line-item-retry", kwargs={"pk": document.pk}))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("ai_intake:detail", kwargs={"pk": document.pk}))
        retry_invoice_review_mock.assert_called_once_with(invoice_review=job.invoice_review, actor=self.staff)

    def test_phase3_line_item_workspace_save_persists_review_edits(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase3-save", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=19,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase3-save"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=self._invoice_provider_success())
        line_item = job.invoice_review.line_items.order_by("line_number").first()
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("ai_intake:line-item-save", kwargs={"pk": line_item.pk}),
            {
                "action": "mark_reviewed",
                "normalized_description": "Dell Latitude 7400 Business Laptop",
                "quantity": "1.00",
                "unit_price": "1240.00",
                "line_total": "1240.00",
                "final_inventory_type": "asset",
                "final_category_name": "Reviewed Laptops",
                "supplier_name": "Reviewed Supplier",
                "invoice_number": "INV-UPDATED",
                "order_number": "PO-UPDATED",
                "invoice_date": "2026-06-25",
                "requires_review": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        line_item.refresh_from_db()
        job.invoice_review.refresh_from_db()
        self.assertEqual(line_item.normalized_description, "Dell Latitude 7400 Business Laptop")
        self.assertEqual(line_item.final_inventory_type, "asset")
        self.assertEqual(line_item.final_category_name, "Reviewed Laptops")
        self.assertEqual(line_item.review_status, AIIntakeLineItem.ReviewStatus.REVIEWED)
        self.assertEqual(job.invoice_review.invoice_metadata["supplier_name"], "Reviewed Supplier")
        self.assertEqual(job.invoice_review.invoice_metadata["invoice_number"], "INV-UPDATED")
        self.assertEqual(job.invoice_review.status, AIIntakeInvoiceReview.Status.PARTIALLY_REVIEWED)

    def test_phase3_save_progress_moves_extracted_item_to_pending_and_persists_routing(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase3-pending", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=22,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase3-pending"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=self._invoice_provider_success())
        line_item = job.invoice_review.line_items.order_by("line_number").first()
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("ai_intake:line-item-save", kwargs={"pk": line_item.pk}),
            {
                "action": "save",
                "normalized_description": "Dell Latitude 7400 Business Laptop",
                "quantity": "1.00",
                "unit_price": "1240.00",
                "line_total": "1240.00",
                "final_inventory_type": "asset",
                "final_category_name": "Reviewed Laptops",
                "supplier_name": "Reviewed Supplier",
                "invoice_number": "INV-UPDATED",
                "order_number": "PO-UPDATED",
                "invoice_date": "2026-06-25",
                "company": str(self.company.pk),
                "review_notes": "keep this routing",
            },
        )

        self.assertEqual(response.status_code, 302)
        line_item.refresh_from_db()
        job.invoice_review.refresh_from_db()
        self.assertEqual(line_item.review_status, AIIntakeLineItem.ReviewStatus.PENDING_REVIEW)
        self.assertEqual(job.invoice_review.review_company, self.company)
        self.assertEqual(line_item.reviewer_feedback["review_notes"], "keep this routing")

        workspace_response = self.client.get(reverse("ai_intake:line-item-workspace", kwargs={"pk": document.pk}))
        self.assertEqual(workspace_response.status_code, 200)
        self.assertEqual(workspace_response.context["line_item_approve_form"].initial["company"], str(self.company.pk))
        self.assertEqual(workspace_response.context["line_item_approve_form"].initial["review_notes"], "keep this routing")

    def test_phase3_mark_reviewed_redirects_to_next_actionable_line_item(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase3-next-row", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=23,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase3-next-row"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=self._invoice_provider_success())
        line_items = list(job.invoice_review.line_items.order_by("line_number"))
        first_item = line_items[0]
        second_item = line_items[1]
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("ai_intake:line-item-save", kwargs={"pk": first_item.pk}),
            {
                "action": "mark_reviewed",
                "normalized_description": first_item.normalized_description,
                "quantity": str(first_item.quantity),
                "unit_price": str(first_item.unit_price),
                "line_total": str(first_item.line_total),
                "final_inventory_type": "asset",
                "final_category_name": "Reviewed Laptops",
                "supplier_name": "AI Supplier",
                "invoice_number": "INV-2001",
                "order_number": "PO-2001",
                "invoice_date": "2026-06-24",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            f"{reverse('ai_intake:line-item-workspace', kwargs={'pk': document.pk})}?line_item={second_item.pk}",
        )

    def test_phase3_workspace_shows_ready_for_recording_when_all_rows_reviewed(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase3-ready", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=20,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase3-ready"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=self._invoice_provider_success())
        for line_item in job.invoice_review.line_items.order_by("line_number"):
            line_item.review_status = AIIntakeLineItem.ReviewStatus.REVIEWED
            line_item.final_inventory_type = line_item.predicted_inventory_type
            line_item.final_category_name = line_item.predicted_category_name
            line_item.save(update_fields=["review_status", "final_inventory_type", "final_category_name", "updated_at"])
        job.invoice_review.status = AIIntakeInvoiceReview.Status.PARTIALLY_REVIEWED
        job.invoice_review.save(update_fields=["status", "updated_at"])
        self.client.force_login(self.staff)

        response = self.client.get(reverse("ai_intake:line-item-workspace", kwargs={"pk": document.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ready for Recording")
        self.assertNotContains(response, "Partially Reviewed")

    def test_phase4_approve_line_item_creates_asset_record(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase4-asset", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=20,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase4-asset"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=self._invoice_provider_success())
        line_item = job.invoice_review.line_items.order_by("line_number").first()
        line_item.final_inventory_type = "asset"
        line_item.final_category_name = "Reviewed Laptops"
        line_item.save(update_fields=["final_inventory_type", "final_category_name", "updated_at"])

        created = approve_line_item(line_item=line_item, actor=self.staff, company=self.company)

        line_item.refresh_from_db()
        job.invoice_review.refresh_from_db()
        created.refresh_from_db()
        self.assertIsInstance(created, Asset)
        self.assertEqual(created.company, self.company)
        self.assertEqual(created.supplier.name, "AI Supplier")
        self.assertEqual(created.order_number, "PO-2001")
        self.assertEqual(created.purchase_date.isoformat(), "2026-06-24")
        self.assertEqual(created.purchase_cost, line_item.line_total)
        self.assertEqual(created.model.category.name, "Reviewed Laptops")
        self.assertEqual(line_item.review_status, AIIntakeLineItem.ReviewStatus.APPROVED)
        self.assertEqual(line_item.created_record, created)
        self.assertEqual(job.invoice_review.status, AIIntakeInvoiceReview.Status.PARTIALLY_APPROVED)

    def test_phase4_line_item_approve_view_rolls_back_review_state_when_validation_fails(self):
        Asset.objects.create(asset_tag="AST-DUP-ROLLBACK", model=self.model, status_label=self.status, company=self.company, serial="SER-NEW-1")
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase4-rollback", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=24,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase4-rollback"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=self._invoice_provider_success())
        line_item = job.invoice_review.line_items.order_by("line_number").first()
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("ai_intake:line-item-approve", kwargs={"pk": line_item.pk}),
            {
                "normalized_description": line_item.normalized_description,
                "quantity": "1.00",
                "unit_price": "1250.00",
                "line_total": "1250.00",
                "final_inventory_type": "asset",
                "final_category_name": "Reviewed Laptops",
                "supplier_name": "AI Supplier",
                "invoice_number": "INV-2001",
                "order_number": "PO-2001",
                "invoice_date": "2026-06-24",
                "company": self.company.pk,
            },
        )

        self.assertEqual(response.status_code, 302)
        line_item.refresh_from_db()
        job.invoice_review.refresh_from_db()
        self.assertEqual(line_item.review_status, AIIntakeLineItem.ReviewStatus.PENDING_REVIEW)
        self.assertEqual(line_item.final_inventory_type, "")
        self.assertEqual(line_item.final_category_name, "")
        self.assertIsNone(line_item.created_record_object_id)
        self.assertEqual(job.invoice_review.status, AIIntakeInvoiceReview.Status.EXTRACTED)

    def test_phase4_approve_line_item_redirects_to_next_actionable_row(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase4-next-approve", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=26,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase4-next-approve"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=self._invoice_provider_success())
        line_items = list(job.invoice_review.line_items.order_by("line_number"))
        first_item = line_items[0]
        second_item = line_items[1]
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("ai_intake:line-item-approve", kwargs={"pk": first_item.pk}),
            {
                "normalized_description": first_item.normalized_description,
                "quantity": "1.00",
                "unit_price": "1250.00",
                "line_total": "1250.00",
                "final_inventory_type": "asset",
                "final_category_name": "Reviewed Laptops",
                "supplier_name": "AI Supplier",
                "invoice_number": "INV-2001",
                "order_number": "PO-2001",
                "invoice_date": "2026-06-24",
                "company": self.company.pk,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            f"{reverse('ai_intake:line-item-workspace', kwargs={'pk': document.pk})}?line_item={second_item.pk}",
        )

    def test_phase4_approve_line_item_creates_multiple_asset_records_for_quantity_rows(self):
        provider = Mock()
        provider.extract_invoice_payload.return_value = ExtractionResult(
            payload=InvoiceIntakeExtraction.model_validate(
                {
                    "invoice_header": {
                        "supplier_name": "AI Supplier",
                        "invoice_number": "INV-ASSET-QTY-1",
                        "order_number": "PO-ASSET-QTY-1",
                        "invoice_date": "2026-06-24",
                        "currency": "INR",
                        "total_amount": "52510.00",
                    },
                    "line_items": [
                        {
                            "raw_description": "Dell 17 inch Monitor",
                            "normalized_description": "Dell 17 inch Monitor",
                            "quantity": "5",
                            "unit_price": "10502.00",
                            "line_total": "52510.00",
                            "manufacturer_hint": "Dell",
                            "model_hint": "17 inch Monitor",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        }
                    ],
                }
            ),
            raw_response={"ok": True, "pipeline": "invoice"},
            latency_ms=140,
            provider_request_id="req-phase4-asset-qty",
        )
        provider.classify_invoice_line_items.return_value = [
            InvoiceLineItemClassification.model_validate(
                {
                    "line_number": 1,
                    "classification": {
                        "inventory_type": "asset",
                        "inventory_confidence": 0.94,
                        "classification_rationale": "Monitor row with hardware signals.",
                        "requires_review": True,
                        "normalized_item_name": "Dell 17 inch Monitor",
                        "suggested_category_name": "Monitors",
                        "unsupported_for_approval": False,
                    },
                }
            )
        ]

        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.jpg", b"invoice-phase4-asset-qty", content_type="image/jpeg"),
            original_filename="invoice.jpg",
            content_type="image/jpeg",
            size_bytes=24,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase4-asset-qty"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=provider)
        line_item = job.invoice_review.line_items.get()
        line_item.final_inventory_type = "asset"
        line_item.final_category_name = "Monitors"
        line_item.save(update_fields=["final_inventory_type", "final_category_name", "updated_at"])

        created = approve_line_item(line_item=line_item, actor=self.staff, company=self.company)

        line_item.refresh_from_db()
        created_assets = Asset.objects.filter(order_number="PO-ASSET-QTY-1").order_by("asset_tag")
        self.assertEqual(created_assets.count(), 5)
        self.assertEqual(created.asset_tag, "AI-LI-%05d" % line_item.pk)
        self.assertEqual(created_assets[1].asset_tag, f"AI-LI-{line_item.pk:05d}-02")
        self.assertEqual(line_item.review_status, AIIntakeLineItem.ReviewStatus.APPROVED)
        self.assertEqual(line_item.reviewer_feedback["created_record_count"], 5)

    def test_phase4_approve_view_bulk_approves_all_reviewed_rows(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase4-bulk-approve", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=28,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase4-bulk-approve"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=self._invoice_provider_success())
        line_items = list(job.invoice_review.line_items.order_by("line_number"))
        for line_item in line_items:
            line_item.review_status = AIIntakeLineItem.ReviewStatus.REVIEWED
            line_item.final_inventory_type = "asset" if line_item.line_number == 1 else "consumable"
            line_item.final_category_name = "Reviewed Laptops" if line_item.line_number == 1 else "Ink"
            line_item.save(update_fields=["review_status", "final_inventory_type", "final_category_name", "updated_at"])
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("ai_intake:line-item-approve", kwargs={"pk": line_items[0].pk}),
            {
                "normalized_description": line_items[0].normalized_description,
                "quantity": "1.00",
                "unit_price": "1250.00",
                "line_total": "1250.00",
                "final_inventory_type": "asset",
                "final_category_name": "Reviewed Laptops",
                "supplier_name": "AI Supplier",
                "invoice_number": "INV-2001",
                "order_number": "PO-2001",
                "invoice_date": "2026-06-24",
                "company": self.company.pk,
            },
        )

        self.assertEqual(response.status_code, 302)
        for line_item in line_items:
            line_item.refresh_from_db()
            self.assertEqual(line_item.review_status, AIIntakeLineItem.ReviewStatus.APPROVED)
            self.assertIsNotNone(line_item.created_record_object_id)
        job.invoice_review.refresh_from_db()
        document.refresh_from_db()
        self.assertEqual(job.invoice_review.status, AIIntakeInvoiceReview.Status.APPROVED_COMPLETE)
        self.assertEqual(document.status, AIIntakeDocument.Status.COMPLETED)

    def test_phase4_single_line_approval_hides_reapprove_action(self):
        provider = Mock()
        provider.extract_invoice_payload.return_value = ExtractionResult(
            payload=InvoiceIntakeExtraction.model_validate(
                {
                    "invoice_header": {
                        "supplier_name": "AI Supplier",
                        "invoice_number": "INV-SINGLE-1",
                        "order_number": "PO-SINGLE-1",
                        "invoice_date": "2026-06-24",
                        "currency": "INR",
                        "total_amount": "1240.00",
                    },
                    "line_items": [
                        {
                            "raw_description": "Dell Latitude 7400 Laptop",
                            "normalized_description": "Dell Latitude 7400 Laptop",
                            "quantity": "1",
                            "unit_price": "1240.00",
                            "line_total": "1240.00",
                            "manufacturer_hint": "Dell",
                            "model_hint": "Latitude 7400",
                            "serial_hint": "SER-SINGLE-1",
                            "part_number_hint": "LAT-7400",
                            "reference_hint": "",
                            "notes": "",
                        }
                    ],
                }
            ),
            raw_response={"ok": True, "pipeline": "invoice"},
            latency_ms=138,
            provider_request_id="req-phase4-single",
        )
        provider.classify_invoice_line_items.return_value = [
            InvoiceLineItemClassification.model_validate(
                {
                    "line_number": 1,
                    "classification": {
                        "inventory_type": "asset",
                        "inventory_confidence": 0.98,
                        "classification_rationale": "Serialized hardware line item.",
                        "requires_review": True,
                        "normalized_item_name": "Dell Latitude 7400 Laptop",
                        "suggested_category_name": "Reviewed Laptops",
                        "unsupported_for_approval": False,
                    },
                }
            )
        ]

        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("single-invoice.pdf", b"invoice-phase4-single", content_type="application/pdf"),
            original_filename="single-invoice.pdf",
            content_type="application/pdf",
            size_bytes=20,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase4-single"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=provider)
        line_item = job.invoice_review.line_items.get()
        self.client.force_login(self.staff)

        approve_response = self.client.post(
            reverse("ai_intake:line-item-approve", kwargs={"pk": line_item.pk}),
            {
                "normalized_description": "Dell Latitude 7400 Laptop",
                "quantity": "1.00",
                "unit_price": "1240.00",
                "line_total": "1240.00",
                "final_inventory_type": "asset",
                "final_category_name": "Reviewed Laptops",
                "supplier_name": "AI Supplier",
                "invoice_number": "INV-SINGLE-1",
                "order_number": "PO-SINGLE-1",
                "invoice_date": "2026-06-24",
                "company": self.company.pk,
            },
        )

        self.assertEqual(approve_response.status_code, 302)
        line_item.refresh_from_db()
        job.invoice_review.refresh_from_db()
        self.assertEqual(line_item.review_status, AIIntakeLineItem.ReviewStatus.APPROVED)
        self.assertIsNotNone(line_item.created_record_object_id)
        self.assertEqual(job.invoice_review.status, AIIntakeInvoiceReview.Status.APPROVED_COMPLETE)

        workspace_response = self.client.get(
            reverse("ai_intake:line-item-workspace", kwargs={"pk": document.pk})
        )

        self.assertContains(workspace_response, "Record Already Created")
        self.assertContains(workspace_response, 'id="line-item-approve-button" class="btn btn-primary d-none"')
        self.assertContains(workspace_response, "Approved Complete")

    def test_phase4_approve_line_item_creates_accessory_record_with_quantity_and_location(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase4-accessory", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=24,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase4-accessory"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=self._invoice_provider_success())
        line_item = job.invoice_review.line_items.order_by("line_number")[1]
        line_item.final_inventory_type = "accessory"
        line_item.final_category_name = "Printer Support"
        line_item.save(update_fields=["final_inventory_type", "final_category_name", "updated_at"])

        created = approve_line_item(line_item=line_item, actor=self.staff, company=self.company)

        line_item.refresh_from_db()
        self.assertIsInstance(created, Accessory)
        self.assertEqual(created.quantity, 2)
        self.assertEqual(created.company, self.company)
        self.assertEqual(created.supplier.name, "AI Supplier")
        self.assertEqual(created.category.category_type, "accessory")
        self.assertEqual(line_item.created_record, created)

    def test_phase4_approve_line_item_blocks_duplicate_accessory_against_existing_unscoped_record(self):
        Accessory.objects.create(
            name="Printer Ink Cartridge",
            category=Category.objects.create(name="Printer Support", category_type="accessory"),
            supplier=self.supplier,
            quantity=2,
        )
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase4-accessory-dup", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=28,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase4-accessory-dup"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=self._invoice_provider_success())
        line_item = job.invoice_review.line_items.order_by("line_number")[1]
        line_item.final_inventory_type = "accessory"
        line_item.final_category_name = "Printer Support"
        line_item.save(update_fields=["final_inventory_type", "final_category_name", "updated_at"])

        with self.assertRaises(ValidationError):
            approve_line_item(line_item=line_item, actor=self.staff, company=self.company)

    def test_phase4_approve_line_item_creates_consumable_record_with_quantity(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase4-consumable", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=25,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase4-consumable"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=self._invoice_provider_success())
        line_item = job.invoice_review.line_items.order_by("line_number")[1]
        line_item.final_inventory_type = "consumable"
        line_item.final_category_name = "Ink"
        line_item.save(update_fields=["final_inventory_type", "final_category_name", "updated_at"])

        created = approve_line_item(line_item=line_item, actor=self.staff, company=self.company)

        line_item.refresh_from_db()
        self.assertIsInstance(created, Consumable)
        self.assertEqual(created.quantity, 2)
        self.assertEqual(created.company, self.company)
        self.assertEqual(created.supplier.name, "AI Supplier")
        self.assertEqual(created.category.category_type, "consumable")
        self.assertEqual(line_item.created_record, created)

    def test_phase6_approve_line_item_creates_component_record(self):
        provider = self._invoice_provider_success()
        provider.extract_invoice_payload.return_value = ExtractionResult(
            payload=InvoiceIntakeExtraction.model_validate(
                {
                    "invoice_header": {
                        "supplier_name": "AI Supplier",
                        "invoice_number": "INV-6001",
                        "order_number": "PO-6001",
                        "invoice_date": "2026-06-24",
                        "currency": "INR",
                        "total_amount": "4500.00",
                    },
                    "line_items": [
                        {
                            "raw_description": "Replacement SSD 1TB",
                            "normalized_description": "Replacement SSD 1TB",
                            "quantity": "4",
                            "unit_price": "1125.00",
                            "line_total": "4500.00",
                            "manufacturer_hint": "",
                            "model_hint": "",
                            "serial_hint": "",
                            "part_number_hint": "SSD-1TB-INT",
                            "reference_hint": "REF-CMP-44",
                            "component_role_hint": "replacement",
                            "notes": "",
                        }
                    ],
                }
            ),
            raw_response={"ok": True, "pipeline": "invoice"},
            latency_ms=132,
            provider_request_id="req-phase6-component",
        )
        provider.classify_invoice_line_items.return_value = [
            InvoiceLineItemClassification.model_validate(
                {
                    "line_number": 1,
                    "classification": {
                        "inventory_type": "component",
                        "inventory_confidence": 0.88,
                        "classification_rationale": "Spare part signals are explicit.",
                        "requires_review": True,
                        "normalized_item_name": "Replacement SSD 1TB",
                        "suggested_category_name": "Storage Parts",
                        "unsupported_for_approval": False,
                    },
                }
            )
        ]
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase6-component", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=23,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase6-component"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=provider)
        line_item = job.invoice_review.line_items.get()
        line_item.final_inventory_type = "component"
        line_item.final_category_name = "Storage Parts"
        line_item.reviewer_feedback = {
            **(line_item.reviewer_feedback or {}),
            "type_specific_review": {
                "component_role_hint": "replacement",
                "component_min_quantity": 1,
                "component_part_number": "SSD-1TB-INT",
                "component_reference": "REF-CMP-44",
            },
        }
        line_item.save(update_fields=["final_inventory_type", "final_category_name", "reviewer_feedback", "updated_at"])

        created = approve_line_item(line_item=line_item, actor=self.staff, company=self.company)

        line_item.refresh_from_db()
        self.assertIsInstance(created, Component)
        self.assertEqual(created.quantity, 4)
        self.assertEqual(created.min_quantity, 1)
        self.assertEqual(created.category.category_type, "component")
        self.assertIn("Part number: SSD-1TB-INT", created.notes)
        self.assertEqual(line_item.created_record, created)

    def test_phase6_approve_line_item_creates_license_record(self):
        provider = self._invoice_provider_success()
        provider.extract_invoice_payload.return_value = ExtractionResult(
            payload=InvoiceIntakeExtraction.model_validate(
                {
                    "invoice_header": {
                        "supplier_name": "AI Supplier",
                        "invoice_number": "INV-6002",
                        "order_number": "PO-6002",
                        "invoice_date": "2026-06-24",
                        "currency": "INR",
                        "total_amount": "12000.00",
                    },
                    "line_items": [
                        {
                            "raw_description": "Microsoft 365 Business Premium Annual Subscription",
                            "normalized_description": "Microsoft 365 Business Premium",
                            "quantity": "15",
                            "unit_price": "800.00",
                            "line_total": "12000.00",
                            "manufacturer_hint": "Microsoft",
                            "model_hint": "",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "LIC-REF-02",
                            "seat_hint": 15,
                            "product_key_hint": "AAAA-BBBB-CCCC-DDDD",
                            "license_reference_hint": "LIC-REF-02",
                            "expiry_date_hint": "2027-06-24",
                            "renewal_date_hint": "2027-06-20",
                            "billing_term_hint": "Annual",
                            "notes": "",
                        }
                    ],
                }
            ),
            raw_response={"ok": True, "pipeline": "invoice"},
            latency_ms=136,
            provider_request_id="req-phase6-license",
        )
        provider.classify_invoice_line_items.return_value = [
            InvoiceLineItemClassification.model_validate(
                {
                    "line_number": 1,
                    "classification": {
                        "inventory_type": "license",
                        "inventory_confidence": 0.92,
                        "classification_rationale": "Subscription and seat signals are explicit.",
                        "requires_review": True,
                        "normalized_item_name": "Microsoft 365 Business Premium",
                        "suggested_category_name": "Productivity SaaS",
                        "unsupported_for_approval": False,
                    },
                }
            )
        ]
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase6-license", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=21,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase6-license"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=provider)
        line_item = job.invoice_review.line_items.get()
        line_item.final_inventory_type = "license"
        line_item.final_category_name = "Productivity SaaS"
        line_item.reviewer_feedback = {
            **(line_item.reviewer_feedback or {}),
            "type_specific_review": {
                "license_seats": 15,
                "license_product_key": "AAAA-BBBB-CCCC-DDDD",
                "license_reference": "LIC-REF-02",
                "license_expiration_date": "2027-06-24",
                "license_renewal_date": "2027-06-20",
                "license_billing_term": "Annual",
            },
        }
        line_item.save(update_fields=["final_inventory_type", "final_category_name", "reviewer_feedback", "updated_at"])

        created = approve_line_item(line_item=line_item, actor=self.staff, company=self.company)

        line_item.refresh_from_db()
        self.assertIsInstance(created, License)
        self.assertEqual(created.seats, 15)
        self.assertEqual(created.product_key, "AAAA-BBBB-CCCC-DDDD")
        self.assertEqual(created.reference_code, "LIC-REF-02")
        self.assertEqual(created.billing_term, "Annual")
        self.assertEqual(created.renewal_date.isoformat(), "2027-06-20")
        self.assertEqual(created.expiration_date.isoformat(), "2027-06-24")
        self.assertEqual(created.category.category_type, "license")
        self.assertEqual(line_item.created_record, created)

    def test_phase6_approve_line_item_blocks_duplicate_component(self):
        Component.objects.create(
            name="Replacement SSD 1TB",
            category=Category.objects.create(name="Storage Parts", category_type="component"),
            supplier=self.supplier,
            quantity=4,
        )
        provider = self._invoice_provider_success()
        provider.extract_invoice_payload.return_value = ExtractionResult(
            payload=InvoiceIntakeExtraction.model_validate(
                {
                    "invoice_header": {
                        "supplier_name": "AI Supplier",
                        "invoice_number": "INV-6001",
                        "order_number": "PO-6001",
                        "invoice_date": "2026-06-24",
                        "currency": "INR",
                        "total_amount": "4500.00",
                    },
                    "line_items": [
                        {
                            "raw_description": "Replacement SSD 1TB",
                            "normalized_description": "Replacement SSD 1TB",
                            "quantity": "4",
                            "unit_price": "1125.00",
                            "line_total": "4500.00",
                            "part_number_hint": "SSD-1TB-INT",
                            "reference_hint": "REF-CMP-44",
                            "component_role_hint": "replacement",
                            "notes": "",
                        }
                    ],
                }
            ),
            raw_response={"ok": True, "pipeline": "invoice"},
            latency_ms=132,
            provider_request_id="req-phase6-component-dup",
        )
        provider.classify_invoice_line_items.return_value = [
            InvoiceLineItemClassification.model_validate(
                {
                    "line_number": 1,
                    "classification": {
                        "inventory_type": "component",
                        "inventory_confidence": 0.88,
                        "classification_rationale": "Spare part signals are explicit.",
                        "requires_review": True,
                        "normalized_item_name": "Replacement SSD 1TB",
                        "suggested_category_name": "Storage Parts",
                        "unsupported_for_approval": False,
                    },
                }
            )
        ]
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase6-component-dup", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=27,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase6-component-dup"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=provider)
        line_item = job.invoice_review.line_items.get()
        line_item.final_inventory_type = "component"
        line_item.final_category_name = "Storage Parts"
        line_item.reviewer_feedback = {
            **(line_item.reviewer_feedback or {}),
            "type_specific_review": {
                "component_role_hint": "replacement",
                "component_min_quantity": 1,
                "component_part_number": "SSD-1TB-INT",
                "component_reference": "REF-CMP-44",
            },
        }
        line_item.save(update_fields=["final_inventory_type", "final_category_name", "reviewer_feedback", "updated_at"])

        with self.assertRaises(ValidationError):
            approve_line_item(line_item=line_item, actor=self.staff, company=self.company)

    def test_phase6_approve_line_item_blocks_duplicate_license(self):
        License.objects.create(
            name="Microsoft 365 Business Premium",
            product_key="AAAA-BBBB-CCCC-DDDD",
            reference_code="LIC-REF-02",
            seats=15,
            category=Category.objects.create(name="Productivity SaaS", category_type="license"),
            manufacturer=self.manufacturer,
            supplier=self.supplier,
            purchase_date=date(2026, 6, 24),
            expiration_date=date(2027, 6, 24),
            renewal_date=date(2027, 6, 20),
            billing_term="Annual",
            order_number="PO-6002",
        )
        provider = self._invoice_provider_success()
        provider.extract_invoice_payload.return_value = ExtractionResult(
            payload=InvoiceIntakeExtraction.model_validate(
                {
                    "invoice_header": {
                        "supplier_name": "AI Supplier",
                        "invoice_number": "INV-6002",
                        "order_number": "PO-6002",
                        "invoice_date": "2026-06-24",
                        "currency": "INR",
                        "total_amount": "12000.00",
                    },
                    "line_items": [
                        {
                            "raw_description": "Microsoft 365 Business Premium Annual Subscription",
                            "normalized_description": "Microsoft 365 Business Premium",
                            "quantity": "15",
                            "unit_price": "800.00",
                            "line_total": "12000.00",
                            "manufacturer_hint": "Dell",
                            "reference_hint": "LIC-REF-02",
                            "seat_hint": 15,
                            "product_key_hint": "AAAA-BBBB-CCCC-DDDD",
                            "license_reference_hint": "LIC-REF-02",
                            "expiry_date_hint": "2027-06-24",
                            "renewal_date_hint": "2027-06-20",
                            "billing_term_hint": "Annual",
                            "notes": "",
                        }
                    ],
                }
            ),
            raw_response={"ok": True, "pipeline": "invoice"},
            latency_ms=136,
            provider_request_id="req-phase6-license-dup",
        )
        provider.classify_invoice_line_items.return_value = [
            InvoiceLineItemClassification.model_validate(
                {
                    "line_number": 1,
                    "classification": {
                        "inventory_type": "license",
                        "inventory_confidence": 0.92,
                        "classification_rationale": "Subscription and seat signals are explicit.",
                        "requires_review": True,
                        "normalized_item_name": "Microsoft 365 Business Premium",
                        "suggested_category_name": "Productivity SaaS",
                        "unsupported_for_approval": False,
                    },
                }
            )
        ]
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase6-license-dup", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=25,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase6-license-dup"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=provider)
        line_item = job.invoice_review.line_items.get()
        line_item.final_inventory_type = "license"
        line_item.final_category_name = "Productivity SaaS"
        line_item.reviewer_feedback = {
            **(line_item.reviewer_feedback or {}),
            "type_specific_review": {
                "license_seats": 15,
                "license_product_key": "AAAA-BBBB-CCCC-DDDD",
                "license_reference": "LIC-REF-02",
                "license_expiration_date": "2027-06-24",
                "license_renewal_date": "2027-06-20",
                "license_billing_term": "Annual",
            },
        }
        line_item.save(update_fields=["final_inventory_type", "final_category_name", "reviewer_feedback", "updated_at"])

        with self.assertRaises(ValidationError):
            approve_line_item(line_item=line_item, actor=self.staff, company=self.company)

    def test_phase6_approve_line_item_blocks_duplicate_single_asset_without_serial(self):
        duplicate_category, _ = Category.objects.get_or_create(name="Laptops AI", category_type="asset")
        duplicate_model = AssetModel.objects.create(name="Latitude 7400", model_number="", manufacturer=self.manufacturer, category=duplicate_category)
        Asset.objects.create(
            asset_tag="AST-DUP-NOSERIAL",
            name="Dell Latitude 7400 Laptop",
            serial="",
            model=duplicate_model,
            status_label=self.status,
            company=self.company,
            supplier=self.supplier,
            purchase_date=date(2026, 6, 24),
            order_number="PO-7001",
        )
        provider = self._invoice_provider_success()
        provider.extract_invoice_payload.return_value = ExtractionResult(
            payload=InvoiceIntakeExtraction.model_validate(
                {
                    "invoice_header": {
                        "supplier_name": "AI Supplier",
                        "invoice_number": "INV-7001",
                        "order_number": "PO-7001",
                        "invoice_date": "2026-06-24",
                        "currency": "INR",
                        "total_amount": "64000.00",
                    },
                    "line_items": [
                        {
                            "raw_description": "Dell Latitude 7400 Laptop",
                            "normalized_description": "Dell Latitude 7400 Laptop",
                            "quantity": "1",
                            "unit_price": "64000.00",
                            "line_total": "64000.00",
                            "manufacturer_hint": "Dell",
                            "model_hint": "Latitude 7400",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        }
                    ],
                }
            ),
            raw_response={"ok": True, "pipeline": "invoice"},
            latency_ms=144,
            provider_request_id="req-phase6-asset-dup-no-serial",
        )
        provider.classify_invoice_line_items.return_value = [
            InvoiceLineItemClassification.model_validate(
                {
                    "line_number": 1,
                    "classification": {
                        "inventory_type": "asset",
                        "inventory_confidence": 0.86,
                        "classification_rationale": "Model and supplier match an existing single-asset procurement.",
                        "requires_review": True,
                        "normalized_item_name": "Dell Latitude 7400 Laptop",
                        "suggested_category_name": "Laptops AI",
                        "unsupported_for_approval": False,
                    },
                }
            )
        ]
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase6-asset-dup-no-serial", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=33,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase6-asset-dup-no-serial"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=provider)
        line_item = job.invoice_review.line_items.get()
        line_item.final_inventory_type = "asset"
        line_item.final_category_name = "Laptops AI"
        line_item.save(update_fields=["final_inventory_type", "final_category_name", "updated_at"])

        with self.assertRaises(ValidationError):
            approve_line_item(line_item=line_item, actor=self.staff, company=self.company)

    def test_phase4_asset_line_item_with_quantity_gt_one_requires_review_split(self):
        provider = self._invoice_provider_success()
        provider.extract_invoice_payload.return_value = ExtractionResult(
            payload=InvoiceIntakeExtraction.model_validate(
                {
                    "invoice_header": {
                        "supplier_name": "AI Supplier",
                        "invoice_number": "INV-3001",
                        "order_number": "PO-3001",
                        "invoice_date": "2026-06-24",
                        "currency": "INR",
                        "subtotal_amount": "2500.00",
                        "tax_amount": "450.00",
                        "total_amount": "2950.00",
                    },
                    "line_items": [
                        {
                            "raw_description": "Dell Latitude 7400 Laptop bundle",
                            "normalized_description": "Dell Latitude 7400 Laptop bundle",
                            "quantity": "2",
                            "unit_price": "1250.00",
                            "line_total": "2500.00",
                            "manufacturer_hint": "Dell",
                            "model_hint": "Latitude 7400",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        }
                    ],
                }
            ),
            raw_response={"ok": True, "pipeline": "invoice"},
            latency_ms=151,
            provider_request_id="req-invoice-qty-2",
        )
        provider.classify_invoice_line_items.return_value = [
            InvoiceLineItemClassification.model_validate(
                {
                    "line_number": 1,
                    "classification": {
                        "inventory_type": "asset",
                        "inventory_confidence": 0.79,
                        "classification_rationale": "Bundle likely represents multiple tracked assets.",
                        "requires_review": True,
                        "normalized_item_name": "Dell Latitude 7400 Laptop bundle",
                        "suggested_category_name": "Laptops AI",
                        "unsupported_for_approval": False,
                    },
                }
            )
        ]
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase4-qty2", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=20,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase4-qty2"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=provider)
        line_item = job.invoice_review.line_items.get()
        line_item.final_inventory_type = "asset"
        line_item.final_category_name = "Laptops AI"
        line_item.save(update_fields=["final_inventory_type", "final_category_name", "updated_at"])

        with self.assertRaises(ValidationError):
            approve_line_item(line_item=line_item, actor=self.staff, company=self.company)

    def test_phase5_line_item_approval_persists_training_signal(self):
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase5-signal", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=21,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase5-signal"),
            uploaded_by=self.staff,
        )
        job = process_document_line_items(document=document, actor=self.staff, provider=self._invoice_provider_success())
        line_item = job.invoice_review.line_items.order_by("line_number")[1]
        line_item.final_inventory_type = "consumable"
        line_item.final_category_name = "Ink"
        line_item.save(update_fields=["final_inventory_type", "final_category_name", "updated_at"])

        approve_line_item(line_item=line_item, actor=self.staff, company=self.company)

        signal = AIIntakeClassificationSignal.objects.get(source_line_item=line_item)
        self.assertEqual(signal.company, self.company)
        self.assertEqual(signal.predicted_inventory_type, "consumable")
        self.assertEqual(signal.final_inventory_type, "consumable")
        self.assertEqual(signal.final_category_name, "Ink")
        self.assertEqual(signal.supplier_name_snapshot, "AI Supplier")

    def test_phase5_similarity_matches_prefer_same_company_examples(self):
        other_company = Company.objects.create(name="Other AI Co")
        same_company_signal = AIIntakeClassificationSignal.objects.create(
            company=self.company,
            predicted_inventory_type="accessory",
            final_inventory_type="consumable",
            predicted_category_name="Office",
            final_category_name="Labels",
            raw_description="Premium shipping labels",
            normalized_item_name="Premium shipping labels",
            supplier_name_snapshot="AI Supplier",
            correction_applied=True,
        )
        AIIntakeClassificationSignal.objects.create(
            company=other_company,
            predicted_inventory_type="accessory",
            final_inventory_type="accessory",
            predicted_category_name="Office",
            final_category_name="Desk Accessories",
            raw_description="Premium shipping labels",
            normalized_item_name="Premium shipping labels",
            supplier_name_snapshot="AI Supplier",
            correction_applied=False,
        )

        matches = find_similarity_matches(
            normalized_description="Premium shipping labels",
            supplier_name="AI Supplier",
            company=self.company,
            limit=2,
        )

        self.assertEqual(matches[0].signal_id, same_company_signal.pk)
        self.assertEqual(matches[0].final_inventory_type, "consumable")

    def test_phase5_similarity_assist_uses_approved_examples_without_global_forcing(self):
        AIIntakeClassificationSignal.objects.create(
            company=self.company,
            predicted_inventory_type="accessory",
            final_inventory_type="consumable",
            predicted_category_name="Office",
            final_category_name="Labels",
            raw_description="Premium shipping labels",
            normalized_item_name="Premium shipping labels",
            supplier_name_snapshot="AI Supplier",
            correction_applied=True,
        )
        provider = Mock()
        provider.extract_invoice_payload.return_value = ExtractionResult(
            payload=InvoiceIntakeExtraction.model_validate(
                {
                    "invoice_header": {
                        "supplier_name": "AI Supplier",
                        "invoice_number": "INV-5001",
                        "order_number": "PO-5001",
                        "invoice_date": "2026-06-24",
                        "currency": "INR",
                    },
                    "line_items": [
                        {
                            "raw_description": "Premium shipping labels",
                            "normalized_description": "Premium shipping labels",
                            "quantity": "1",
                            "unit_price": "80.00",
                            "line_total": "80.00",
                            "manufacturer_hint": "",
                            "model_hint": "",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        }
                    ],
                }
            ),
            raw_response={"ok": True},
            latency_ms=111,
            provider_request_id="req-phase5-similarity",
        )
        provider.classify_invoice_line_items = AzureOpenAIIntakeClient().classify_invoice_line_items
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase5-similarity", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=25,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase5-similarity"),
            uploaded_by=self.staff,
        )

        job = process_document_line_items(document=document, actor=self.staff, provider=provider)

        line_item = job.invoice_review.line_items.get()
        self.assertEqual(line_item.predicted_inventory_type, "consumable")
        self.assertEqual(line_item.predicted_category_name, "Labels")
        self.assertTrue(line_item.reviewer_feedback["retrieval_examples"])

        other_company = Company.objects.create(name="Tenant B")
        AIIntakeClassificationSignal.objects.all().delete()
        AIIntakeClassificationSignal.objects.create(
            company=other_company,
            predicted_inventory_type="accessory",
            final_inventory_type="consumable",
            predicted_category_name="Office",
            final_category_name="Labels",
            raw_description="Premium shipping labels",
            normalized_item_name="Premium shipping labels",
            supplier_name_snapshot="AI Supplier",
            correction_applied=True,
        )
        second_document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase5-other-tenant", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=27,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase5-other-tenant"),
            uploaded_by=self.staff,
        )

        second_job = process_document_line_items(document=second_document, actor=self.staff, provider=provider)

        second_line_item = second_job.invoice_review.line_items.get()
        self.assertEqual(second_line_item.predicted_inventory_type, "accessory")

    def test_phase5_review_analytics_reports_corrections_and_unsupported_volume(self):
        AIIntakeClassificationSignal.objects.create(
            company=self.company,
            predicted_inventory_type="accessory",
            final_inventory_type="consumable",
            predicted_category_name="Office",
            final_category_name="Ink",
            raw_description="Printer ink cartridge",
            normalized_item_name="Printer ink cartridge",
            supplier_name_snapshot="AI Supplier",
            classification_confidence="0.540",
            correction_applied=True,
        )
        AIIntakeClassificationSignal.objects.create(
            company=self.company,
            predicted_inventory_type="asset",
            final_inventory_type="asset",
            predicted_category_name="Laptops AI",
            final_category_name="Laptops AI",
            raw_description="Latitude 7400 laptop",
            normalized_item_name="Latitude 7400 laptop",
            supplier_name_snapshot="AI Supplier",
            classification_confidence="0.650",
            correction_applied=False,
        )
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-phase5-analytics", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=24,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-phase5-analytics"),
            uploaded_by=self.staff,
        )
        job = AIIntakeJob.objects.create(document=document, status=AIIntakeJob.Status.SUCCEEDED)
        review = AIIntakeInvoiceReview.objects.create(job=job, review_company=self.company)
        AIIntakeLineItem.objects.create(
            invoice_review=review,
            line_number=1,
            raw_description="Replacement SSD",
            normalized_description="Replacement SSD",
            predicted_inventory_type="component",
            classification_rationale="Reserved rollout type.",
            review_status=AIIntakeLineItem.ReviewStatus.UNSUPPORTED,
            unsupported_for_approval=True,
        )

        analytics = get_review_analytics(company=self.company)

        self.assertEqual(analytics["top_corrected_predicted_types"][0]["predicted_inventory_type"], "accessory")
        self.assertTrue(any(entry["family"] == "printer ink cartridge" for entry in analytics["low_confidence_item_families"]))
        self.assertEqual(analytics["unsupported_item_volume"]["total"], 1)
        self.assertEqual(analytics["unlock_candidates"][0]["predicted_inventory_type"], "component")

    def test_phase0_to_phase6_end_to_end_smoke_path_remains_coherent(self):
        legacy_document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("legacy-invoice.pdf", b"legacy-smoke-bytes", content_type="application/pdf"),
            original_filename="legacy-invoice.pdf",
            content_type="application/pdf",
            size_bytes=18,
            sha256=AIIntakeDocument.hash_bytes(b"legacy-smoke-bytes"),
            uploaded_by=self.staff,
        )
        legacy_job = process_document(document=legacy_document, actor=self.staff, provider=self._provider_success(serial="SER-SMOKE-LEGACY"))
        legacy_asset = legacy_job.draft
        self.assertEqual(legacy_asset.status, AIIntakeDraft.Status.PENDING_REVIEW)
        self.client.force_login(self.staff)
        response = self.client.post(reverse("ai_intake:approve", kwargs={"pk": legacy_job.draft.pk}), {"company": self.company.pk})
        self.assertEqual(response.status_code, 302)
        legacy_job.draft.refresh_from_db()
        self.assertEqual(legacy_job.draft.status, AIIntakeDraft.Status.APPROVED)
        self.assertIsNotNone(legacy_job.draft.approved_asset)

        provider = Mock()
        provider.extract_invoice_payload.return_value = ExtractionResult(
            payload=InvoiceIntakeExtraction.model_validate(
                {
                    "invoice_header": {
                        "supplier_name": "AI Supplier",
                        "invoice_number": "INV-SMOKE-6000",
                        "order_number": "PO-SMOKE-6000",
                        "invoice_date": "2026-06-24",
                        "currency": "INR",
                        "subtotal_amount": "17750.00",
                        "tax_amount": "3195.00",
                        "total_amount": "20945.00",
                    },
                    "line_items": [
                        {
                            "raw_description": "Dell Latitude 7400 Laptop",
                            "normalized_description": "Dell Latitude 7400 Laptop",
                            "quantity": "1",
                            "unit_price": "1250.00",
                            "line_total": "1250.00",
                            "manufacturer_hint": "Dell",
                            "model_hint": "Latitude 7400",
                            "serial_hint": "SER-SMOKE-ASSET",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                        {
                            "raw_description": "USB-C Docking Station",
                            "normalized_description": "USB-C Docking Station",
                            "quantity": "3",
                            "unit_price": "2500.00",
                            "line_total": "7500.00",
                            "manufacturer_hint": "",
                            "model_hint": "",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                        {
                            "raw_description": "Printer Ink Cartridge",
                            "normalized_description": "Printer Ink Cartridge",
                            "quantity": "5",
                            "unit_price": "300.00",
                            "line_total": "1500.00",
                            "manufacturer_hint": "",
                            "model_hint": "",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                        {
                            "raw_description": "Replacement SSD 1TB",
                            "normalized_description": "Replacement SSD 1TB",
                            "quantity": "2",
                            "unit_price": "1800.00",
                            "line_total": "3600.00",
                            "manufacturer_hint": "",
                            "model_hint": "",
                            "serial_hint": "",
                            "part_number_hint": "SSD-SMOKE-1TB",
                            "reference_hint": "CMP-SMOKE-1",
                            "component_role_hint": "replacement",
                            "notes": "",
                        },
                        {
                            "raw_description": "Microsoft 365 Business Premium Annual Subscription",
                            "normalized_description": "Microsoft 365 Business Premium",
                            "quantity": "8",
                            "unit_price": "636.875",
                            "line_total": "5095.00",
                            "manufacturer_hint": "Microsoft",
                            "model_hint": "",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "LIC-SMOKE-1",
                            "seat_hint": 8,
                            "product_key_hint": "SMOKE-AAAA-BBBB-CCCC",
                            "license_reference_hint": "LIC-SMOKE-1",
                            "expiry_date_hint": "2027-06-24",
                            "renewal_date_hint": "2027-06-20",
                            "billing_term_hint": "Annual",
                            "notes": "",
                        },
                    ],
                }
            ),
            raw_response={"ok": True, "pipeline": "invoice"},
            latency_ms=160,
            provider_request_id="req-phase-smoke",
        )
        provider.classify_invoice_line_items.return_value = [
            InvoiceLineItemClassification.model_validate(
                {
                    "line_number": 1,
                    "classification": {
                        "inventory_type": "asset",
                        "inventory_confidence": 0.97,
                        "classification_rationale": "Serial and model identity are explicit.",
                        "requires_review": True,
                        "normalized_item_name": "Dell Latitude 7400 Laptop",
                        "suggested_category_name": "Laptops AI",
                        "unsupported_for_approval": False,
                    },
                }
            ),
            InvoiceLineItemClassification.model_validate(
                {
                    "line_number": 2,
                    "classification": {
                        "inventory_type": "accessory",
                        "inventory_confidence": 0.83,
                        "classification_rationale": "Issued support gear with quantity-based handling.",
                        "requires_review": True,
                        "normalized_item_name": "USB-C Docking Station",
                        "suggested_category_name": "Docking",
                        "unsupported_for_approval": False,
                    },
                }
            ),
            InvoiceLineItemClassification.model_validate(
                {
                    "line_number": 3,
                    "classification": {
                        "inventory_type": "consumable",
                        "inventory_confidence": 0.85,
                        "classification_rationale": "Depleting stock item.",
                        "requires_review": True,
                        "normalized_item_name": "Printer Ink Cartridge",
                        "suggested_category_name": "Ink",
                        "unsupported_for_approval": False,
                    },
                }
            ),
            InvoiceLineItemClassification.model_validate(
                {
                    "line_number": 4,
                    "classification": {
                        "inventory_type": "component",
                        "inventory_confidence": 0.88,
                        "classification_rationale": "Spare internal part signals are explicit.",
                        "requires_review": True,
                        "normalized_item_name": "Replacement SSD 1TB",
                        "suggested_category_name": "Storage Parts",
                        "unsupported_for_approval": False,
                    },
                }
            ),
            InvoiceLineItemClassification.model_validate(
                {
                    "line_number": 5,
                    "classification": {
                        "inventory_type": "license",
                        "inventory_confidence": 0.92,
                        "classification_rationale": "Seat and subscription signals are explicit.",
                        "requires_review": True,
                        "normalized_item_name": "Microsoft 365 Business Premium",
                        "suggested_category_name": "Productivity SaaS",
                        "unsupported_for_approval": False,
                    },
                }
            ),
        ]

        modern_document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("modern-invoice.pdf", b"modern-smoke-bytes", content_type="application/pdf"),
            original_filename="modern-invoice.pdf",
            content_type="application/pdf",
            size_bytes=18,
            sha256=AIIntakeDocument.hash_bytes(b"modern-smoke-bytes"),
            uploaded_by=self.staff,
        )
        modern_job = process_document_line_items(document=modern_document, actor=self.staff, provider=provider)
        review = modern_job.invoice_review
        self.assertEqual(review.line_items.count(), 5)
        self.assertEqual(review.status, AIIntakeInvoiceReview.Status.EXTRACTED)

        expected = {
            1: ("asset", "Laptops AI", {}),
            2: ("accessory", "Docking", {}),
            3: ("consumable", "Ink", {}),
            4: (
                "component",
                "Storage Parts",
                {
                    "type_specific_review": {
                        "component_role_hint": "replacement",
                        "component_min_quantity": 1,
                        "component_part_number": "SSD-SMOKE-1TB",
                        "component_reference": "CMP-SMOKE-1",
                    }
                },
            ),
            5: (
                "license",
                "Productivity SaaS",
                {
                    "type_specific_review": {
                        "license_seats": 8,
                        "license_product_key": "SMOKE-AAAA-BBBB-CCCC",
                        "license_reference": "LIC-SMOKE-1",
                        "license_expiration_date": "2027-06-24",
                        "license_renewal_date": "2027-06-20",
                        "license_billing_term": "Annual",
                    }
                },
            ),
        }

        created_models = []
        for line_item in review.line_items.order_by("line_number"):
            final_type, final_category, review_feedback = expected[line_item.line_number]
            line_item.final_inventory_type = final_type
            line_item.final_category_name = final_category
            line_item.reviewer_feedback = {
                **(line_item.reviewer_feedback or {}),
                **review_feedback,
            }
            line_item.save(update_fields=["final_inventory_type", "final_category_name", "reviewer_feedback", "updated_at"])
            created_models.append(approve_line_item(line_item=line_item, actor=self.staff, company=self.company))

        review.refresh_from_db()
        modern_document.refresh_from_db()
        self.assertEqual(review.status, AIIntakeInvoiceReview.Status.APPROVED_COMPLETE)
        self.assertEqual(modern_document.status, AIIntakeDocument.Status.COMPLETED)
        self.assertEqual([record._meta.model_name for record in created_models], ["asset", "accessory", "consumable", "component", "license"])
        self.assertEqual(AIIntakeClassificationSignal.objects.filter(source_line_item__invoice_review=review).count(), 5)


    def test_invoice_schema_failure_uses_single_repair_retry(self):
        provider = Mock()
        provider.extract_invoice_payload.side_effect = AIProviderSchemaError(
            "Azure OpenAI returned an invalid structured payload.",
            raw_text='{"invoice_header": {"supplier_name": "AI Supplier"}, "line_items": [',
        )
        provider.repair_invoice_payload.return_value = ExtractionResult(
            payload=InvoiceIntakeExtraction.model_validate(
                {
                    "invoice_header": {
                        "supplier_name": "AI Supplier",
                        "invoice_number": "INV-REPAIR-1",
                        "order_number": "PO-REPAIR-1",
                        "invoice_date": "2026-06-24",
                        "currency": "INR",
                        "subtotal_amount": "1240.00",
                        "tax_amount": "0.00",
                        "total_amount": "1240.00",
                    },
                    "line_items": [
                        {
                            "raw_description": "Dell Latitude 7400 Laptop",
                            "normalized_description": "Dell Latitude 7400 Laptop",
                            "quantity": "1",
                            "unit_price": "1240.00",
                            "line_total": "1240.00",
                            "manufacturer_hint": "Dell",
                            "model_hint": "Latitude 7400",
                            "serial_hint": "SER-REPAIR-1",
                            "part_number_hint": "LAT-7400",
                            "reference_hint": "",
                            "notes": "",
                        }
                    ],
                }
            ),
            raw_response={"repair_response": {"ok": True}},
            latency_ms=160,
            provider_request_id="req-repair-1",
        )
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-repair-success", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=22,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-repair-success"),
            uploaded_by=self.staff,
        )

        job = process_document_line_items(document=document, actor=self.staff, provider=provider)

        self.assertEqual(job.status, AIIntakeJob.Status.SUCCEEDED)
        self.assertTrue(job.raw_response["repair_attempted"])
        self.assertTrue(job.raw_response["repair_succeeded"])
        self.assertTrue(job.invoice_review.review_summary["repair_succeeded"])
        self.assertTrue(job.invoice_review.review_summary["document_retry_attempted"])
        self.assertTrue(job.invoice_review.review_summary["document_retry_used"])
        provider.repair_invoice_payload.assert_called_once_with(
            raw_text='{"invoice_header": {"supplier_name": "AI Supplier"}, "line_items": [',
            file_name="invoice.pdf",
            content_type="application/pdf",
            file_bytes=b"invoice-repair-success",
        )
        self.assertEqual(job.invoice_review.line_items.count(), 1)

    def test_invoice_schema_failure_uses_json_repair_when_document_retry_is_also_malformed(self):
        provider = Mock()
        provider.extract_invoice_payload.side_effect = AIProviderSchemaError(
            "Azure OpenAI returned an invalid structured payload.",
            raw_text='{"invoice_header": {"supplier_name": "AI Supplier"}, "line_items": [',
        )
        provider.repair_invoice_payload.side_effect = [
            AIProviderSchemaError(
                "Azure OpenAI returned an invalid structured payload.",
                raw_text='{"invoice_header": {"supplier_name": "AI Supplier", "invoice_number": "INV-REPAIR-2"}, "line_items": [',
            ),
            ExtractionResult(
                payload=InvoiceIntakeExtraction.model_validate(
                    {
                        "invoice_header": {
                            "supplier_name": "AI Supplier",
                            "invoice_number": "INV-REPAIR-2",
                            "order_number": "PO-REPAIR-2",
                            "invoice_date": "2026-06-24",
                            "currency": "INR",
                            "subtotal_amount": "1240.00",
                            "tax_amount": "0.00",
                            "total_amount": "1240.00",
                        },
                        "line_items": [
                            {
                                "raw_description": "Dell Latitude 7400 Laptop",
                                "normalized_description": "Dell Latitude 7400 Laptop",
                                "quantity": "1",
                                "unit_price": "1240.00",
                                "line_total": "1240.00",
                                "manufacturer_hint": "Dell",
                                "model_hint": "Latitude 7400",
                                "serial_hint": "SER-REPAIR-2",
                                "part_number_hint": "LAT-7400",
                                "reference_hint": "",
                                "notes": "",
                            }
                        ],
                    }
                ),
                raw_response={"repair_mode": "json_repair"},
                latency_ms=155,
                provider_request_id="req-repair-2",
            ),
        ]
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-repair-document-fallback", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=32,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-repair-document-fallback"),
            uploaded_by=self.staff,
        )

        job = process_document_line_items(document=document, actor=self.staff, provider=provider)

        self.assertEqual(job.status, AIIntakeJob.Status.SUCCEEDED)
        self.assertTrue(job.raw_response["repair_attempted"])
        self.assertTrue(job.raw_response["repair_succeeded"])
        self.assertEqual(job.invoice_review.line_items.count(), 1)
        self.assertEqual(provider.repair_invoice_payload.call_count, 2)

    def test_invoice_schema_failure_records_guided_failure_after_repair(self):
        provider = Mock()
        provider.extract_invoice_payload.side_effect = AIProviderSchemaError(
            "Azure OpenAI returned an invalid structured payload.",
            raw_text='{"invoice_header": {}, "line_items": [',
        )
        provider.repair_invoice_payload.side_effect = AIProviderError("Repair attempt failed")
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-repair-fail", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=19,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-repair-fail"),
            uploaded_by=self.staff,
        )

        job = process_document_line_items(document=document, actor=self.staff, provider=provider)
        document.refresh_from_db()

        self.assertEqual(job.status, AIIntakeJob.Status.FAILED)
        self.assertEqual(document.status, AIIntakeDocument.Status.FAILED)
        self.assertTrue(job.raw_response["repair_attempted"])
        self.assertIn("Repair attempt failed", job.raw_response["failure_reason"])

    def test_invoice_completeness_retry_re_reads_document_and_prefers_more_complete_result(self):
        provider = Mock()
        provider.extract_invoice_payload.return_value = ExtractionResult(
            payload=InvoiceIntakeExtraction.model_validate(
                {
                    "invoice_header": {
                        "supplier_name": "AI Supplier",
                        "invoice_number": "INV-RETRY-ROWS-1",
                        "order_number": "PO-RETRY-ROWS-1",
                        "invoice_date": "2026-06-24",
                        "currency": "INR",
                        "merchandise_row_count": 5,
                        "subtotal_amount": "1700.00",
                        "tax_amount": "0.00",
                        "total_amount": "1700.00",
                    },
                    "line_items": [
                        {
                            "raw_description": "Dell Inspiron",
                            "normalized_description": "Dell Inspiron",
                            "quantity": "1",
                            "unit_price": "1000.00",
                            "line_total": "1000.00",
                            "manufacturer_hint": "Dell",
                            "model_hint": "Inspiron",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                        {
                            "raw_description": "ADATA SSD",
                            "normalized_description": "ADATA SSD",
                            "quantity": "1",
                            "unit_price": "200.00",
                            "line_total": "200.00",
                            "manufacturer_hint": "ADATA",
                            "model_hint": "SSD",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                        {
                            "raw_description": "8GB DDR4 RAM",
                            "normalized_description": "8GB DDR4 RAM",
                            "quantity": "1",
                            "unit_price": "100.00",
                            "line_total": "100.00",
                            "manufacturer_hint": "",
                            "model_hint": "",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                    ],
                }
            ),
            raw_response={"ok": True, "pipeline": "invoice"},
            latency_ms=140,
            provider_request_id="req-retry-rows-1",
        )
        provider.repair_invoice_payload.return_value = ExtractionResult(
            payload=InvoiceIntakeExtraction.model_validate(
                {
                    "invoice_header": {
                        "supplier_name": "AI Supplier",
                        "invoice_number": "INV-RETRY-ROWS-1",
                        "order_number": "PO-RETRY-ROWS-1",
                        "invoice_date": "2026-06-24",
                        "currency": "INR",
                        "merchandise_row_count": 5,
                        "subtotal_amount": "1700.00",
                        "tax_amount": "0.00",
                        "total_amount": "1700.00",
                    },
                    "line_items": [
                        {
                            "raw_description": "Dell Inspiron",
                            "normalized_description": "Dell Inspiron",
                            "quantity": "1",
                            "unit_price": "1000.00",
                            "line_total": "1000.00",
                            "manufacturer_hint": "Dell",
                            "model_hint": "Inspiron",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                        {
                            "raw_description": "ADATA SSD",
                            "normalized_description": "ADATA SSD",
                            "quantity": "1",
                            "unit_price": "200.00",
                            "line_total": "200.00",
                            "manufacturer_hint": "ADATA",
                            "model_hint": "SSD",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                        {
                            "raw_description": "8GB DDR4 RAM",
                            "normalized_description": "8GB DDR4 RAM",
                            "quantity": "1",
                            "unit_price": "100.00",
                            "line_total": "100.00",
                            "manufacturer_hint": "",
                            "model_hint": "",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                        {
                            "raw_description": "Dell Essential Back Pack",
                            "normalized_description": "Dell Essential Back Pack",
                            "quantity": "1",
                            "unit_price": "250.00",
                            "line_total": "250.00",
                            "manufacturer_hint": "Dell",
                            "model_hint": "Essential Back Pack",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                        {
                            "raw_description": "Sandisk Cruzer CZ50 16gb Pendrive",
                            "normalized_description": "Sandisk Cruzer CZ50 16gb Pendrive",
                            "quantity": "1",
                            "unit_price": "150.00",
                            "line_total": "150.00",
                            "manufacturer_hint": "Sandisk",
                            "model_hint": "Cruzer CZ50 16gb",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                    ],
                }
            ),
            raw_response={"repair_mode": "document_retry"},
            latency_ms=165,
            provider_request_id="req-retry-rows-2",
        )
        provider.classify_invoice_line_items.return_value = []
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-complete-retry", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=22,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-complete-retry"),
            uploaded_by=self.staff,
        )

        job = process_document_line_items(document=document, actor=self.staff, provider=provider)
        review = job.invoice_review

        self.assertEqual(review.line_items.count(), 5)
        self.assertTrue(review.review_summary["document_retry_attempted"])
        self.assertTrue(review.review_summary["document_retry_succeeded"])
        self.assertTrue(review.review_summary["document_retry_used"])
        self.assertTrue(review.review_summary["document_retry_reasons"])
        self.assertEqual(review.review_summary["expected_merchandise_row_count"], 5)
        provider.repair_invoice_payload.assert_called_once()
        repair_kwargs = provider.repair_invoice_payload.call_args.kwargs
        self.assertEqual(repair_kwargs["file_name"], "invoice.pdf")
        self.assertEqual(repair_kwargs["content_type"], "application/pdf")
        self.assertEqual(repair_kwargs["file_bytes"], b"invoice-complete-retry")
        self.assertIn("Dell Essential Back Pack", [item.raw_description for item in review.line_items.order_by("line_number")])
        self.assertIn("Sandisk Cruzer CZ50 16gb Pendrive", [item.raw_description for item in review.line_items.order_by("line_number")])

    def test_invoice_cleanup_filters_non_inventory_rows_and_records_reconciliation(self):
        provider = Mock()
        provider.extract_invoice_payload.return_value = ExtractionResult(
            payload=InvoiceIntakeExtraction.model_validate(
                {
                    "invoice_header": {
                        "supplier_name": "AI Supplier",
                        "invoice_number": "INV-CLEAN-1",
                        "order_number": "PO-CLEAN-1",
                        "invoice_date": "24/06/2026",
                        "currency": "inr",
                        "subtotal_amount": "1300.00",
                        "tax_amount": "0.00",
                        "total_amount": "1300.00",
                    },
                    "line_items": [
                        {
                            "raw_description": "Dell Latitude 7400 Laptop",
                            "normalized_description": "Dell Latitude 7400 Laptop",
                            "quantity": "1",
                            "unit_price": "1240.00",
                            "line_total": "1250.00",
                            "manufacturer_hint": "Dell",
                            "model_hint": "Latitude 7400",
                            "serial_hint": "SER-CLEAN-1",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                        {
                            "raw_description": "Shipping Charges",
                            "normalized_description": "Shipping Charges",
                            "quantity": "1",
                            "unit_price": "60.00",
                            "line_total": "60.00",
                            "manufacturer_hint": "",
                            "model_hint": "",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                    ],
                }
            ),
            raw_response={"ok": True},
            latency_ms=140,
            provider_request_id="req-clean-1",
        )
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-cleanup", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=15,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-cleanup"),
            uploaded_by=self.staff,
        )

        job = process_document_line_items(document=document, actor=self.staff, provider=provider)
        review = job.invoice_review
        review.refresh_from_db()

        self.assertEqual(review.line_items.count(), 1)
        self.assertEqual(review.review_summary["filtered_non_inventory_count"], 1)
        self.assertEqual(review.invoice_metadata["invoice_date"], "2026-06-24")
        self.assertTrue(review.review_summary["reconciliation"]["issues"])
        self.assertIn("Shipping Charges", review.review_summary["filtered_non_inventory_rows"][0]["raw_description"])

    def test_invoice_declared_row_count_triggers_retry_even_when_subtotal_coverage_is_high(self):
        provider = Mock()
        provider.extract_invoice_payload.return_value = ExtractionResult(
            payload=InvoiceIntakeExtraction.model_validate(
                {
                    "invoice_header": {
                        "supplier_name": "AI Supplier",
                        "invoice_number": "INV-ROWCOUNT-1",
                        "order_number": "PO-ROWCOUNT-1",
                        "invoice_date": "2026-06-24",
                        "currency": "INR",
                        "merchandise_row_count": 5,
                        "subtotal_amount": "12364.81",
                        "tax_amount": "0.00",
                        "total_amount": "12364.81",
                    },
                    "line_items": [
                        {
                            "raw_description": "Dell Inspiron 5570",
                            "normalized_description": "Dell Inspiron 5570",
                            "quantity": "1",
                            "unit_price": "4746.10",
                            "line_total": "4746.10",
                            "manufacturer_hint": "Dell",
                            "model_hint": "Inspiron 5570",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                        {
                            "raw_description": "ADATA SU800 SSD",
                            "normalized_description": "ADATA SU800 SSD",
                            "quantity": "1",
                            "unit_price": "3686.44",
                            "line_total": "3686.44",
                            "manufacturer_hint": "ADATA",
                            "model_hint": "SU800 SSD",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                        {
                            "raw_description": "8GB DDR4 RAM",
                            "normalized_description": "8GB DDR4 RAM",
                            "quantity": "1",
                            "unit_price": "2991.53",
                            "line_total": "2991.53",
                            "manufacturer_hint": "",
                            "model_hint": "",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                        {
                            "raw_description": "Dell Essential Back Pack",
                            "normalized_description": "Dell Essential Back Pack",
                            "quantity": "1",
                            "unit_price": "762.71",
                            "line_total": "762.71",
                            "manufacturer_hint": "Dell",
                            "model_hint": "Essential Back Pack",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                    ],
                }
            ),
            raw_response={"ok": True, "pipeline": "invoice"},
            latency_ms=140,
            provider_request_id="req-rowcount-1",
        )
        provider.repair_invoice_payload.return_value = ExtractionResult(
            payload=InvoiceIntakeExtraction.model_validate(
                {
                    "invoice_header": {
                        "supplier_name": "AI Supplier",
                        "invoice_number": "INV-ROWCOUNT-1",
                        "order_number": "PO-ROWCOUNT-1",
                        "invoice_date": "2026-06-24",
                        "currency": "INR",
                        "merchandise_row_count": 5,
                        "subtotal_amount": "12364.81",
                        "tax_amount": "0.00",
                        "total_amount": "12364.81",
                    },
                    "line_items": [
                        {
                            "raw_description": "Dell Inspiron 5570",
                            "normalized_description": "Dell Inspiron 5570",
                            "quantity": "1",
                            "unit_price": "4746.10",
                            "line_total": "4746.10",
                            "manufacturer_hint": "Dell",
                            "model_hint": "Inspiron 5570",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                        {
                            "raw_description": "ADATA SU800 SSD",
                            "normalized_description": "ADATA SU800 SSD",
                            "quantity": "1",
                            "unit_price": "3686.44",
                            "line_total": "3686.44",
                            "manufacturer_hint": "ADATA",
                            "model_hint": "SU800 SSD",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                        {
                            "raw_description": "8GB DDR4 RAM",
                            "normalized_description": "8GB DDR4 RAM",
                            "quantity": "1",
                            "unit_price": "2991.53",
                            "line_total": "2991.53",
                            "manufacturer_hint": "",
                            "model_hint": "",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                        {
                            "raw_description": "Dell Essential Back Pack",
                            "normalized_description": "Dell Essential Back Pack",
                            "quantity": "1",
                            "unit_price": "762.71",
                            "line_total": "762.71",
                            "manufacturer_hint": "Dell",
                            "model_hint": "Essential Back Pack",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                        {
                            "raw_description": "Sandisk Cruzer CZ50 16gb Pendrive",
                            "normalized_description": "Sandisk Cruzer CZ50 16gb Pendrive",
                            "quantity": "1",
                            "unit_price": "177.97",
                            "line_total": "177.97",
                            "manufacturer_hint": "Sandisk",
                            "model_hint": "Cruzer CZ50 16gb Pendrive",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        },
                    ],
                }
            ),
            raw_response={"repair_mode": "document_retry"},
            latency_ms=165,
            provider_request_id="req-rowcount-2",
        )
        provider.classify_invoice_line_items.return_value = []
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.jpg", b"invoice-rowcount-retry", content_type="image/jpeg"),
            original_filename="invoice.jpg",
            content_type="image/jpeg",
            size_bytes=22,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-rowcount-retry"),
            uploaded_by=self.staff,
        )

        job = process_document_line_items(document=document, actor=self.staff, provider=provider)
        review = job.invoice_review

        self.assertEqual(review.line_items.count(), 5)
        self.assertTrue(review.review_summary["document_retry_used"])
        self.assertIn("Invoice indicates 5 merchandise row(s) but only 4 row(s) were extracted.", review.review_summary["document_retry_reasons"])
        self.assertEqual(review.review_summary["expected_merchandise_row_count"], 5)
        provider.repair_invoice_payload.assert_called_once()

    def test_invoice_policy_forces_review_for_weak_license_and_missing_header(self):
        provider = Mock()
        provider.extract_invoice_payload.return_value = ExtractionResult(
            payload=InvoiceIntakeExtraction.model_validate(
                {
                    "invoice_header": {
                        "supplier_name": "",
                        "invoice_number": "INV-WEAK-LIC-1",
                        "order_number": "",
                        "invoice_date": "",
                        "currency": "INR",
                        "subtotal_amount": "999.00",
                        "tax_amount": "0.00",
                        "total_amount": "999.00",
                    },
                    "line_items": [
                        {
                            "raw_description": "Adobe Creative Cloud",
                            "normalized_description": "Adobe Creative Cloud",
                            "quantity": "1",
                            "unit_price": "999.00",
                            "line_total": "999.00",
                            "manufacturer_hint": "Adobe",
                            "model_hint": "",
                            "serial_hint": "",
                            "part_number_hint": "",
                            "reference_hint": "",
                            "notes": "",
                        }
                    ],
                }
            ),
            raw_response={"ok": True},
            latency_ms=135,
            provider_request_id="req-weak-license",
        )
        provider.classify_invoice_line_items.return_value = [
            InvoiceLineItemClassification.model_validate(
                {
                    "line_number": 1,
                    "classification": {
                        "inventory_type": "license",
                        "inventory_confidence": 0.93,
                        "classification_rationale": "License-like vendor signal.",
                        "requires_review": False,
                        "normalized_item_name": "Adobe Creative Cloud",
                        "suggested_category_name": "Creative SaaS",
                        "unsupported_for_approval": False,
                    },
                }
            )
        ]
        document = AIIntakeDocument.objects.create(
            file=SimpleUploadedFile("invoice.pdf", b"invoice-weak-license", content_type="application/pdf"),
            original_filename="invoice.pdf",
            content_type="application/pdf",
            size_bytes=20,
            sha256=AIIntakeDocument.hash_bytes(b"invoice-weak-license"),
            uploaded_by=self.staff,
        )

        job = process_document_line_items(document=document, actor=self.staff, provider=provider)
        line_item = job.invoice_review.line_items.get()

        self.assertEqual(line_item.predicted_inventory_type, "license")
        self.assertTrue(line_item.requires_review)
        self.assertIn("Supplier name is missing", line_item.classification_rationale)
        self.assertIn("Invoice date is missing", line_item.classification_rationale)

