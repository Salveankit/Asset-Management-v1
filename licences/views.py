from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView, View
from rest_framework import permissions, viewsets

from catalogue.views import SoftDeleteView
from core.scoping import filter_for_user_company
from core.views import SearchableListMixin, StaffRequiredMixin

from .forms import LicenseForm, LicenseSeatAssignmentForm
from .models import License, LicenseSeat
from .serializers import LicenseSerializer, LicenseSeatSerializer
from .services import assign_license_seat, release_license_seat


class StaffWritePermission(permissions.IsAuthenticated):
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_staff)


class LicenseListView(LoginRequiredMixin, SearchableListMixin, ListView):
    model = License
    template_name = "licences/list.html"
    context_object_name = "objects"
    search_fields = ("name", "product_key", "company__name")

    def get_queryset(self):
        queryset = super().get_queryset().filter(deleted_at__isnull=True).select_related(
            "company", "category", "manufacturer", "supplier", "depreciation"
        )
        return filter_for_user_company(queryset, self.request.user)


class LicenseCreateView(StaffRequiredMixin, CreateView):
    model = License
    form_class = LicenseForm
    template_name = "licences/form.html"
    success_url = reverse_lazy("licences:list")

    def form_valid(self, form):
        messages.success(self.request, "License created.")
        return super().form_valid(form)


class LicenseUpdateView(StaffRequiredMixin, UpdateView):
    model = License
    form_class = LicenseForm
    template_name = "licences/form.html"
    success_url = reverse_lazy("licences:list")

    def get_queryset(self):
        return License.objects.filter(deleted_at__isnull=True)

    def form_valid(self, form):
        messages.success(self.request, "License updated.")
        return super().form_valid(form)


class LicenseDetailView(LoginRequiredMixin, DetailView):
    model = License
    template_name = "licences/detail.html"
    context_object_name = "object"

    def get_queryset(self):
        queryset = License.objects.filter(deleted_at__isnull=True).select_related(
            "company", "category", "manufacturer", "supplier", "depreciation"
        ).prefetch_related("seats_assigned__assigned_user", "seats_assigned__assigned_asset")
        return filter_for_user_company(queryset, self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["seat_form"] = LicenseSeatAssignmentForm(company=self.object.company)
        context["seat_assign_url"] = reverse("licences:seat-assign", kwargs={"pk": self.object.pk})
        return context


class LicenseDeleteView(SoftDeleteView):
    model = License
    success_url = reverse_lazy("licences:list")
    success_message = "License archived."


class LicenseSeatAssignView(StaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        license_obj = get_object_or_404(License.objects.filter(deleted_at__isnull=True), pk=kwargs["pk"])
        form = LicenseSeatAssignmentForm(request.POST, company=license_obj.company)
        if form.is_valid():
            try:
                assign_license_seat(
                    license=license_obj,
                    assigned_user=form.cleaned_data["assigned_user"],
                    assigned_asset=form.cleaned_data["assigned_asset"],
                    note=form.cleaned_data["note"],
                )
                messages.success(request, "License seat assigned.")
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
        else:
            messages.error(request, "License seat assignment failed.")
        return redirect(reverse("licences:detail", kwargs={"pk": license_obj.pk}))


class LicenseSeatReleaseView(StaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        seat = get_object_or_404(LicenseSeat, pk=kwargs["pk"])
        try:
            release_license_seat(seat=seat)
            messages.success(request, "License seat released.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect(reverse("licences:detail", kwargs={"pk": seat.license_id}))


class LicenseViewSet(viewsets.ModelViewSet):
    serializer_class = LicenseSerializer
    permission_classes = [StaffWritePermission]

    def get_queryset(self):
        queryset = License.objects.filter(deleted_at__isnull=True).select_related(
            "company", "category", "manufacturer", "supplier", "depreciation"
        )
        return filter_for_user_company(queryset, self.request.user)


class LicenseSeatViewSet(viewsets.ModelViewSet):
    serializer_class = LicenseSeatSerializer
    permission_classes = [StaffWritePermission]

    def get_queryset(self):
        queryset = LicenseSeat.objects.filter(deleted_at__isnull=True).select_related(
            "license", "assigned_user", "assigned_asset"
        )
        return filter_for_user_company(queryset, self.request.user, "license__company_id")
