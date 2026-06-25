from django.urls import path

from . import views

app_name = "licences"

urlpatterns = [
    path("", views.LicenseListView.as_view(), name="list"),
    path("create/", views.LicenseCreateView.as_view(), name="create"),
    path("<int:pk>/", views.LicenseDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.LicenseUpdateView.as_view(), name="edit"),
    path("<int:pk>/delete/", views.LicenseDeleteView.as_view(), name="delete"),
    path("<int:pk>/seats/assign/", views.LicenseSeatAssignView.as_view(), name="seat-assign"),
    path("seats/<int:pk>/release/", views.LicenseSeatReleaseView.as_view(), name="seat-release"),
]
