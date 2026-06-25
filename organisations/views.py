from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from rest_framework import permissions, viewsets

from catalogue.views import SoftDeleteView
from core.views import SearchableListMixin, StaffRequiredMixin

from .forms import CompanyForm, DepartmentForm
from .models import Company, Department
from .serializers import CompanySerializer, DepartmentSerializer


class StaffWritePermission(permissions.IsAuthenticated):
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_staff)


class CompanyListView(LoginRequiredMixin, SearchableListMixin, ListView):
    model = Company
    template_name = "organisations/list.html"
    context_object_name = "objects"
    search_fields = ("name", "code", "email_domain")

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class CompanyCreateView(StaffRequiredMixin, CreateView):
    model = Company
    form_class = CompanyForm
    template_name = "organisations/form.html"
    success_url = reverse_lazy("organisations:company-list")

    def form_valid(self, form):
        messages.success(self.request, "Company created.")
        return super().form_valid(form)


class CompanyUpdateView(StaffRequiredMixin, UpdateView):
    model = Company
    form_class = CompanyForm
    template_name = "organisations/form.html"
    success_url = reverse_lazy("organisations:company-list")

    def get_queryset(self):
        return Company.objects.filter(deleted_at__isnull=True)

    def form_valid(self, form):
        messages.success(self.request, "Company updated.")
        return super().form_valid(form)


class CompanyDetailView(LoginRequiredMixin, DetailView):
    model = Company
    template_name = "organisations/detail.html"
    context_object_name = "object"

    def get_queryset(self):
        return Company.objects.filter(deleted_at__isnull=True)


class CompanyDeleteView(SoftDeleteView):
    model = Company
    success_url = reverse_lazy("organisations:company-list")
    success_message = "Company archived."


class DepartmentListView(LoginRequiredMixin, SearchableListMixin, ListView):
    model = Department
    template_name = "organisations/department_list.html"
    context_object_name = "objects"
    search_fields = ("name", "company__name")

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True).select_related("company")


class DepartmentCreateView(StaffRequiredMixin, CreateView):
    model = Department
    form_class = DepartmentForm
    template_name = "organisations/form.html"
    success_url = reverse_lazy("organisations:department-list")

    def form_valid(self, form):
        messages.success(self.request, "Department created.")
        return super().form_valid(form)


class DepartmentUpdateView(StaffRequiredMixin, UpdateView):
    model = Department
    form_class = DepartmentForm
    template_name = "organisations/form.html"
    success_url = reverse_lazy("organisations:department-list")

    def get_queryset(self):
        return Department.objects.filter(deleted_at__isnull=True)

    def form_valid(self, form):
        messages.success(self.request, "Department updated.")
        return super().form_valid(form)


class DepartmentDetailView(LoginRequiredMixin, DetailView):
    model = Department
    template_name = "organisations/detail.html"
    context_object_name = "object"

    def get_queryset(self):
        return Department.objects.filter(deleted_at__isnull=True).select_related("company")


class DepartmentDeleteView(SoftDeleteView):
    model = Department
    success_url = reverse_lazy("organisations:department-list")
    success_message = "Department archived."


class CompanyViewSet(viewsets.ModelViewSet):
    queryset = Company.objects.filter(deleted_at__isnull=True)
    serializer_class = CompanySerializer
    permission_classes = [StaffWritePermission]


class DepartmentViewSet(viewsets.ModelViewSet):
    queryset = Department.objects.filter(deleted_at__isnull=True).select_related("company")
    serializer_class = DepartmentSerializer
    permission_classes = [StaffWritePermission]
