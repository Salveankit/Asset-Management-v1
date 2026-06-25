import csv

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render

from assets.models import Asset
from audits.models import AuditLog
from core.scoping import filter_for_user_company


def _scope_audit_logs_for_user(queryset, user):
    if user.is_staff or user.is_superuser or not getattr(user, "company_id", None):
        return queryset
    return queryset.filter(asset__company_id=user.company_id)


@login_required
def asset_report(request):
    assets = filter_for_user_company(
        Asset.objects.filter(deleted_at__isnull=True).select_related("model", "status_label", "company", "default_location"),
        request.user,
    ).order_by("asset_tag")
    return render(request, "reports/asset_report.html", {"objects": assets})


@login_required
def activity_report(request):
    activity = _scope_audit_logs_for_user(
        AuditLog.objects.select_related("asset", "actor").order_by("-created_at"),
        request.user,
    )[:100]
    return render(request, "reports/activity_report.html", {"objects": activity})


@login_required
def asset_report_csv(request):
    assets = filter_for_user_company(
        Asset.objects.filter(deleted_at__isnull=True).select_related("model", "status_label", "company", "default_location"),
        request.user,
    ).order_by("asset_tag")
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="asset-report.csv"'
    writer = csv.writer(response)
    writer.writerow(["Asset Tag", "Name", "Model", "Status", "Company", "Location", "Custody"])
    for asset in assets:
        writer.writerow(
            [
                asset.asset_tag,
                asset.name,
                asset.model,
                asset.status_label,
                asset.company or "",
                asset.default_location or "",
                asset.custody_state,
            ]
        )
    return response
