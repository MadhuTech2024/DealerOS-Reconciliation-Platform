import csv
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from reconcile.models import ImportIssue, Organization, Location, SystemARecord, SystemBEntry


def parse_decimal_safe(raw_str: str | None) -> tuple[Decimal | None, bool, str]:
    """
    Safely parse monetary value into Decimal with 2 decimal places.
    Handles Indian-style commas (e.g. '1,25,400.00' -> Decimal('125400.00')).
    Preserves blank values as None (never coerces to 0.00).

    Returns:
        (parsed_decimal, is_blank, warning_message)
    """
    if raw_str is None:
        return None, True, ""
    cleaned = str(raw_str).strip()
    if cleaned == "":
        return None, True, ""

    # Remove commas (handles both Western 100,000.00 and Indian 1,25,400.00)
    sanitized = cleaned.replace(",", "")
    try:
        val = Decimal(sanitized)
        if not val.is_finite():
            return None, False, f"Invalid monetary format: '{raw_str}'"
        val = val.quantize(Decimal("0.01"))
        if val.copy_abs() >= Decimal("1000000000000"):
            return None, False, f"Monetary value exceeds supported precision: '{raw_str}'"
        return val, False, ""
    except (InvalidOperation, ValueError):
        return None, False, f"Invalid monetary format: '{raw_str}'"


def parse_date_safe(raw_str: str | None) -> tuple[date | None, str]:
    """
    Safely parse date string into date object.
    Supports YYYY-MM-DD.
    """
    if raw_str is None or not str(raw_str).strip():
        return None, ""
    cleaned = str(raw_str).strip()
    try:
        return datetime.strptime(cleaned, "%Y-%m-%d").date(), ""
    except (TypeError, ValueError):
        return None, f"Invalid date format: '{raw_str}'"


def _clean_cell(row, key: str) -> str:
    """Return a trimmed CSV cell string, treating missing cells as blank."""
    value = row.get(key)
    return str(value).strip() if value is not None else ""


class Command(BaseCommand):
    help = "Imports locations, System A records, and System B entries from CSV files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Replace existing imported records. Safe re-run requires this flag.",
        )
        parser.add_argument(
            "--data-dir",
            type=str,
            default=None,
            help="Directory containing the CSV files. Defaults to backend/data/.",
        )
        parser.add_argument(
            "--locations-file",
            type=str,
            default=None,
            help="Custom path to locations.csv.",
        )
        parser.add_argument(
            "--system-a-file",
            type=str,
            default=None,
            help="Custom path to system_a.csv.",
        )
        parser.add_argument(
            "--system-b-file",
            type=str,
            default=None,
            help="Custom path to system_b.csv.",
        )

    def handle(self, *args, **options):
        replace = options["replace"]
        base_dir = Path(__file__).resolve().parent.parent.parent.parent

        data_dir = Path(options["data_dir"]) if options["data_dir"] else base_dir / "data"
        locations_path = Path(options["locations_file"]) if options["locations_file"] else data_dir / "locations.csv"
        system_a_path = Path(options["system_a_file"]) if options["system_a_file"] else data_dir / "system_a.csv"
        system_b_path = Path(options["system_b_file"]) if options["system_b_file"] else data_dir / "system_b.csv"

        # Validate that files exist
        for path in [locations_path, system_a_path, system_b_path]:
            if not path.is_file():
                raise CommandError(f"Required CSV file not found: {path}")

        system_a_filename = system_a_path.name
        system_b_filename = system_b_path.name
        locations_filename = locations_path.name

        # Check existing data to prevent accidental duplicate imports
        has_existing_a = SystemARecord.objects.filter(source_file=system_a_filename).exists()
        has_existing_b = SystemBEntry.objects.filter(source_file=system_b_filename).exists()

        if (has_existing_a or has_existing_b) and not replace:
            raise CommandError(
                "Existing imported records detected in the database. "
                "To replace existing records, re-run with the '--replace' option: "
                "python manage.py import_csv_data --replace"
            )

        self.stdout.write(self.style.NOTICE("Beginning atomic CSV import process..."))

        # Tracking metrics
        stats = {
            "organizations_created": 0,
            "locations_imported": 0,
            "locations_updated": 0,
            "location_issues": 0,
            "location_rows_retained": 0,
            "system_a_imported": 0,
            "system_b_imported": 0,
            "blank_monetary_values": 0,
            "unparseable_monetary_values": 0,
            "unresolved_locations": 0,
            "warnings": [],
        }

        # Atomic transaction wraps the entire import
        with transaction.atomic():
            if replace:
                self.stdout.write("Replacing existing data for target source files...")
                SystemARecord.objects.filter(source_file=system_a_filename).delete()
                SystemBEntry.objects.filter(source_file=system_b_filename).delete()
                ImportIssue.objects.filter(source_file=locations_filename).delete()
                # Keep locations and organizations in place so independently sourced
                # records retain their foreign-key relationships during replacement.

            # 1. Import locations.csv
            self.stdout.write(f"Reading locations from: {locations_path.name}")
            with open(locations_path, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                seen_location_ids = set()
                for line_idx, row in enumerate(reader, start=2):
                    stats["location_rows_retained"] += 1
                    loc_id = _clean_cell(row, "location_id")
                    org_id = _clean_cell(row, "org_id")
                    loc_name = _clean_cell(row, "location_name")
                    row_issues = []
                    missing_cells = [
                        column for column in (reader.fieldnames or [])
                        if row.get(column) is None
                    ]
                    if missing_cells:
                        row_issues.append(
                            f"missing CSV cell(s): {', '.join(missing_cells)}"
                        )

                    if not loc_id or not org_id:
                        row_issues.append("missing location_id or org_id")
                    elif loc_id in seen_location_ids:
                        row_issues.append(f"duplicate location_id '{loc_id}'")
                    else:
                        seen_location_ids.add(loc_id)

                    if row_issues:
                        warn = (
                            f"Row {line_idx} in {locations_path.name}: "
                            + "; ".join(row_issues)
                        )
                        stats["warnings"].append(warn)
                        ImportIssue.objects.update_or_create(
                            source_file=locations_filename,
                            source_row_number=line_idx,
                            defaults={"raw_data": dict(row), "message": warn},
                        )
                        stats["location_issues"] += 1

                    if not loc_id or not org_id or any(
                        issue.startswith("duplicate location_id") for issue in row_issues
                    ):
                        continue

                    # Organization ownership comes exclusively from locations.csv.
                    org, org_created = Organization.objects.get_or_create(
                        org_id=org_id,
                        defaults={"name": org_id},
                    )
                    if org_created:
                        stats["organizations_created"] += 1

                    location_defaults = {
                        "organization": org,
                        "location_name": loc_name,
                        "raw_data": dict(row),
                    }
                    if "location_name" in missing_cells:
                        location, created = Location.objects.get_or_create(
                            location_id=loc_id,
                            defaults=location_defaults,
                        )
                        if not created:
                            location.organization = org
                            location.raw_data = dict(row)
                            location.save(update_fields=["organization", "raw_data", "updated_at"])
                    else:
                        _, created = Location.objects.update_or_create(
                            location_id=loc_id,
                            defaults=location_defaults,
                        )
                    if created:
                        stats["locations_imported"] += 1
                    else:
                        stats["locations_updated"] += 1

            # Cache location map
            location_map = {loc.location_id: loc for loc in Location.objects.all()}

            # 2. Import system_a.csv
            self.stdout.write(f"Reading System A records from: {system_a_path.name}")
            with open(system_a_path, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for line_idx, row in enumerate(reader, start=2):
                    record_id = _clean_cell(row, "record_id")
                    raw_loc_id = _clean_cell(row, "location_id")
                    loc_obj = location_map.get(raw_loc_id)
                    row_warnings = []

                    if not record_id:
                        row_warnings.append("Missing record_id")
                    if not loc_obj:
                        stats["unresolved_locations"] += 1
                        row_warnings.append(f"Unresolved location_id: '{raw_loc_id}'")

                    event_date, date_warn = parse_date_safe(row.get("event_date"))
                    if date_warn:
                        row_warnings.append(date_warn)

                    base_val, base_blank, base_warn = parse_decimal_safe(row.get("base_value"))
                    if base_blank:
                        stats["blank_monetary_values"] += 1
                    if base_warn:
                        stats["unparseable_monetary_values"] += 1
                        row_warnings.append(f"base_value: {base_warn}")

                    adj_val, adj_blank, adj_warn = parse_decimal_safe(row.get("adjustment"))
                    if adj_blank:
                        stats["blank_monetary_values"] += 1
                    if adj_warn:
                        stats["unparseable_monetary_values"] += 1
                        row_warnings.append(f"adjustment: {adj_warn}")

                    tot_val, tot_blank, tot_warn = parse_decimal_safe(row.get("total_value"))
                    if tot_blank:
                        stats["blank_monetary_values"] += 1
                    if tot_warn:
                        stats["unparseable_monetary_values"] += 1
                        row_warnings.append(f"total_value: {tot_warn}")

                    actor_id = _clean_cell(row, "actor_id") or None

                    combined_warning = "; ".join(row_warnings)
                    if combined_warning:
                        stats["warnings"].append(f"System A line {line_idx} ({record_id}): {combined_warning}")

                    SystemARecord.objects.create(
                        source_file=system_a_filename,
                        source_row_number=line_idx,
                        record_id=record_id,
                        raw_location_id=raw_loc_id,
                        location=loc_obj,
                        event_date=event_date,
                        category_code=_clean_cell(row, "category_code"),
                        actor_id=actor_id,
                        base_value=base_val,
                        adjustment=adj_val,
                        total_value=tot_val,
                        state=_clean_cell(row, "state"),
                        raw_data=dict(row),
                        import_warning=combined_warning,
                    )
                    stats["system_a_imported"] += 1

            # 3. Import system_b.csv
            self.stdout.write(f"Reading System B entries from: {system_b_path.name}")
            with open(system_b_path, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for line_idx, row in enumerate(reader, start=2):
                    entry_id = _clean_cell(row, "entry_id")
                    raw_reference = row.get("record_ref")
                    raw_record_ref = str(raw_reference) if raw_reference is not None else ""
                    raw_loc_id = _clean_cell(row, "location_id")
                    loc_obj = location_map.get(raw_loc_id)
                    row_warnings = []

                    if not entry_id:
                        row_warnings.append("Missing entry_id")
                    if not raw_record_ref.strip():
                        row_warnings.append("Missing record_ref")
                    if not loc_obj:
                        stats["unresolved_locations"] += 1
                        row_warnings.append(f"Unresolved location_id: '{raw_loc_id}'")

                    rec_date, date_warn = parse_date_safe(row.get("recorded_on"))
                    if date_warn:
                        row_warnings.append(date_warn)

                    raw_value = row.get("value")
                    raw_val_str = str(raw_value) if raw_value is not None else ""
                    parsed_val, is_blank, val_warn = parse_decimal_safe(raw_val_str)
                    if is_blank:
                        stats["blank_monetary_values"] += 1
                    if val_warn:
                        stats["unparseable_monetary_values"] += 1
                        row_warnings.append(f"value: {val_warn}")

                    combined_warning = "; ".join(row_warnings)
                    if combined_warning:
                        stats["warnings"].append(f"System B line {line_idx} ({entry_id}): {combined_warning}")

                    SystemBEntry.objects.create(
                        source_file=system_b_filename,
                        source_row_number=line_idx,
                        entry_id=entry_id,
                        record_ref=raw_record_ref,  # Preserves raw formatting exactly
                        raw_location_id=raw_loc_id,
                        location=loc_obj,
                        recorded_on=rec_date,
                        raw_value=raw_val_str,
                        value=parsed_val,
                        is_value_blank=is_blank,
                        label=_clean_cell(row, "label"),
                        raw_data=dict(row),
                        import_warning=combined_warning,
                    )
                    stats["system_b_imported"] += 1

        # Print import summary
        self.stdout.write(self.style.SUCCESS("\n=========================================="))
        self.stdout.write(self.style.SUCCESS("       CSV IMPORT SUMMARY REPORT          "))
        self.stdout.write(self.style.SUCCESS("=========================================="))
        self.stdout.write(f"Organizations created:               {stats['organizations_created']}")
        self.stdout.write(f"Locations imported:                  {stats['locations_imported']}")
        self.stdout.write(f"Locations updated:                   {stats['locations_updated']}")
        self.stdout.write(f"Malformed/duplicate location rows:   {stats['location_issues']}")
        self.stdout.write(f"System A records imported:           {stats['system_a_imported']}")
        self.stdout.write(f"System B entries imported:           {stats['system_b_imported']}")
        retained_rows = (
            stats["location_rows_retained"]
            + stats["system_a_imported"]
            + stats["system_b_imported"]
        )
        self.stdout.write(f"Total source rows retained:          {retained_rows}")
        self.stdout.write(f"Blank monetary values detected:      {stats['blank_monetary_values']}")
        self.stdout.write(f"Unparseable monetary values:         {stats['unparseable_monetary_values']}")
        self.stdout.write(f"Unresolved location references:      {stats['unresolved_locations']}")
        self.stdout.write(f"Total import warnings:               {len(stats['warnings'])}")

        if stats["warnings"]:
            self.stdout.write(self.style.WARNING("\nImport Warnings Details:"))
            for w in stats["warnings"][:10]:
                self.stdout.write(self.style.WARNING(f"  - {w}"))
            if len(stats["warnings"]) > 10:
                self.stdout.write(self.style.WARNING(f"  ... and {len(stats['warnings']) - 10} more warnings"))

        self.stdout.write(self.style.SUCCESS("==========================================\n"))
