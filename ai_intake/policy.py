from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from django.utils.dateparse import parse_date
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from .schemas import InvoiceHeaderExtraction, InvoiceIntakeExtraction, InvoiceLineItemExtraction


class InventoryType(StrEnum):
    ASSET = "asset"
    ACCESSORY = "accessory"
    CONSUMABLE = "consumable"
    COMPONENT = "component"
    LICENSE = "license"


PHASE6_POLICY_VERSION = "phase6-v1"
PHASE6_SUPPORTED_APPROVAL_TYPES = {
    InventoryType.ASSET,
    InventoryType.ACCESSORY,
    InventoryType.CONSUMABLE,
    InventoryType.COMPONENT,
    InventoryType.LICENSE,
}

NON_INVENTORY_CHARGE_TERMS = {
    "shipping",
    "freight",
    "delivery",
    "handling",
    "packing",
    "courier",
    "transport",
    "tax",
    "gst",
    "vat",
    "discount",
    "round off",
    "round-off",
    "subtotal",
    "sub total",
    "total",
}

ACCESSORY_TERMS = {
    "backpack",
    "bag",
    "briefcase",
    "sleeve",
    "case",
    "dock",
    "docking",
    "keyboard",
    "mouse",
    "headset",
    "webcam",
    "tripod",
    "stand",
    "cable",
    "adapter",
    "hub",
    "charger",
}

COMPONENT_TERMS = {
    "replacement",
    "spare",
    "internal",
    "module",
    "battery",
    "ssd",
    "hdd",
    "ram",
    "memory",
    "dimm",
    "nvme",
    "adapter card",
    "motherboard",
    "fan",
}

LICENSE_TERMS = {
    "license",
    "licence",
    "subscription",
    "renewal",
    "seat",
    "seats",
    "saas",
    "microsoft 365",
    "office 365",
    "adobe",
    "autodesk",
    "endpoint protection",
    "antivirus",
    "cloud",
}

CONSUMABLE_TERMS = {
    "ink",
    "toner",
    "paper",
    "ribbon",
    "cartridge",
}

DATE_CLEANUP_PATTERN = re.compile(r"[^\d/\-.]")
HEADER_REVIEW_FIELDS = {
    "supplier_name": "Supplier name is missing from the extracted invoice header.",
    "invoice_number": "Invoice number is missing from the extracted invoice header.",
    "invoice_date": "Invoice date is missing from the extracted invoice header.",
}


class InventoryClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inventory_type: InventoryType
    inventory_confidence: float = Field(ge=0.0, le=1.0)
    classification_rationale: str = Field(default="")
    requires_review: bool = True
    normalized_item_name: str = Field(default="")
    suggested_category_name: str = Field(default="")
    unsupported_for_approval: bool = False


class ReviewerCorrectionFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_extracted_description: str = Field(default="")
    predicted_type: str = Field(default="")
    final_approved_type: str = Field(default="")
    predicted_category: str = Field(default="")
    final_category: str = Field(default="")
    supplier_invoice_context: str = Field(default="")


class InventoryClassificationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_version: str = PHASE6_POLICY_VERSION
    approval_mode: Literal["human_review_required"] = "human_review_required"
    supported_approval_types: list[str]
    unsupported_rollout_types: list[str]
    reserved_types: list[str]
    line_item_output_fields: list[str]
    reviewer_feedback_fields: list[str]


def _clean_text(value, *, default: str = "") -> str:
    cleaned = str(value or "").strip()
    return cleaned or default


def _normalize_decimal(value):
    if value in ("", None):
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (ArithmeticError, InvalidOperation, TypeError, ValueError):
        return None


def _normalize_positive_int(value):
    if value in ("", None):
        return None
    try:
        normalized = int(str(value).strip())
    except (ArithmeticError, TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _normalize_date_text(value: str) -> str:
    cleaned = DATE_CLEANUP_PATTERN.sub("", _clean_text(value))
    if not cleaned:
        return ""
    normalized = cleaned.replace(".", "-").replace("/", "-")
    parsed = parse_date(normalized)
    if parsed:
        return parsed.isoformat()
    parts = [part for part in normalized.split("-") if part]
    if len(parts) == 3 and len(parts[2]) == 4:
        day, month, year = parts
        try:
            parsed = parse_date(f"{int(year):04d}-{int(month):02d}-{int(day):02d}")
        except ValueError:
            parsed = None
        if parsed:
            return parsed.isoformat()
    return cleaned


def _line_item_description_text(extracted_item: "InvoiceLineItemExtraction") -> str:
    return " ".join(
        filter(
            None,
            [
                extracted_item.raw_description,
                extracted_item.normalized_description,
                extracted_item.notes,
                extracted_item.component_role_hint,
                extracted_item.billing_term_hint,
            ],
        )
    ).lower()


def get_inventory_classification_policy() -> InventoryClassificationPolicy:
    return InventoryClassificationPolicy(
        supported_approval_types=sorted(item.value for item in PHASE6_SUPPORTED_APPROVAL_TYPES),
        unsupported_rollout_types=[],
        reserved_types=[],
        line_item_output_fields=[
            "inventory_type",
            "inventory_confidence",
            "classification_rationale",
            "requires_review",
            "normalized_item_name",
            "suggested_category_name",
        ],
        reviewer_feedback_fields=[
            "original_extracted_description",
            "predicted_type",
            "final_approved_type",
            "predicted_category",
            "final_category",
            "supplier_invoice_context",
        ],
    )


def normalize_inventory_classification(payload: InventoryClassification | dict) -> InventoryClassification:
    classification = payload if isinstance(payload, InventoryClassification) else InventoryClassification.model_validate(payload)
    return classification.model_copy(update={"unsupported_for_approval": False})


def normalize_invoice_header(header: "InvoiceHeaderExtraction" | dict) -> "InvoiceHeaderExtraction":
    from .schemas import InvoiceHeaderExtraction

    current = header if isinstance(header, InvoiceHeaderExtraction) else InvoiceHeaderExtraction.model_validate(header or {})
    return InvoiceHeaderExtraction.model_validate(
        {
            "supplier_name": _clean_text(current.supplier_name),
            "invoice_number": _clean_text(current.invoice_number),
            "order_number": _clean_text(current.order_number),
            "invoice_date": _normalize_date_text(current.invoice_date),
            "currency": _clean_text(current.currency).upper(),
            "merchandise_row_count": _normalize_positive_int(current.merchandise_row_count),
            "subtotal_amount": _normalize_decimal(current.subtotal_amount),
            "tax_amount": _normalize_decimal(current.tax_amount),
            "total_amount": _normalize_decimal(current.total_amount),
            "notes": _clean_text(current.notes),
        }
    )


def normalize_invoice_line_item(item: "InvoiceLineItemExtraction" | dict) -> "InvoiceLineItemExtraction":
    from .schemas import InvoiceLineItemExtraction

    current = item if isinstance(item, InvoiceLineItemExtraction) else InvoiceLineItemExtraction.model_validate(item or {})
    normalized_description = _clean_text(current.normalized_description) or _clean_text(current.raw_description)
    return InvoiceLineItemExtraction.model_validate(
        {
            "raw_description": _clean_text(current.raw_description),
            "normalized_description": normalized_description,
            "quantity": _normalize_decimal(current.quantity),
            "unit_price": _normalize_decimal(current.unit_price),
            "line_total": _normalize_decimal(current.line_total),
            "manufacturer_hint": _clean_text(current.manufacturer_hint),
            "model_hint": _clean_text(current.model_hint),
            "serial_hint": _clean_text(current.serial_hint),
            "part_number_hint": _clean_text(current.part_number_hint),
            "reference_hint": _clean_text(current.reference_hint),
            "seat_hint": current.seat_hint,
            "product_key_hint": _clean_text(current.product_key_hint),
            "license_reference_hint": _clean_text(current.license_reference_hint),
            "expiry_date_hint": _normalize_date_text(current.expiry_date_hint),
            "renewal_date_hint": _normalize_date_text(current.renewal_date_hint),
            "billing_term_hint": _clean_text(current.billing_term_hint),
            "component_role_hint": _clean_text(current.component_role_hint),
            "notes": _clean_text(current.notes),
        }
    )


def normalize_invoice_extraction(payload: "InvoiceIntakeExtraction" | dict) -> "InvoiceIntakeExtraction":
    from .schemas import InvoiceIntakeExtraction

    current = payload if isinstance(payload, InvoiceIntakeExtraction) else InvoiceIntakeExtraction.model_validate(payload or {})
    return InvoiceIntakeExtraction.model_validate(
        {
            "invoice_header": normalize_invoice_header(current.invoice_header).model_dump(mode="json"),
            "line_items": [normalize_invoice_line_item(item).model_dump(mode="json") for item in current.line_items],
        }
    )


def _is_non_inventory_charge_line(extracted_item: "InvoiceLineItemExtraction") -> tuple[bool, str]:
    description_text = _line_item_description_text(extracted_item)
    if not description_text:
        return False, ""
    if not any(term in description_text for term in NON_INVENTORY_CHARGE_TERMS):
        return False, ""
    if any(term in description_text for term in {"label", "labels", "box", "boxes", "kit"}):
        return False, ""
    has_identity_signals = bool(
        extracted_item.serial_hint
        or extracted_item.model_hint
        or extracted_item.manufacturer_hint
        or extracted_item.part_number_hint
        or extracted_item.component_role_hint
        or extracted_item.seat_hint
        or extracted_item.product_key_hint
    )
    if has_identity_signals:
        return False, ""
    matched_term = next((term for term in NON_INVENTORY_CHARGE_TERMS if term in description_text), "summary charge")
    return True, f"Excluded non-inventory row due to {matched_term} signal."


def filter_invoice_line_items(invoice_payload: "InvoiceIntakeExtraction") -> tuple["InvoiceIntakeExtraction", list[dict]]:
    from .schemas import InvoiceIntakeExtraction
    kept_items = []
    excluded_items = []
    for index, item in enumerate(invoice_payload.line_items, start=1):
        is_excluded, reason = _is_non_inventory_charge_line(item)
        if is_excluded:
            excluded_items.append(
                {
                    "original_line_number": index,
                    "raw_description": item.raw_description,
                    "reason": reason,
                }
            )
            continue
        kept_items.append(item.model_dump(mode="json"))
    filtered_payload = InvoiceIntakeExtraction.model_validate(
        {
            "invoice_header": invoice_payload.invoice_header.model_dump(mode="json"),
            "line_items": kept_items,
        }
    )
    return filtered_payload, excluded_items


def derive_invoice_line_item_classification(
    *,
    extracted_item: "InvoiceLineItemExtraction",
    initial_classification: InventoryClassification | dict | None = None,
    retrieval_examples: list[dict] | None = None,
    invoice_header: "InvoiceHeaderExtraction" | dict | None = None,
) -> InventoryClassification:
    normalized = normalize_inventory_classification(initial_classification) if initial_classification else None
    description_text = _line_item_description_text(extracted_item)
    quantity = extracted_item.quantity or Decimal("0")
    has_trackable_identity = bool(extracted_item.serial_hint or extracted_item.model_hint or extracted_item.manufacturer_hint)
    has_component_evidence = bool(
        extracted_item.component_role_hint
        or (extracted_item.part_number_hint and not extracted_item.serial_hint)
        or any(term in description_text for term in COMPONENT_TERMS)
    )
    has_license_evidence = bool(
        extracted_item.seat_hint
        or extracted_item.product_key_hint
        or extracted_item.license_reference_hint
        or extracted_item.expiry_date_hint
        or extracted_item.renewal_date_hint
        or any(term in description_text for term in LICENSE_TERMS)
    )
    has_accessory_signals = any(term in description_text for term in ACCESSORY_TERMS)
    has_consumable_signals = any(term in description_text for term in CONSUMABLE_TERMS)

    if normalized is None:
        if has_license_evidence:
            normalized = InventoryClassification(
                inventory_type=InventoryType.LICENSE,
                inventory_confidence=0.9 if (extracted_item.product_key_hint or extracted_item.seat_hint or extracted_item.license_reference_hint) else 0.78,
                classification_rationale="Seat, subscription, or entitlement signals are present in the extracted line item.",
                requires_review=True,
                normalized_item_name=extracted_item.normalized_description or extracted_item.raw_description,
                suggested_category_name="",
            )
        elif has_component_evidence:
            normalized = InventoryClassification(
                inventory_type=InventoryType.COMPONENT,
                inventory_confidence=0.84 if (extracted_item.part_number_hint or extracted_item.component_role_hint) else 0.72,
                classification_rationale="Part-level hardware signals suggest quantity-tracked component inventory.",
                requires_review=True,
                normalized_item_name=extracted_item.normalized_description or extracted_item.raw_description,
                suggested_category_name="",
            )
        elif has_trackable_identity:
            normalized = InventoryClassification(
                inventory_type=InventoryType.ASSET,
                inventory_confidence=0.95 if extracted_item.serial_hint else 0.88,
                classification_rationale="Trackable hardware signals are present in the extracted line item.",
                requires_review=True,
                normalized_item_name=extracted_item.normalized_description or extracted_item.raw_description,
                suggested_category_name="",
            )
        elif has_consumable_signals:
            normalized = InventoryClassification(
                inventory_type=InventoryType.CONSUMABLE,
                inventory_confidence=0.82,
                classification_rationale="Depleting stock-item signals are present in the extracted line item.",
                requires_review=True,
                normalized_item_name=extracted_item.normalized_description or extracted_item.raw_description,
                suggested_category_name="",
            )
        elif quantity > 1:
            normalized = InventoryClassification(
                inventory_type=InventoryType.CONSUMABLE,
                inventory_confidence=0.62,
                classification_rationale="Quantity-driven line item lacks trackable identity signals and needs review.",
                requires_review=True,
                normalized_item_name=extracted_item.normalized_description or extracted_item.raw_description,
                suggested_category_name="",
            )
        else:
            normalized = InventoryClassification(
                inventory_type=InventoryType.ACCESSORY,
                inventory_confidence=0.58,
                classification_rationale="Supporting item signals are stronger than trackable-asset signals, but review remains required.",
                requires_review=True,
                normalized_item_name=extracted_item.normalized_description or extracted_item.raw_description,
                suggested_category_name="",
            )

    rationale_parts = [normalized.classification_rationale.strip()] if normalized.classification_rationale.strip() else []
    updated_type = normalized.inventory_type
    updated_confidence = normalized.inventory_confidence
    requires_review = normalized.requires_review

    if has_license_evidence and updated_type != InventoryType.LICENSE:
        updated_type = InventoryType.LICENSE
        updated_confidence = max(updated_confidence, 0.84)
        rationale_parts.append("Deterministic rules corrected the working type to license because entitlement signals are explicit.")
    elif has_component_evidence and updated_type not in {InventoryType.COMPONENT, InventoryType.LICENSE}:
        updated_type = InventoryType.COMPONENT
        updated_confidence = max(updated_confidence, 0.8)
        rationale_parts.append("Deterministic rules corrected the working type to component because part-style signals are explicit.")
    elif has_accessory_signals and not has_component_evidence and updated_type == InventoryType.COMPONENT:
        updated_type = InventoryType.ACCESSORY
        updated_confidence = min(max(updated_confidence, 0.72), 0.86)
        rationale_parts.append("Description signals match an issued accessory rather than a spare or internal component.")
    elif has_consumable_signals and not has_trackable_identity and updated_type not in {InventoryType.LICENSE, InventoryType.COMPONENT}:
        updated_type = InventoryType.CONSUMABLE
        updated_confidence = max(updated_confidence, 0.8)
        rationale_parts.append("Deterministic rules corrected the working type to consumable because depleting stock signals are explicit.")
    elif has_trackable_identity and updated_type not in {InventoryType.LICENSE, InventoryType.COMPONENT}:
        updated_type = InventoryType.ASSET
        updated_confidence = max(updated_confidence, 0.82)
        rationale_parts.append("Serial, manufacturer, or model evidence makes this line asset-leaning.")
    elif has_accessory_signals and updated_type not in {InventoryType.LICENSE, InventoryType.COMPONENT, InventoryType.CONSUMABLE, InventoryType.ASSET}:
        updated_type = InventoryType.ACCESSORY
        updated_confidence = max(updated_confidence, 0.72)
        rationale_parts.append("Deterministic rules preserved accessory classification because issued-support signals are explicit.")

    if updated_type == InventoryType.COMPONENT and not has_component_evidence:
        updated_confidence = min(updated_confidence, 0.68)
        requires_review = True
        rationale_parts.append("Component confidence was reduced because part-level evidence is weak.")

    if updated_type == InventoryType.ASSET and quantity > 1 and not extracted_item.serial_hint:
        updated_confidence = min(updated_confidence, 0.72)
        requires_review = True
        rationale_parts.append("Quantity is greater than 1 without serial-level identity, so reviewer confirmation is required.")
    elif updated_type == InventoryType.ASSET and quantity > 1 and updated_confidence < 0.8:
        requires_review = True
        rationale_parts.append("Quantity suggests multiple potential records and the prediction must be reviewed.")

    if updated_type == InventoryType.ASSET and not has_trackable_identity and updated_confidence < 0.75:
        requires_review = True
        rationale_parts.append("Trackable identity signals are weak, so this line should stay in review.")

    if updated_type == InventoryType.LICENSE and not has_license_evidence:
        updated_confidence = min(updated_confidence, 0.68)
        requires_review = True
        rationale_parts.append("License confidence was reduced because entitlement signals are weak.")

    header = normalize_invoice_header(invoice_header or {})
    missing_header_reasons = [message for key, message in HEADER_REVIEW_FIELDS.items() if not getattr(header, key)]
    if missing_header_reasons:
        requires_review = True
        rationale_parts.extend(missing_header_reasons)

    retrieval_examples = retrieval_examples or []
    suggested_category_name = _clean_text(normalized.suggested_category_name)
    if retrieval_examples:
        top_example = retrieval_examples[0]
        if not suggested_category_name:
            suggested_category_name = _clean_text(top_example.get("final_category_name"))
        rationale_parts.append("Reviewer similarity examples were attached as reference context.")

    return normalize_inventory_classification(
        InventoryClassification(
            inventory_type=updated_type,
            inventory_confidence=round(float(max(0.0, min(updated_confidence, 0.99))), 2),
            classification_rationale=" ".join(part for part in rationale_parts if part).strip(),
            requires_review=requires_review,
            normalized_item_name=_clean_text(normalized.normalized_item_name or extracted_item.normalized_description or extracted_item.raw_description),
            suggested_category_name=suggested_category_name,
        )
    )


def summarize_invoice_reconciliation(invoice_payload: "InvoiceIntakeExtraction") -> dict:
    tolerance = Decimal("1.00")
    issues = []
    line_total_sum = Decimal("0")

    for field_name, message in HEADER_REVIEW_FIELDS.items():
        if not getattr(invoice_payload.invoice_header, field_name):
            issues.append({"severity": "warning", "code": f"missing_{field_name}", "message": message})

    for index, item in enumerate(invoice_payload.line_items, start=1):
        quantity = _normalize_decimal(item.quantity)
        unit_price = _normalize_decimal(item.unit_price)
        line_total = _normalize_decimal(item.line_total)
        if line_total is not None:
            line_total_sum += line_total
        if quantity is None or unit_price is None or line_total is None:
            continue
        expected_total = quantity * unit_price
        if abs(expected_total - line_total) > tolerance:
            issues.append(
                {
                    "severity": "warning",
                    "code": "line_total_mismatch",
                    "line_number": index,
                    "message": f"Line {index} total does not align with quantity x unit price ({quantity} x {unit_price} != {line_total}).",
                }
            )

    subtotal_amount = _normalize_decimal(invoice_payload.invoice_header.subtotal_amount)
    total_amount = _normalize_decimal(invoice_payload.invoice_header.total_amount)
    tax_amount = _normalize_decimal(invoice_payload.invoice_header.tax_amount) or Decimal("0")

    if subtotal_amount is not None and abs(line_total_sum - subtotal_amount) > tolerance:
        issues.append(
            {
                "severity": "warning",
                "code": "subtotal_mismatch",
                "message": f"Extracted merchandise rows sum to {line_total_sum} but subtotal is {subtotal_amount}.",
            }
        )
    if total_amount is not None and subtotal_amount is not None and abs((subtotal_amount + tax_amount) - total_amount) > tolerance:
        issues.append(
            {
                "severity": "warning",
                "code": "invoice_total_mismatch",
                "message": f"Subtotal plus tax does not align with invoice total ({subtotal_amount} + {tax_amount} != {total_amount}).",
            }
        )

    return {
        "tolerance": format(tolerance, "f"),
        "line_total_sum": format(line_total_sum, "f"),
        "issues": issues,
        "requires_review": bool(issues),
    }


def build_reviewer_feedback(
    *,
    original_description: str,
    classification: InventoryClassification,
    supplier_name: str = "",
    order_number: str = "",
    purchase_date: str = "",
) -> ReviewerCorrectionFeedback:
    context_bits = [bit for bit in [supplier_name.strip(), order_number.strip(), purchase_date.strip()] if bit]
    return ReviewerCorrectionFeedback(
        original_extracted_description=original_description.strip(),
        predicted_type=classification.inventory_type.value,
        predicted_category=classification.suggested_category_name,
        supplier_invoice_context=" | ".join(context_bits),
    )


def build_legacy_asset_classification(extracted_data: dict) -> InventoryClassification:
    asset_name = str(extracted_data.get("asset_name") or "").strip()
    manufacturer_name = str(extracted_data.get("manufacturer_name") or "").strip()
    model_name = str(extracted_data.get("model_name") or "").strip()
    category_name = str(extracted_data.get("category_name") or "Imported Assets").strip() or "Imported Assets"
    serial = str(extracted_data.get("serial") or "").strip()
    quantity = extracted_data.get("quantity") or 1

    normalized_name = asset_name or " ".join(part for part in [manufacturer_name, model_name] if part).strip() or "Imported Asset"
    evidence = []
    confidence = 0.68

    if serial:
        evidence.append("serial captured")
        confidence = max(confidence, 0.96)
    if manufacturer_name and model_name:
        evidence.append("manufacturer and model captured")
        confidence = max(confidence, 0.9)
    elif model_name:
        evidence.append("model captured")
        confidence = max(confidence, 0.82)
    elif asset_name:
        evidence.append("asset name captured")
        confidence = max(confidence, 0.75)

    requires_review = quantity != 1 or not asset_name
    if quantity != 1:
        evidence.append("quantity suggests reviewer confirmation is needed")
        confidence = min(confidence, 0.72)
    if not asset_name:
        evidence.append("asset name needs reviewer confirmation")

    rationale = ", ".join(evidence) if evidence else "Legacy asset extraction defaulted to asset classification."
    return normalize_inventory_classification(
        InventoryClassification(
            inventory_type=InventoryType.ASSET,
            inventory_confidence=round(confidence, 2),
            classification_rationale=rationale,
            requires_review=requires_review or True,
            normalized_item_name=normalized_name,
            suggested_category_name=category_name,
        )
    )


def enrich_legacy_extracted_data(extracted_data: dict) -> dict:
    enriched = dict(extracted_data or {})
    classification = normalize_inventory_classification(
        enriched.get("inventory_classification") or build_legacy_asset_classification(enriched)
    )
    original_description = str(enriched.get("asset_name") or classification.normalized_item_name or "").strip()
    feedback = ReviewerCorrectionFeedback.model_validate(
        enriched.get("reviewer_feedback")
        or build_reviewer_feedback(
            original_description=original_description,
            classification=classification,
            supplier_name=str(enriched.get("supplier_name") or ""),
            order_number=str(enriched.get("order_number") or ""),
            purchase_date=str(enriched.get("purchase_date") or ""),
        )
    )
    enriched["inventory_policy"] = get_inventory_classification_policy().model_dump(mode="json")
    enriched["inventory_classification"] = classification.model_dump(mode="json")
    enriched["reviewer_feedback"] = feedback.model_dump(mode="json")
    return enriched


def get_extracted_inventory_classification(extracted_data: dict) -> InventoryClassification:
    if extracted_data and extracted_data.get("inventory_classification"):
        return normalize_inventory_classification(extracted_data["inventory_classification"])
    return build_legacy_asset_classification(extracted_data or {})
