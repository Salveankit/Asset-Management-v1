from django import forms

from locations.models import Location
from organisations.models import Company

from .models import AIIntakeDocument


class AIIntakeUploadForm(forms.ModelForm):
    class Meta:
        model = AIIntakeDocument
        fields = ["file"]


class AIIntakeApproveForm(forms.Form):
    company = forms.ModelChoiceField(
        label="Company",
        queryset=Company.objects.none(),
        required=False,
        empty_label="Select company",
    )
    location = forms.ModelChoiceField(
        label="Location",
        queryset=Location.objects.none(),
        required=False,
        empty_label="Select location",
    )
    review_notes = forms.CharField(
        label="Reviewer notes",
        required=False,
        widget=forms.Textarea(
            attrs={
                "id": "routing-review-notes",
                "rows": 3,
                "placeholder": "Add corrections, context, or approval notes.",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["company"].queryset = Company.objects.filter(deleted_at__isnull=True).order_by("name")
        self.fields["location"].queryset = Location.objects.filter(deleted_at__isnull=True).order_by("name")


class AIIntakeRejectForm(forms.Form):
    review_notes = forms.CharField(
        label="Rejection reason",
        required=True,
        widget=forms.Textarea(
            attrs={
                "id": "reject-review-notes",
                "rows": 3,
                "placeholder": "Explain why this draft is being rejected.",
            }
        ),
    )
