from django.urls import path

from . import views

app_name = "catalogue"

urlpatterns = [
    path("categories/", views.CategoryListView.as_view(), name="category-list"),
    path("categories/create/", views.CategoryCreateView.as_view(), name="category-create"),
    path("categories/<int:pk>/", views.CategoryDetailView.as_view(), name="category-detail"),
    path("categories/<int:pk>/edit/", views.CategoryUpdateView.as_view(), name="category-edit"),
    path("categories/<int:pk>/delete/", views.CategoryDeleteView.as_view(), name="category-delete"),
    path("manufacturers/", views.ManufacturerListView.as_view(), name="manufacturer-list"),
    path("manufacturers/create/", views.ManufacturerCreateView.as_view(), name="manufacturer-create"),
    path("manufacturers/<int:pk>/", views.ManufacturerDetailView.as_view(), name="manufacturer-detail"),
    path("manufacturers/<int:pk>/edit/", views.ManufacturerUpdateView.as_view(), name="manufacturer-edit"),
    path("manufacturers/<int:pk>/delete/", views.ManufacturerDeleteView.as_view(), name="manufacturer-delete"),
    path("status-labels/", views.StatusLabelListView.as_view(), name="status-label-list"),
    path("status-labels/create/", views.StatusLabelCreateView.as_view(), name="status-label-create"),
    path("status-labels/<int:pk>/", views.StatusLabelDetailView.as_view(), name="status-label-detail"),
    path("status-labels/<int:pk>/edit/", views.StatusLabelUpdateView.as_view(), name="status-label-edit"),
    path("status-labels/<int:pk>/delete/", views.StatusLabelDeleteView.as_view(), name="status-label-delete"),
]
