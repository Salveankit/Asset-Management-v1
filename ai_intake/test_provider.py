import json
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from .provider import AzureOpenAIIntakeClient, GeminiIntakeClient, get_intake_provider


class _FakeGeminiResponse:
    text = json.dumps(
        {
            "asset_name": "Latitude 7400",
            "manufacturer_name": "Dell",
            "model_name": "Latitude",
            "model_number": "7400",
            "supplier_name": "Example Supplier",
            "category_name": "Laptops",
            "serial": "SER-1",
            "order_number": "PO-1",
            "purchase_date": "2026-07-02",
            "purchase_cost": "1000.00",
            "notes": "",
            "quantity": 1,
        }
    )
    sdk_http_response = None

    def model_dump(self, **kwargs):
        return {"text": self.text}


class GeminiIntakeProviderTests(SimpleTestCase):
    @override_settings(
        AI_INTAKE_PROVIDER="auto",
        GEMINI={"api_key": "gemini-test-only", "model": "gemini-2.5-flash", "timeout_seconds": 30},
        AZURE_OPENAI={
            "endpoint": "https://example.openai.azure.com",
            "api_key": "azure-test-only",
            "deployment": "test",
            "api_version": "2025-01-01-preview",
            "timeout_seconds": 30,
            "max_retries": 1,
        },
    )
    def test_auto_provider_prefers_gemini_when_key_is_present(self):
        self.assertIsInstance(get_intake_provider(), GeminiIntakeClient)

    @override_settings(
        AI_INTAKE_PROVIDER="auto",
        GEMINI={"api_key": "", "model": "gemini-2.5-flash", "timeout_seconds": 30},
        AZURE_OPENAI={
            "endpoint": "https://example.openai.azure.com",
            "api_key": "azure-test-only",
            "deployment": "test",
            "api_version": "2025-01-01-preview",
            "timeout_seconds": 30,
            "max_retries": 1,
        },
    )
    def test_auto_provider_falls_back_to_fully_configured_azure(self):
        self.assertIsInstance(get_intake_provider(), AzureOpenAIIntakeClient)

    @override_settings(AI_INTAKE_PROVIDER="gemini")
    def test_provider_factory_selects_gemini(self):
        self.assertIsInstance(get_intake_provider(), GeminiIntakeClient)

    @override_settings(GEMINI={"api_key": "", "model": "gemini-2.5-flash", "timeout_seconds": 30})
    def test_gemini_without_key_is_not_configured(self):
        self.assertFalse(GeminiIntakeClient().is_configured())

    @override_settings(GEMINI={"api_key": "test-only", "model": "gemini-2.5-flash", "timeout_seconds": 30})
    def test_gemini_image_response_uses_existing_asset_schema(self):
        sdk_client = Mock()
        sdk_client.models.generate_content.return_value = _FakeGeminiResponse()

        with patch.object(GeminiIntakeClient, "_client", return_value=sdk_client):
            result = GeminiIntakeClient().extract_asset_draft(
                file_name="invoice.jpg",
                content_type="image/jpeg",
                file_bytes=b"image-bytes",
            )

        self.assertEqual(result.payload.asset_name, "Latitude 7400")
        self.assertEqual(str(result.payload.purchase_cost), "1000.00")
        call = sdk_client.models.generate_content.call_args
        self.assertEqual(call.kwargs["model"], "gemini-2.5-flash")
        self.assertEqual(call.kwargs["config"].response_mime_type, "application/json")
