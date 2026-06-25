import mimetypes
import os

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.db import transaction
from django.utils.decorators import method_decorator
from django.urls import reverse, reverse_lazy
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.generic import DetailView, FormView, ListView, View

from core.views import SearchableListMixin, StaffRequiredMixin

from .forms import AIIntakeApproveForm, AIIntakeRejectForm, AIIntakeUploadForm
from .models import AIIntakeAuditEvent, AIIntakeDocument, AIIntakeDraft, AIIntakeInvoiceReview, AIIntakeJob, AIIntakeLineItem
from .policy import enrich_legacy_extracted_data, get_extracted_inventory_classification
from .services import approve_draft, approve_line_item, approve_reviewed_line_items, evaluate_duplicate_risk, process_document, process_document_line_items, reject_draft, retry_draft, retry_invoice_review

REVIEW_FIELD_DEFAULTS = {
    "asset_name": "",
    "manufacturer_name": "",
    "model_name": "",
    "model_number": "",
    "supplier_name": "",
    "category_name": "Imported Assets",
    "serial": "",
    "order_number": "",
    "purchase_date": "",
    "purchase_cost": None,
    "notes": "",
    "quantity": 1,
}

REVIEW_GROUPS = [
    {
        "title": "Asset Information",
        "description": "Core asset identity fields that will be used when the final asset record is created.",
        "fields": [
            ("asset_name", "Asset name", "full", "Dell 27-inch Monitor"),
            ("manufacturer_name", "Manufacturer", "half", "Dell"),
            ("category_name", "Category", "half", "Monitor"),
            ("model_name", "Model name", "half", "P2725H"),
            ("model_number", "Model number", "half", "P2725H-UK"),
            ("serial", "Serial number", "full", "Serial or service tag"),
        ],
    },
    {
        "title": "Procurement Information",
        "description": "Commercial information extracted from the source document for cross-checking before approval.",
        "fields": [
            ("supplier_name", "Supplier", "full", "Ace Electronics"),
            ("order_number", "Order number", "half", "RW/001"),
            ("purchase_date", "Purchase date", "half", "2026-06-24"),
            ("quantity", "Quantity", "half", "1"),
            ("purchase_cost", "Total amount", "half", "44500.00"),
        ],
    },
    {
        "title": "Additional Information",
        "description": "Reviewer context and any freeform notes preserved with the draft.",
        "fields": [
            ("notes", "Notes", "full", "Add supporting notes or extracted context"),
        ],
    },
]

LINE_ITEM_REVIEW_DEFAULTS = {
    "component_role_hint": "",
    "component_min_quantity": 0,
    "component_part_number": "",
    "component_reference": "",
    "license_seats": 1,
    "license_product_key": "",
    "license_reference": "",
    "license_expiration_date": "",
    "license_renewal_date": "",
    "license_billing_term": "",
}


PROCESSING_REFRESH_SECONDS = 3


def _status_pill_tone(status_value: str) -> str:
    if status_value in {AIIntakeDocument.Status.REVIEW, AIIntakeDocument.Status.COMPLETED, AIIntakeJob.Status.SUCCEEDED}:
        return "success"
    if status_value in {AIIntakeDocument.Status.FAILED, AIIntakeJob.Status.FAILED}:
        return "danger"
    if status_value in {AIIntakeDocument.Status.PROCESSING, AIIntakeJob.Status.RUNNING, AIIntakeJob.Status.PENDING, AIIntakeDocument.Status.UPLOADED}:
        return "warning"
    return "neutral"


def _build_processing_state(document: AIIntakeDocument, latest_job) -> dict | None:
    job_status = getattr(latest_job, "status", "") or ""
    is_processing = document.status in {AIIntakeDocument.Status.UPLOADED, AIIntakeDocument.Status.PROCESSING} or job_status in {
        AIIntakeJob.Status.PENDING,
        AIIntakeJob.Status.RUNNING,
    }
    if not is_processing:
        return None

    validation_state = "completed" if latest_job else "active"
    extraction_state = "active" if job_status == AIIntakeJob.Status.RUNNING else "pending"
    review_state = "pending"
    message = "This page refreshes automatically while extraction is running. You can stay here and wait for the review workspace to appear."

    if job_status == AIIntakeJob.Status.PENDING:
        validation_state = "active"
        extraction_state = "pending"
        message = "The file is queued for AI intake processing. This page checks for status updates automatically."

    steps = [
        {
            "key": "upload_received",
            "label": "Upload received",
            "detail": "The file is stored and queued for AI intake processing.",
            "state": "completed",
        },
        {
            "key": "validating_document",
            "label": "Validating document",
            "detail": "The system is checking file structure and preparing the extraction request.",
            "state": validation_state,
        },
        {
            "key": "ai_extraction",
            "label": "AI extraction in progress",
            "detail": "AI is reading the document, extracting asset fields, and structuring the review payload.",
            "state": extraction_state,
        },
        {
            "key": "preparing_review_workspace",
            "label": "Preparing review workspace",
            "detail": "The review console will open automatically once extraction is complete.",
            "state": review_state,
        },
    ]
    return {
        "title": "AI intake is processing this document",
        "message": message,
        "steps": steps,
        "refresh_seconds": PROCESSING_REFRESH_SECONDS,
        "document_status": document.status,
        "document_status_display": document.get_status_display(),
        "job_status": job_status,
        "job_status_display": latest_job.get_status_display() if latest_job else "",
        "job_status_tone": _status_pill_tone(job_status) if job_status else "neutral",
    }


def _coerce_quantity(raw_value, fallback):
    try:
        value = int(str(raw_value).strip())
        return value if value > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def _coerce_non_negative_quantity(raw_value, fallback=0):
    try:
        value = int(str(raw_value).strip())
        return value if value >= 0 else fallback
    except (TypeError, ValueError):
        return fallback


def _build_line_item_review_values(line_item: AIIntakeLineItem) -> dict:
    extraction_payload = dict(line_item.extraction_payload or {})
    feedback = dict(line_item.reviewer_feedback or {})
    type_specific = dict(feedback.get("type_specific_review") or {})
    quantity_default = _coerce_quantity(line_item.quantity or extraction_payload.get("seat_hint") or 1, 1)
    return {
        "component_role_hint": str(type_specific.get("component_role_hint") or extraction_payload.get("component_role_hint") or "").strip(),
        "component_min_quantity": _coerce_non_negative_quantity(type_specific.get("component_min_quantity"), 0),
        "component_part_number": str(type_specific.get("component_part_number") or line_item.part_number_hint or "").strip(),
        "component_reference": str(
            type_specific.get("component_reference")
            or line_item.reference_hint
            or extraction_payload.get("reference_hint")
            or ""
        ).strip(),
        "license_seats": _coerce_quantity(type_specific.get("license_seats") or extraction_payload.get("seat_hint") or quantity_default, 1),
        "license_product_key": str(type_specific.get("license_product_key") or extraction_payload.get("product_key_hint") or "").strip(),
        "license_reference": str(
            type_specific.get("license_reference")
            or extraction_payload.get("license_reference_hint")
            or line_item.reference_hint
            or extraction_payload.get("reference_hint")
            or ""
        ).strip(),
        "license_expiration_date": str(type_specific.get("license_expiration_date") or extraction_payload.get("expiry_date_hint") or "").strip(),
        "license_renewal_date": str(type_specific.get("license_renewal_date") or extraction_payload.get("renewal_date_hint") or "").strip(),
        "license_billing_term": str(type_specific.get("license_billing_term") or extraction_payload.get("billing_term_hint") or "").strip(),
    }


def _build_invoice_review_overview(invoice_review: AIIntakeInvoiceReview) -> dict:
    line_items = list(invoice_review.line_items.order_by("line_number", "id"))
    type_counts = {}
    for item in line_items:
        inventory_type = str(item.final_inventory_type or item.predicted_inventory_type or "unclassified").strip() or "unclassified"
        type_counts[inventory_type] = type_counts.get(inventory_type, 0) + 1

    return {
        "line_item_count": len(line_items),
        "approved_count": sum(1 for item in line_items if item.review_status == AIIntakeLineItem.ReviewStatus.APPROVED),
        "pending_count": sum(1 for item in line_items if item.review_status == AIIntakeLineItem.ReviewStatus.PENDING_REVIEW),
        "reviewed_count": sum(1 for item in line_items if item.review_status == AIIntakeLineItem.ReviewStatus.REVIEWED),
        "unsupported_count": sum(1 for item in line_items if item.review_status == AIIntakeLineItem.ReviewStatus.UNSUPPORTED),
        "preview_items": line_items[:3],
        "type_counts": [
            {"inventory_type": inventory_type, "count": count}
            for inventory_type, count in sorted(type_counts.items())
        ],
    }


def _build_invoice_review_alerts(invoice_review: AIIntakeInvoiceReview | None, latest_job=None) -> list[dict]:
    alerts = []
    if latest_job and latest_job.error_message:
        alerts.append({"severity": "error", "title": "Extraction failed", "message": latest_job.error_message})
    if invoice_review is None:
        return alerts

    review_summary = dict(invoice_review.review_summary or {})
    expected_count = int(review_summary.get("expected_merchandise_row_count") or 0)
    extracted_count = int(review_summary.get("line_item_count") or 0)
    if expected_count and extracted_count and extracted_count < expected_count:
        alerts.append(
            {
                "severity": "warning",
                "title": "Possible missing rows",
                "message": f"Invoice appears to contain {expected_count} merchandise row(s), but only {extracted_count} row(s) were extracted.",
            }
        )

    reconciliation = dict(review_summary.get("reconciliation") or {})
    for issue in reconciliation.get("issues") or []:
        alerts.append({"severity": issue.get("severity", "warning"), "title": "Invoice review warning", "message": issue.get("message", "Review this extracted invoice warning.")})

    return alerts


def _normalise_extracted_data(post_data, draft):
    current = enrich_legacy_extracted_data(draft.extracted_data or {})
    normalised = {key: value for key, value in current.items() if key not in REVIEW_FIELD_DEFAULTS}

    for key, default in REVIEW_FIELD_DEFAULTS.items():
        if key == "quantity":
            normalised[key] = _coerce_quantity(post_data.get(key, current.get(key, default)), current.get(key, default) or 1)
            continue

        raw_value = post_data.get(key, current.get(key, default))
        if key == "purchase_cost":
            value = str(raw_value).strip()
            normalised[key] = value or None
            continue

        value = str(raw_value).strip()
        if key == "category_name" and not value:
            value = "Imported Assets"
        normalised[key] = value

    return enrich_legacy_extracted_data(normalised)


def _build_validation_issues(draft):
    extracted = enrich_legacy_extracted_data({**REVIEW_FIELD_DEFAULTS, **(draft.extracted_data or {})})
    classification = get_extracted_inventory_classification(extracted)
    issues = []

    severity = "error" if draft.duplicate_risk_level == AIIntakeDraft.RiskLevel.HIGH else "warning"
    for reason in draft.duplicate_risk_reasons:
        related_field = "serial" if "serial" in reason.lower() else "order_number" if "order number" in reason.lower() else "supplier_name"
        issues.append(
            {
                "severity": severity,
                "title": "Duplicate-risk rule triggered",
                "message": reason,
                "field": related_field,
            }
        )

    required_checks = [
        ("asset_name", "Asset name missing", "Add a clear asset name before approving this draft.", "error"),
        ("supplier_name", "Supplier missing", "The supplier field is empty, so procurement provenance is incomplete.", "warning"),
        ("model_name", "Model name missing", "Capture a usable model name so the asset model can be resolved consistently.", "warning"),
        ("purchase_date", "Purchase date missing", "Purchase date is empty and should be verified against the invoice.", "warning"),
        ("purchase_cost", "Total amount missing", "Total amount is empty and should be confirmed before approval.", "warning"),
    ]
    for field_name, title, message, severity_level in required_checks:
        if extracted.get(field_name):
            continue
        issues.append(
            {
                "severity": severity_level,
                "title": title,
                "message": message,
                "field": field_name,
            }
        )

    if classification.unsupported_for_approval:
        issues.append(
            {
                "severity": "error",
                "title": "Unsupported rollout type",
                "message": "This item was classified into a reserved inventory type and requires manual follow-up instead of approval in the current flow.",
                "field": "asset_name",
            }
        )
    elif classification.inventory_type.value != "asset":
        issues.append(
            {
                "severity": "error",
                "title": "Legacy approval flow mismatch",
                "message": f"This draft is classified as {classification.inventory_type.value} and cannot be created through the current asset-only approval path.",
                "field": "asset_name",
            }
        )

    if classification.classification_rationale:
        issues.append(
            {
                "severity": "warning" if classification.requires_review else "info",
                "title": "Classification rationale",
                "message": classification.classification_rationale,
                "field": "asset_name",
            }
        )

    if draft.status == AIIntakeDraft.Status.RETRY_REQUIRED:
        issues.insert(
            0,
            {
                "severity": "error",
                "title": "Extraction requires attention",
                "message": "The last extraction attempt failed or requires retry. Review the source file before retrying.",
                "field": "document",
            },
        )

    return issues


def _update_draft_from_review(draft, post_data, *, persist_notes=True):
    extracted = _normalise_extracted_data(post_data, draft)
    risk_level, reasons, action = evaluate_duplicate_risk(extracted)
    draft.extracted_data = extracted
    draft.duplicate_risk_level = risk_level
    draft.duplicate_risk_reasons = reasons
    draft.recommended_action = action
    update_fields = ["extracted_data", "duplicate_risk_level", "duplicate_risk_reasons", "recommended_action", "updated_at"]

    if persist_notes and "review_notes" in post_data:
        draft.review_notes = str(post_data.get("review_notes") or "").strip()
        update_fields.append("review_notes")

    draft.save(update_fields=update_fields)
    return draft


def _coerce_decimal_input(raw_value):
    value = str(raw_value or "").strip()
    return value or None


def _ordered_actionable_line_items(line_items):
    actionable_statuses = {
        AIIntakeLineItem.ReviewStatus.PENDING_REVIEW,
        AIIntakeLineItem.ReviewStatus.REVIEWED,
    }
    return [item for item in line_items if item.review_status in actionable_statuses]



def _next_actionable_line_item(invoice_review: AIIntakeInvoiceReview, current_line_item: AIIntakeLineItem | None = None):
    line_items = list(invoice_review.line_items.order_by("line_number", "id"))
    actionable = _ordered_actionable_line_items(line_items)
    if not actionable:
        return current_line_item or (line_items[0] if line_items else None)
    if current_line_item is None:
        return actionable[0]

    current_index = next((index for index, item in enumerate(actionable) if item.pk == current_line_item.pk), None)
    if current_index is None:
        return actionable[0]
    if current_index + 1 < len(actionable):
        return actionable[current_index + 1]
    return actionable[current_index]



def _workspace_status_label(invoice_review: AIIntakeInvoiceReview, line_items) -> str:
    status = invoice_review.status
    pending_exists = any(item.review_status == AIIntakeLineItem.ReviewStatus.PENDING_REVIEW for item in line_items)
    reviewed_exists = any(item.review_status == AIIntakeLineItem.ReviewStatus.REVIEWED for item in line_items)
    approved_exists = any(item.review_status == AIIntakeLineItem.ReviewStatus.APPROVED for item in line_items)

    if status == AIIntakeInvoiceReview.Status.PARTIALLY_REVIEWED and reviewed_exists and not pending_exists:
        return "Ready for Recording"
    if status == AIIntakeInvoiceReview.Status.PARTIALLY_APPROVED and (pending_exists or reviewed_exists):
        return "Recording In Progress"
    if status == AIIntakeInvoiceReview.Status.APPROVED_COMPLETE:
        return "Approved Complete"
    return invoice_review.get_status_display()



def _line_item_approval_blocker(line_item: AIIntakeLineItem | None) -> str:
    del line_item
    return ""



def _build_line_item_routing_values(line_item: AIIntakeLineItem | None) -> dict:
    if line_item is None:
        return {"company": "", "location": "", "review_notes": ""}

    feedback = dict(line_item.reviewer_feedback or {})
    invoice_review = line_item.invoice_review
    return {
        "company": str(invoice_review.review_company_id or ""),
        "location": str(feedback.get("routing_location_id") or ""),
        "review_notes": str(feedback.get("review_notes") or "").strip(),
    }



def _serialize_line_item_for_workspace(document_id: int, line_item: AIIntakeLineItem) -> dict:
    current_type = str(line_item.final_inventory_type or line_item.predicted_inventory_type or "").strip()
    return {
        "id": line_item.pk,
        "line_number": line_item.line_number,
        "review_title": f"Line {line_item.line_number} Review",
        "normalized_description": str(line_item.normalized_description or line_item.raw_description or "").strip(),
        "quantity": "" if line_item.quantity in (None, "") else str(line_item.quantity),
        "unit_price": "" if line_item.unit_price in (None, "") else str(line_item.unit_price),
        "line_total": "" if line_item.line_total in (None, "") else str(line_item.line_total),
        "final_inventory_type": current_type,
        "final_category_name": str(line_item.final_category_name or line_item.predicted_category_name or "").strip(),
        "review_status": line_item.review_status,
        "is_approved": bool(
            line_item.review_status == AIIntakeLineItem.ReviewStatus.APPROVED and line_item.created_record_object_id
        ),
        "save_url": reverse("ai_intake:line-item-save", kwargs={"pk": line_item.pk}),
        "approve_url": reverse("ai_intake:line-item-approve", kwargs={"pk": line_item.pk}),
        "blocker": _line_item_approval_blocker(line_item),
        "type_specific_review": _build_line_item_review_values(line_item),
        "routing": _build_line_item_routing_values(line_item),
    }



def _workspace_redirect(document_id: int, target_line_item: AIIntakeLineItem | None = None) -> str:
    base_url = reverse('ai_intake:line-item-workspace', kwargs={'pk': document_id})
    if target_line_item is None:
        return base_url
    return f"{base_url}?line_item={target_line_item.pk}"


def _resolve_line_item_review_status(action: str, current_status: str) -> str:
    if action == "save":
        if current_status in {"", AIIntakeLineItem.ReviewStatus.EXTRACTED}:
            return AIIntakeLineItem.ReviewStatus.PENDING_REVIEW
        return current_status or AIIntakeLineItem.ReviewStatus.PENDING_REVIEW

    mapping = {
        "mark_reviewed": AIIntakeLineItem.ReviewStatus.REVIEWED,
        "skip": AIIntakeLineItem.ReviewStatus.SKIPPED,
        "unsupported": AIIntakeLineItem.ReviewStatus.UNSUPPORTED,
        "follow_up": AIIntakeLineItem.ReviewStatus.PENDING_REVIEW,
    }
    return mapping.get(action, current_status or AIIntakeLineItem.ReviewStatus.PENDING_REVIEW)


def _recalculate_invoice_review_status(invoice_review: AIIntakeInvoiceReview) -> str:
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
    if any(item.final_inventory_type or item.final_category_name or item.review_status == AIIntakeLineItem.ReviewStatus.APPROVED for item in line_items):
        return AIIntakeInvoiceReview.Status.PARTIALLY_REVIEWED
    return AIIntakeInvoiceReview.Status.EXTRACTED


@transaction.atomic
def _update_line_item_from_review(line_item: AIIntakeLineItem, post_data, *, action: str = "save"):
    invoice_review = line_item.invoice_review
    invoice_metadata = dict(invoice_review.invoice_metadata or {})
    invoice_metadata["supplier_name"] = str(post_data.get("supplier_name", invoice_metadata.get("supplier_name", "")) or "").strip()
    invoice_metadata["invoice_number"] = str(post_data.get("invoice_number", invoice_metadata.get("invoice_number", "")) or "").strip()
    invoice_metadata["order_number"] = str(post_data.get("order_number", invoice_metadata.get("order_number", "")) or "").strip()
    invoice_metadata["invoice_date"] = str(post_data.get("invoice_date", invoice_metadata.get("invoice_date", "")) or "").strip()
    raw_company_id = str(post_data.get("company", invoice_review.review_company_id or "") or "").strip()
    invoice_review.review_company_id = int(raw_company_id) if raw_company_id.isdigit() else None
    invoice_review.invoice_metadata = invoice_metadata
    invoice_review.save(update_fields=["invoice_metadata", "review_company", "updated_at"])

    line_item.normalized_description = str(post_data.get("normalized_description", line_item.normalized_description) or "").strip()
    line_item.quantity = _coerce_decimal_input(post_data.get("quantity", line_item.quantity))
    line_item.unit_price = _coerce_decimal_input(post_data.get("unit_price", line_item.unit_price))
    line_item.line_total = _coerce_decimal_input(post_data.get("line_total", line_item.line_total))
    line_item.final_inventory_type = str(post_data.get("final_inventory_type", line_item.final_inventory_type or line_item.predicted_inventory_type) or "").strip()
    line_item.final_category_name = str(post_data.get("final_category_name", line_item.final_category_name or line_item.predicted_category_name) or "").strip()
    line_item.requires_review = bool(post_data.get("requires_review")) if "requires_review" in post_data else line_item.requires_review
    line_item.unsupported_for_approval = action == "unsupported"
    line_item.review_status = _resolve_line_item_review_status(action, line_item.review_status)

    feedback = dict(line_item.reviewer_feedback or {})
    feedback["final_approved_type"] = line_item.final_inventory_type
    feedback["final_category"] = line_item.final_category_name
    feedback["supplier_invoice_context"] = " | ".join(
        value for value in [invoice_metadata.get("supplier_name", ""), invoice_metadata.get("order_number", ""), invoice_metadata.get("invoice_date", "")] if value
    )
    raw_location_id = str(post_data.get("location", feedback.get("routing_location_id", "")) or "").strip()
    feedback["routing_location_id"] = int(raw_location_id) if raw_location_id.isdigit() else None
    feedback["review_notes"] = str(post_data.get("review_notes", feedback.get("review_notes", "")) or "").strip()
    current_type_specific = _build_line_item_review_values(line_item)
    feedback["type_specific_review"] = {
        "component_role_hint": str(post_data.get("component_role_hint", current_type_specific["component_role_hint"]) or "").strip(),
        "component_min_quantity": _coerce_non_negative_quantity(
            post_data.get("component_min_quantity", current_type_specific["component_min_quantity"]),
            current_type_specific["component_min_quantity"],
        ),
        "component_part_number": str(post_data.get("component_part_number", current_type_specific["component_part_number"]) or "").strip(),
        "component_reference": str(post_data.get("component_reference", current_type_specific["component_reference"]) or "").strip(),
        "license_seats": _coerce_quantity(
            post_data.get("license_seats", current_type_specific["license_seats"]),
            current_type_specific["license_seats"],
        ),
        "license_product_key": str(post_data.get("license_product_key", current_type_specific["license_product_key"]) or "").strip(),
        "license_reference": str(post_data.get("license_reference", current_type_specific["license_reference"]) or "").strip(),
        "license_expiration_date": str(
            post_data.get("license_expiration_date", current_type_specific["license_expiration_date"]) or ""
        ).strip(),
        "license_renewal_date": str(post_data.get("license_renewal_date", current_type_specific["license_renewal_date"]) or "").strip(),
        "license_billing_term": str(post_data.get("license_billing_term", current_type_specific["license_billing_term"]) or "").strip(),
    }
    line_item.reviewer_feedback = feedback
    line_item.save(
        update_fields=[
            "normalized_description",
            "quantity",
            "unit_price",
            "line_total",
            "final_inventory_type",
            "final_category_name",
            "requires_review",
            "unsupported_for_approval",
            "review_status",
            "reviewer_feedback",
            "updated_at",
        ]
    )

    invoice_review.review_summary = {
        **(invoice_review.review_summary or {}),
        "line_item_count": invoice_review.line_items.count(),
        "last_reviewed_line_item_id": line_item.pk,
    }
    invoice_review.status = _recalculate_invoice_review_status(invoice_review)
    invoice_review.save(update_fields=["review_summary", "status", "updated_at"])
    return line_item


class AIIntakeListView(LoginRequiredMixin, SearchableListMixin, ListView):
    model = AIIntakeDocument
    template_name = "ai_intake/list.html"
    context_object_name = "objects"
    search_fields = ("original_filename", "sha256")

    def get_queryset(self):
        return super().get_queryset().select_related("uploaded_by").prefetch_related("jobs__draft")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        objects = context.get("objects") or []
        context["processing_count"] = sum(
            1 for document in objects if document.status in {AIIntakeDocument.Status.UPLOADED, AIIntakeDocument.Status.PROCESSING}
        )
        return context


class AIIntakeUploadView(StaffRequiredMixin, FormView):
    template_name = "ai_intake/upload.html"
    form_class = AIIntakeUploadForm
    success_url = reverse_lazy("ai_intake:list")

    def form_valid(self, form):
        upload = self.request.FILES["file"]
        file_bytes = upload.read()
        upload.seek(0)
        sha256 = AIIntakeDocument.hash_bytes(file_bytes)
        existing_document = AIIntakeDocument.objects.filter(sha256=sha256).first()
        if existing_document is not None:
            messages.warning(
                self.request,
                f"This document was already uploaded earlier as {existing_document.original_filename}. Opened the existing intake record instead of creating a duplicate.",
            )
            return redirect(reverse("ai_intake:detail", kwargs={"pk": existing_document.pk}))

        document = AIIntakeDocument.objects.create(
            file=upload,
            original_filename=upload.name,
            content_type=upload.content_type or "",
            size_bytes=upload.size,
            sha256=sha256,
            uploaded_by=self.request.user,
        )
        AIIntakeAuditEvent.objects.create(
            document=document,
            actor=self.request.user,
            event_type=AIIntakeAuditEvent.EventType.DOCUMENT_UPLOADED,
            status=document.status,
            metadata={"filename": document.original_filename},
        )
        process_document_line_items(document=document, actor=self.request.user)
        messages.success(self.request, "Document uploaded and processed successfully.")
        return redirect(reverse("ai_intake:detail", kwargs={"pk": document.pk}))


class AIIntakeDetailView(LoginRequiredMixin, DetailView):
    model = AIIntakeDocument
    template_name = "ai_intake/detail.html"
    context_object_name = "object"

    def get_queryset(self):
        return AIIntakeDocument.objects.select_related("uploaded_by").prefetch_related("jobs__draft__approved_asset", "jobs__invoice_review__line_items", "audit_events")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        latest_job = self.object.jobs.first()
        draft = getattr(latest_job, "draft", None) if latest_job else None
        invoice_review = getattr(latest_job, "invoice_review", None) if latest_job else None
        content_type = self.object.content_type or ""
        approve_form = AIIntakeApproveForm(initial={"review_notes": getattr(draft, "review_notes", "")})
        extracted = enrich_legacy_extracted_data({**REVIEW_FIELD_DEFAULTS, **((draft.extracted_data if draft else {}) or {})})

        context["latest_job"] = latest_job
        context["draft"] = draft
        context["invoice_review"] = invoice_review
        context["approve_form"] = approve_form
        context["reject_form"] = AIIntakeRejectForm()
        context["preview_url"] = reverse("ai_intake:preview", kwargs={"pk": self.object.pk})
        context["is_preview_image"] = content_type.startswith("image/")
        context["is_preview_pdf"] = content_type == "application/pdf" or self.object.original_filename.lower().endswith(".pdf")
        context["company_count"] = approve_form.fields["company"].queryset.count()
        context["location_count"] = approve_form.fields["location"].queryset.count()
        context["extracted"] = extracted
        context["review_groups"] = REVIEW_GROUPS
        context["validation_issues"] = _build_validation_issues(draft) if draft else []
        context["line_item_workspace_url"] = (
            reverse("ai_intake:line-item-workspace", kwargs={"pk": self.object.pk}) if invoice_review else ""
        )
        context["invoice_review_overview"] = _build_invoice_review_overview(invoice_review) if invoice_review else {}
        context["invoice_review_alerts"] = _build_invoice_review_alerts(invoice_review, latest_job)
        context["document_status_tone"] = _status_pill_tone(self.object.status)
        context["job_status_tone"] = _status_pill_tone(latest_job.status) if latest_job else "neutral"
        context["processing_state"] = _build_processing_state(self.object, latest_job)
        context["processing_status_url"] = reverse("ai_intake:processing-status", kwargs={"pk": self.object.pk})
        return context


class AIIntakeProcessingStatusView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        document = get_object_or_404(AIIntakeDocument.objects.prefetch_related("jobs"), pk=kwargs["pk"])
        latest_job = document.jobs.first()
        processing_state = _build_processing_state(document, latest_job)
        payload = {
            "is_processing": bool(processing_state),
            "document_status": document.status,
            "document_status_display": document.get_status_display(),
            "document_status_tone": _status_pill_tone(document.status),
            "detail_url": reverse("ai_intake:detail", kwargs={"pk": document.pk}),
        }
        if latest_job:
            payload.update(
                {
                    "job_status": latest_job.status,
                    "job_status_display": latest_job.get_status_display(),
                    "job_status_tone": _status_pill_tone(latest_job.status),
                }
            )
        if processing_state:
            payload["processing_state"] = processing_state
        return JsonResponse(payload)


@method_decorator(xframe_options_sameorigin, name="dispatch")
class AIIntakePreviewView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        document = get_object_or_404(AIIntakeDocument, pk=kwargs["pk"])
        if not document.file:
            raise Http404
        content_type = document.content_type or mimetypes.guess_type(document.original_filename)[0] or "application/octet-stream"
        response = FileResponse(document.file.open("rb"), content_type=content_type)
        response["Content-Disposition"] = f'inline; filename="{os.path.basename(document.original_filename)}"'
        return response


class AIIntakeDeleteView(StaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        document = get_object_or_404(AIIntakeDocument, pk=kwargs["pk"])
        filename = document.original_filename
        if document.file:
            document.file.close()
            document.file.delete(save=False)
        document.delete()
        messages.success(request, f"Deleted intake document {filename} and its extracted review data.")
        return redirect(reverse("ai_intake:list"))


class AIIntakeSaveDraftView(StaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        draft = get_object_or_404(AIIntakeDraft.objects.select_related("job__document"), pk=kwargs["pk"])
        _update_draft_from_review(draft, request.POST)
        messages.success(request, "Draft review saved.")
        return redirect(reverse("ai_intake:detail", kwargs={"pk": draft.job.document_id}))


class AIIntakeApproveView(StaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        draft = get_object_or_404(AIIntakeDraft.objects.select_related("job__document"), pk=kwargs["pk"])
        _update_draft_from_review(draft, request.POST)
        form = AIIntakeApproveForm(request.POST)
        if form.is_valid():
            try:
                asset = approve_draft(
                    draft=draft,
                    actor=request.user,
                    company=form.cleaned_data["company"],
                    location=form.cleaned_data["location"],
                )
                messages.success(request, f"Draft approved and asset {asset.asset_tag} created.")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        else:
            messages.error(request, "Draft approval failed.")
        return redirect(reverse("ai_intake:detail", kwargs={"pk": draft.job.document_id}))


class AIIntakeRejectView(StaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        draft = get_object_or_404(AIIntakeDraft.objects.select_related("job__document"), pk=kwargs["pk"])
        form = AIIntakeRejectForm(request.POST)
        if form.is_valid():
            reject_draft(draft=draft, actor=request.user, notes=form.cleaned_data["review_notes"])
            messages.success(request, "Draft rejected.")
        else:
            messages.error(request, "Draft rejection failed.")
        return redirect(reverse("ai_intake:detail", kwargs={"pk": draft.job.document_id}))


class AIIntakeRetryView(StaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        draft = get_object_or_404(AIIntakeDraft.objects.select_related("job__document"), pk=kwargs["pk"])
        retry_draft(draft=draft, actor=request.user)
        messages.success(request, "Draft retry started.")
        return redirect(reverse("ai_intake:detail", kwargs={"pk": draft.job.document_id}))


class AIIntakeInvoiceRetryView(StaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        document = get_object_or_404(
            AIIntakeDocument.objects.prefetch_related("jobs__invoice_review"),
            pk=kwargs["pk"],
        )
        latest_job = document.jobs.first()
        invoice_review = getattr(latest_job, "invoice_review", None) if latest_job else None
        if invoice_review is None:
            raise Http404
        retry_invoice_review(invoice_review=invoice_review, actor=request.user)
        messages.success(request, "Invoice extraction retry started.")
        return redirect(reverse("ai_intake:detail", kwargs={"pk": document.pk}))


class AIIntakeLineItemWorkspaceView(LoginRequiredMixin, DetailView):
    model = AIIntakeDocument
    template_name = "ai_intake/line_item_workspace.html"
    context_object_name = "object"

    def get_queryset(self):
        return AIIntakeDocument.objects.select_related("uploaded_by").prefetch_related("jobs__invoice_review__line_items", "audit_events")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        latest_job = self.object.jobs.first()
        invoice_review = getattr(latest_job, "invoice_review", None) if latest_job else None
        if invoice_review is None:
            raise Http404
        selected_line_item_id = self.request.GET.get("line_item")
        line_items = list(invoice_review.line_items.order_by("line_number", "id"))
        selected_line_item = next((item for item in line_items if str(item.pk) == str(selected_line_item_id)), None)
        if selected_line_item is None:
            selected_line_item = _next_actionable_line_item(invoice_review)

        unsupported_count = sum(1 for item in line_items if item.review_status == AIIntakeLineItem.ReviewStatus.UNSUPPORTED)
        pending_count = sum(1 for item in line_items if item.review_status == AIIntakeLineItem.ReviewStatus.PENDING_REVIEW)
        reviewed_count = sum(1 for item in line_items if item.review_status == AIIntakeLineItem.ReviewStatus.REVIEWED)

        context["latest_job"] = latest_job
        context["invoice_review"] = invoice_review
        context["line_items"] = line_items
        context["selected_line_item"] = selected_line_item
        context["preview_url"] = reverse("ai_intake:preview", kwargs={"pk": self.object.pk})
        context["is_preview_image"] = (self.object.content_type or "").startswith("image/")
        context["is_preview_pdf"] = self.object.content_type == "application/pdf" or self.object.original_filename.lower().endswith(".pdf")
        context["workspace_metrics"] = {
            "line_item_count": len(line_items),
            "pending_count": pending_count,
            "reviewed_count": reviewed_count,
            "unsupported_count": unsupported_count,
        }
        context["line_item_approve_form"] = AIIntakeApproveForm(initial=_build_line_item_routing_values(selected_line_item))
        context["selected_line_item_review"] = _build_line_item_review_values(selected_line_item) if selected_line_item else {}
        context["selected_line_item_is_approved"] = bool(
            selected_line_item
            and selected_line_item.review_status == AIIntakeLineItem.ReviewStatus.APPROVED
            and selected_line_item.created_record_object_id
        )
        reviewable_remaining = [
            item for item in line_items
            if item.review_status in {AIIntakeLineItem.ReviewStatus.REVIEWED, AIIntakeLineItem.ReviewStatus.PENDING_REVIEW}
        ]
        context["bulk_approve_ready"] = bool(reviewed_count and not pending_count and len(reviewable_remaining) > 1)
        context["workspace_status_label"] = _workspace_status_label(invoice_review, line_items)
        context["selected_line_item_blocker"] = _line_item_approval_blocker(selected_line_item)
        context["line_item_browser_state"] = {
            "selectedLineItemId": selected_line_item.pk if selected_line_item else None,
            "items": [_serialize_line_item_for_workspace(self.object.pk, item) for item in line_items],
        }
        context["invoice_review_alerts"] = _build_invoice_review_alerts(invoice_review, latest_job)
        return context


class AIIntakeLineItemUpdateView(StaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        line_item = get_object_or_404(
            AIIntakeLineItem.objects.select_related("invoice_review__job__document"),
            pk=kwargs["pk"],
        )
        action = str(request.POST.get("action") or "save").strip()
        _update_line_item_from_review(line_item, request.POST, action=action)
        if action == "unsupported":
            messages.warning(request, f"Line item {line_item.line_number} marked as unsupported for the current rollout.")
        elif action == "skip":
            messages.success(request, f"Line item {line_item.line_number} marked as skipped.")
        else:
            messages.success(request, f"Line item {line_item.line_number} review saved.")
        next_line_item = line_item
        if action in {"mark_reviewed", "skip", "unsupported"}:
            next_line_item = _next_actionable_line_item(line_item.invoice_review, line_item)
        return redirect(_workspace_redirect(line_item.invoice_review.job.document_id, next_line_item))


class AIIntakeLineItemApproveView(StaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        line_item = get_object_or_404(
            AIIntakeLineItem.objects.select_related("invoice_review__job__document"),
            pk=kwargs["pk"],
        )
        form = AIIntakeApproveForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    _update_line_item_from_review(line_item, request.POST, action="save")
                    invoice_review = line_item.invoice_review
                    line_items = list(invoice_review.line_items.order_by("line_number", "id"))
                    pending_count = sum(1 for item in line_items if item.review_status == AIIntakeLineItem.ReviewStatus.PENDING_REVIEW)
                    reviewed_count = sum(1 for item in line_items if item.review_status == AIIntakeLineItem.ReviewStatus.REVIEWED)
                    if reviewed_count and not pending_count and len([item for item in line_items if item.review_status in {AIIntakeLineItem.ReviewStatus.REVIEWED, AIIntakeLineItem.ReviewStatus.PENDING_REVIEW}]) > 1:
                        created_records = approve_reviewed_line_items(
                            invoice_review=invoice_review,
                            actor=request.user,
                            company=form.cleaned_data["company"],
                            location=form.cleaned_data["location"],
                        )
                        created_count = len(created_records)
                        messages.success(
                            request,
                            f"Approved {created_count} reviewed line item(s) and created their records.",
                        )
                    else:
                        created_record = approve_line_item(
                            line_item=line_item,
                            actor=request.user,
                            company=form.cleaned_data["company"],
                            location=form.cleaned_data["location"],
                        )
                        messages.success(
                            request,
                            f"Line item {line_item.line_number} approved and {created_record._meta.verbose_name} record created.",
                        )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        else:
            messages.error(request, "Line item approval failed.")
        next_line_item = _next_actionable_line_item(line_item.invoice_review, line_item)
        return redirect(_workspace_redirect(line_item.invoice_review.job.document_id, next_line_item))


class AIIntakeAuditListView(LoginRequiredMixin, ListView):
    model = AIIntakeAuditEvent
    template_name = "ai_intake/audit_list.html"
    context_object_name = "objects"

    def get_queryset(self):
        return AIIntakeAuditEvent.objects.select_related("document", "job", "draft", "actor")
