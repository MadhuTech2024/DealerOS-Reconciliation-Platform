from django.contrib import admin

from reconcile.models import ImportIssue


@admin.register(ImportIssue)
class ImportIssueAdmin(admin.ModelAdmin):
    list_display = ("source_file", "source_row_number", "created_at")
    list_filter = ("source_file",)
    readonly_fields = ("source_file", "source_row_number", "raw_data", "message", "created_at")
