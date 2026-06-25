from django import forms

from catalogue.models import Category
from organisations.models import Company
from suppliers.models import Supplier

from .models import Consumable


class ConsumableForm(forms.ModelForm):
    class Meta:
        model = Consumable
        fields = ["name", "category", "company", "supplier", "quantity", "min_quantity", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(deleted_at__isnull=True, category_type="consumable")
        self.fields["company"].queryset = Company.objects.filter(deleted_at__isnull=True)
        self.fields["supplier"].queryset = Supplier.objects.filter(deleted_at__isnull=True)
