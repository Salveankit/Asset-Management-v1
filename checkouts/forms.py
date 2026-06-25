from django import forms

from assets.models import Asset
from locations.models import Location


class AssignmentTargetMixin(forms.Form):
    TARGET_CHOICES = (
        ("user", "User"),
        ("location", "Location"),
        ("asset", "Asset"),
    )

    target_type = forms.ChoiceField(choices=TARGET_CHOICES)
    assigned_user = forms.ModelChoiceField(queryset=None, required=False)
    assigned_location = forms.ModelChoiceField(queryset=None, required=False)
    assigned_asset = forms.ModelChoiceField(queryset=None, required=False)
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        user_model = kwargs.pop("user_model")
        asset = kwargs.pop("asset", None)
        super().__init__(*args, **kwargs)
        self.asset = asset
        self.fields["assigned_user"].queryset = user_model.objects.order_by("username")
        self.fields["assigned_location"].queryset = Location.objects.filter(deleted_at__isnull=True).order_by("name")
        asset_queryset = Asset.objects.filter(deleted_at__isnull=True).order_by("asset_tag")
        if asset and asset.pk:
            asset_queryset = asset_queryset.exclude(pk=asset.pk)
        self.fields["assigned_asset"].queryset = asset_queryset

    def clean(self):
        cleaned_data = super().clean()
        target_type = cleaned_data.get("target_type")
        target_field = {
            "user": "assigned_user",
            "location": "assigned_location",
            "asset": "assigned_asset",
        }.get(target_type)
        if not target_field:
            raise forms.ValidationError("Select a valid assignment target.")
        if not cleaned_data.get(target_field):
            self.add_error(target_field, "This field is required for the selected target type.")
        return cleaned_data


class AssetAssignmentForm(AssignmentTargetMixin):
    pass


class AssetCheckoutForm(AssignmentTargetMixin):
    expected_checkin = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )


class AssetCheckinForm(forms.Form):
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
