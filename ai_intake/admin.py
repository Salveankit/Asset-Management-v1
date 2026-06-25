from django.contrib import admin

from .models import AIIntakeAuditEvent, AIIntakeDocument, AIIntakeDraft, AIIntakeInvoiceReview, AIIntakeJob, AIIntakeLineItem

admin.site.register(AIIntakeDocument)
admin.site.register(AIIntakeJob)
admin.site.register(AIIntakeDraft)
admin.site.register(AIIntakeInvoiceReview)
admin.site.register(AIIntakeLineItem)
admin.site.register(AIIntakeAuditEvent)
