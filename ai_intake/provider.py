from __future__ import annotations

import base64
import json
import mimetypes
import re
import time
from dataclasses import dataclass
from io import BytesIO
from typing import TypeVar

from django.conf import settings
from openai import APIConnectionError, APIError, APIStatusError, AzureOpenAI, DefaultHttpxClient
from pydantic import BaseModel, ValidationError as PydanticValidationError

from .schemas import AssetIntakeExtraction, InvoiceIntakeExtraction, InvoiceLineItemClassification


class AIProviderError(Exception):
    pass


class AIProviderNotConfiguredError(AIProviderError):
    pass


class AIProviderSchemaError(AIProviderError):
    def __init__(self, message: str, *, raw_text: str = "", raw_response: dict | None = None) -> None:
        super().__init__(message)
        self.raw_text = raw_text
        self.raw_response = raw_response or {}



@dataclass
class ExtractionResult:
    payload: BaseModel
    raw_response: dict
    latency_ms: int
    provider_request_id: str


StructuredPayload = TypeVar("StructuredPayload", bound=BaseModel)


class AzureOpenAIIntakeClient:
    def __init__(self) -> None:
        self.config = settings.AZURE_OPENAI

    def is_configured(self) -> bool:
        required = (
            self.config.get("endpoint"),
            self.config.get("api_key"),
            self.config.get("deployment"),
            self.config.get("api_version"),
        )
        return all(required)

    def _client(self) -> AzureOpenAI:
        if not self.is_configured():
            raise AIProviderNotConfiguredError("Azure OpenAI is not configured.")
        return AzureOpenAI(
            api_key=self.config["api_key"],
            api_version=self.config["api_version"],
            azure_endpoint=self.config["endpoint"],
            timeout=self.config["timeout_seconds"],
            max_retries=self.config["max_retries"],
            http_client=DefaultHttpxClient(trust_env=False),
        )

    @staticmethod
    def _prompt() -> str:
        return (
            "Extract one reviewable IT asset draft from this document. "
            "Return only valid JSON with keys: asset_name, manufacturer_name, model_name, "
            "model_number, supplier_name, category_name, serial, order_number, purchase_date, "
            "purchase_cost, notes, quantity. "
            "If a field is missing, return an empty string, null, or quantity 1 as appropriate."
        )

    @staticmethod
    def _invoice_extraction_prompt() -> str:
        return (
            "Extract invoice metadata and only actual merchandise or inventory line items from this document. "
            "Return only valid JSON with keys: invoice_header and line_items. "
            "invoice_header must contain: supplier_name, invoice_number, order_number, invoice_date, "
            "currency, merchandise_row_count, subtotal_amount, tax_amount, total_amount, notes. "
            "Exclude non-item rows such as shipping charges, delivery charges, freight, handling fees, packing fees, "
            "round-off adjustments, taxes, discounts, and summary totals from line_items. "
            "Each line item must contain: raw_description, normalized_description, quantity, unit_price, "
            "line_total, manufacturer_hint, model_hint, serial_hint, part_number_hint, reference_hint, "
            "seat_hint, product_key_hint, license_reference_hint, expiry_date_hint, renewal_date_hint, "
            "billing_term_hint, component_role_hint, notes. "
            "Set merchandise_row_count to the number of actual merchandise rows visible on the invoice, using row numbers or total quantity markers when shown. "
            "Preserve ambiguous descriptions verbatim in raw_description and prefer empty strings or nulls over guesses."
        )

    @staticmethod
    def _invoice_repair_prompt(raw_text: str) -> str:
        return (
            "Repair the following malformed invoice extraction into exact schema-compliant JSON only. "
            "Return only valid JSON with keys invoice_header and line_items. "
            "Do not add commentary, markdown, or explanations. "
            "invoice_header must include merchandise_row_count. "
            "Keep only merchandise or inventory rows and exclude shipping, freight, tax, discount, round-off, and totals.\n\n"
            f"Malformed extraction:\n{raw_text}"
        )

    @staticmethod
    def _invoice_document_retry_prompt(raw_text: str = "") -> str:
        prompt = (
            "Re-read the original invoice document and extract invoice metadata plus every merchandise or inventory line item. "
            "Do not rely only on a previous partial or malformed extraction. "
            "Return only valid JSON with keys: invoice_header and line_items. "
            "invoice_header must contain: supplier_name, invoice_number, order_number, invoice_date, "
            "currency, merchandise_row_count, subtotal_amount, tax_amount, total_amount, notes. "
            "Exclude non-item rows such as shipping charges, delivery charges, freight, handling fees, packing fees, "
            "round-off adjustments, taxes, discounts, and summary totals from line_items. "
            "Each line item must contain: raw_description, normalized_description, quantity, unit_price, "
            "line_total, manufacturer_hint, model_hint, serial_hint, part_number_hint, reference_hint, "
            "seat_hint, product_key_hint, license_reference_hint, expiry_date_hint, renewal_date_hint, "
            "billing_term_hint, component_role_hint, notes. "
            "Preserve all visible merchandise rows across the full page or pages, including rows after malformed sections or broken table structure. "
            "If the invoice shows numbered rows such as 1..N or a total like 5 Nos, merchandise_row_count must match that visible count. "
            "Prefer empty strings or nulls over guesses."
        )
        if raw_text.strip():
            return (
                f"{prompt}\n\n"
                "Previous extraction was incomplete or malformed. Use it only as a caution signal, not as source truth.\n"
                f"Previous extraction text:\n{raw_text}"
            )
        return prompt

    @staticmethod
    def _line_item_classification_prompt(invoice_payload: InvoiceIntakeExtraction, retrieval_examples_by_line: dict[int, list[dict]] | None = None) -> str:
        policy = (
            "Classify each line item into one of: asset, accessory, consumable, component, license. "
            "Asset means independently tracked hardware with lifecycle/custody identity. "
            "Accessory means supporting issued item, often quantity based or low-value support gear. "
            "Consumable means stock that is depleted or replenished and not lifecycle tracked. "
            "Component means quantity-tracked spare or internal part inventory. "
            "License means seats, subscriptions, or entitlement records rather than physical goods. "
            "Return only valid JSON with key line_item_classifications. "
            "Each entry must contain: line_number and classification. "
            "classification must contain inventory_type, inventory_confidence, classification_rationale, "
            "requires_review, normalized_item_name, suggested_category_name, unsupported_for_approval. "
            "Avoid fixed keyword assumptions; prefer requires_review=true when ambiguous."
        )
        payload_json = invoice_payload.model_dump_json()
        retrieval_context = []
        for line_number, examples in (retrieval_examples_by_line or {}).items():
            if not examples:
                continue
            retrieval_context.append(
                {
                    "line_number": line_number,
                    "approved_examples": examples,
                }
            )
        retrieval_json = json.dumps(retrieval_context)
        return f"{policy}\n\nApproved example retrieval context:\n{retrieval_json}\n\nInvoice payload:\n{payload_json}"

    @staticmethod
    def _normalize_decimal_value(value):
        if value in ("", None):
            return None
        if isinstance(value, (int, float)):
            return value
        text = str(value).strip()
        if not text:
            return None
        text = text.replace(",", "")
        text = re.sub(r"[^\d.\-]", "", text)
        return text or None

    @classmethod
    def _sanitize_asset_payload(cls, payload: dict) -> dict:
        cleaned = dict(payload or {})
        cleaned["purchase_cost"] = cls._normalize_decimal_value(cleaned.get("purchase_cost"))
        for key in [
            "asset_name",
            "manufacturer_name",
            "model_name",
            "model_number",
            "supplier_name",
            "category_name",
            "serial",
            "order_number",
            "purchase_date",
            "notes",
        ]:
            cleaned[key] = "" if cleaned.get(key) is None else str(cleaned.get(key))
        return cleaned

    @classmethod
    def _sanitize_invoice_payload(cls, payload: dict) -> dict:
        cleaned = dict(payload or {})
        header = dict(cleaned.get("invoice_header") or {})
        for key in ["supplier_name", "invoice_number", "order_number", "invoice_date", "currency", "notes"]:
            header[key] = "" if header.get(key) is None else str(header.get(key))
        merchandise_row_count = header.get("merchandise_row_count")
        if merchandise_row_count in ("", None):
            header["merchandise_row_count"] = None
        else:
            try:
                normalized_row_count = int(str(merchandise_row_count).strip())
            except (TypeError, ValueError):
                normalized_row_count = None
            header["merchandise_row_count"] = normalized_row_count if normalized_row_count and normalized_row_count > 0 else None
        for key in ["subtotal_amount", "tax_amount", "total_amount"]:
            header[key] = cls._normalize_decimal_value(header.get(key))
        cleaned["invoice_header"] = header

        line_items = []
        for raw_item in cleaned.get("line_items") or []:
            item = dict(raw_item or {})
            for key in [
                "raw_description",
                "normalized_description",
                "manufacturer_hint",
                "model_hint",
                "serial_hint",
                "part_number_hint",
                "reference_hint",
                "product_key_hint",
                "license_reference_hint",
                "expiry_date_hint",
                "renewal_date_hint",
                "billing_term_hint",
                "component_role_hint",
                "notes",
            ]:
                item[key] = "" if item.get(key) is None else str(item.get(key))
            for key in ["quantity", "unit_price", "line_total"]:
                item[key] = cls._normalize_decimal_value(item.get(key))
            seat_hint = item.get("seat_hint")
            if seat_hint in ("", None):
                item["seat_hint"] = None
            else:
                try:
                    item["seat_hint"] = int(str(seat_hint).strip())
                except (TypeError, ValueError):
                    item["seat_hint"] = None
            line_items.append(item)
        cleaned["line_items"] = line_items
        return cleaned

    @classmethod
    def _sanitize_payload_for_schema(cls, payload: dict, schema: type[StructuredPayload]) -> dict:
        if schema is AssetIntakeExtraction:
            return cls._sanitize_asset_payload(payload)
        if schema is InvoiceIntakeExtraction:
            return cls._sanitize_invoice_payload(payload)
        return payload

    @staticmethod
    def _parse_payload(raw_text: str, schema: type[StructuredPayload], *, raw_response: dict | None = None) -> StructuredPayload:
        try:
            payload = json.loads(raw_text)
            payload = AzureOpenAIIntakeClient._sanitize_payload_for_schema(payload, schema)
            return schema.model_validate(payload)
        except (json.JSONDecodeError, PydanticValidationError) as exc:
            raise AIProviderSchemaError(
                "Azure OpenAI returned an invalid structured payload.",
                raw_text=raw_text,
                raw_response=raw_response,
            ) from exc

    @staticmethod
    def _dump_response(response) -> dict:
        return json.loads(response.model_dump_json()) if hasattr(response, "model_dump_json") else {}

    @staticmethod
    def _chat_message_text(response) -> str:
        message = response.choices[0].message
        if message.content:
            return message.content
        if getattr(message, "refusal", None):
            raise AIProviderError(f"Azure OpenAI fallback refused request: {message.refusal}")
        raise AIProviderSchemaError("Azure OpenAI fallback returned an empty message payload.")

    def _fallback_extract(self, *, client: AzureOpenAI, prompt: str, file_name: str, media_type: str, encoded: str) -> tuple[str, dict, str]:
        if media_type.startswith("image/"):
            response = client.chat.completions.create(
                model=self.config["deployment"],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{encoded}"}},
                        ],
                    }
                ],
                response_format={"type": "json_object"},
                max_tokens=800,
            )
            raw_text = self._chat_message_text(response)
            return raw_text, self._dump_response(response), getattr(response, "_request_id", "") or ""

        if media_type == "application/pdf":
            try:
                from pypdf import PdfReader
            except ImportError as exc:
                raise AIProviderError(
                    "Azure Responses API is unavailable for this deployment, and PDF fallback requires pypdf to be installed."
                ) from exc

            reader = PdfReader(BytesIO(base64.b64decode(encoded)))
            extracted_pages = [page.extract_text() or "" for page in reader.pages]
            extracted_text = "\n\n".join(part.strip() for part in extracted_pages if part.strip())
            if not extracted_text:
                raise AIProviderError(
                    f"Azure Responses API is unavailable for this deployment, and no extractable text was found in PDF {file_name}."
                )

            response = client.chat.completions.create(
                model=self.config["deployment"],
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"{prompt}\n\n"
                            f"Document filename: {file_name}\n"
                            "Document text follows between markers.\n"
                            "---DOCUMENT START---\n"
                            f"{extracted_text}\n"
                            "---DOCUMENT END---"
                        ),
                    }
                ],
                response_format={"type": "json_object"},
                max_tokens=800,
            )
            raw_text = self._chat_message_text(response)
            return raw_text, self._dump_response(response), getattr(response, "_request_id", "") or ""

        raise AIProviderError(
            f"Azure Responses API is unavailable for this deployment, and no fallback is implemented for content type {media_type}."
        )

    def _request_structured_payload(
        self,
        *,
        prompt: str,
        file_name: str,
        content_type: str,
        file_bytes: bytes,
        schema: type[StructuredPayload],
    ) -> ExtractionResult:
        client = self._client()
        media_type = content_type or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        encoded = base64.b64encode(file_bytes).decode("utf-8")
        started = time.monotonic()
        raw_response = {}
        request_id = ""
        try:
            response = client.responses.create(
                model=self.config["deployment"],
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {
                                "type": "input_file",
                                "filename": file_name,
                                "file_data": f"data:{media_type};base64,{encoded}",
                            },
                        ],
                    }
                ],
                text={"format": {"type": "json_object"}},
            )
            raw_text = getattr(response, "output_text", "") or ""
            raw_response = self._dump_response(response)
            request_id = getattr(response, "_request_id", "") or ""
        except APIStatusError as exc:
            if exc.status_code == 404:
                try:
                    raw_text, raw_response, request_id = self._fallback_extract(
                        client=client,
                        prompt=prompt,
                        file_name=file_name,
                        media_type=media_type,
                        encoded=encoded,
                    )
                except (APIConnectionError, APIStatusError, APIError) as fallback_exc:
                    raise AIProviderError(f"Azure OpenAI fallback request failed: {fallback_exc}") from fallback_exc
            else:
                raise AIProviderError(f"Azure OpenAI request failed: {exc}") from exc
        except (APIConnectionError, APIError) as exc:
            raise AIProviderError(f"Azure OpenAI request failed: {exc}") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        payload = self._parse_payload(raw_text, schema, raw_response=raw_response)
        return ExtractionResult(
            payload=payload,
            raw_response=raw_response,
            latency_ms=latency_ms,
            provider_request_id=request_id,
        )

    def extract_asset_draft(self, *, file_name: str, content_type: str, file_bytes: bytes) -> ExtractionResult:
        return self._request_structured_payload(
            prompt=self._prompt(),
            file_name=file_name,
            content_type=content_type,
            file_bytes=file_bytes,
            schema=AssetIntakeExtraction,
        )

    def extract_invoice_payload(self, *, file_name: str, content_type: str, file_bytes: bytes) -> ExtractionResult:
        return self._request_structured_payload(
            prompt=self._invoice_extraction_prompt(),
            file_name=file_name,
            content_type=content_type,
            file_bytes=file_bytes,
            schema=InvoiceIntakeExtraction,
        )

    def repair_invoice_payload(
        self,
        *,
        raw_text: str = "",
        file_name: str = "",
        content_type: str = "",
        file_bytes: bytes | None = None,
    ) -> ExtractionResult:
        if file_bytes is not None:
            try:
                result = self._request_structured_payload(
                    prompt=self._invoice_document_retry_prompt(raw_text),
                    file_name=file_name,
                    content_type=content_type,
                    file_bytes=file_bytes,
                    schema=InvoiceIntakeExtraction,
                )
                return ExtractionResult(
                    payload=result.payload,
                    raw_response={
                        "repair_prompt_used": True,
                        "repair_mode": "document_retry",
                        "document_retry_response": result.raw_response,
                    },
                    latency_ms=result.latency_ms,
                    provider_request_id=result.provider_request_id,
                )
            except AIProviderSchemaError as exc:
                fallback_result = self.repair_invoice_payload(raw_text=exc.raw_text or raw_text)
                return ExtractionResult(
                    payload=fallback_result.payload,
                    raw_response={
                        "repair_prompt_used": True,
                        "repair_mode": "document_retry_with_json_repair",
                        "document_retry_schema_error": exc.raw_response or {},
                        "json_repair_response": fallback_result.raw_response,
                    },
                    latency_ms=fallback_result.latency_ms,
                    provider_request_id=fallback_result.provider_request_id,
                )

        client = self._client()
        started = time.monotonic()
        try:
            response = client.chat.completions.create(
                model=self.config["deployment"],
                messages=[
                    {
                        "role": "user",
                        "content": self._invoice_repair_prompt(raw_text),
                    }
                ],
                response_format={"type": "json_object"},
                max_tokens=1200,
            )
        except (APIConnectionError, APIStatusError, APIError) as exc:
            raise AIProviderError(f"Azure OpenAI repair request failed: {exc}") from exc

        repaired_text = self._chat_message_text(response)
        raw_response = self._dump_response(response)
        payload = self._parse_payload(repaired_text, InvoiceIntakeExtraction, raw_response=raw_response)
        return ExtractionResult(
            payload=payload,
            raw_response={
                "repair_prompt_used": True,
                "repair_mode": "json_repair",
                "repair_response": raw_response,
            },
            latency_ms=int((time.monotonic() - started) * 1000),
            provider_request_id=getattr(response, "_request_id", "") or "",
        )

    def classify_invoice_line_items(
        self,
        *,
        invoice_payload: InvoiceIntakeExtraction,
        retrieval_examples_by_line: dict[int, list[dict]] | None = None,
    ) -> list[InvoiceLineItemClassification]:
        component_terms = {
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
        license_terms = {
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
        line_item_classifications = []
        for index, item in enumerate(invoice_payload.line_items, start=1):
            normalized_name = item.normalized_description or item.raw_description
            description_text = " ".join(
                filter(
                    None,
                    [
                        item.raw_description,
                        item.normalized_description,
                        item.notes,
                        item.component_role_hint,
                        item.billing_term_hint,
                    ],
                )
            ).lower()
            if (
                item.seat_hint
                or item.product_key_hint
                or item.license_reference_hint
                or item.expiry_date_hint
                or item.renewal_date_hint
                or any(term in description_text for term in license_terms)
            ):
                inventory_type = "license"
                confidence = 0.9 if (item.product_key_hint or item.seat_hint or item.license_reference_hint) else 0.78
                rationale = "Seat, subscription, or entitlement signals are present in the extracted line item."
            elif (
                item.component_role_hint
                or (item.part_number_hint and not item.serial_hint)
                or any(term in description_text for term in component_terms)
            ):
                inventory_type = "component"
                confidence = 0.84 if (item.part_number_hint or item.component_role_hint) else 0.72
                rationale = "Part-level hardware signals suggest quantity-tracked component inventory."
            elif item.serial_hint or (item.manufacturer_hint and item.model_hint):
                inventory_type = "asset"
                confidence = 0.95 if item.serial_hint else 0.88
                rationale = "Trackable hardware signals are present in the extracted line item."
            elif (item.quantity or 0) > 1 and not item.serial_hint and not item.model_hint:
                inventory_type = "consumable"
                confidence = 0.62
                rationale = "Quantity-driven line item lacks trackable identity signals and needs review."
            else:
                inventory_type = "accessory"
                confidence = 0.58
                rationale = "Supporting item signals are stronger than trackable-asset signals, but review remains required."

            retrieval_examples = (retrieval_examples_by_line or {}).get(index) or []
            top_example = retrieval_examples[0] if retrieval_examples else None
            suggested_category_name = ""
            if top_example:
                top_type = top_example.get("final_inventory_type") or ""
                top_score = float(top_example.get("score") or 0.0)
                company_scope = str(top_example.get("company_scope") or "")
                suggested_category_name = str(top_example.get("final_category_name") or "").strip()
                if (
                    top_score >= 0.9
                    and confidence < 0.8
                    and company_scope != "cross_company"
                    and top_type in {"asset", "accessory", "consumable", "component", "license"}
                    and top_type != inventory_type
                ):
                    inventory_type = top_type
                    confidence = max(confidence, round(min(top_score, 0.94), 2))
                    rationale = (
                        f"{rationale} Similar approved example matched strongly and shifted the working prediction toward {inventory_type}."
                    )
                else:
                    confidence = max(confidence, round(min((confidence + top_score) / 2, 0.97), 2))
                    rationale = f"{rationale} Similar approved examples were added as reviewer context."

            raw_payload = {
                "line_number": index,
                "classification": {
                    "inventory_type": inventory_type,
                    "inventory_confidence": confidence,
                    "classification_rationale": rationale,
                    "requires_review": True,
                    "normalized_item_name": normalized_name,
                    "suggested_category_name": suggested_category_name,
                    "unsupported_for_approval": False,
                },
            }
            line_item_classifications.append(InvoiceLineItemClassification.model_validate(raw_payload))
        return line_item_classifications


class GeminiIntakeClient(AzureOpenAIIntakeClient):
    """Gemini-backed document extraction with the existing intake schema contract."""

    def __init__(self) -> None:
        self.config = settings.GEMINI

    def is_configured(self) -> bool:
        return bool(self.config.get("api_key") and self.config.get("model"))

    def _client(self):
        if not self.is_configured():
            raise AIProviderNotConfiguredError("Gemini is not configured. Add GEMINI_API_KEY to .env.local.")
        try:
            from google import genai
        except ImportError as exc:
            raise AIProviderError("Gemini support requires the google-genai package.") from exc
        return genai.Client(
            api_key=self.config["api_key"],
            http_options={"timeout": int(self.config["timeout_seconds"]) * 1000},
        )

    @staticmethod
    def _dump_gemini_response(response) -> dict:
        if hasattr(response, "model_dump"):
            return response.model_dump(mode="json", exclude_none=True)
        return {}

    @staticmethod
    def _gemini_request_id(response) -> str:
        sdk_response = getattr(response, "sdk_http_response", None)
        headers = getattr(sdk_response, "headers", None) or {}
        return headers.get("x-request-id", "") or headers.get("x-goog-request-id", "")

    def _generate_json(
        self,
        *,
        prompt: str,
        schema: type[StructuredPayload],
        file_bytes: bytes | None = None,
        media_type: str = "",
    ) -> ExtractionResult:
        client = self._client()
        try:
            from google.genai import types
        except ImportError as exc:
            raise AIProviderError("Gemini support requires the google-genai package.") from exc

        contents = [prompt]
        if file_bytes is not None:
            contents.append(types.Part.from_bytes(data=file_bytes, mime_type=media_type))

        started = time.monotonic()
        try:
            response = client.models.generate_content(
                model=self.config["model"],
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=schema.model_json_schema(),
                    max_output_tokens=8192,
                ),
            )
        except Exception as exc:
            raise AIProviderError(f"Gemini request failed: {exc}") from exc

        raw_text = getattr(response, "text", "") or ""
        raw_response = self._dump_gemini_response(response)
        payload = self._parse_payload(raw_text, schema, raw_response=raw_response)
        return ExtractionResult(
            payload=payload,
            raw_response=raw_response,
            latency_ms=int((time.monotonic() - started) * 1000),
            provider_request_id=self._gemini_request_id(response),
        )

    def _request_structured_payload(
        self,
        *,
        prompt: str,
        file_name: str,
        content_type: str,
        file_bytes: bytes,
        schema: type[StructuredPayload],
    ) -> ExtractionResult:
        media_type = content_type or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        return self._generate_json(
            prompt=prompt,
            schema=schema,
            file_bytes=file_bytes,
            media_type=media_type,
        )

    def repair_invoice_payload(
        self,
        *,
        raw_text: str = "",
        file_name: str = "",
        content_type: str = "",
        file_bytes: bytes | None = None,
    ) -> ExtractionResult:
        if file_bytes is not None:
            return self._request_structured_payload(
                prompt=self._invoice_document_retry_prompt(raw_text),
                file_name=file_name,
                content_type=content_type,
                file_bytes=file_bytes,
                schema=InvoiceIntakeExtraction,
            )
        return self._generate_json(
            prompt=self._invoice_repair_prompt(raw_text),
            schema=InvoiceIntakeExtraction,
        )


def get_intake_provider():
    provider_name = getattr(settings, "AI_INTAKE_PROVIDER", "auto")
    if provider_name == "auto":
        if settings.GEMINI.get("api_key"):
            return GeminiIntakeClient()
        if all(
            settings.AZURE_OPENAI.get(key)
            for key in ("endpoint", "api_key", "deployment", "api_version")
        ):
            return AzureOpenAIIntakeClient()
        raise AIProviderNotConfiguredError(
            "AI intake is not configured. Add GEMINI_API_KEY, or configure all Azure OpenAI settings."
        )
    if provider_name in {"azure", "azure_openai"}:
        return AzureOpenAIIntakeClient()
    if provider_name == "gemini":
        return GeminiIntakeClient()
    raise AIProviderNotConfiguredError(
        f"Unsupported AI_INTAKE_PROVIDER {provider_name!r}. Use 'auto', 'azure_openai', or 'gemini'."
    )
