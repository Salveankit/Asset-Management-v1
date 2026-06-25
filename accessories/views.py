from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from rest_framework import permissions, viewsets

from catalogue.views import SoftDeleteView
from core.scoping import filter_for_user_company
from core.views import SearchableListMixin, StaffRequiredMixin

from .forms import AccessoryForm
from .models import Accessory
from .serializers import AccessorySerializer


class StaffWritePermission(permissions.IsAuthenticated):
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_staff)


class AccessoryListView(LoginRequiredMixin, SearchableListMixin, ListView):
    model = Accessory
    template_name = "inventory/list.html"
    context_object_name = "objects"
    search_fields = ("name", "company__name")

    def get_queryset(self):
        queryset = super().get_queryset().filter(deleted_at__isnull=True).select_related("category", "company", "supplier", "location")
        return filter_for_user_company(queryset, self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Accessories"
        context["subtitle"] = "Assignable supporting inventory."
        context["create_url"] = reverse_lazy("accessories:create")
        context["detail_route"] = "accessories:detail"
        return context


class AccessoryCreateView(StaffRequiredMixin, CreateView):
    model = Accessory
    form_class = AccessoryForm
    template_name = "inventory/form.html"
    success_url = reverse_lazy("accessories:list")

    def form_valid(self, form):
        messages.success(self.request, "Accessory created.")
        return super().form_valid(form)


class AccessoryUpdateView(StaffRequiredMixin, UpdateView):
    model = Accessory
    form_class = AccessoryForm
    template_name = "inventory/form.html"
    success_url = reverse_lazy("accessories:list")

    def form_valid(self, form):
        messages.success(self.request, "Accessory updated.")
        return super().form_valid(form)


class AccessoryDetailView(LoginRequiredMixin, DetailView):
    model = Accessory
    template_name = "inventory/detail.html"
    context_object_name = "object"

    def get_queryset(self):
        queryset = Accessory.objects.filter(deleted_at__isnull=True).select_related("category", "company", "supplier", "location")
        return filter_for_user_company(queryset, self.request.user)


class AccessoryDeleteView(SoftDeleteView):
    model = Accessory
    success_url = reverse_lazy("accessories:list")
    success_message = "Accessory archived."


class AccessoryViewSet(viewsets.ModelViewSet):
    serializer_class = AccessorySerializer
    permission_classes = [StaffWritePermission]

    def get_queryset(self):
        queryset = Accessory.objects.filter(deleted_at__isnull=True).select_related("category", "company", "supplier", "location")
        return filter_for_user_company(queryset, self.request.user)
