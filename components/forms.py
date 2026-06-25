from django import forms

from catalogue.models import Category
from organisations.models import Company
from suppliers.models import Supplier

from .models import Component


class ComponentForm(forms.ModelForm):
    class Meta:
        model = Component
        fields = ["name", "category", "company", "supplier", "quantity", "assigned_quantity", "min_quantity", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(deleted_at__isnull=True, category_type="component")
        self.fields["company"].queryset = Company.objects.filter(deleted_at__isnull=True)
        self.fields["supplier"].queryset = Supplier.objects.filter(deleted_at__isnull=True)
