from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView, View
from rest_framework import permissions, viewsets

from core.views import SearchableListMixin, StaffRequiredMixin

from .forms import LocationForm
from .models import Location
from .serializers import LocationSerializer


class LocationListView(LoginRequiredMixin, SearchableListMixin, ListView):
    model = Location
    template_name = "locations/list.html"
    context_object_name = "objects"
    search_fields = ("name", "city", "country", "ldap_ou")

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(deleted_at__isnull=True)
            .select_related("parent", "manager")
        )


class LocationCreateView(StaffRequiredMixin, CreateView):
    model = Location
    form_class = LocationForm
    template_name = "locations/form.html"
    success_url = reverse_lazy("locations:list")

    def form_valid(self, form):
        messages.success(self.request, "Location created.")
        return super().form_valid(form)


class LocationUpdateView(StaffRequiredMixin, UpdateView):
    model = Location
    form_class = LocationForm
    template_name = "locations/form.html"
    success_url = reverse_lazy("locations:list")

    def get_queryset(self):
        return Location.objects.filter(deleted_at__isnull=True)

    def form_valid(self, form):
        messages.success(self.request, "Location updated.")
        return super().form_valid(form)


class LocationDetailView(LoginRequiredMixin, DetailView):
    model = Location
    template_name = "locations/detail.html"
    context_object_name = "object"

    def get_queryset(self):
        return Location.objects.filter(deleted_at__isnull=True).select_related("parent", "manager")


class LocationDeleteView(StaffRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        location = Location.objects.filter(deleted_at__isnull=True).get(pk=kwargs["pk"])
        location.soft_delete()
        messages.success(request, "Location archived.")
        return redirect("locations:list")


class StaffWritePermission(permissions.IsAuthenticated):
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_staff)


class LocationViewSet(viewsets.ModelViewSet):
    queryset = Location.objects.filter(deleted_at__isnull=True).select_related("parent", "manager")
    serializer_class = LocationSerializer
    permission_classes = [StaffWritePermission]
