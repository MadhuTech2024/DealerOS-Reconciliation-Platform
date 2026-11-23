import pytest
from decimal import Decimal
from django.db import IntegrityError
from reconcile.models import Organization, Location, SystemARecord, SystemBEntry


@pytest.mark.django_db
def test_organization_and_location_creation():
    org = Organization.objects.create(org_id="ORG-TEST", name="Test Org")
    assert str(org) == "ORG-TEST"

    loc = Location.objects.create(
        location_id="LOC-TEST-1",
        organization=org,
        location_name="Test Location 1",
        raw_data={"location_id": "LOC-TEST-1", "org_id": "ORG-TEST"}
    )
    assert str(loc) == "LOC-TEST-1 (ORG-TEST)"
    assert loc.organization == org
    assert org.locations.count() == 1


@pytest.mark.django_db
def test_system_a_record_fields_and_constraints():
    org = Organization.objects.create(org_id="ORG-TEST")
    loc = Location.objects.create(location_id="LOC-TEST-1", organization=org, location_name="Loc 1")

    rec = SystemARecord.objects.create(
        source_file="test_a.csv",
        source_row_number=2,
        record_id="REC-9001",
        raw_location_id="LOC-TEST-1",
        location=loc,
        actor_id=None,  # Missing actor supported
        base_value=Decimal("100.50"),
        adjustment=Decimal("20.25"),
        total_value=Decimal("120.75"),
        state="CONFIRMED",
        raw_data={"record_id": "REC-9001", "actor_id": ""}
    )
    assert rec.actor_id is None
    assert rec.total_value == Decimal("120.75")
    assert isinstance(rec.total_value, Decimal)

    # Unique constraint on (source_file, source_row_number)
    with pytest.raises(IntegrityError):
        SystemARecord.objects.create(
            source_file="test_a.csv",
            source_row_number=2,
            record_id="REC-9002",
        )


@pytest.mark.django_db
def test_system_b_entry_duplicate_references_allowed():
    """
    Ensure System B allows duplicate and split business references (record_ref)
    without raising an IntegrityError.
    """
    entry1 = SystemBEntry.objects.create(
        source_file="test_b.csv",
        source_row_number=2,
        entry_id="ENT/TEST/01",
        record_ref="REC-DUPLICATE",
        raw_value="100.00",
        value=Decimal("100.00"),
        is_value_blank=False,
    )
    entry2 = SystemBEntry.objects.create(
        source_file="test_b.csv",
        source_row_number=3,
        entry_id="ENT/TEST/02",
        record_ref="REC-DUPLICATE",  # Same business reference
        raw_value="100.00",
        value=Decimal("100.00"),
        is_value_blank=False,
    )
    assert entry1.record_ref == entry2.record_ref
    assert SystemBEntry.objects.filter(record_ref="REC-DUPLICATE").count() == 2


@pytest.mark.django_db
def test_system_b_blank_monetary_value():
    """Blank monetary values must remain None, never coerced to 0.00."""
    entry = SystemBEntry.objects.create(
        source_file="test_b.csv",
        source_row_number=4,
        entry_id="ENT/TEST/03",
        record_ref="REC-BLANK",
        raw_value="",
        value=None,
        is_value_blank=True,
    )
    assert entry.value is None
    assert entry.is_value_blank is True
    assert entry.raw_value == ""
