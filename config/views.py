from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render

from accessories.models import Accessory
from assets.models import Asset
from audits.models import AuditLog
from components.models import Component
from consumables.models import Consumable
from core.scoping import filter_for_user_company
from licences.models import License


def _scope_audit_logs_for_user(queryset, user):
    if user.is_staff or user.is_superuser or not getattr(user, "company_id", None):
        return queryset
    return queryset.filter(asset__company_id=user.company_id)


def health(request):
    return JsonResponse({"status": "ok"})


@login_required
def dashboard(request):
    assets = filter_for_user_company(Asset.objects.filter(deleted_at__isnull=True), request.user)
    licenses = filter_for_user_company(License.objects.filter(deleted_at__isnull=True), request.user)
    accessories = filter_for_user_company(Accessory.objects.filter(deleted_at__isnull=True), request.user)
    consumables = filter_for_user_company(Consumable.objects.filter(deleted_at__isnull=True), request.user)
    components = filter_for_user_company(Component.objects.filter(deleted_at__isnull=True), request.user)
    people_qs = filter_for_user_company(request.user.__class__.objects.all(), request.user)
    metrics = [
        {"label": "Assets", "value": assets.count(), "tone": "teal"},
        {"label": "Licences", "value": licenses.count(), "tone": "maroon"},
        {"label": "Accessories", "value": accessories.count(), "tone": "orange"},
        {"label": "Consumables", "value": consumables.count(), "tone": "purple"},
        {"label": "Components", "value": components.count(), "tone": "gold"},
        {"label": "People", "value": people_qs.count(), "tone": "blue"},
    ]
    recent_activity = _scope_audit_logs_for_user(
        AuditLog.objects.select_related("asset", "actor").order_by("-created_at"),
        request.user,
    )[:5]
    return render(request, "dashboard/index.html", {"metrics": metrics, "recent_activity": recent_activity})
