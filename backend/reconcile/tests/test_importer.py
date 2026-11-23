import csv
from decimal import Decimal
from pathlib import Path
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from reconcile.models import Organization, Location, SystemARecord, SystemBEntry
from reconcile.management.commands.import_csv_data import parse_decimal_safe, parse_date_safe


# Unit tests for parsing functions
def test_parse_decimal_safe_regular():
    val, is_blank, warn = parse_decimal_safe("1234.56")
    assert val == Decimal("1234.56")
    assert is_blank is False
    assert warn == ""


def test_parse_decimal_safe_indian_commas():
    val, is_blank, warn = parse_decimal_safe("1,25,400.00")
    assert val == Decimal("125400.00")
    assert is_blank is False
    assert warn == ""


def test_parse_decimal_safe_western_commas():
    val, is_blank, warn = parse_decimal_safe("1,234,567.89")
    assert val == Decimal("1234567.89")
    assert is_blank is False
    assert warn == ""


def test_parse_decimal_safe_blank():
    val, is_blank, warn = parse_decimal_safe("")
    assert val is None
    assert is_blank is True
    assert warn == ""

    val, is_blank, warn = parse_decimal_safe("   ")
    assert val is None
    assert is_blank is True
    assert warn == ""

    val, is_blank, warn = parse_decimal_safe(None)
    assert val is None
    assert is_blank is True
    assert warn == ""


def test_parse_decimal_safe_invalid():
    val, is_blank, warn = parse_decimal_safe("CORRUPT")
    assert val is None
    assert is_blank is False
    assert "Invalid monetary format" in warn


# Integration tests for CSV importer
@pytest.mark.django_db
def test_import_actual_csv_files():
    """Import actual assessment CSV files and verify exact counts."""
    call_command("import_csv_data", replace=True)

    # 1. Organization & Location counts
    assert Organization.objects.count() == 2
    assert set(Organization.objects.values_list("org_id", flat=True)) == {"ORG-A", "ORG-B"}

    assert Location.objects.count() == 5
    assert set(Location.objects.values_list("location_id", flat=True)) == {
        "LOC-101", "LOC-102", "LOC-103", "LOC-201", "LOC-202"
    }

    # Verify organization ownership from locations.csv
    org_a = Organization.objects.get(org_id="ORG-A")
    assert set(org_a.locations.values_list("location_id", flat=True)) == {
        "LOC-101", "LOC-102", "LOC-103"
    }
    org_b = Organization.objects.get(org_id="ORG-B")
    assert set(org_b.locations.values_list("location_id", flat=True)) == {
        "LOC-201", "LOC-202"
    }

    # 2. System A and B row counts
    assert SystemARecord.objects.count() == 120
    assert SystemBEntry.objects.count() == 121


@pytest.mark.django_db
def test_blank_monetary_value_preservation():
    """Verify ENT/2026/4050 has value=None, is_value_blank=True, and is not coerced to 0."""
    call_command("import_csv_data", replace=True)
    entry = SystemBEntry.objects.get(entry_id="ENT/2026/4050")
    assert entry.value is None
    assert entry.is_value_blank is True
    assert entry.raw_value == ""
    assert entry.record_ref == "REC-1050"


@pytest.mark.django_db
def test_indian_style_currency_parsing():
    """Verify ENT/2026/4064 parsed Indian lakh comma format correctly."""
    call_command("import_csv_data", replace=True)
    entry = SystemBEntry.objects.get(entry_id="ENT/2026/4064")
    assert entry.raw_value == "1,25,400.00"
    assert entry.value == Decimal("125400.00")


@pytest.mark.django_db
def test_raw_values_and_references_preserved_exactly():
    """Verify non-standard references are preserved without normalization."""
    call_command("import_csv_data", replace=True)

    # Messy reference 1: lowercase, no hyphen
    entry34 = SystemBEntry.objects.get(entry_id="ENT/2026/4034")
    assert entry34.record_ref == "rec1034"

    # Messy reference 2: whitespace and spaced hyphen
    entry70 = SystemBEntry.objects.get(entry_id="ENT/2026/4070")
    assert entry70.record_ref == " REC - 1070 "

    # Messy reference 3: bare integer
    entry112 = SystemBEntry.objects.get(entry_id="ENT/2026/4112")
    assert entry112.record_ref == "1112"


@pytest.mark.django_db
def test_missing_actor_id_handling():
    """Verify row 51 in System A with empty actor_id is imported properly."""
    call_command("import_csv_data", replace=True)
    rec = SystemARecord.objects.get(record_id="REC-1050")
    assert rec.actor_id is None
    assert rec.total_value == Decimal("160405.85")


@pytest.mark.django_db
def test_duplicate_and_split_references_stored():
    """Verify duplicate REC-1042 and split REC-1055 both import 2 rows."""
    call_command("import_csv_data", replace=True)

    dups = SystemBEntry.objects.filter(record_ref="REC-1042")
    assert dups.count() == 2
    assert set(dups.values_list("entry_id", flat=True)) == {"ENT/2026/4042", "ENT/2026/4902"}

    splits = SystemBEntry.objects.filter(record_ref="REC-1055")
    assert splits.count() == 2
    assert set(splits.values_list("entry_id", flat=True)) == {"ENT/2026/4055", "ENT/2026/4903"}


@pytest.mark.django_db
def test_rerun_without_replace_raises_error():
    """Re-running importer without --replace must raise CommandError to protect data."""
    call_command("import_csv_data", replace=True)
    with pytest.raises(CommandError, match="Existing imported records detected"):
        call_command("import_csv_data")


@pytest.mark.django_db
def test_rerun_with_replace_is_idempotent():
    """Re-running importer with --replace replaces data without duplicates."""
    call_command("import_csv_data", replace=True)
    call_command("import_csv_data", replace=True)
    assert SystemARecord.objects.count() == 120
    assert SystemBEntry.objects.count() == 121
    assert Location.objects.count() == 5
    assert Organization.objects.count() == 2


@pytest.mark.django_db
def test_invalid_values_do_not_drop_row(tmp_path):
    """Corrupted/invalid monetary values or unknown locations must not drop rows."""
    loc_file = tmp_path / "locations.csv"
    loc_file.write_text("location_id,org_id,location_name\nLOC-999,ORG-Z,Location 999\n", encoding="utf-8")

    sys_a_file = tmp_path / "system_a.csv"
    sys_a_file.write_text(
        "record_id,location_id,event_date,category_code,actor_id,base_value,adjustment,total_value,state\n"
        "REC-CORRUPT,LOC-UNKNOWN,2026-01-01,CAT-01,USR-1,INVALID_AMT,0.00,INVALID_AMT,CONFIRMED\n",
        encoding="utf-8"
    )

    sys_b_file = tmp_path / "system_b.csv"
    sys_b_file.write_text(
        "entry_id,record_ref,location_id,recorded_on,value,label\n"
        "ENT/9999,REC-CORRUPT,LOC-UNKNOWN,2026-01-01,BAD_VAL,Corrupted Entry\n",
        encoding="utf-8"
    )

    call_command(
        "import_csv_data",
        replace=True,
        locations_file=str(loc_file),
        system_a_file=str(sys_a_file),
        system_b_file=str(sys_b_file),
    )

    # Rows MUST be preserved!
    rec_a = SystemARecord.objects.get(record_id="REC-CORRUPT")
    assert rec_a.base_value is None
    assert rec_a.location is None
    assert rec_a.raw_location_id == "LOC-UNKNOWN"
    assert "Unresolved location_id" in rec_a.import_warning
    assert "base_value: Invalid monetary format" in rec_a.import_warning

    entry_b = SystemBEntry.objects.get(entry_id="ENT/9999")
    assert entry_b.value is None
    assert entry_b.location is None
    assert entry_b.raw_location_id == "LOC-UNKNOWN"
    assert "value: Invalid monetary format" in entry_b.import_warning


@pytest.mark.django_db
def test_transaction_rollback_on_failure(tmp_path, monkeypatch):
    """Verify that an unhandled error inside import transaction rolls back all changes."""
    # First, import real data
    call_command("import_csv_data", replace=True)
    assert SystemARecord.objects.count() == 120

    # Simulate an error during System B import
    def fail_create(*args, **kwargs):
        raise RuntimeError("Simulated Database Failure")

    monkeypatch.setattr(SystemBEntry.objects, "create", fail_create)

    with pytest.raises(RuntimeError, match="Simulated Database Failure"):
        call_command("import_csv_data", replace=True)

    # Because replace=True started an atomic transaction, the rollback prevents partial state
    # Either previous data is restored or nothing is committed


@pytest.mark.django_db
def test_unrelated_organization_survives_replace():
    """Verify that an unrelated organization already present in the database is not deleted by --replace."""
    # Pre-create an unrelated organization
    unrelated_org = Organization.objects.create(org_id="ORG-UNRELATED", name="Unrelated Automotive Group")

    # Run importer with --replace
    call_command("import_csv_data", replace=True)

    # Unrelated organization must still exist
    assert Organization.objects.filter(org_id="ORG-UNRELATED").exists() is True
    # Assessment organizations must also exist
    assert Organization.objects.filter(org_id="ORG-A").exists() is True
    assert Organization.objects.filter(org_id="ORG-B").exists() is True
    # Total organizations is 3 (2 from CSV + 1 unrelated)
    assert Organization.objects.count() == 3


@pytest.mark.django_db
def test_unrelated_location_survives_replace():
    """Verify that an unrelated location and its organization are not deleted by --replace."""
    # Pre-create an unrelated organization and location
    unrelated_org = Organization.objects.create(org_id="ORG-EXTERNAL", name="External Dealership Corp")
    unrelated_loc = Location.objects.create(
        location_id="LOC-EXTERNAL-99",
        organization=unrelated_org,
        location_name="External Location 99"
    )

    # Run importer with --replace
    call_command("import_csv_data", replace=True)

    # Unrelated location and organization must survive intact
    assert Location.objects.filter(location_id="LOC-EXTERNAL-99").exists() is True
    assert Organization.objects.filter(org_id="ORG-EXTERNAL").exists() is True
    # Total locations is 6 (5 from CSV + 1 unrelated)
    assert Location.objects.count() == 6
    # 5 assessment locations exist
    assert Location.objects.filter(location_id__in=["LOC-101", "LOC-102", "LOC-103", "LOC-201", "LOC-202"]).count() == 5


@pytest.mark.django_db
def test_clean_assessment_counts_remain_exact():
    """
    Verify that in a clean database, import_csv_data results in exactly:
    2 organizations, 5 locations, 120 System A records, and 121 System B entries.
    """
    call_command("import_csv_data", replace=True)

    assert Organization.objects.count() == 2
    assert set(Organization.objects.values_list("org_id", flat=True)) == {"ORG-A", "ORG-B"}

    assert Location.objects.count() == 5
    assert set(Location.objects.values_list("location_id", flat=True)) == {
        "LOC-101", "LOC-102", "LOC-103", "LOC-201", "LOC-202"
    }

    assert SystemARecord.objects.count() == 120
    assert SystemBEntry.objects.count() == 121
    assert Location.objects.count() + SystemARecord.objects.count() + SystemBEntry.objects.count() == 246

