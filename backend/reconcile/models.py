from django.db import models


class Organization(models.Model):
    """
    Represents a tenant dealership entity.
    Ownership is strictly defined by locations.csv.
    """
    org_id = models.CharField(max_length=64, primary_key=True, help_text="Organization identifier (e.g. ORG-A)")
    name = models.CharField(max_length=128, blank=True, default='', help_text="Organization display name")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Organization"
        verbose_name_plural = "Organizations"
        ordering = ['org_id']

    def __str__(self):
        return self.org_id


class Location(models.Model):
    """
    Represents a physical dealership location belonging to an Organization.
    Defined in locations.csv.
    """
    location_id = models.CharField(max_length=64, primary_key=True, help_text="Location identifier (e.g. LOC-101)")
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='locations',
        help_text="Organization that owns this location"
    )
    location_name = models.CharField(max_length=128, help_text="Location display name")
    raw_data = models.JSONField(default=dict, blank=True, help_text="Raw source row data from locations.csv")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Location"
        verbose_name_plural = "Locations"
        ordering = ['location_id']

    def __str__(self):
        return f"{self.location_id} ({self.organization.org_id})"


class SystemARecord(models.Model):
    """
    Represents an operational transaction record from System A (system_a.csv).
    Captures financial base, adjustment, and total values.
    """
    source_file = models.CharField(max_length=64, default='system_a.csv', help_text="Source CSV filename")
    source_row_number = models.PositiveIntegerField(help_text="1-based row number in CSV file")
    record_id = models.CharField(max_length=64, db_index=True, help_text="Operational transaction identifier")
    raw_location_id = models.CharField(max_length=64, blank=True, default='', help_text="Raw location ID from CSV")
    location = models.ForeignKey(
        Location,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='system_a_records',
        help_text="Resolved location if valid"
    )
    event_date = models.DateField(null=True, blank=True, help_text="Transaction occurrence date")
    category_code = models.CharField(max_length=64, blank=True, default='', help_text="Transaction category")
    actor_id = models.CharField(max_length=64, blank=True, null=True, default='', help_text="User/Operator identifier")
    base_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, help_text="Base transaction amount")
    adjustment = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, help_text="Transaction adjustment amount")
    total_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, help_text="Total settlement amount (base + adjustment)")
    state = models.CharField(max_length=32, blank=True, default='', help_text="Transaction state (e.g. CONFIRMED, VOIDED)")
    raw_data = models.JSONField(default=dict, blank=True, help_text="Exact key-value pairs from source CSV row")
    import_warning = models.TextField(blank=True, default='', help_text="Warnings generated during import")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "System A Record"
        verbose_name_plural = "System A Records"
        ordering = ['source_row_number']
        constraints = [
            models.UniqueConstraint(fields=['source_file', 'source_row_number'], name='unique_system_a_source_row')
        ]

    def __str__(self):
        return f"{self.record_id} (row {self.source_row_number})"


class SystemBEntry(models.Model):
    """
    Represents an accounting / ledger entry from System B (system_b.csv).
    Preserves raw references and raw monetary strings without premature normalization.
    """
    source_file = models.CharField(max_length=64, default='system_b.csv', help_text="Source CSV filename")
    source_row_number = models.PositiveIntegerField(help_text="1-based row number in CSV file")
    entry_id = models.CharField(max_length=64, blank=True, default='', db_index=True, help_text="Ledger entry identifier")
    record_ref = models.CharField(max_length=64, db_index=True, help_text="Raw preserved record reference string")
    raw_location_id = models.CharField(max_length=64, blank=True, default='', help_text="Raw location ID from CSV")
    location = models.ForeignKey(
        Location,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='system_b_entries',
        help_text="Resolved location if valid"
    )
    recorded_on = models.DateField(null=True, blank=True, help_text="Date recorded in accounting system")
    raw_value = models.CharField(max_length=64, blank=True, default='', help_text="Exact preserved value string from CSV")
    value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, help_text="Parsed Decimal amount; None if blank or unparseable")
    is_value_blank = models.BooleanField(default=False, help_text="True if monetary value was missing/blank in source")
    label = models.CharField(max_length=255, blank=True, default='', help_text="Transaction label / description")
    raw_data = models.JSONField(default=dict, blank=True, help_text="Exact key-value pairs from source CSV row")
    import_warning = models.TextField(blank=True, default='', help_text="Warnings generated during import")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "System B Entry"
        verbose_name_plural = "System B Entries"
        ordering = ['source_row_number']
        constraints = [
            models.UniqueConstraint(fields=['source_file', 'source_row_number'], name='unique_system_b_source_row')
        ]

    def __str__(self):
        return f"{self.entry_id or self.record_ref} (row {self.source_row_number})"
