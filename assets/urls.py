from django.urls import path

from . import views

app_name = "assets"

urlpatterns = [
    path("depreciations/", views.DepreciationProfileListView.as_view(), name="depreciation-list"),
    path("depreciations/create/", views.DepreciationProfileCreateView.as_view(), name="depreciation-create"),
    path("depreciations/<int:pk>/", views.DepreciationProfileDetailView.as_view(), name="depreciation-detail"),
    path("depreciations/<int:pk>/edit/", views.DepreciationProfileUpdateView.as_view(), name="depreciation-edit"),
    path("depreciations/<int:pk>/delete/", views.DepreciationProfileDeleteView.as_view(), name="depreciation-delete"),
    path("models/", views.AssetModelListView.as_view(), name="model-list"),
    path("models/create/", views.AssetModelCreateView.as_view(), name="model-create"),
    path("models/<int:pk>/", views.AssetModelDetailView.as_view(), name="model-detail"),
    path("models/<int:pk>/edit/", views.AssetModelUpdateView.as_view(), name="model-edit"),
    path("models/<int:pk>/delete/", views.AssetModelDeleteView.as_view(), name="model-delete"),
    path("", views.AssetListView.as_view(), name="list"),
    path("create/", views.AssetCreateView.as_view(), name="create"),
    path("<int:pk>/", views.AssetDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.AssetUpdateView.as_view(), name="edit"),
    path("<int:pk>/delete/", views.AssetDeleteView.as_view(), name="delete"),
]
