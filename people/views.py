from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import Group
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from rest_framework import permissions, viewsets

from core.scoping import filter_for_user_company
from core.views import SearchableListMixin, StaffRequiredMixin

from .forms import GroupForm, UserForm
from .serializers import GroupSerializer, UserSerializer


class StaffWritePermission(permissions.IsAuthenticated):
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_staff)


class UserListView(LoginRequiredMixin, SearchableListMixin, ListView):
    model = get_user_model()
    template_name = "people/user_list.html"
    context_object_name = "objects"
    search_fields = ("username", "display_name", "email", "company__name")

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related("company", "department", "location")
            .prefetch_related("groups")
        )
        return filter_for_user_company(queryset, self.request.user)


class UserCreateView(StaffRequiredMixin, CreateView):
    model = get_user_model()
    form_class = UserForm
    template_name = "people/form.html"
    success_url = reverse_lazy("people:user-list")

    def form_valid(self, form):
        messages.success(self.request, "User created.")
        return super().form_valid(form)


class UserUpdateView(StaffRequiredMixin, UpdateView):
    model = get_user_model()
    form_class = UserForm
    template_name = "people/form.html"
    success_url = reverse_lazy("people:user-list")

    def form_valid(self, form):
        messages.success(self.request, "User updated.")
        return super().form_valid(form)


class UserDetailView(LoginRequiredMixin, DetailView):
    model = get_user_model()
    template_name = "people/user_detail.html"
    context_object_name = "object"

    def get_queryset(self):
        queryset = get_user_model().objects.select_related("company", "department", "location").prefetch_related("groups")
        return filter_for_user_company(queryset, self.request.user)


class GroupListView(LoginRequiredMixin, ListView):
    model = Group
    template_name = "people/group_list.html"
    context_object_name = "objects"

    def get_queryset(self):
        return Group.objects.order_by("name").prefetch_related("permissions")


class GroupCreateView(StaffRequiredMixin, CreateView):
    model = Group
    form_class = GroupForm
    template_name = "people/form.html"
    success_url = reverse_lazy("people:group-list")

    def form_valid(self, form):
        messages.success(self.request, "Group created.")
        return super().form_valid(form)


class GroupUpdateView(StaffRequiredMixin, UpdateView):
    model = Group
    form_class = GroupForm
    template_name = "people/form.html"
    success_url = reverse_lazy("people:group-list")

    def form_valid(self, form):
        messages.success(self.request, "Group updated.")
        return super().form_valid(form)


class GroupDetailView(LoginRequiredMixin, DetailView):
    model = Group
    template_name = "people/group_detail.html"
    context_object_name = "object"


class UserViewSet(viewsets.ModelViewSet):
    serializer_class = UserSerializer
    permission_classes = [StaffWritePermission]

    def get_queryset(self):
        queryset = get_user_model().objects.select_related("company", "department", "location")
        return filter_for_user_company(queryset, self.request.user)


class GroupViewSet(viewsets.ModelViewSet):
    queryset = Group.objects.order_by("name")
    serializer_class = GroupSerializer
    permission_classes = [StaffWritePermission]
