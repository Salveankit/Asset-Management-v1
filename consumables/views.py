from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from rest_framework import permissions, viewsets

from catalogue.views import SoftDeleteView
from core.scoping import filter_for_user_company
from core.views import SearchableListMixin, StaffRequiredMixin

from .forms import ConsumableForm
from .models import Consumable
from .serializers import ConsumableSerializer


class StaffWritePermission(permissions.IsAuthenticated):
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_staff)


class ConsumableListView(LoginRequiredMixin, SearchableListMixin, ListView):
    model = Consumable
    template_name = "inventory/list.html"
    context_object_name = "objects"
    search_fields = ("name", "company__name")

    def get_queryset(self):
        queryset = super().get_queryset().filter(deleted_at__isnull=True).select_related("category", "company", "supplier")
        return filter_for_user_company(queryset, self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Consumables"
        context["subtitle"] = "Quantity-tracked stock items."
        context["create_url"] = reverse_lazy("consumables:create")
        context["detail_route"] = "consumables:detail"
        return context


class ConsumableCreateView(StaffRequiredMixin, CreateView):
    model = Consumable
    form_class = ConsumableForm
    template_name = "inventory/form.html"
    success_url = reverse_lazy("consumables:list")

    def form_valid(self, form):
        messages.success(self.request, "Consumable created.")
        return super().form_valid(form)


class ConsumableUpdateView(StaffRequiredMixin, UpdateView):
    model = Consumable
    form_class = ConsumableForm
    template_name = "inventory/form.html"
    success_url = reverse_lazy("consumables:list")

    def form_valid(self, form):
        messages.success(self.request, "Consumable updated.")
        return super().form_valid(form)


class ConsumableDetailView(LoginRequiredMixin, DetailView):
    model = Consumable
    template_name = "inventory/detail.html"
    context_object_name = "object"

    def get_queryset(self):
        queryset = Consumable.objects.filter(deleted_at__isnull=True).select_related("category", "company", "supplier")
        return filter_for_user_company(queryset, self.request.user)


class ConsumableDeleteView(SoftDeleteView):
    model = Consumable
    success_url = reverse_lazy("consumables:list")
    success_message = "Consumable archived."


class ConsumableViewSet(viewsets.ModelViewSet):
    serializer_class = ConsumableSerializer
    permission_classes = [StaffWritePermission]

    def get_queryset(self):
        queryset = Consumable.objects.filter(deleted_at__isnull=True).select_related("category", "company", "supplier")
        return filter_for_user_company(queryset, self.request.user)
