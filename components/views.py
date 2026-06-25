from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from rest_framework import permissions, viewsets

from catalogue.views import SoftDeleteView
from core.scoping import filter_for_user_company
from core.views import SearchableListMixin, StaffRequiredMixin

from .forms import ComponentForm
from .models import Component
from .serializers import ComponentSerializer


class StaffWritePermission(permissions.IsAuthenticated):
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_staff)


class ComponentListView(LoginRequiredMixin, SearchableListMixin, ListView):
    model = Component
    template_name = "inventory/list.html"
    context_object_name = "objects"
    search_fields = ("name", "company__name")

    def get_queryset(self):
        queryset = super().get_queryset().filter(deleted_at__isnull=True).select_related("category", "company", "supplier")
        return filter_for_user_company(queryset, self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Components"
        context["subtitle"] = "Attachable component inventory."
        context["create_url"] = reverse_lazy("components:create")
        context["detail_route"] = "components:detail"
        return context


class ComponentCreateView(StaffRequiredMixin, CreateView):
    model = Component
    form_class = ComponentForm
    template_name = "inventory/form.html"
    success_url = reverse_lazy("components:list")

    def form_valid(self, form):
        messages.success(self.request, "Component created.")
        return super().form_valid(form)


class ComponentUpdateView(StaffRequiredMixin, UpdateView):
    model = Component
    form_class = ComponentForm
    template_name = "inventory/form.html"
    success_url = reverse_lazy("components:list")

    def form_valid(self, form):
        messages.success(self.request, "Component updated.")
        return super().form_valid(form)


class ComponentDetailView(LoginRequiredMixin, DetailView):
    model = Component
    template_name = "inventory/detail.html"
    context_object_name = "object"

    def get_queryset(self):
        queryset = Component.objects.filter(deleted_at__isnull=True).select_related("category", "company", "supplier")
        return filter_for_user_company(queryset, self.request.user)


class ComponentDeleteView(SoftDeleteView):
    model = Component
    success_url = reverse_lazy("components:list")
    success_message = "Component archived."


class ComponentViewSet(viewsets.ModelViewSet):
    serializer_class = ComponentSerializer
    permission_classes = [StaffWritePermission]

    def get_queryset(self):
        queryset = Component.objects.filter(deleted_at__isnull=True).select_related("category", "company", "supplier")
        return filter_for_user_company(queryset, self.request.user)
