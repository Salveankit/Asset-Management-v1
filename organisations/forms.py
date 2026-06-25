from django import forms

from .models import Company, Department


class CompanyForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = ["name", "code", "email_domain", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
        }


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ["company", "name", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
        }
