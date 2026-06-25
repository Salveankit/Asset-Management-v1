from django.urls import path

from . import views

app_name = "people"

urlpatterns = [
    path("users/", views.UserListView.as_view(), name="user-list"),
    path("users/create/", views.UserCreateView.as_view(), name="user-create"),
    path("users/<int:pk>/", views.UserDetailView.as_view(), name="user-detail"),
    path("users/<int:pk>/edit/", views.UserUpdateView.as_view(), name="user-edit"),
    path("groups/", views.GroupListView.as_view(), name="group-list"),
    path("groups/create/", views.GroupCreateView.as_view(), name="group-create"),
    path("groups/<int:pk>/", views.GroupDetailView.as_view(), name="group-detail"),
    path("groups/<int:pk>/edit/", views.GroupUpdateView.as_view(), name="group-edit"),
]
