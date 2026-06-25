from django import forms

from .models import AssetAttachment


class AssetAttachmentForm(forms.ModelForm):
    class Meta:
        model = AssetAttachment
        fields = ["file", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
