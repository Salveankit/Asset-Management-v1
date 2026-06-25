from django.urls import path

from . import views

app_name = "checkouts"

urlpatterns = [
    path("assets/<int:pk>/assign/", views.AssetAssignView.as_view(), name="asset-assign"),
    path("assets/<int:pk>/clear-assignment/", views.AssetClearAssignmentView.as_view(), name="asset-clear-assignment"),
    path("assets/<int:pk>/checkout/", views.AssetCheckoutView.as_view(), name="asset-checkout"),
    path("assets/<int:pk>/checkin/", views.AssetCheckinView.as_view(), name="asset-checkin"),
]
