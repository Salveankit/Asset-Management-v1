from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import View

from assets.models import Asset

from .forms import AssetAssignmentForm, AssetCheckinForm, AssetCheckoutForm
from .services import assign_asset, checkin_asset, checkout_asset, clear_asset_assignment


class StaffLifecycleView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff:
            return redirect("assets:detail", pk=kwargs["pk"])
        self.asset = get_object_or_404(Asset.objects.filter(deleted_at__isnull=True), pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)


class AssetAssignView(StaffLifecycleView):
    def post(self, request, *args, **kwargs):
        form = AssetAssignmentForm(request.POST, user_model=get_user_model(), asset=self.asset)
        if form.is_valid():
            try:
                assign_asset(
                    asset=self.asset,
                    actor=request.user,
                    target_type=form.cleaned_data["target_type"],
                    user=form.cleaned_data["assigned_user"],
                    location=form.cleaned_data["assigned_location"],
                    related_asset=form.cleaned_data["assigned_asset"],
                    note=form.cleaned_data["note"],
                )
                messages.success(request, "Asset assignment updated.")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        else:
            messages.error(request, "Assignment update failed.")
        return redirect(reverse("assets:detail", kwargs={"pk": self.asset.pk}))


class AssetClearAssignmentView(StaffLifecycleView):
    def post(self, request, *args, **kwargs):
        form = AssetCheckinForm(request.POST)
        if form.is_valid():
            try:
                clear_asset_assignment(asset=self.asset, actor=request.user, note=form.cleaned_data["note"])
                messages.success(request, "Asset assignment cleared.")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        return redirect(reverse("assets:detail", kwargs={"pk": self.asset.pk}))


class AssetCheckoutView(StaffLifecycleView):
    def post(self, request, *args, **kwargs):
        form = AssetCheckoutForm(request.POST, user_model=get_user_model(), asset=self.asset)
        if form.is_valid():
            try:
                checkout_asset(
                    asset=self.asset,
                    actor=request.user,
                    target_type=form.cleaned_data["target_type"],
                    user=form.cleaned_data["assigned_user"],
                    location=form.cleaned_data["assigned_location"],
                    related_asset=form.cleaned_data["assigned_asset"],
                    expected_checkin=form.cleaned_data["expected_checkin"],
                    note=form.cleaned_data["note"],
                )
                messages.success(request, "Asset checked out.")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        else:
            messages.error(request, "Check-out failed.")
        return redirect(reverse("assets:detail", kwargs={"pk": self.asset.pk}))


class AssetCheckinView(StaffLifecycleView):
    def post(self, request, *args, **kwargs):
        form = AssetCheckinForm(request.POST)
        if form.is_valid():
            try:
                checkin_asset(asset=self.asset, actor=request.user, note=form.cleaned_data["note"])
                messages.success(request, "Asset checked in.")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        return redirect(reverse("assets:detail", kwargs={"pk": self.asset.pk}))
