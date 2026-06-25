from django import forms

from catalogue.models import Category, StatusLabel
from locations.models import Location
from organisations.models import Company
from suppliers.models import Supplier

from .models import Asset, AssetModel, DepreciationProfile


class DepreciationProfileForm(forms.ModelForm):
    class Meta:
        model = DepreciationProfile
        fields = ["name", "months", "depreciation_min", "depreciation_type", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
        }


class AssetModelForm(forms.ModelForm):
    class Meta:
        model = AssetModel
        fields = [
            "name",
            "model_number",
            "manufacturer",
            "category",
            "depreciation",
            "eol_months",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(
            deleted_at__isnull=True,
            category_type=Category.CategoryType.ASSET,
        )


class AssetForm(forms.ModelForm):
    class Meta:
        model = Asset
        fields = [
            "asset_tag",
            "name",
            "serial",
            "model",
            "status_label",
            "company",
            "supplier",
            "default_location",
            "purchase_date",
            "purchase_cost",
            "order_number",
            "warranty_months",
            "requestable",
            "notes",
        ]
        widgets = {
            "purchase_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["model"].queryset = AssetModel.objects.filter(deleted_at__isnull=True)
        self.fields["status_label"].queryset = StatusLabel.objects.filter(deleted_at__isnull=True)
        self.fields["company"].queryset = Company.objects.filter(deleted_at__isnull=True)
        self.fields["supplier"].queryset = Supplier.objects.filter(deleted_at__isnull=True)
        self.fields["default_location"].queryset = Location.objects.filter(deleted_at__isnull=True)
