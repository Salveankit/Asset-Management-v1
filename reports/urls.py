from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("assets/", views.asset_report, name="asset-report"),
    path("assets/export.csv", views.asset_report_csv, name="asset-report-csv"),
    path("activity/", views.activity_report, name="activity-report"),
]
