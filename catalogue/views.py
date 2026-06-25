from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView, View
from rest_framework import permissions, viewsets

from core.views import SearchableListMixin, StaffRequiredMixin

from .forms import CategoryForm, ManufacturerForm, StatusLabelForm
from .models import Category, Manufacturer, StatusLabel
from .serializers import CategorySerializer, ManufacturerSerializer, StatusLabelSerializer


class SoftDeleteView(StaffRequiredMixin, View):
    model = None
    success_url = None
    success_message = "Record archived."

    def post(self, request, *args, **kwargs):
        obj = self.model.objects.filter(deleted_at__isnull=True).get(pk=kwargs["pk"])
        obj.soft_delete()
        messages.success(request, self.success_message)
        return redirect(self.success_url)


class ActiveOnlyMixin:
    def get_queryset(self):
        return self.model.objects.filter(deleted_at__isnull=True)


class CategoryListView(LoginRequiredMixin, SearchableListMixin, ListView):
    model = Category
    template_name = "catalogue/list.html"
    context_object_name = "objects"
    search_fields = ("name", "category_type", "notes")

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Categories"
        context["create_url"] = reverse_lazy("catalogue:category-create")
        context["detail_route"] = "catalogue:category-detail"
        context["columns"] = [
            ("Name", "name"),
            ("Type", "category_type"),
            ("Acceptance", "require_acceptance"),
            ("Check-in Email", "checkin_email"),
        ]
        return context


class CategoryCreateView(StaffRequiredMixin, CreateView):
    model = Category
    form_class = CategoryForm
    template_name = "catalogue/form.html"
    success_url = reverse_lazy("catalogue:category-list")

    def form_valid(self, form):
        messages.success(self.request, "Category created.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Create Category"
        context["cancel_url"] = reverse_lazy("catalogue:category-list")
        return context


class CategoryUpdateView(StaffRequiredMixin, UpdateView):
    model = Category
    form_class = CategoryForm
    template_name = "catalogue/form.html"
    success_url = reverse_lazy("catalogue:category-list")

    def get_queryset(self):
        return Category.objects.filter(deleted_at__isnull=True)

    def form_valid(self, form):
        messages.success(self.request, "Category updated.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Edit Category"
        context["cancel_url"] = reverse_lazy("catalogue:category-detail", kwargs={"pk": self.object.pk})
        return context


class CategoryDetailView(LoginRequiredMixin, DetailView):
    model = Category
    template_name = "catalogue/detail.html"
    context_object_name = "object"

    def get_queryset(self):
        return Category.objects.filter(deleted_at__isnull=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Category Detail"
        context["edit_url"] = reverse_lazy("catalogue:category-edit", kwargs={"pk": self.object.pk})
        context["delete_url"] = reverse_lazy("catalogue:category-delete", kwargs={"pk": self.object.pk})
        context["back_url"] = reverse_lazy("catalogue:category-list")
        context["fields"] = [
            ("Name", self.object.name),
            ("Type", self.object.get_category_type_display()),
            ("Require Acceptance", "Yes" if self.object.require_acceptance else "No"),
            ("Use Default EULA", "Yes" if self.object.use_default_eula else "No"),
            ("Check-in Email", "Yes" if self.object.checkin_email else "No"),
            ("Alert on Response", "Yes" if self.object.alert_on_response else "No"),
            ("Notes", self.object.notes or "-"),
        ]
        return context


class CategoryDeleteView(SoftDeleteView):
    model = Category
    success_url = reverse_lazy("catalogue:category-list")
    success_message = "Category archived."


class ManufacturerListView(LoginRequiredMixin, SearchableListMixin, ListView):
    model = Manufacturer
    template_name = "catalogue/list.html"
    context_object_name = "objects"
    search_fields = ("name", "notes", "support_email")

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Manufacturers"
        context["create_url"] = reverse_lazy("catalogue:manufacturer-create")
        context["detail_route"] = "catalogue:manufacturer-detail"
        context["columns"] = [
            ("Name", "name"),
            ("Support Email", "support_email"),
            ("Support Phone", "support_phone"),
            ("Support URL", "support_url"),
        ]
        return context


class ManufacturerCreateView(StaffRequiredMixin, CreateView):
    model = Manufacturer
    form_class = ManufacturerForm
    template_name = "catalogue/form.html"
    success_url = reverse_lazy("catalogue:manufacturer-list")

    def form_valid(self, form):
        messages.success(self.request, "Manufacturer created.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Create Manufacturer"
        context["cancel_url"] = reverse_lazy("catalogue:manufacturer-list")
        return context


class ManufacturerUpdateView(StaffRequiredMixin, UpdateView):
    model = Manufacturer
    form_class = ManufacturerForm
    template_name = "catalogue/form.html"
    success_url = reverse_lazy("catalogue:manufacturer-list")

    def get_queryset(self):
        return Manufacturer.objects.filter(deleted_at__isnull=True)

    def form_valid(self, form):
        messages.success(self.request, "Manufacturer updated.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Edit Manufacturer"
        context["cancel_url"] = reverse_lazy("catalogue:manufacturer-detail", kwargs={"pk": self.object.pk})
        return context


class ManufacturerDetailView(LoginRequiredMixin, DetailView):
    model = Manufacturer
    template_name = "catalogue/detail.html"
    context_object_name = "object"

    def get_queryset(self):
        return Manufacturer.objects.filter(deleted_at__isnull=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Manufacturer Detail"
        context["edit_url"] = reverse_lazy("catalogue:manufacturer-edit", kwargs={"pk": self.object.pk})
        context["delete_url"] = reverse_lazy("catalogue:manufacturer-delete", kwargs={"pk": self.object.pk})
        context["back_url"] = reverse_lazy("catalogue:manufacturer-list")
        context["fields"] = [
            ("Name", self.object.name),
            ("URL", self.object.url or "-"),
            ("Support Email", self.object.support_email or "-"),
            ("Support Phone", self.object.support_phone or "-"),
            ("Support URL", self.object.support_url or "-"),
            ("Warranty Lookup", self.object.warranty_lookup_url or "-"),
            ("Notes", self.object.notes or "-"),
        ]
        return context


class ManufacturerDeleteView(SoftDeleteView):
    model = Manufacturer
    success_url = reverse_lazy("catalogue:manufacturer-list")
    success_message = "Manufacturer archived."


class StatusLabelListView(LoginRequiredMixin, SearchableListMixin, ListView):
    model = StatusLabel
    template_name = "catalogue/list.html"
    context_object_name = "objects"
    search_fields = ("name", "notes")

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Status Labels"
        context["create_url"] = reverse_lazy("catalogue:status-label-create")
        context["detail_route"] = "catalogue:status-label-detail"
        context["columns"] = [
            ("Name", "name"),
            ("Type", "status_type"),
            ("Show in Nav", "show_in_nav"),
            ("Default", "default_label"),
        ]
        return context


class StatusLabelCreateView(StaffRequiredMixin, CreateView):
    model = StatusLabel
    form_class = StatusLabelForm
    template_name = "catalogue/form.html"
    success_url = reverse_lazy("catalogue:status-label-list")

    def form_valid(self, form):
        messages.success(self.request, "Status label created.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Create Status Label"
        context["cancel_url"] = reverse_lazy("catalogue:status-label-list")
        return context


class StatusLabelUpdateView(StaffRequiredMixin, UpdateView):
    model = StatusLabel
    form_class = StatusLabelForm
    template_name = "catalogue/form.html"
    success_url = reverse_lazy("catalogue:status-label-list")

    def get_queryset(self):
        return StatusLabel.objects.filter(deleted_at__isnull=True)

    def form_valid(self, form):
        messages.success(self.request, "Status label updated.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Edit Status Label"
        context["cancel_url"] = reverse_lazy("catalogue:status-label-detail", kwargs={"pk": self.object.pk})
        return context


class StatusLabelDetailView(LoginRequiredMixin, DetailView):
    model = StatusLabel
    template_name = "catalogue/detail.html"
    context_object_name = "object"

    def get_queryset(self):
        return StatusLabel.objects.filter(deleted_at__isnull=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Status Label Detail"
        context["edit_url"] = reverse_lazy("catalogue:status-label-edit", kwargs={"pk": self.object.pk})
        context["delete_url"] = reverse_lazy("catalogue:status-label-delete", kwargs={"pk": self.object.pk})
        context["back_url"] = reverse_lazy("catalogue:status-label-list")
        context["fields"] = [
            ("Name", self.object.name),
            ("Type", self.object.status_type.title()),
            ("Show in Nav", "Yes" if self.object.show_in_nav else "No"),
            ("Default", "Yes" if self.object.default_label else "No"),
            ("Notes", self.object.notes or "-"),
        ]
        return context


class StatusLabelDeleteView(SoftDeleteView):
    model = StatusLabel
    success_url = reverse_lazy("catalogue:status-label-list")
    success_message = "Status label archived."


class StaffWritePermission(permissions.IsAuthenticated):
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_staff)


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.filter(deleted_at__isnull=True)
    serializer_class = CategorySerializer
    permission_classes = [StaffWritePermission]


class ManufacturerViewSet(viewsets.ModelViewSet):
    queryset = Manufacturer.objects.filter(deleted_at__isnull=True)
    serializer_class = ManufacturerSerializer
    permission_classes = [StaffWritePermission]


class StatusLabelViewSet(viewsets.ModelViewSet):
    queryset = StatusLabel.objects.filter(deleted_at__isnull=True)
    serializer_class = StatusLabelSerializer
    permission_classes = [StaffWritePermission]
