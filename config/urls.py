from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from accessories.views import AccessoryViewSet
from ai_intake.views import AIIntakeAuditListView
from assets.views import AssetModelViewSet, AssetViewSet, DepreciationProfileViewSet
from audits.views import AuditLogViewSet
from catalogue.views import CategoryViewSet, ManufacturerViewSet, StatusLabelViewSet
from components.views import ComponentViewSet
from config import views
from consumables.views import ConsumableViewSet
from files.views import AssetAttachmentViewSet
from licences.views import LicenseSeatViewSet, LicenseViewSet
from locations.views import LocationViewSet
from organisations.views import CompanyViewSet, DepartmentViewSet
from people.views import GroupViewSet, UserViewSet
from suppliers.views import SupplierViewSet

router = DefaultRouter()
router.register("depreciations", DepreciationProfileViewSet, basename="api-depreciation")
router.register("asset-models", AssetModelViewSet, basename="api-asset-model")
router.register("assets", AssetViewSet, basename="api-asset")
router.register("asset-attachments", AssetAttachmentViewSet, basename="api-asset-attachment")
router.register("audit-logs", AuditLogViewSet, basename="api-audit-log")
router.register("categories", CategoryViewSet, basename="api-category")
router.register("manufacturers", ManufacturerViewSet, basename="api-manufacturer")
router.register("status-labels", StatusLabelViewSet, basename="api-status-label")
router.register("locations", LocationViewSet, basename="api-location")
router.register("companies", CompanyViewSet, basename="api-company")
router.register("departments", DepartmentViewSet, basename="api-department")
router.register("users", UserViewSet, basename="api-user")
router.register("groups", GroupViewSet, basename="api-group")
router.register("suppliers", SupplierViewSet, basename="api-supplier")
router.register("licenses", LicenseViewSet, basename="api-license")
router.register("license-seats", LicenseSeatViewSet, basename="api-license-seat")
router.register("accessories", AccessoryViewSet, basename="api-accessory")
router.register("components", ComponentViewSet, basename="api-component")
router.register("consumables", ConsumableViewSet, basename="api-consumable")

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.dashboard, name='dashboard'),
    path('health/', views.health, name='health'),
    path("ai-intake/", include("ai_intake.urls")),
    path("assets/", include("assets.urls")),
    path("", include("checkouts.urls")),
    path("catalogue/", include("catalogue.urls")),
    path("files/", include("files.urls")),
    path("accessories/", include("accessories.urls")),
    path("components/", include("components.urls")),
    path("consumables/", include("consumables.urls")),
    path("licenses/", include("licences.urls")),
    path("locations/", include("locations.urls")),
    path("organisations/", include("organisations.urls")),
    path("people/", include("people.urls")),
    path("reports/", include("reports.urls")),
    path("suppliers/", include("suppliers.urls")),
    path("api/v1/", include(router.urls)),
    path(
        'accounts/login/',
        auth_views.LoginView.as_view(template_name='registration/login.html'),
        name='login',
    ),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
]
