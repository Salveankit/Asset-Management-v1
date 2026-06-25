import hashlib
import os
import uuid

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models

from core.models import TimestampedSoftDeleteModel


def intake_document_upload_to(instance, filename: str) -> str:
    extension = os.path.splitext(filename)[1].lower()
    return f"ai-intake/{instance.pk or uuid.uuid4().hex}/{uuid.uuid4().hex}{extension}"


class AIIntakeDocument(TimestampedSoftDeleteModel):
    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Uploaded"
        PROCESSING = "processing", "Processing"
        REVIEW = "review", "Review"
        FAILED = "failed", "Failed"
        COMPLETED = "completed", "Completed"

    file = models.FileField(
        upload_to=intake_document_upload_to,
        validators=[FileExtensionValidator(allowed_extensions=["pdf", "png", "jpg", "jpeg", "webp"])],
    )
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, unique=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_intake_documents",
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.UPLOADED)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.original_filename

    @staticmethod
    def hash_bytes(file_bytes: bytes) -> str:
        return hashlib.sha256(file_bytes).hexdigest()


class AIIntakeJob(TimestampedSoftDeleteModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    document = models.ForeignKey(AIIntakeDocument, on_delete=models.CASCADE, related_name="jobs")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    provider_name = models.CharField(max_length=100, default="azure_openai")
    provider_request_id = models.CharField(max_length=255, blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    raw_response = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"AI Intake Job {self.pk}"


class AIIntakeDraft(TimestampedSoftDeleteModel):
    class Status(models.TextChoices):
        PENDING_REVIEW = "pending_review", "Pending Review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        RETRY_REQUIRED = "retry_required", "Retry Required"

    class RiskLevel(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    class RecommendedAction(models.TextChoices):
        ALLOW = "allow", "Allow"
        REVIEW = "review", "Review"
        BLOCK = "block", "Block"

    job = models.OneToOneField(AIIntakeJob, on_delete=models.CASCADE, related_name="draft")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING_REVIEW)
    extracted_data = models.JSONField(default=dict, blank=True)
    duplicate_risk_level = models.CharField(max_length=24, choices=RiskLevel.choices, default=RiskLevel.LOW)
    duplicate_risk_reasons = models.JSONField(default=list, blank=True)
    recommended_action = models.CharField(max_length=24, choices=RecommendedAction.choices, default=RecommendedAction.ALLOW)
    review_notes = models.TextField(blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_ai_intake_drafts",
    )
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rejected_ai_intake_drafts",
    )
    approved_asset = models.ForeignKey(
        "assets.Asset",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_intake_drafts",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"AI Draft {self.pk}"


class AIIntakeInvoiceReview(TimestampedSoftDeleteModel):
    class Status(models.TextChoices):
        EXTRACTED = "extracted", "Extracted"
        PARTIALLY_REVIEWED = "partially_reviewed", "Partially Reviewed"
        PARTIALLY_APPROVED = "partially_approved", "Partially Approved"
        APPROVED_COMPLETE = "approved_complete", "Approved Complete"
        REJECTED = "rejected", "Rejected"
        UNSUPPORTED_ITEMS_PENDING = "unsupported_items_pending", "Unsupported Items Pending"

    job = models.OneToOneField(AIIntakeJob, on_delete=models.CASCADE, related_name="invoice_review")
    legacy_draft = models.ForeignKey(
        AIIntakeDraft,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoice_reviews",
    )
    review_company = models.ForeignKey(
        "organisations.Company",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_intake_invoice_reviews",
    )
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.EXTRACTED)
    extracted_invoice_data = models.JSONField(default=dict, blank=True)
    invoice_metadata = models.JSONField(default=dict, blank=True)
    review_summary = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"AI Invoice Review {self.pk}"


class AIIntakeLineItem(TimestampedSoftDeleteModel):
    class ReviewStatus(models.TextChoices):
        EXTRACTED = "extracted", "Extracted"
        PENDING_REVIEW = "pending_review", "Pending Review"
        REVIEWED = "reviewed", "Reviewed"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        SKIPPED = "skipped", "Skipped"
        UNSUPPORTED = "unsupported", "Unsupported"

    invoice_review = models.ForeignKey(AIIntakeInvoiceReview, on_delete=models.CASCADE, related_name="line_items")
    line_number = models.PositiveIntegerField(default=1)
    raw_description = models.TextField(blank=True)
    normalized_description = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    line_total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    manufacturer_hint = models.CharField(max_length=255, blank=True)
    model_hint = models.CharField(max_length=255, blank=True)
    serial_hint = models.CharField(max_length=255, blank=True)
    part_number_hint = models.CharField(max_length=255, blank=True)
    reference_hint = models.CharField(max_length=255, blank=True)
    predicted_inventory_type = models.CharField(max_length=32, blank=True)
    predicted_category_name = models.CharField(max_length=255, blank=True)
    classification_confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    classification_rationale = models.TextField(blank=True)
    review_status = models.CharField(max_length=24, choices=ReviewStatus.choices, default=ReviewStatus.EXTRACTED)
    final_inventory_type = models.CharField(max_length=32, blank=True)
    final_category_name = models.CharField(max_length=255, blank=True)
    requires_review = models.BooleanField(default=True)
    unsupported_for_approval = models.BooleanField(default=False)
    created_record_content_type = models.ForeignKey(
        ContentType,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_intake_line_items",
    )
    created_record_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    created_record = GenericForeignKey("created_record_content_type", "created_record_object_id")
    extraction_payload = models.JSONField(default=dict, blank=True)
    reviewer_feedback = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["invoice_review_id", "line_number", "id"]

    def __str__(self) -> str:
        return f"AI Line Item {self.pk}"


class AIIntakeClassificationSignal(TimestampedSoftDeleteModel):
    source_draft = models.OneToOneField(
        AIIntakeDraft,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="classification_signal",
    )
    source_line_item = models.OneToOneField(
        AIIntakeLineItem,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="classification_signal",
    )
    company = models.ForeignKey(
        "organisations.Company",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_intake_classification_signals",
    )
    supplier = models.ForeignKey(
        "suppliers.Supplier",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_intake_classification_signals",
    )
    predicted_inventory_type = models.CharField(max_length=32, blank=True)
    final_inventory_type = models.CharField(max_length=32)
    predicted_category_name = models.CharField(max_length=255, blank=True)
    final_category_name = models.CharField(max_length=255, blank=True)
    raw_description = models.TextField(blank=True)
    normalized_item_name = models.CharField(max_length=255)
    supplier_name_snapshot = models.CharField(max_length=255, blank=True)
    order_number_snapshot = models.CharField(max_length=255, blank=True)
    purchase_date_snapshot = models.CharField(max_length=50, blank=True)
    classification_confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    correction_applied = models.BooleanField(default=False)
    unsupported_predicted = models.BooleanField(default=False)
    source_context = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.final_inventory_type} :: {self.normalized_item_name}"


class AIIntakeAuditEvent(models.Model):
    class EventType(models.TextChoices):
        DOCUMENT_UPLOADED = "document_uploaded", "Document Uploaded"
        EXTRACTION_SUCCEEDED = "extraction_succeeded", "Extraction Succeeded"
        EXTRACTION_FAILED = "extraction_failed", "Extraction Failed"
        DRAFT_APPROVED = "draft_approved", "Draft Approved"
        DRAFT_REJECTED = "draft_rejected", "Draft Rejected"
        DRAFT_RETRIED = "draft_retried", "Draft Retried"

    document = models.ForeignKey(AIIntakeDocument, null=True, blank=True, on_delete=models.CASCADE, related_name="audit_events")
    job = models.ForeignKey(AIIntakeJob, null=True, blank=True, on_delete=models.CASCADE, related_name="audit_events")
    draft = models.ForeignKey(AIIntakeDraft, null=True, blank=True, on_delete=models.CASCADE, related_name="audit_events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_intake_audit_events",
    )
    event_type = models.CharField(max_length=32, choices=EventType.choices)
    status = models.CharField(max_length=24, blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.get_event_type_display()} :: {self.created_at}"
