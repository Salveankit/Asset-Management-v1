from django.urls import path

from . import views

app_name = "organisations"

urlpatterns = [
    path("companies/", views.CompanyListView.as_view(), name="company-list"),
    path("companies/create/", views.CompanyCreateView.as_view(), name="company-create"),
    path("companies/<int:pk>/", views.CompanyDetailView.as_view(), name="company-detail"),
    path("companies/<int:pk>/edit/", views.CompanyUpdateView.as_view(), name="company-edit"),
    path("companies/<int:pk>/delete/", views.CompanyDeleteView.as_view(), name="company-delete"),
    path("departments/", views.DepartmentListView.as_view(), name="department-list"),
    path("departments/create/", views.DepartmentCreateView.as_view(), name="department-create"),
    path("departments/<int:pk>/", views.DepartmentDetailView.as_view(), name="department-detail"),
    path("departments/<int:pk>/edit/", views.DepartmentUpdateView.as_view(), name="department-edit"),
    path("departments/<int:pk>/delete/", views.DepartmentDeleteView.as_view(), name="department-delete"),
]
