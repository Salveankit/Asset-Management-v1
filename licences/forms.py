from django import forms

from assets.models import Asset, DepreciationProfile
from catalogue.models import Category, Manufacturer
from organisations.models import Company
from suppliers.models import Supplier

from .models import License, LicenseSeat


class LicenseForm(forms.ModelForm):
    class Meta:
        model = License
        fields = [
            "name",
            "product_key",
            "reference_code",
            "seats",
            "company",
            "category",
            "manufacturer",
            "supplier",
            "depreciation",
            "purchase_date",
            "expiration_date",
            "renewal_date",
            "billing_term",
            "order_number",
            "purchase_cost",
            "notes",
        ]
        widgets = {
            "purchase_date": forms.DateInput(attrs={"type": "date"}),
            "expiration_date": forms.DateInput(attrs={"type": "date"}),
            "renewal_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["company"].queryset = Company.objects.filter(deleted_at__isnull=True)
        self.fields["category"].queryset = Category.objects.filter(deleted_at__isnull=True, category_type="license")
        self.fields["manufacturer"].queryset = Manufacturer.objects.filter(deleted_at__isnull=True)
        self.fields["supplier"].queryset = Supplier.objects.filter(deleted_at__isnull=True)
        self.fields["depreciation"].queryset = DepreciationProfile.objects.filter(deleted_at__isnull=True)


class LicenseSeatAssignmentForm(forms.ModelForm):
    class Meta:
        model = LicenseSeat
        fields = ["assigned_user", "assigned_asset", "note"]
        widgets = {
            "note": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        company = kwargs.pop("company", None)
        super().__init__(*args, **kwargs)
        user_qs = self.fields["assigned_user"].queryset
        asset_qs = Asset.objects.filter(deleted_at__isnull=True)
        if company:
            user_qs = user_qs.filter(company=company)
            asset_qs = asset_qs.filter(company=company)
        self.fields["assigned_user"].queryset = user_qs.order_by("username")
        self.fields["assigned_asset"].queryset = asset_qs.order_by("asset_tag")
