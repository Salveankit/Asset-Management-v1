from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher

from django.db.models import Count, Q

from .models import AIIntakeClassificationSignal, AIIntakeDraft, AIIntakeLineItem

TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
SIMILARITY_OVERRIDE_THRESHOLD = 0.9
SIMILARITY_REVIEW_THRESHOLD = 0.72
LOW_CONFIDENCE_THRESHOLD = Decimal("0.700")


def _clean_text(value, *, default: str = "") -> str:
    cleaned = str(value or "").strip()
    return cleaned or default


def _tokens(value: str) -> set[str]:
    return set(TOKEN_PATTERN.findall(value.lower()))


def _token_overlap(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _item_family_label(value: str) -> str:
    tokens = TOKEN_PATTERN.findall(value.lower())
    if not tokens:
        return "unclassified"
    return " ".join(tokens[:4])


def _to_decimal(value) -> Decimal | None:
    if value in ("", None):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


@dataclass(frozen=True)
class SimilarityMatch:
    signal_id: int
    score: float
    final_inventory_type: str
    final_category_name: str
    normalized_item_name: str
    supplier_name: str
    company_id: int | None
    company_scope: str
    correction_applied: bool
    source_context: dict

    def as_prompt_example(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "score": round(self.score, 3),
            "final_inventory_type": self.final_inventory_type,
            "final_category_name": self.final_category_name,
            "normalized_item_name": self.normalized_item_name,
            "supplier_name": self.supplier_name,
            "company_id": self.company_id,
            "company_scope": self.company_scope,
            "correction_applied": self.correction_applied,
            "source_context": self.source_context,
        }


def _score_signal(*, query: str, supplier_name: str, company_id: int | None, signal: AIIntakeClassificationSignal) -> float:
    normalized_query = _clean_text(query).lower()
    signal_name = _clean_text(signal.normalized_item_name).lower()
    sequence_score = SequenceMatcher(None, normalized_query, signal_name).ratio()
    token_score = _token_overlap(normalized_query, signal_name)
    score = (sequence_score * 0.72) + (token_score * 0.18)

    if company_id and signal.company_id == company_id:
        score += 0.08
    elif company_id and signal.company_id and signal.company_id != company_id:
        score -= 0.08

    cleaned_supplier = _clean_text(supplier_name).lower()
    signal_supplier = _clean_text(signal.supplier_name_snapshot or getattr(signal.supplier, "name", "")).lower()
    if cleaned_supplier and signal_supplier == cleaned_supplier:
        score += 0.06

    if signal.correction_applied:
        score += 0.02

    return max(0.0, min(score, 0.999))


def find_similarity_matches(
    *,
    normalized_description: str,
    supplier_name: str = "",
    company=None,
    limit: int = 3,
) -> list[SimilarityMatch]:
    query = _clean_text(normalized_description)
    if not query:
        return []

    company_id = getattr(company, "id", None)
    signals = list(
        AIIntakeClassificationSignal.objects.filter(deleted_at__isnull=True)
        .exclude(normalized_item_name="")
        .select_related("company", "supplier")
    )
    ranked = []
    for signal in signals:
        score = _score_signal(query=query, supplier_name=supplier_name, company_id=company_id, signal=signal)
        if score < SIMILARITY_REVIEW_THRESHOLD:
            continue
        ranked.append(
            SimilarityMatch(
                signal_id=signal.pk,
                score=score,
                final_inventory_type=signal.final_inventory_type,
                final_category_name=signal.final_category_name,
                normalized_item_name=signal.normalized_item_name,
                supplier_name=signal.supplier_name_snapshot or getattr(signal.supplier, "name", ""),
                company_id=signal.company_id,
                company_scope=(
                    "same_company"
                    if company_id and signal.company_id == company_id
                    else "cross_company"
                    if company_id and signal.company_id and signal.company_id != company_id
                    else "global"
                ),
                correction_applied=signal.correction_applied,
                source_context=signal.source_context or {},
            )
        )
    ranked.sort(key=lambda item: (-item.score, item.company_id is None, item.normalized_item_name))
    return ranked[:limit]


def build_similarity_examples_for_invoice(*, invoice_payload, company=None, limit: int = 3) -> dict[int, list[dict]]:
    supplier_name = _clean_text(invoice_payload.invoice_header.supplier_name)
    examples_by_line = {}
    for index, item in enumerate(invoice_payload.line_items, start=1):
        query = item.normalized_description or item.raw_description
        matches = find_similarity_matches(
            normalized_description=query,
            supplier_name=supplier_name,
            company=company,
            limit=limit,
        )
        examples_by_line[index] = [match.as_prompt_example() for match in matches]
    return examples_by_line


def record_training_signal_from_draft(*, draft: AIIntakeDraft, company=None) -> AIIntakeClassificationSignal:
    extracted = dict(draft.extracted_data or {})
    inventory_classification = extracted.get("inventory_classification") or {}
    feedback = extracted.get("reviewer_feedback") or {}
    supplier_name = _clean_text(extracted.get("supplier_name"))
    signal, _ = AIIntakeClassificationSignal.objects.update_or_create(
        source_draft=draft,
        defaults={
            "company": company,
            "supplier": getattr(draft.approved_asset, "supplier", None),
            "predicted_inventory_type": _clean_text(inventory_classification.get("inventory_type")),
            "final_inventory_type": _clean_text(feedback.get("final_approved_type"), default="asset"),
            "predicted_category_name": _clean_text(inventory_classification.get("suggested_category_name")),
            "final_category_name": _clean_text(feedback.get("final_category")),
            "raw_description": _clean_text(extracted.get("asset_name")),
            "normalized_item_name": _clean_text(extracted.get("asset_name"), default="Imported Asset"),
            "supplier_name_snapshot": supplier_name,
            "order_number_snapshot": _clean_text(extracted.get("order_number")),
            "purchase_date_snapshot": _clean_text(extracted.get("purchase_date")),
            "classification_confidence": _to_decimal(inventory_classification.get("inventory_confidence")),
            "correction_applied": _clean_text(inventory_classification.get("inventory_type")) != _clean_text(feedback.get("final_approved_type"), default="asset"),
            "unsupported_predicted": bool(inventory_classification.get("unsupported_for_approval")),
            "source_context": {
                "source": "legacy_draft",
                "review_notes": _clean_text(draft.review_notes),
            },
        },
    )
    return signal


def record_training_signal_from_line_item(*, line_item: AIIntakeLineItem, company=None) -> AIIntakeClassificationSignal:
    feedback = dict(line_item.reviewer_feedback or {})
    invoice_review = line_item.invoice_review
    invoice_metadata = invoice_review.invoice_metadata or {}
    signal, _ = AIIntakeClassificationSignal.objects.update_or_create(
        source_line_item=line_item,
        defaults={
            "company": company,
            "supplier": getattr(line_item.created_record, "supplier", None),
            "predicted_inventory_type": _clean_text(line_item.predicted_inventory_type),
            "final_inventory_type": _clean_text(line_item.final_inventory_type or line_item.predicted_inventory_type),
            "predicted_category_name": _clean_text(line_item.predicted_category_name),
            "final_category_name": _clean_text(line_item.final_category_name),
            "raw_description": _clean_text(line_item.raw_description),
            "normalized_item_name": _clean_text(line_item.normalized_description or line_item.raw_description, default="Imported Item"),
            "supplier_name_snapshot": _clean_text(invoice_metadata.get("supplier_name")),
            "order_number_snapshot": _clean_text(invoice_metadata.get("order_number")),
            "purchase_date_snapshot": _clean_text(invoice_metadata.get("invoice_date")),
            "classification_confidence": _to_decimal(line_item.classification_confidence),
            "correction_applied": _clean_text(line_item.predicted_inventory_type) != _clean_text(line_item.final_inventory_type or line_item.predicted_inventory_type),
            "unsupported_predicted": bool(line_item.unsupported_for_approval),
            "source_context": {
                "source": "line_item",
                "line_number": line_item.line_number,
                "invoice_number": _clean_text(invoice_metadata.get("invoice_number")),
                "retrieval_examples": feedback.get("retrieval_examples") or [],
            },
        },
    )
    return signal


def get_review_analytics(*, company=None) -> dict:
    signal_filters = Q(deleted_at__isnull=True)
    if getattr(company, "id", None):
        signal_filters &= Q(company=company)

    corrected_types = list(
        AIIntakeClassificationSignal.objects.filter(signal_filters, correction_applied=True)
        .exclude(predicted_inventory_type="")
        .values("predicted_inventory_type")
        .annotate(total=Count("id"))
        .order_by("-total", "predicted_inventory_type")[:5]
    )

    low_confidence_signals = AIIntakeClassificationSignal.objects.filter(signal_filters)
    low_confidence_signals = low_confidence_signals.filter(classification_confidence__lt=LOW_CONFIDENCE_THRESHOLD)
    family_counts: dict[str, int] = {}
    for signal in low_confidence_signals:
        family = _item_family_label(signal.normalized_item_name)
        family_counts[family] = family_counts.get(family, 0) + 1
    low_confidence_item_families = [
        {"family": family, "total": total}
        for family, total in sorted(family_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]

    unsupported_query = AIIntakeLineItem.objects.filter(deleted_at__isnull=True, unsupported_for_approval=True)
    if getattr(company, "id", None):
        unsupported_query = unsupported_query.filter(invoice_review__review_company=company)
    unsupported_item_volume = {
        "total": unsupported_query.count(),
        "by_predicted_type": list(
            unsupported_query.values("predicted_inventory_type")
            .annotate(total=Count("id"))
            .order_by("-total", "predicted_inventory_type")
        ),
    }

    unlock_candidates = list(
        unsupported_query.filter(predicted_inventory_type__in=["component", "license"])
        .values("predicted_inventory_type")
        .annotate(total=Count("id"))
        .order_by("-total", "predicted_inventory_type")
    )

    return {
        "top_corrected_predicted_types": corrected_types,
        "low_confidence_item_families": low_confidence_item_families,
        "unsupported_item_volume": unsupported_item_volume,
        "unlock_candidates": unlock_candidates,
    }
