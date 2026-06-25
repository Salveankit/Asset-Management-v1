from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from .policy import InventoryClassification


class AssetIntakeExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_name: str = Field(default="")
    manufacturer_name: str = Field(default="")
    model_name: str = Field(default="")
    model_number: str = Field(default="")
    supplier_name: str = Field(default="")
    category_name: str = Field(default="Imported Assets")
    serial: str = Field(default="")
    order_number: str = Field(default="")
    purchase_date: str = Field(default="")
    purchase_cost: Decimal | None = None
    notes: str = Field(default="")
    quantity: int = Field(default=1, ge=1)


class InvoiceHeaderExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_name: str = Field(default="")
    invoice_number: str = Field(default="")
    order_number: str = Field(default="")
    invoice_date: str = Field(default="")
    currency: str = Field(default="")
    merchandise_row_count: int | None = Field(default=None, ge=1)
    subtotal_amount: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal | None = None
    notes: str = Field(default="")


class InvoiceLineItemExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_description: str = Field(default="")
    normalized_description: str = Field(default="")
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None
    manufacturer_hint: str = Field(default="")
    model_hint: str = Field(default="")
    serial_hint: str = Field(default="")
    part_number_hint: str = Field(default="")
    reference_hint: str = Field(default="")
    seat_hint: int | None = Field(default=None, ge=1)
    product_key_hint: str = Field(default="")
    license_reference_hint: str = Field(default="")
    expiry_date_hint: str = Field(default="")
    renewal_date_hint: str = Field(default="")
    billing_term_hint: str = Field(default="")
    component_role_hint: str = Field(default="")
    notes: str = Field(default="")


class InvoiceIntakeExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_header: InvoiceHeaderExtraction = Field(default_factory=InvoiceHeaderExtraction)
    line_items: list[InvoiceLineItemExtraction] = Field(default_factory=list)


class InvoiceLineItemClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_number: int = Field(ge=1)
    classification: InventoryClassification
