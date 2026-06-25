from django.urls import path

from . import views

app_name = "ai_intake"

urlpatterns = [
    path("", views.AIIntakeListView.as_view(), name="list"),
    path("upload/", views.AIIntakeUploadView.as_view(), name="upload"),
    path("audit/", views.AIIntakeAuditListView.as_view(), name="audit-list"),
    path("<int:pk>/", views.AIIntakeDetailView.as_view(), name="detail"),
    path("<int:pk>/status/", views.AIIntakeProcessingStatusView.as_view(), name="processing-status"),
    path("<int:pk>/line-items/", views.AIIntakeLineItemWorkspaceView.as_view(), name="line-item-workspace"),
    path("<int:pk>/line-items/retry/", views.AIIntakeInvoiceRetryView.as_view(), name="line-item-retry"),
    path("<int:pk>/preview/", views.AIIntakePreviewView.as_view(), name="preview"),
    path("<int:pk>/delete/", views.AIIntakeDeleteView.as_view(), name="delete"),
    path("line-items/<int:pk>/save/", views.AIIntakeLineItemUpdateView.as_view(), name="line-item-save"),
    path("line-items/<int:pk>/approve/", views.AIIntakeLineItemApproveView.as_view(), name="line-item-approve"),
    path("drafts/<int:pk>/save/", views.AIIntakeSaveDraftView.as_view(), name="save"),
    path("drafts/<int:pk>/approve/", views.AIIntakeApproveView.as_view(), name="approve"),
    path("drafts/<int:pk>/reject/", views.AIIntakeRejectView.as_view(), name="reject"),
    path("drafts/<int:pk>/retry/", views.AIIntakeRetryView.as_view(), name="retry"),
]
