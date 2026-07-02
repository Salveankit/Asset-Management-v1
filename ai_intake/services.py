from __future__ import annotations

import json
from decimal import Decimal

from accessories.models import Accessory
from components.models import Component
from consumables.models import Consumable
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils.dateparse import parse_date

from assets.models import Asset, AssetModel
from catalogue.models import Category, Manufacturer, StatusLabel
from licences.models import License
from suppliers.models import Supplier

from .learning import build_similarity_examples_for_invoice, record_training_signal_from_draft, record_training_signal_from_line_item
from .models import AIIntakeAuditEvent, AIIntakeDocument, AIIntakeDraft, AIIntakeInvoiceReview, AIIntakeJob, AIIntakeLineItem
from .policy import (
    build_reviewer_feedback,
    derive_invoice_line_item_classification,
    enrich_legacy_extracted_data,
    filter_invoice_line_items,
    get_extracted_inventory_classification,
    normalize_inventory_classification,
    normalize_invoice_extraction,
    summarize_invoice_reconciliation,
)
from .provider import AIProviderError, AIProviderNotConfiguredError, AIProviderSchemaError, AzureOpenAIIntakeClient, get_intake_provider
from .schemas import InvoiceIntakeExtraction, InvoiceLineItemClassification, InvoiceLineItemExtraction


def log_ai_event(*, event_type: str, actor=None, document=None, job=None, draft=None, status: str = "", latency_ms=None, metadata=None):
    return AIIntakeAuditEvent.objects.create(
        document=document,
        job=job,
        draft=draft,
        actor=actor,
        event_type=event_type,
        status=status,
        latency_ms=latency_ms,
        metadata=metadata or {},
    )


def evaluate_duplicate_risk(extracted_data: dict) -> tuple[str, list[str], str]:
    reasons = []
    serial = (extracted_data.get("serial") or "").strip()
    order_number = (extracted_data.get("order_number") or "").strip()
    model_name = (extracted_data.get("model_name") or "").strip()
    supplier_name = (extracted_data.get("supplier_name") or "").strip()

    if serial and Asset.objects.filter(serial__iexact=serial, deleted_at__isnull=True).exists():
        reasons.append(f"Existing asset uses serial {serial}.")
    if order_number and Asset.objects.filter(order_number__iexact=order_number, deleted_at__isnull=True).exists():
        reasons.append(f"Existing asset uses order number {order_number}.")
    if model_name and supplier_name and Asset.objects.filter(
        model__name__iexact=model_name,
        supplier__name__iexact=supplier_name,
        deleted_at__isnull=True,
    ).exists():
        reasons.append("Existing asset shares model and supplier combination.")

    if any("serial" in reason.lower() or "order number" in reason.lower() for reason in reasons):
        return AIIntakeDraft.RiskLevel.HIGH, reasons, AIIntakeDraft.RecommendedAction.BLOCK
    if reasons:
        return AIIntakeDraft.RiskLevel.MEDIUM, reasons, AIIntakeDraft.RecommendedAction.REVIEW
    return AIIntakeDraft.RiskLevel.LOW, reasons, AIIntakeDraft.RecommendedAction.ALLOW


def _decimal_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _decimal_to_positive_int(value, *, default: int = 1) -> int:
    try:
        quantity = int(Decimal(str(value)))
    except (ArithmeticError, TypeError, ValueError):
        return default
    return quantity if quantity > 0 else default


def _clean_string(value, *, default: str = "") -> str:
    cleaned = str(value or "").strip()
    return cleaned or default


def _resolve_supplier(*, supplier_name: str):
    if not supplier_name:
        return None
    supplier, _ = Supplier.objects.get_or_create(name=supplier_name)
    return supplier


def _resolve_manufacturer(*, manufacturer_name: str):
    if not manufacturer_name:
        return None
    manufacturer, _ = Manufacturer.objects.get_or_create(name=manufacturer_name)
    return manufacturer


def _resolve_category(*, category_name: str, category_type: str) -> Category:
    category, _ = Category.objects.get_or_create(
        name=category_name,
        category_type=category_type,
        defaults={"notes": "Created by AI intake approval."},
    )
    return category


def _resolve_asset_status_label() -> StatusLabel:
    resolved_status = StatusLabel.objects.filter(default_label=True, deleted_at__isnull=True).first()
    if resolved_status is None:
        resolved_status = StatusLabel.objects.filter(deleted_at__isnull=True, deployable=True).first()
    if resolved_status is None:
        resolved_status = StatusLabel.objects.create(
            name="Ready for Deployment",
            deployable=True,
            default_label=True,
            notes="Created automatically during AI intake approval.",
        )
    return resolved_status


def _save_validated_instance(instance):
    instance.full_clean()
    instance.save()
    return instance


def _find_existing_asset_duplicate(*, name: str, model: AssetModel, company=None, supplier=None, location=None, purchase_date=None, order_number: str = ""):
    cleaned_name = _clean_string(name)
    cleaned_order_number = _clean_string(order_number)
    if not cleaned_name:
        return None

    duplicates = Asset.objects.filter(
        deleted_at__isnull=True,
        name__iexact=cleaned_name,
        model=model,
        serial="",
    )

    duplicates = duplicates.filter(supplier_id=supplier.id) if supplier else duplicates.filter(supplier__isnull=True)
    duplicates = duplicates.filter(default_location_id=location.id) if location else duplicates.filter(default_location__isnull=True)
    duplicates = duplicates.filter(purchase_date=purchase_date) if purchase_date else duplicates.filter(purchase_date__isnull=True)
    duplicates = duplicates.filter(order_number__iexact=cleaned_order_number) if cleaned_order_number else duplicates.filter(order_number="")

    if company:
        duplicates = duplicates.filter(Q(company=company) | Q(company__isnull=True))
    else:
        duplicates = duplicates.filter(company__isnull=True)

    return duplicates.first()



def _normalize_invoice_extraction(payload: InvoiceIntakeExtraction | dict) -> InvoiceIntakeExtraction:
    return normalize_invoice_extraction(payload)


def _invoice_completeness_signals(
    *,
    invoice_payload: InvoiceIntakeExtraction,
    excluded_items: list[dict],
    reconciliation_summary: dict,
) -> list[str]:
    reasons = []
    line_count = len(invoice_payload.line_items)
    if line_count == 0:
        reasons.append("No merchandise line items were extracted.")
        return reasons

    expected_row_count = invoice_payload.invoice_header.merchandise_row_count
    if expected_row_count and line_count < expected_row_count:
        reasons.append(
            f"Invoice indicates {expected_row_count} merchandise row(s) but only {line_count} row(s) were extracted."
        )

    subtotal_amount = invoice_payload.invoice_header.subtotal_amount
    line_total_sum = None
    try:
        line_total_sum = Decimal(str(reconciliation_summary.get("line_total_sum") or "0"))
    except Exception:
        line_total_sum = None

    if subtotal_amount is not None and line_total_sum is not None:
        gap = subtotal_amount - line_total_sum
        coverage = (line_total_sum / subtotal_amount) if subtotal_amount else Decimal("1")
        if line_count <= 4 and gap > Decimal("1.00") and coverage < Decimal("0.90"):
            reasons.append("Extracted merchandise subtotal covers too little of the invoice subtotal for such a small row count.")

    subtotal_issue = next((issue for issue in reconciliation_summary.get("issues", []) if issue.get("code") == "subtotal_mismatch"), None)
    if line_count <= 4 and subtotal_issue and not excluded_items:
        reasons.append("Row count is still small and subtotal reconciliation suggests one or more merchandise rows are missing.")

    if line_count <= 2 and not excluded_items:
        if subtotal_issue:
            reasons.append("Very few rows were extracted and subtotal reconciliation suggests missing merchandise rows.")

    return reasons


def _invoice_result_quality(invoice_payload: InvoiceIntakeExtraction, reconciliation_summary: dict) -> tuple[int, Decimal, int]:
    subtotal_amount = invoice_payload.invoice_header.subtotal_amount
    try:
        line_total_sum = Decimal(str(reconciliation_summary.get("line_total_sum") or "0"))
    except Exception:
        line_total_sum = Decimal("0")

    if subtotal_amount in (None, Decimal("0")):
        subtotal_gap = Decimal("0") if invoice_payload.line_items else Decimal("999999")
    else:
        subtotal_gap = abs(subtotal_amount - line_total_sum)

    return (len(invoice_payload.line_items), subtotal_gap, len(reconciliation_summary.get("issues", [])))


def _should_prefer_retry_result(
    *,
    current_payload: InvoiceIntakeExtraction,
    current_reconciliation: dict,
    retry_payload: InvoiceIntakeExtraction,
    retry_reconciliation: dict,
) -> bool:
    current_quality = _invoice_result_quality(current_payload, current_reconciliation)
    retry_quality = _invoice_result_quality(retry_payload, retry_reconciliation)

    if retry_quality[0] > current_quality[0]:
        return True
    if retry_quality[0] < current_quality[0]:
        return False
    if retry_quality[1] < current_quality[1]:
        return True
    if retry_quality[1] > current_quality[1]:
        return False
    return retry_quality[2] < current_quality[2]


def _repair_invoice_payload_from_source(
    *,
    provider,
    raw_text: str,
    file_name: str,
    content_type: str,
    file_bytes: bytes,
):
    try:
        return provider.repair_invoice_payload(
            raw_text=raw_text,
            file_name=file_name,
            content_type=content_type,
            file_bytes=file_bytes,
        )
    except TypeError:
        return provider.repair_invoice_payload(raw_text=raw_text)
    except AIProviderSchemaError as exc:
        return provider.repair_invoice_payload(raw_text=exc.raw_text or raw_text)


def _get_raw_line_item_classifications(
    *,
    provider: AzureOpenAIIntakeClient | None,
    invoice_payload: InvoiceIntakeExtraction,
    retrieval_examples_by_line: dict[int, list[dict]],
) -> list[InvoiceLineItemClassification]:
    if provider is None or isinstance(provider, AzureOpenAIIntakeClient):
        return []
    classify = getattr(provider, "classify_invoice_line_items", None)
    if not callable(classify):
        return []
    try:
        result = classify(
            invoice_payload=invoice_payload,
            retrieval_examples_by_line=retrieval_examples_by_line,
        )
    except TypeError:
        result = classify(invoice_payload=invoice_payload)
    return result if isinstance(result, list) else []


def _parse_optional_date(value):
    cleaned = _clean_string(value)
    if not cleaned:
        return None
    return parse_date(cleaned)


def _get_line_item_review_data(line_item: AIIntakeLineItem) -> dict:
    feedback = dict(line_item.reviewer_feedback or {})
    return dict(feedback.get("type_specific_review") or {})


def _validate_line_item_classification(
    *,
    extracted_item,
    raw_classification: InvoiceLineItemClassification,
    retrieval_examples: list[dict] | None = None,
    invoice_header=None,
) -> InvoiceLineItemClassification:
    classification = derive_invoice_line_item_classification(
        extracted_item=extracted_item,
        initial_classification=raw_classification.classification,
        retrieval_examples=retrieval_examples,
        invoice_header=invoice_header,
    )
    return InvoiceLineItemClassification(
        line_number=raw_classification.line_number,
        classification=classification,
    )


@transaction.atomic
def process_document_line_items(*, document: AIIntakeDocument, actor=None, provider: AzureOpenAIIntakeClient | None = None) -> AIIntakeJob:
    provider = provider or get_intake_provider()
    review_company = getattr(actor, "company", None) if actor is not None else None
    document.status = AIIntakeDocument.Status.PROCESSING
    document.save(update_fields=["status", "updated_at"])
    job = AIIntakeJob.objects.create(document=document, status=AIIntakeJob.Status.RUNNING)
    try:
        file_bytes = document.file.read()
        document.file.seek(0)
    finally:
        document.file.close()

    extraction_attempts = {
        "repair_attempted": False,
        "repair_succeeded": False,
        "document_retry_attempted": False,
        "document_retry_succeeded": False,
        "document_retry_used": False,
        "document_retry_reasons": [],
    }
    try:
        try:
            invoice_result = provider.extract_invoice_payload(
                file_name=document.original_filename,
                content_type=document.content_type,
                file_bytes=file_bytes,
            )
        except AIProviderSchemaError as exc:
            extraction_attempts["repair_attempted"] = True
            extraction_attempts["document_retry_attempted"] = True
            invoice_result = _repair_invoice_payload_from_source(
                provider=provider,
                raw_text=exc.raw_text,
                file_name=document.original_filename,
                content_type=document.content_type,
                file_bytes=file_bytes,
            )
            extraction_attempts["repair_succeeded"] = True
            extraction_attempts["document_retry_succeeded"] = True
            extraction_attempts["document_retry_used"] = True
            extraction_attempts["document_retry_reasons"] = ["Initial structured extraction was malformed, so the document was re-read from source."]

        invoice_payload = _normalize_invoice_extraction(invoice_result.payload)
        invoice_payload, excluded_items = filter_invoice_line_items(invoice_payload)
        reconciliation_summary = summarize_invoice_reconciliation(invoice_payload)

        completeness_reasons = _invoice_completeness_signals(
            invoice_payload=invoice_payload,
            excluded_items=excluded_items,
            reconciliation_summary=reconciliation_summary,
        )
        if completeness_reasons:
            extraction_attempts["document_retry_attempted"] = True
            extraction_attempts["document_retry_reasons"] = completeness_reasons
            retry_result = _repair_invoice_payload_from_source(
                provider=provider,
                raw_text=json.dumps(invoice_payload.model_dump(mode="json")),
                file_name=document.original_filename,
                content_type=document.content_type,
                file_bytes=file_bytes,
            )
            retry_payload = _normalize_invoice_extraction(retry_result.payload)
            retry_payload, retry_excluded_items = filter_invoice_line_items(retry_payload)
            retry_reconciliation_summary = summarize_invoice_reconciliation(retry_payload)
            extraction_attempts["document_retry_succeeded"] = True
            if _should_prefer_retry_result(
                current_payload=invoice_payload,
                current_reconciliation=reconciliation_summary,
                retry_payload=retry_payload,
                retry_reconciliation=retry_reconciliation_summary,
            ):
                invoice_result = retry_result
                invoice_payload = retry_payload
                excluded_items = retry_excluded_items
                reconciliation_summary = retry_reconciliation_summary
                extraction_attempts["document_retry_used"] = True

        retrieval_examples_by_line = build_similarity_examples_for_invoice(invoice_payload=invoice_payload, company=review_company)
        raw_classifications = _get_raw_line_item_classifications(
            provider=provider,
            invoice_payload=invoice_payload,
            retrieval_examples_by_line=retrieval_examples_by_line,
        )
    except (AIProviderNotConfiguredError, AIProviderError, AIProviderSchemaError) as exc:
        job.status = AIIntakeJob.Status.FAILED
        job.error_message = str(exc)
        job.raw_response = {
            "pipeline": "invoice_line_items",
            "repair_attempted": extraction_attempts["repair_attempted"],
            "repair_succeeded": extraction_attempts["repair_succeeded"],
            "document_retry_attempted": extraction_attempts["document_retry_attempted"],
            "document_retry_succeeded": extraction_attempts["document_retry_succeeded"],
            "document_retry_used": extraction_attempts["document_retry_used"],
            "document_retry_reasons": extraction_attempts["document_retry_reasons"],
            "failure_reason": str(exc),
        }
        job.save(update_fields=["status", "error_message", "raw_response", "updated_at"])
        document.status = AIIntakeDocument.Status.FAILED
        document.save(update_fields=["status", "updated_at"])
        log_ai_event(
            event_type=AIIntakeAuditEvent.EventType.EXTRACTION_FAILED,
            actor=actor,
            document=document,
            job=job,
            status=job.status,
            metadata={
                "error": str(exc),
                "pipeline": "invoice_line_items",
                "repair_attempted": extraction_attempts["repair_attempted"],
                "document_retry_attempted": extraction_attempts["document_retry_attempted"],
            },
        )
        return job

    review_summary = {
        "line_item_count": len(invoice_payload.line_items),
        "expected_merchandise_row_count": invoice_payload.invoice_header.merchandise_row_count,
        "pipeline": "invoice_line_items",
        "repair_attempted": extraction_attempts["repair_attempted"],
        "repair_succeeded": extraction_attempts["repair_succeeded"],
        "filtered_non_inventory_count": len(excluded_items),
        "filtered_non_inventory_rows": excluded_items,
        "reconciliation": reconciliation_summary,
        "requires_review": bool(reconciliation_summary.get("requires_review")),
        "document_retry_attempted": extraction_attempts["document_retry_attempted"],
        "document_retry_succeeded": extraction_attempts["document_retry_succeeded"],
        "document_retry_used": extraction_attempts["document_retry_used"],
        "document_retry_reasons": extraction_attempts["document_retry_reasons"],
    }
    invoice_review = AIIntakeInvoiceReview.objects.create(
        job=job,
        review_company=review_company,
        status=AIIntakeInvoiceReview.Status.EXTRACTED,
        extracted_invoice_data=invoice_payload.model_dump(mode="json"),
        invoice_metadata=invoice_payload.invoice_header.model_dump(mode="json"),
        review_summary=review_summary,
    )

    raw_lookup = {entry.line_number: entry for entry in raw_classifications}
    validated_classifications = []
    for index, extracted_item in enumerate(invoice_payload.line_items, start=1):
        classification = raw_lookup.get(index)
        if classification is None:
            classification = InvoiceLineItemClassification.model_validate(
                {
                    "line_number": index,
                    "classification": {
                        "inventory_type": "asset",
                        "inventory_confidence": 0.0,
                        "classification_rationale": "No provider classification was supplied for this line item, so backend rules derived the working prediction.",
                        "requires_review": True,
                        "normalized_item_name": extracted_item.normalized_description or extracted_item.raw_description,
                        "suggested_category_name": "",
                        "unsupported_for_approval": False,
                    },
                }
            )
        validated_classifications.append(
            _validate_line_item_classification(
                extracted_item=extracted_item,
                raw_classification=classification,
                retrieval_examples=retrieval_examples_by_line.get(index, []),
                invoice_header=invoice_payload.invoice_header,
            )
        )

    line_item_rows = []
    forced_review_count = 0
    for index, extracted_item in enumerate(invoice_payload.line_items, start=1):
        classification = next(entry.classification for entry in validated_classifications if entry.line_number == index)
        review_status = AIIntakeLineItem.ReviewStatus.UNSUPPORTED if classification.unsupported_for_approval else AIIntakeLineItem.ReviewStatus.PENDING_REVIEW
        feedback = build_reviewer_feedback(
            original_description=extracted_item.raw_description,
            classification=classification,
            supplier_name=invoice_payload.invoice_header.supplier_name,
            order_number=invoice_payload.invoice_header.order_number,
            purchase_date=invoice_payload.invoice_header.invoice_date,
        ).model_dump(mode="json")
        feedback["retrieval_examples"] = retrieval_examples_by_line.get(index, [])
        feedback["review_warnings"] = [classification.classification_rationale] if classification.classification_rationale else []
        if classification.requires_review:
            forced_review_count += 1
        line_item_rows.append(
            AIIntakeLineItem(
                invoice_review=invoice_review,
                line_number=index,
                raw_description=extracted_item.raw_description,
                normalized_description=extracted_item.normalized_description,
                quantity=extracted_item.quantity,
                unit_price=extracted_item.unit_price,
                line_total=extracted_item.line_total,
                manufacturer_hint=extracted_item.manufacturer_hint,
                model_hint=extracted_item.model_hint,
                serial_hint=extracted_item.serial_hint,
                part_number_hint=extracted_item.part_number_hint,
                reference_hint=extracted_item.reference_hint,
                predicted_inventory_type=classification.inventory_type.value,
                predicted_category_name=classification.suggested_category_name,
                classification_confidence=classification.inventory_confidence,
                classification_rationale=classification.classification_rationale,
                review_status=review_status,
                final_inventory_type="",
                final_category_name="",
                requires_review=classification.requires_review,
                unsupported_for_approval=classification.unsupported_for_approval,
                extraction_payload=extracted_item.model_dump(mode="json"),
                reviewer_feedback=feedback,
            )
        )
    AIIntakeLineItem.objects.bulk_create(line_item_rows)

    if any(item.unsupported_for_approval for item in (row.classification for row in validated_classifications)):
        invoice_review.status = AIIntakeInvoiceReview.Status.UNSUPPORTED_ITEMS_PENDING
    invoice_review.review_summary = {
        **(invoice_review.review_summary or {}),
        "retrieval_match_count": sum(1 for examples in retrieval_examples_by_line.values() if examples),
        "forced_review_count": forced_review_count,
    }
    invoice_review.save(update_fields=["status", "review_summary", "updated_at"])

    job.status = AIIntakeJob.Status.SUCCEEDED
    job.provider_request_id = invoice_result.provider_request_id
    job.latency_ms = invoice_result.latency_ms
    job.raw_response = {
        "invoice_extraction": invoice_result.raw_response,
        "line_item_classifications": [entry.model_dump(mode="json") for entry in validated_classifications],
        "retrieval_examples_by_line": retrieval_examples_by_line,
        "repair_attempted": extraction_attempts["repair_attempted"],
        "repair_succeeded": extraction_attempts["repair_succeeded"],
        "document_retry_attempted": extraction_attempts["document_retry_attempted"],
        "document_retry_succeeded": extraction_attempts["document_retry_succeeded"],
        "document_retry_used": extraction_attempts["document_retry_used"],
        "document_retry_reasons": extraction_attempts["document_retry_reasons"],
        "filtered_non_inventory_rows": excluded_items,
        "reconciliation": reconciliation_summary,
    }
    job.save(update_fields=["status", "provider_request_id", "latency_ms", "raw_response", "updated_at"])
    document.status = AIIntakeDocument.Status.REVIEW
    document.save(update_fields=["status", "updated_at"])
    log_ai_event(
        event_type=AIIntakeAuditEvent.EventType.EXTRACTION_SUCCEEDED,
        actor=actor,
        document=document,
        job=job,
        status=job.status,
        latency_ms=invoice_result.latency_ms,
        metadata={
            "pipeline": "invoice_line_items",
            "invoice_review_id": invoice_review.pk,
            "line_item_count": len(invoice_payload.line_items),
            "filtered_non_inventory_count": len(excluded_items),
            "repair_attempted": extraction_attempts["repair_attempted"],
            "document_retry_attempted": extraction_attempts["document_retry_attempted"],
            "document_retry_used": extraction_attempts["document_retry_used"],
        },
    )
    return job


def process_document(*, document: AIIntakeDocument, actor=None, provider: AzureOpenAIIntakeClient | None = None) -> AIIntakeJob:
    provider = provider or get_intake_provider()
    document.status = AIIntakeDocument.Status.PROCESSING
    document.save(update_fields=["status", "updated_at"])
    job = AIIntakeJob.objects.create(document=document, status=AIIntakeJob.Status.RUNNING)
    try:
        file_bytes = document.file.read()
        document.file.seek(0)
    finally:
        document.file.close()

    try:
        result = provider.extract_asset_draft(
            file_name=document.original_filename,
            content_type=document.content_type,
            file_bytes=file_bytes,
        )
    except (AIProviderNotConfiguredError, AIProviderError, AIProviderSchemaError) as exc:
        job.status = AIIntakeJob.Status.FAILED
        job.error_message = str(exc)
        job.save(update_fields=["status", "error_message", "updated_at"])
        draft = AIIntakeDraft.objects.create(
            job=job,
            status=AIIntakeDraft.Status.RETRY_REQUIRED,
            extracted_data={},
            duplicate_risk_level=AIIntakeDraft.RiskLevel.HIGH,
            duplicate_risk_reasons=[str(exc)],
            recommended_action=AIIntakeDraft.RecommendedAction.REVIEW,
        )
        document.status = AIIntakeDocument.Status.FAILED
        document.save(update_fields=["status", "updated_at"])
        log_ai_event(
            event_type=AIIntakeAuditEvent.EventType.EXTRACTION_FAILED,
            actor=actor,
            document=document,
            job=job,
            draft=draft,
            status=job.status,
            metadata={"error": str(exc)},
        )
        return job

    extracted_data = enrich_legacy_extracted_data(result.payload.model_dump(mode="json"))
    risk_level, reasons, action = evaluate_duplicate_risk(extracted_data)
    draft = AIIntakeDraft.objects.create(
        job=job,
        status=AIIntakeDraft.Status.PENDING_REVIEW,
        extracted_data=extracted_data,
        duplicate_risk_level=risk_level,
        duplicate_risk_reasons=reasons,
        recommended_action=action,
    )
    job.status = AIIntakeJob.Status.SUCCEEDED
    job.provider_request_id = result.provider_request_id
    job.latency_ms = result.latency_ms
    job.raw_response = result.raw_response
    job.save(update_fields=["status", "provider_request_id", "latency_ms", "raw_response", "updated_at"])
    document.status = AIIntakeDocument.Status.REVIEW
    document.save(update_fields=["status", "updated_at"])
    log_ai_event(
        event_type=AIIntakeAuditEvent.EventType.EXTRACTION_SUCCEEDED,
        actor=actor,
        document=document,
        job=job,
        draft=draft,
        status=job.status,
        latency_ms=result.latency_ms,
        metadata={"risk_level": risk_level, "recommended_action": action},
    )
    return job


def _create_asset_from_legacy_draft(*, draft: AIIntakeDraft, company=None, location=None) -> Asset:
    extracted = enrich_legacy_extracted_data(draft.extracted_data)
    category = _resolve_category(
        category_name=_clean_string(extracted.get("category_name"), default="Imported Assets"),
        category_type=Category.CategoryType.ASSET,
    )
    manufacturer = _resolve_manufacturer(manufacturer_name=_clean_string(extracted.get("manufacturer_name")))
    supplier = _resolve_supplier(supplier_name=_clean_string(extracted.get("supplier_name")))
    model, _ = AssetModel.objects.get_or_create(
        name=extracted.get("model_name") or "AI Imported Model",
        model_number=extracted.get("model_number") or "",
        defaults={"category": category, "manufacturer": manufacturer, "notes": "Created by AI intake approval."},
    )
    if not model.category_id:
        model.category = category
        model.manufacturer = manufacturer
        model.save(update_fields=["category", "manufacturer", "updated_at"])

    return Asset.objects.create(
        asset_tag=f"AI-{draft.pk:05d}",
        name=extracted.get("asset_name", ""),
        serial=extracted.get("serial", ""),
        model=model,
        status_label=_resolve_asset_status_label(),
        company=company,
        supplier=supplier,
        default_location=location,
        purchase_date=extracted.get("purchase_date") or None,
        purchase_cost=extracted.get("purchase_cost") or None,
        order_number=extracted.get("order_number", ""),
        notes=extracted.get("notes", ""),
    )


def _get_invoice_header_value(invoice_review: AIIntakeInvoiceReview, key: str, *, default=None):
    invoice_metadata = invoice_review.invoice_metadata or {}
    if invoice_metadata.get(key) not in ("", None):
        return invoice_metadata.get(key)

    header = ((invoice_review.extracted_invoice_data or {}).get("invoice_header") or {})
    if header.get(key) not in ("", None):
        return header.get(key)
    return default


def _get_line_item_quantity(line_item: AIIntakeLineItem) -> int:
    quantity = line_item.quantity
    if quantity in ("", None):
        quantity = (line_item.extraction_payload or {}).get("quantity")
    return _decimal_to_positive_int(quantity, default=1)


def _build_asset_tag_for_line_item(line_item: AIIntakeLineItem, index: int = 1) -> str:
    suffix = f"-{index:02d}" if index > 1 else ""
    return f"AI-LI-{line_item.pk:05d}{suffix}"


def _build_line_item_note(*, line_item: AIIntakeLineItem, invoice_review: AIIntakeInvoiceReview, extra_note_parts: list[str] | None = None) -> str:
    note_parts = []
    invoice_number = _clean_string(_get_invoice_header_value(invoice_review, "invoice_number"))
    order_number = _clean_string(_get_invoice_header_value(invoice_review, "order_number"))
    invoice_date = _clean_string(_get_invoice_header_value(invoice_review, "invoice_date"))
    if invoice_number:
        note_parts.append(f"Invoice: {invoice_number}")
    if order_number:
        note_parts.append(f"Order: {order_number}")
    if invoice_date:
        note_parts.append(f"Date: {invoice_date}")

    extracted_note = _clean_string((line_item.extraction_payload or {}).get("notes"))
    if extracted_note:
        note_parts.append(extracted_note)
    if extra_note_parts:
        note_parts.extend(part for part in extra_note_parts if _clean_string(part))
    return "\n".join(note_parts)


def _create_asset_from_line_item(*, line_item: AIIntakeLineItem, company=None, location=None):
    quantity = _get_line_item_quantity(line_item)

    invoice_review = line_item.invoice_review
    supplier_name = _clean_string(_get_invoice_header_value(invoice_review, "supplier_name"))
    order_number = _clean_string(_get_invoice_header_value(invoice_review, "order_number"))
    risk_level, reasons, action = evaluate_duplicate_risk(
        {
            "serial": _clean_string(line_item.serial_hint),
            "order_number": order_number,
            "model_name": _clean_string(line_item.model_hint),
            "supplier_name": supplier_name,
        }
    )
    if risk_level == AIIntakeDraft.RiskLevel.HIGH or action == AIIntakeDraft.RecommendedAction.BLOCK:
        raise ValidationError("; ".join(reasons) or "Duplicate-risk rules blocked approval for this line item.")

    category = _resolve_category(
        category_name=_clean_string(line_item.final_category_name or line_item.predicted_category_name, default="Imported Assets"),
        category_type=Category.CategoryType.ASSET,
    )
    manufacturer = _resolve_manufacturer(manufacturer_name=_clean_string(line_item.manufacturer_hint))
    supplier = _resolve_supplier(supplier_name=supplier_name)
    model, _ = AssetModel.objects.get_or_create(
        name=_clean_string(line_item.model_hint, default="AI Imported Model"),
        model_number=_clean_string(line_item.part_number_hint),
        defaults={"category": category, "manufacturer": manufacturer, "notes": "Created by AI intake approval."},
    )
    if not model.category_id:
        model.category = category
        model.manufacturer = manufacturer
        model.save(update_fields=["category", "manufacturer", "updated_at"])

    created_assets = []
    shared_name = _clean_string(line_item.normalized_description or line_item.raw_description)
    shared_serial = _clean_string(line_item.serial_hint)
    purchase_date = _parse_optional_date(_get_invoice_header_value(invoice_review, "invoice_date"))
    shared_purchase_cost = line_item.line_total or _get_invoice_header_value(invoice_review, "total_amount") or None

    if quantity == 1 and not shared_serial:
        existing_asset = _find_existing_asset_duplicate(
            name=shared_name,
            model=model,
            company=company,
            supplier=supplier,
            location=location,
            purchase_date=purchase_date,
            order_number=order_number,
        )
        if existing_asset is not None:
            raise ValidationError(
                "An active asset with the same model and procurement metadata already exists. "
                "Change the sourcing details or reuse the existing record instead of creating a duplicate."
            )

    for index in range(1, quantity + 1):
        extra_note_parts = []
        if quantity > 1:
            extra_note_parts.append(f"Imported from quantity-based invoice row ({index}/{quantity}).")
        asset = Asset(
            asset_tag=_build_asset_tag_for_line_item(line_item, index),
            name=shared_name,
            serial=shared_serial if quantity == 1 else "",
            model=model,
            status_label=_resolve_asset_status_label(),
            company=company,
            supplier=supplier,
            default_location=location,
            purchase_date=purchase_date,
            purchase_cost=shared_purchase_cost,
            order_number=order_number,
            notes=_build_line_item_note(line_item=line_item, invoice_review=invoice_review, extra_note_parts=extra_note_parts),
        )
        created_assets.append(_save_validated_instance(asset))

    return created_assets[0], created_assets

def _create_accessory_from_line_item(*, line_item: AIIntakeLineItem, company=None, location=None) -> Accessory:
    invoice_review = line_item.invoice_review
    category = _resolve_category(
        category_name=_clean_string(line_item.final_category_name or line_item.predicted_category_name, default="Imported Accessories"),
        category_type=Category.CategoryType.ACCESSORY,
    )
    supplier = _resolve_supplier(supplier_name=_clean_string(_get_invoice_header_value(invoice_review, "supplier_name")))
    accessory = Accessory(
        name=_clean_string(line_item.normalized_description or line_item.raw_description, default="Imported Accessory"),
        category=category,
        company=company,
        supplier=supplier,
        location=location,
        quantity=_get_line_item_quantity(line_item),
        notes=_build_line_item_note(line_item=line_item, invoice_review=invoice_review),
    )
    return _save_validated_instance(accessory)

def _create_consumable_from_line_item(*, line_item: AIIntakeLineItem, company=None, location=None) -> Consumable:
    invoice_review = line_item.invoice_review
    category = _resolve_category(
        category_name=_clean_string(line_item.final_category_name or line_item.predicted_category_name, default="Imported Consumables"),
        category_type=Category.CategoryType.CONSUMABLE,
    )
    supplier = _resolve_supplier(supplier_name=_clean_string(_get_invoice_header_value(invoice_review, "supplier_name")))
    consumable = Consumable(
        name=_clean_string(line_item.normalized_description or line_item.raw_description, default="Imported Consumable"),
        category=category,
        company=company,
        supplier=supplier,
        quantity=_get_line_item_quantity(line_item),
        notes=_build_line_item_note(line_item=line_item, invoice_review=invoice_review),
    )
    return _save_validated_instance(consumable)

def _create_component_from_line_item(*, line_item: AIIntakeLineItem, company=None, location=None) -> Component:
    del location
    invoice_review = line_item.invoice_review
    review_data = _get_line_item_review_data(line_item)
    component_role = _clean_string(review_data.get("component_role_hint") or (line_item.extraction_payload or {}).get("component_role_hint"))
    part_number = _clean_string(review_data.get("component_part_number") or line_item.part_number_hint)
    reference_value = _clean_string(
        review_data.get("component_reference")
        or line_item.reference_hint
        or (line_item.extraction_payload or {}).get("license_reference_hint")
        or (line_item.extraction_payload or {}).get("reference_hint")
    )
    category = _resolve_category(
        category_name=_clean_string(line_item.final_category_name or line_item.predicted_category_name, default="Imported Components"),
        category_type=Category.CategoryType.COMPONENT,
    )
    supplier = _resolve_supplier(supplier_name=_clean_string(_get_invoice_header_value(invoice_review, "supplier_name")))
    extra_parts = []
    if component_role:
        extra_parts.append(f"Component role: {component_role}")
    if part_number:
        extra_parts.append(f"Part number: {part_number}")
    if reference_value:
        extra_parts.append(f"Reference: {reference_value}")

    component = Component(
        name=_clean_string(line_item.normalized_description or line_item.raw_description, default="Imported Component"),
        category=category,
        company=company,
        supplier=supplier,
        quantity=_get_line_item_quantity(line_item),
        min_quantity=_decimal_to_positive_int(review_data.get("component_min_quantity"), default=0),
        notes=_build_line_item_note(line_item=line_item, invoice_review=invoice_review, extra_note_parts=extra_parts),
    )
    return _save_validated_instance(component)

def _create_license_from_line_item(*, line_item: AIIntakeLineItem, company=None, location=None) -> License:
    del location
    invoice_review = line_item.invoice_review
    review_data = _get_line_item_review_data(line_item)
    extraction_payload = line_item.extraction_payload or {}
    manufacturer = _resolve_manufacturer(manufacturer_name=_clean_string(line_item.manufacturer_hint or extraction_payload.get("manufacturer_hint")))
    supplier = _resolve_supplier(supplier_name=_clean_string(_get_invoice_header_value(invoice_review, "supplier_name")))
    category = _resolve_category(
        category_name=_clean_string(line_item.final_category_name or line_item.predicted_category_name, default="Imported Licenses"),
        category_type=Category.CategoryType.LICENSE,
    )
    seats = _decimal_to_positive_int(
        review_data.get("license_seats") or extraction_payload.get("seat_hint") or line_item.quantity,
        default=1,
    )
    product_key = _clean_string(review_data.get("license_product_key") or extraction_payload.get("product_key_hint"))
    reference_code = _clean_string(
        review_data.get("license_reference")
        or extraction_payload.get("license_reference_hint")
        or line_item.reference_hint
        or extraction_payload.get("reference_hint")
    )
    expiration_date = _parse_optional_date(review_data.get("license_expiration_date") or extraction_payload.get("expiry_date_hint"))
    renewal_date = _parse_optional_date(review_data.get("license_renewal_date") or extraction_payload.get("renewal_date_hint"))
    billing_term = _clean_string(review_data.get("license_billing_term") or extraction_payload.get("billing_term_hint"))
    extra_parts = []
    if reference_code:
        extra_parts.append(f"Reference: {reference_code}")
    if billing_term:
        extra_parts.append(f"Billing term: {billing_term}")

    license_record = License(
        name=_clean_string(line_item.normalized_description or line_item.raw_description, default="Imported License"),
        product_key=product_key,
        reference_code=reference_code,
        seats=seats,
        company=company,
        category=category,
        manufacturer=manufacturer,
        supplier=supplier,
        purchase_date=_parse_optional_date(_get_invoice_header_value(invoice_review, "invoice_date")),
        expiration_date=expiration_date,
        renewal_date=renewal_date,
        billing_term=billing_term,
        order_number=_clean_string(_get_invoice_header_value(invoice_review, "order_number")),
        purchase_cost=line_item.line_total or _get_invoice_header_value(invoice_review, "total_amount") or None,
        notes=_build_line_item_note(line_item=line_item, invoice_review=invoice_review, extra_note_parts=extra_parts),
    )
    return _save_validated_instance(license_record)

LEGACY_DRAFT_CREATORS = {
    "asset": _create_asset_from_legacy_draft,
}


LINE_ITEM_CREATORS = {
    "asset": _create_asset_from_line_item,
    "accessory": _create_accessory_from_line_item,
    "consumable": _create_consumable_from_line_item,
    "component": _create_component_from_line_item,
    "license": _create_license_from_line_item,
}


def _recalculate_invoice_review_approval_status(invoice_review: AIIntakeInvoiceReview) -> str:
    line_items = list(invoice_review.line_items.all())
    if not line_items:
        return AIIntakeInvoiceReview.Status.EXTRACTED
    if any(item.review_status == AIIntakeLineItem.ReviewStatus.UNSUPPORTED for item in line_items):
        return AIIntakeInvoiceReview.Status.UNSUPPORTED_ITEMS_PENDING
    if all(item.review_status == AIIntakeLineItem.ReviewStatus.APPROVED for item in line_items):
        return AIIntakeInvoiceReview.Status.APPROVED_COMPLETE
    if any(item.review_status == AIIntakeLineItem.ReviewStatus.APPROVED for item in line_items):
        return AIIntakeInvoiceReview.Status.PARTIALLY_APPROVED
    if all(item.review_status in {AIIntakeLineItem.ReviewStatus.SKIPPED, AIIntakeLineItem.ReviewStatus.REJECTED} for item in line_items):
        return AIIntakeInvoiceReview.Status.REJECTED
    if any(item.review_status == AIIntakeLineItem.ReviewStatus.REVIEWED for item in line_items):
        return AIIntakeInvoiceReview.Status.PARTIALLY_REVIEWED
    if any(item.final_inventory_type or item.final_category_name for item in line_items):
        return AIIntakeInvoiceReview.Status.PARTIALLY_REVIEWED
    return AIIntakeInvoiceReview.Status.EXTRACTED


@transaction.atomic
def approve_draft(*, draft: AIIntakeDraft, actor, company=None, location=None) -> Asset:
    if draft.recommended_action == AIIntakeDraft.RecommendedAction.BLOCK:
        raise ValidationError("Duplicate-risk rules blocked approval for this draft.")
    if draft.status != AIIntakeDraft.Status.PENDING_REVIEW:
        raise ValidationError("Only pending drafts can be approved.")

    extracted = enrich_legacy_extracted_data(draft.extracted_data)
    classification = get_extracted_inventory_classification(extracted)
    if classification.unsupported_for_approval:
        raise ValidationError("This draft is classified into a rollout-reserved inventory type and cannot be approved in the current flow.")
    if classification.inventory_type.value != "asset":
        raise ValidationError(
            f"This draft is classified as {classification.inventory_type.value} and cannot be approved through the legacy asset-only flow."
        )

    asset = LEGACY_DRAFT_CREATORS["asset"](draft=draft, company=company, location=location)
    draft.status = AIIntakeDraft.Status.APPROVED
    draft.approved_by = actor
    draft.approved_asset = asset
    feedback = dict(extracted.get("reviewer_feedback") or {})
    feedback["final_approved_type"] = "asset"
    feedback["final_category"] = asset.model.category.name
    extracted["reviewer_feedback"] = feedback
    draft.extracted_data = extracted
    draft.save(update_fields=["status", "approved_by", "approved_asset", "extracted_data", "updated_at"])
    record_training_signal_from_draft(draft=draft, company=company)
    draft.job.document.status = AIIntakeDocument.Status.COMPLETED
    draft.job.document.save(update_fields=["status", "updated_at"])
    log_ai_event(
        event_type=AIIntakeAuditEvent.EventType.DRAFT_APPROVED,
        actor=actor,
        document=draft.job.document,
        job=draft.job,
        draft=draft,
        status=draft.status,
        metadata={"asset_id": asset.pk},
    )
    return asset


@transaction.atomic
def approve_line_item(*, line_item: AIIntakeLineItem, actor, company=None, location=None):
    if line_item.review_status == AIIntakeLineItem.ReviewStatus.UNSUPPORTED or line_item.unsupported_for_approval:
        raise ValidationError("Unsupported line items cannot be approved in the current rollout.")
    if line_item.review_status == AIIntakeLineItem.ReviewStatus.APPROVED and line_item.created_record_object_id:
        raise ValidationError("This line item has already been approved.")

    final_inventory_type = _clean_string(line_item.final_inventory_type or line_item.predicted_inventory_type)
    creator = LINE_ITEM_CREATORS.get(final_inventory_type)
    if creator is None:
        raise ValidationError(f"Line item approval for inventory type {final_inventory_type or 'unknown'} is not available in the current rollout.")

    creation_result = creator(line_item=line_item, company=company, location=location)
    if isinstance(creation_result, tuple):
        created_record, created_records = creation_result
    else:
        created_record = creation_result
        created_records = [creation_result]

    feedback = dict(line_item.reviewer_feedback or {})
    feedback["final_approved_type"] = final_inventory_type
    feedback["final_category"] = _clean_string(line_item.final_category_name or line_item.predicted_category_name)
    feedback["created_record_count"] = len(created_records)
    feedback["created_record_ids"] = [record.pk for record in created_records]
    line_item.reviewer_feedback = feedback
    line_item.final_inventory_type = final_inventory_type
    if not line_item.final_category_name:
        line_item.final_category_name = feedback["final_category"]
    line_item.review_status = AIIntakeLineItem.ReviewStatus.APPROVED
    line_item.requires_review = False
    line_item.created_record = created_record
    line_item.save(
        update_fields=[
            "reviewer_feedback",
            "final_inventory_type",
            "final_category_name",
            "review_status",
            "requires_review",
            "created_record_content_type",
            "created_record_object_id",
            "updated_at",
        ]
    )

    invoice_review = line_item.invoice_review
    invoice_review.review_summary = {
        **(invoice_review.review_summary or {}),
        "line_item_count": invoice_review.line_items.count(),
        "last_approved_line_item_id": line_item.pk,
    }
    record_training_signal_from_line_item(line_item=line_item, company=company or invoice_review.review_company)
    invoice_review.status = _recalculate_invoice_review_approval_status(invoice_review)
    invoice_review.save(update_fields=["review_summary", "status", "updated_at"])

    if invoice_review.status == AIIntakeInvoiceReview.Status.APPROVED_COMPLETE:
        invoice_review.job.document.status = AIIntakeDocument.Status.COMPLETED
        invoice_review.job.document.save(update_fields=["status", "updated_at"])

    return created_record


@transaction.atomic
def approve_reviewed_line_items(*, invoice_review: AIIntakeInvoiceReview, actor, company=None, location=None):
    line_items = list(invoice_review.line_items.order_by("line_number", "id"))
    pending_or_blocked = [
        item for item in line_items
        if item.review_status in {
            AIIntakeLineItem.ReviewStatus.PENDING_REVIEW,
            AIIntakeLineItem.ReviewStatus.UNSUPPORTED,
            AIIntakeLineItem.ReviewStatus.SKIPPED,
            AIIntakeLineItem.ReviewStatus.REJECTED,
        }
    ]
    if pending_or_blocked:
        raise ValidationError("Finish reviewing every line item before bulk approval.")

    target_items = [item for item in line_items if item.review_status in {AIIntakeLineItem.ReviewStatus.REVIEWED, AIIntakeLineItem.ReviewStatus.APPROVED}]
    if not target_items:
        raise ValidationError("No reviewed line items are ready for approval.")

    created_records = []
    for item in target_items:
        if item.review_status == AIIntakeLineItem.ReviewStatus.APPROVED and item.created_record_object_id:
            continue
        created_records.append(approve_line_item(line_item=item, actor=actor, company=company, location=location))
    return created_records


def reject_draft(*, draft: AIIntakeDraft, actor, notes: str = "") -> AIIntakeDraft:
    if draft.status not in (AIIntakeDraft.Status.PENDING_REVIEW, AIIntakeDraft.Status.RETRY_REQUIRED):
        raise ValidationError("Only reviewable drafts can be rejected.")
    draft.status = AIIntakeDraft.Status.REJECTED
    draft.rejected_by = actor
    draft.review_notes = notes
    draft.save(update_fields=["status", "rejected_by", "review_notes", "updated_at"])
    log_ai_event(
        event_type=AIIntakeAuditEvent.EventType.DRAFT_REJECTED,
        actor=actor,
        document=draft.job.document,
        job=draft.job,
        draft=draft,
        status=draft.status,
        metadata={"notes": notes},
    )
    return draft


def retry_draft(*, draft: AIIntakeDraft, actor, provider: AzureOpenAIIntakeClient | None = None) -> AIIntakeJob:
    log_ai_event(
        event_type=AIIntakeAuditEvent.EventType.DRAFT_RETRIED,
        actor=actor,
        document=draft.job.document,
        job=draft.job,
        draft=draft,
        status=draft.status,
    )
    return process_document(document=draft.job.document, actor=actor, provider=provider)


def retry_invoice_review(*, invoice_review: AIIntakeInvoiceReview, actor, provider: AzureOpenAIIntakeClient | None = None) -> AIIntakeJob:
    log_ai_event(
        event_type=AIIntakeAuditEvent.EventType.DRAFT_RETRIED,
        actor=actor,
        document=invoice_review.job.document,
        job=invoice_review.job,
        status=invoice_review.status,
        metadata={"workflow": "invoice_line_items", "invoice_review_id": invoice_review.pk},
    )
    return process_document_line_items(document=invoice_review.job.document, actor=actor, provider=provider)

