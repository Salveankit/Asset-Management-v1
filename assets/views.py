from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.urls import reverse_lazy
from django.utils.dateparse import parse_date
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from rest_framework.decorators import action
from rest_framework import permissions, viewsets
from rest_framework.response import Response
from rest_framework import status

from audits.models import AuditLog
from audits.services import log_asset_event
from catalogue.models import StatusLabel
from catalogue.views import SoftDeleteView
from checkouts.forms import AssetAssignmentForm, AssetCheckinForm, AssetCheckoutForm
from checkouts.services import assign_asset, checkin_asset, checkout_asset, clear_asset_assignment
from core.views import SearchableListMixin, StaffRequiredMixin
from core.scoping import filter_for_user_company
from files.forms import AssetAttachmentForm
from locations.models import Location

from .forms import AssetForm, AssetModelForm, DepreciationProfileForm
from .models import Asset, AssetModel, DepreciationProfile
from .serializers import AssetModelSerializer, AssetSerializer, DepreciationProfileSerializer


class StaffWritePermission(permissions.IsAuthenticated):
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_staff)


class ActiveOnlyMixin:
    def get_queryset(self):
        return self.model.objects.filter(deleted_at__isnull=True)


class DepreciationProfileListView(LoginRequiredMixin, SearchableListMixin, ListView):
    model = DepreciationProfile
    template_name = "assets/reference_list.html"
    context_object_name = "objects"
    search_fields = ("name", "notes", "depreciation_type")

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            title="Depreciation Profiles",
            subtitle="Lifecycle and finance reference rules for asset models.",
            create_url=reverse_lazy("assets:depreciation-create"),
            detail_route="assets:depreciation-detail",
            columns=[
                ("Name", "name"),
                ("Months", "months"),
                ("Type", "depreciation_type"),
                ("Minimum Value", "depreciation_min"),
            ],
        )
        return context


class DepreciationProfileCreateView(StaffRequiredMixin, CreateView):
    model = DepreciationProfile
    form_class = DepreciationProfileForm
    template_name = "assets/reference_form.html"
    success_url = reverse_lazy("assets:depreciation-list")

    def form_valid(self, form):
        messages.success(self.request, "Depreciation profile created.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Create Depreciation Profile"
        context["cancel_url"] = reverse_lazy("assets:depreciation-list")
        return context


class DepreciationProfileUpdateView(StaffRequiredMixin, UpdateView):
    model = DepreciationProfile
    form_class = DepreciationProfileForm
    template_name = "assets/reference_form.html"
    success_url = reverse_lazy("assets:depreciation-list")

    def get_queryset(self):
        return DepreciationProfile.objects.filter(deleted_at__isnull=True)

    def form_valid(self, form):
        messages.success(self.request, "Depreciation profile updated.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Edit Depreciation Profile"
        context["cancel_url"] = reverse_lazy("assets:depreciation-detail", kwargs={"pk": self.object.pk})
        return context


class DepreciationProfileDetailView(LoginRequiredMixin, DetailView):
    model = DepreciationProfile
    template_name = "assets/reference_detail.html"
    context_object_name = "object"

    def get_queryset(self):
        return DepreciationProfile.objects.filter(deleted_at__isnull=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            title="Depreciation Profile Detail",
            edit_url=reverse_lazy("assets:depreciation-edit", kwargs={"pk": self.object.pk}),
            delete_url=reverse_lazy("assets:depreciation-delete", kwargs={"pk": self.object.pk}),
            back_url=reverse_lazy("assets:depreciation-list"),
            fields=[
                ("Name", self.object.name),
                ("Months", self.object.months),
                ("Type", self.object.get_depreciation_type_display()),
                ("Minimum Value", self.object.depreciation_min),
                ("Notes", self.object.notes or "-"),
            ],
        )
        return context


class DepreciationProfileDeleteView(SoftDeleteView):
    model = DepreciationProfile
    success_url = reverse_lazy("assets:depreciation-list")
    success_message = "Depreciation profile archived."


class AssetModelListView(LoginRequiredMixin, SearchableListMixin, ListView):
    model = AssetModel
    template_name = "assets/reference_list.html"
    context_object_name = "objects"
    search_fields = ("name", "model_number", "manufacturer__name", "category__name")

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(deleted_at__isnull=True)
            .select_related("manufacturer", "category", "depreciation")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            title="Asset Models",
            subtitle="Reusable hardware model metadata linked to catalogue references.",
            create_url=reverse_lazy("assets:model-create"),
            detail_route="assets:model-detail",
            columns=[
                ("Name", "name"),
                ("Model Number", "model_number"),
                ("Manufacturer", "manufacturer"),
                ("Category", "category"),
            ],
        )
        return context


class AssetModelCreateView(StaffRequiredMixin, CreateView):
    model = AssetModel
    form_class = AssetModelForm
    template_name = "assets/reference_form.html"
    success_url = reverse_lazy("assets:model-list")

    def form_valid(self, form):
        messages.success(self.request, "Asset model created.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Create Asset Model"
        context["cancel_url"] = reverse_lazy("assets:model-list")
        return context


class AssetModelUpdateView(StaffRequiredMixin, UpdateView):
    model = AssetModel
    form_class = AssetModelForm
    template_name = "assets/reference_form.html"
    success_url = reverse_lazy("assets:model-list")

    def get_queryset(self):
        return AssetModel.objects.filter(deleted_at__isnull=True)

    def form_valid(self, form):
        messages.success(self.request, "Asset model updated.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Edit Asset Model"
        context["cancel_url"] = reverse_lazy("assets:model-detail", kwargs={"pk": self.object.pk})
        return context


class AssetModelDetailView(LoginRequiredMixin, DetailView):
    model = AssetModel
    template_name = "assets/reference_detail.html"
    context_object_name = "object"

    def get_queryset(self):
        return AssetModel.objects.filter(deleted_at__isnull=True).select_related(
            "manufacturer",
            "category",
            "depreciation",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            title="Asset Model Detail",
            edit_url=reverse_lazy("assets:model-edit", kwargs={"pk": self.object.pk}),
            delete_url=reverse_lazy("assets:model-delete", kwargs={"pk": self.object.pk}),
            back_url=reverse_lazy("assets:model-list"),
            fields=[
                ("Name", self.object.name),
                ("Model Number", self.object.model_number or "-"),
                ("Manufacturer", self.object.manufacturer or "-"),
                ("Category", self.object.category),
                ("Depreciation", self.object.depreciation or "-"),
                ("EOL Months", self.object.eol_months),
                ("Notes", self.object.notes or "-"),
            ],
        )
        return context


class AssetModelDeleteView(SoftDeleteView):
    model = AssetModel
    success_url = reverse_lazy("assets:model-list")
    success_message = "Asset model archived."


class AssetListView(LoginRequiredMixin, SearchableListMixin, ListView):
    model = Asset
    template_name = "assets/asset_list.html"
    context_object_name = "objects"
    search_fields = ("asset_tag", "name", "serial", "model__name", "status_label__name", "supplier__name")
    paginate_by = 25

    def get_ordering(self):
        ordering = self.request.GET.get("sort", "asset_tag")
        allowed = {
            "asset_tag": "asset_tag",
            "-asset_tag": "-asset_tag",
            "name": "name",
            "-name": "-name",
            "created_at": "created_at",
            "-created_at": "-created_at",
        }
        return allowed.get(ordering, "asset_tag")

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .filter(deleted_at__isnull=True)
            .select_related("model", "status_label", "company", "supplier", "default_location")
            .order_by(self.get_ordering())
        )
        queryset = filter_for_user_company(queryset, self.request.user)
        status_id = self.request.GET.get("status")
        model_id = self.request.GET.get("model")
        if status_id:
            queryset = queryset.filter(status_label_id=status_id)
        if model_id:
            queryset = queryset.filter(model_id=model_id)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["statuses"] = StatusLabel.objects.filter(deleted_at__isnull=True).order_by("name")
        context["models"] = AssetModel.objects.filter(deleted_at__isnull=True).order_by("name")
        context["selected_status"] = self.request.GET.get("status", "")
        context["selected_model"] = self.request.GET.get("model", "")
        context["selected_sort"] = self.request.GET.get("sort", "asset_tag")
        return context


class AssetCreateView(StaffRequiredMixin, CreateView):
    model = Asset
    form_class = AssetForm
    template_name = "assets/asset_form.html"
    success_url = reverse_lazy("assets:list")

    def form_valid(self, form):
        response = super().form_valid(form)
        log_asset_event(
            asset=self.object,
            action_type=AuditLog.ActionType.CREATED,
            actor=self.request.user,
            message="Asset created.",
            metadata={"asset_tag": self.object.asset_tag},
        )
        messages.success(self.request, "Asset created.")
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Create Asset"
        context["cancel_url"] = reverse_lazy("assets:list")
        return context


class AssetUpdateView(StaffRequiredMixin, UpdateView):
    model = Asset
    form_class = AssetForm
    template_name = "assets/asset_form.html"
    success_url = reverse_lazy("assets:list")

    def get_queryset(self):
        return Asset.objects.filter(deleted_at__isnull=True)

    def form_valid(self, form):
        response = super().form_valid(form)
        log_asset_event(
            asset=self.object,
            action_type=AuditLog.ActionType.UPDATED,
            actor=self.request.user,
            message="Asset updated.",
        )
        messages.success(self.request, "Asset updated.")
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Edit Asset"
        context["cancel_url"] = reverse_lazy("assets:detail", kwargs={"pk": self.object.pk})
        return context


class AssetDetailView(LoginRequiredMixin, DetailView):
    model = Asset
    template_name = "assets/asset_detail.html"
    context_object_name = "object"

    def get_queryset(self):
        return (
            filter_for_user_company(Asset.objects.filter(deleted_at__isnull=True), self.request.user)
            .select_related(
                "model",
                "model__manufacturer",
                "model__category",
                "model__depreciation",
                "status_label",
                "company",
                "supplier",
                "default_location",
                "assigned_user",
                "assigned_location",
                "assigned_asset",
            )
            .prefetch_related("attachments", "audit_logs", "checkout_events")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["edit_url"] = reverse_lazy("assets:edit", kwargs={"pk": self.object.pk})
        context["delete_url"] = reverse_lazy("assets:delete", kwargs={"pk": self.object.pk})
        context["back_url"] = reverse_lazy("assets:list")
        context["attachment_form"] = AssetAttachmentForm()
        context["attachment_create_url"] = reverse_lazy("files:asset-attachment-create", kwargs={"asset_pk": self.object.pk})
        context["assignment_form"] = AssetAssignmentForm(user_model=get_user_model(), asset=self.object)
        context["checkout_form"] = AssetCheckoutForm(user_model=get_user_model(), asset=self.object)
        context["checkin_form"] = AssetCheckinForm()
        context["assignment_url"] = reverse_lazy("checkouts:asset-assign", kwargs={"pk": self.object.pk})
        context["clear_assignment_url"] = reverse_lazy("checkouts:asset-clear-assignment", kwargs={"pk": self.object.pk})
        context["checkout_url"] = reverse_lazy("checkouts:asset-checkout", kwargs={"pk": self.object.pk})
        context["checkin_url"] = reverse_lazy("checkouts:asset-checkin", kwargs={"pk": self.object.pk})
        return context


class AssetDeleteView(SoftDeleteView):
    model = Asset
    success_url = reverse_lazy("assets:list")
    success_message = "Asset archived."

    def post(self, request, *args, **kwargs):
        asset = Asset.objects.get(pk=kwargs["pk"])
        log_asset_event(
            asset=asset,
            action_type=AuditLog.ActionType.ARCHIVED,
            actor=request.user,
            message="Asset archived.",
        )
        return super().post(request, *args, **kwargs)


class DepreciationProfileViewSet(viewsets.ModelViewSet):
    queryset = DepreciationProfile.objects.filter(deleted_at__isnull=True)
    serializer_class = DepreciationProfileSerializer
    permission_classes = [StaffWritePermission]


class AssetModelViewSet(viewsets.ModelViewSet):
    queryset = AssetModel.objects.filter(deleted_at__isnull=True)
    serializer_class = AssetModelSerializer
    permission_classes = [StaffWritePermission]


class AssetViewSet(viewsets.ModelViewSet):
    serializer_class = AssetSerializer
    permission_classes = [StaffWritePermission]

    def get_queryset(self):
        queryset = Asset.objects.filter(deleted_at__isnull=True).select_related(
            "model",
            "status_label",
            "company",
            "supplier",
            "default_location",
        )
        return filter_for_user_company(queryset, self.request.user)

    @action(detail=True, methods=["post"], permission_classes=[StaffWritePermission])
    def assign(self, request, pk=None):
        asset = self.get_object()
        try:
            assign_asset(
                asset=asset,
                actor=request.user,
                target_type=request.data.get("target_type"),
                user=get_user_model().objects.filter(pk=request.data.get("assigned_user")).first(),
                location=Location.objects.filter(pk=request.data.get("assigned_location"), deleted_at__isnull=True).first(),
                related_asset=Asset.objects.filter(pk=request.data.get("assigned_asset"), deleted_at__isnull=True).first(),
                note=request.data.get("note", ""),
            )
        except ValidationError as exc:
            return Response({"detail": exc.message_dict if hasattr(exc, "message_dict") else exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(asset).data)

    @action(detail=True, methods=["post"], permission_classes=[StaffWritePermission], url_path="clear-assignment")
    def clear_assignment(self, request, pk=None):
        asset = self.get_object()
        try:
            clear_asset_assignment(asset=asset, actor=request.user, note=request.data.get("note", ""))
        except ValidationError as exc:
            return Response({"detail": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(asset).data)

    @action(detail=True, methods=["post"], permission_classes=[StaffWritePermission])
    def checkout(self, request, pk=None):
        asset = self.get_object()
        try:
            checkout_asset(
                asset=asset,
                actor=request.user,
                target_type=request.data.get("target_type"),
                user=get_user_model().objects.filter(pk=request.data.get("assigned_user")).first(),
                location=Location.objects.filter(pk=request.data.get("assigned_location"), deleted_at__isnull=True).first(),
                related_asset=Asset.objects.filter(pk=request.data.get("assigned_asset"), deleted_at__isnull=True).first(),
                expected_checkin=parse_date(request.data.get("expected_checkin", "")) if request.data.get("expected_checkin") else None,
                note=request.data.get("note", ""),
            )
        except ValidationError as exc:
            return Response({"detail": exc.message_dict if hasattr(exc, "message_dict") else exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(asset).data)

    @action(detail=True, methods=["post"], permission_classes=[StaffWritePermission])
    def checkin(self, request, pk=None):
        asset = self.get_object()
        try:
            checkin_asset(asset=asset, actor=request.user, note=request.data.get("note", ""))
        except ValidationError as exc:
            return Response({"detail": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(asset).data)
