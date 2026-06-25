from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView, View
from rest_framework import permissions, viewsets

from core.views import SearchableListMixin, StaffRequiredMixin

from .forms import SupplierForm
from .models import Supplier
from .serializers import SupplierSerializer


class SupplierListView(LoginRequiredMixin, SearchableListMixin, ListView):
    model = Supplier
    template_name = "suppliers/list.html"
    context_object_name = "objects"
    search_fields = ("name", "email", "contact", "city", "country")

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class SupplierCreateView(StaffRequiredMixin, CreateView):
    model = Supplier
    form_class = SupplierForm
    template_name = "suppliers/form.html"
    success_url = reverse_lazy("suppliers:list")

    def form_valid(self, form):
        messages.success(self.request, "Supplier created.")
        return super().form_valid(form)


class SupplierUpdateView(StaffRequiredMixin, UpdateView):
    model = Supplier
    form_class = SupplierForm
    template_name = "suppliers/form.html"
    success_url = reverse_lazy("suppliers:list")

    def get_queryset(self):
        return Supplier.objects.filter(deleted_at__isnull=True)

    def form_valid(self, form):
        messages.success(self.request, "Supplier updated.")
        return super().form_valid(form)


class SupplierDetailView(LoginRequiredMixin, DetailView):
    model = Supplier
    template_name = "suppliers/detail.html"
    context_object_name = "object"

    def get_queryset(self):
        return Supplier.objects.filter(deleted_at__isnull=True)


class SupplierDeleteView(StaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        supplier = Supplier.objects.filter(deleted_at__isnull=True).get(pk=kwargs["pk"])
        supplier.soft_delete()
        messages.success(request, "Supplier archived.")
        return redirect("suppliers:list")


class StaffWritePermission(permissions.IsAuthenticated):
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_staff)


class SupplierViewSet(viewsets.ModelViewSet):
    queryset = Supplier.objects.filter(deleted_at__isnull=True)
    serializer_class = SupplierSerializer
    permission_classes = [StaffWritePermission]
