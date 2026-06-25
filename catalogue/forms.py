from django import forms

from .models import Category, Manufacturer, StatusLabel


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = [
            "name",
            "category_type",
            "require_acceptance",
            "use_default_eula",
            "checkin_email",
            "alert_on_response",
            "tag_color",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
            "tag_color": forms.TextInput(attrs={"type": "color"}),
        }


class ManufacturerForm(forms.ModelForm):
    class Meta:
        model = Manufacturer
        fields = [
            "name",
            "url",
            "support_email",
            "support_phone",
            "support_url",
            "warranty_lookup_url",
            "tag_color",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
            "tag_color": forms.TextInput(attrs={"type": "color"}),
        }


class StatusLabelForm(forms.ModelForm):
    STATUS_TYPE_CHOICES = (
        ("deployable", "Deployable"),
        ("pending", "Pending"),
        ("undeployable", "Undeployable"),
        ("archived", "Archived"),
    )

    status_type = forms.ChoiceField(choices=STATUS_TYPE_CHOICES)

    class Meta:
        model = StatusLabel
        fields = [
            "name",
            "status_type",
            "color",
            "show_in_nav",
            "default_label",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
            "color": forms.TextInput(attrs={"type": "color"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["status_type"].initial = self.instance.status_type

    def clean(self):
        cleaned_data = super().clean()
        status_type = cleaned_data.get("status_type")
        cleaned_data.update(StatusLabel.flags_from_type(status_type))
        return cleaned_data

    def save(self, commit=True):
        self.instance.deployable = self.cleaned_data["deployable"]
        self.instance.pending = self.cleaned_data["pending"]
        self.instance.archived = self.cleaned_data["archived"]
        return super().save(commit=commit)
