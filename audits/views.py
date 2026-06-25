from rest_framework import permissions, viewsets

from .models import AuditLog
from .serializers import AuditLogSerializer


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AuditLog.objects.select_related("asset", "actor")
    serializer_class = AuditLogSerializer
    permission_classes = [permissions.IsAuthenticated]
