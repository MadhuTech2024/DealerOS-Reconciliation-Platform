from datetime import date
from decimal import Decimal
import pytest

from django.db import connection
from django.test.utils import CaptureQueriesContext

from reconcile.engine import (
    ReconciliationEngine,
    DisagreementType,
    ReconciliationItem,
)


# ============================================================================
# PURE IN-MEMORY UNIT TESTS (Decoupled from Database)
# ============================================================================

def test_pure_matched_record():
    sys_a = [{
        "record_id": "REC-0001",
        "location_id": "LOC-1",
        "event_date": date(2026, 3, 1),
        "base_value": Decimal("100.00"),
        "adjustment": Decimal("10.00"),
        "total_value": Decimal("110.00"),
        "state": "CONFIRMED",
    }]
    sys_b = [{
        "entry_id": "ENT/01",
        "record_ref": "REC-0001",
        "location_id": "LOC-1",
        "recorded_on": date(2026, 3, 1),
        "value": Decimal("110.00"),
    }]
    loc_map = {"LOC-1": "ORG-1"}

    report = ReconciliationEngine.reconcile(sys_a, sys_b, loc_map)
    assert report.summary.matched_count == 1
    assert report.items[0].disagreement_type == DisagreementType.MATCHED
    assert report.items[0].difference == Decimal("0.00")


def test_pure_missing_in_b():
    sys_a = [{
        "record_id": "REC-0001",
        "location_id": "LOC-1",
        "event_date": date(2026, 3, 1),
        "total_value": Decimal("250.00"),
        "state": "CONFIRMED",
    }]
    sys_b = []
    loc_map = {"LOC-1": "ORG-1"}

    report = ReconciliationEngine.reconcile(sys_a, sys_b, loc_map)
    assert report.summary.missing_in_b_count == 1
    item = report.items[0]
    assert item.disagreement_type == DisagreementType.MISSING_IN_B
    assert item.system_a_expected_amount == Decimal("250.00")
    assert item.system_b_recorded_amount is None
    assert item.difference == Decimal("-250.00")


def test_pure_orphan_in_b():
    sys_a = []
    sys_b = [{
        "entry_id": "ENT/01",
        "record_ref": "REC-9999",
        "location_id": "LOC-1",
        "recorded_on": date(2026, 3, 1),
        "value": Decimal("300.00"),
    }]
    loc_map = {"LOC-1": "ORG-1"}

    report = ReconciliationEngine.reconcile(sys_a, sys_b, loc_map)
    assert report.summary.orphan_in_b_count == 1
    item = report.items[0]
    assert item.disagreement_type == DisagreementType.ORPHAN_IN_B
    assert item.system_a_expected_amount is None
    assert item.system_b_recorded_amount == Decimal("300.00")
    assert item.difference == Decimal("300.00")


def test_pure_orphan_group_with_all_blank_values_has_unknown_total():
    sys_b = [
        {"entry_id": "ENT/01", "record_ref": "REC-9999", "location_id": "LOC-1", "value": None},
        {"entry_id": "ENT/02", "record_ref": "REC-9999", "location_id": "LOC-1", "value": None},
    ]

    report = ReconciliationEngine.reconcile([], sys_b, {"LOC-1": "ORG-1"})

    item = report.items[0]
    assert item.disagreement_type == DisagreementType.ORPHAN_IN_B
    assert item.system_b_recorded_amount is None
    assert item.difference is None
    assert item.is_value_blank is True
    assert [detail["value"] for detail in item.details] == [None, None]


def test_pure_orphan_group_with_any_blank_value_keeps_total_unknown():
    sys_b = [
        {"entry_id": "ENT/01", "record_ref": "REC-9999", "location_id": "LOC-1", "value": Decimal("25.00")},
        {"entry_id": "ENT/02", "record_ref": "REC-9999", "location_id": "LOC-1", "value": None},
    ]

    report = ReconciliationEngine.reconcile([], sys_b, {"LOC-1": "ORG-1"})

    item = report.items[0]
    assert item.system_b_recorded_amount is None
    assert item.difference is None
    assert item.is_value_blank is True
    assert [detail["value"] for detail in item.details] == ["25.00", None]


def test_pure_valid_split_vs_duplicate():
    # 1. Valid split
    sys_a = [{
        "record_id": "REC-0001",
        "location_id": "LOC-1",
        "event_date": date(2026, 3, 1),
        "total_value": Decimal("100.00"),
        "state": "CONFIRMED",
    }]
    sys_b_split = [
        {"entry_id": "ENT/01", "record_ref": "REC-0001", "location_id": "LOC-1", "value": Decimal("60.00"), "recorded_on": date(2026, 3, 1)},
        {"entry_id": "ENT/02", "record_ref": "REC-0001", "location_id": "LOC-1", "value": Decimal("40.00"), "recorded_on": date(2026, 3, 1)},
    ]
    loc_map = {"LOC-1": "ORG-1"}

    report_split = ReconciliationEngine.reconcile(sys_a, sys_b_split, loc_map)
    assert report_split.summary.valid_split_count == 1
    assert report_split.summary.duplicate_in_b_count == 0
    assert report_split.items[0].disagreement_type == DisagreementType.VALID_SPLIT
    assert report_split.items[0].difference == Decimal("0.00")

    # 2. Duplicate (sum != total_value)
    sys_b_dup = [
        {"entry_id": "ENT/01", "record_ref": "REC-0001", "location_id": "LOC-1", "value": Decimal("100.00"), "recorded_on": date(2026, 3, 1)},
        {"entry_id": "ENT/02", "record_ref": "REC-0001", "location_id": "LOC-1", "value": Decimal("100.00"), "recorded_on": date(2026, 3, 1)},
    ]
    report_dup = ReconciliationEngine.reconcile(sys_a, sys_b_dup, loc_map)
    assert report_dup.summary.duplicate_in_b_count == 1
    assert report_dup.summary.valid_split_count == 0
    assert report_dup.items[0].disagreement_type == DisagreementType.DUPLICATE_IN_B
    assert report_dup.items[0].difference == Decimal("100.00")


def test_pure_base_value_recorded():
    sys_a = [{
        "record_id": "REC-0001",
        "location_id": "LOC-1",
        "event_date": date(2026, 3, 1),
        "base_value": Decimal("80.00"),
        "adjustment": Decimal("20.00"),
        "total_value": Decimal("100.00"),
        "state": "CONFIRMED",
    }]
    sys_b = [{
        "entry_id": "ENT/01",
        "record_ref": "REC-0001",
        "location_id": "LOC-1",
        "recorded_on": date(2026, 3, 1),
        "value": Decimal("80.00"),  # Recorded base_value!
    }]
    loc_map = {"LOC-1": "ORG-1"}

    report = ReconciliationEngine.reconcile(sys_a, sys_b, loc_map)
    assert report.summary.base_value_recorded_count == 1
    item = report.items[0]
    assert item.disagreement_type == DisagreementType.BASE_VALUE_RECORDED
    assert item.is_base_value_mismatch is True
    assert item.difference == Decimal("-20.00")
    assert "omitting adjustment of 20.00" in item.message


def test_pure_blank_monetary_value():
    sys_a = [{
        "record_id": "REC-0001",
        "location_id": "LOC-1",
        "event_date": date(2026, 3, 1),
        "total_value": Decimal("500.00"),
        "state": "CONFIRMED",
    }]
    sys_b = [{
        "entry_id": "ENT/01",
        "record_ref": "REC-0001",
        "location_id": "LOC-1",
        "recorded_on": date(2026, 3, 1),
        "value": None,
        "is_value_blank": True,
    }]
    loc_map = {"LOC-1": "ORG-1"}

    report = ReconciliationEngine.reconcile(sys_a, sys_b, loc_map)
    assert report.summary.blank_value_count == 1
    item = report.items[0]
    assert item.disagreement_type == DisagreementType.BLANK_VALUE
    assert item.is_value_blank is True
    assert item.system_b_recorded_amount is None
    assert item.difference is None  # Cannot compute numeric difference against blank


def test_pure_voided_record():
    sys_a = [{
        "record_id": "REC-0001",
        "location_id": "LOC-1",
        "event_date": date(2026, 3, 1),
        "total_value": Decimal("50.00"),
        "state": "VOIDED",
    }]
    sys_b = [{
        "entry_id": "ENT/01",
        "record_ref": "REC-0001",
        "location_id": "LOC-1",
        "recorded_on": date(2026, 3, 1),
        "value": Decimal("50.00"),
    }]
    loc_map = {"LOC-1": "ORG-1"}

    report = ReconciliationEngine.reconcile(sys_a, sys_b, loc_map)
    assert report.summary.voided_record_count == 1
    item = report.items[0]
    assert item.disagreement_type == DisagreementType.VOIDED_RECORD
    assert item.is_voided is True


def test_pure_voided_record_precedes_blank_and_multiple_entry_classification():
    sys_a = [{
        "record_id": "REC-0001",
        "location_id": "LOC-1",
        "event_date": date(2026, 3, 1),
        "total_value": Decimal("50.00"),
        "state": "VOIDED",
    }]
    sys_b = [
        {"entry_id": "ENT/01", "record_ref": "REC-0001", "location_id": "LOC-1", "value": Decimal("25.00")},
        {"entry_id": "ENT/02", "record_ref": "REC-0001", "location_id": "LOC-1", "value": None},
    ]

    report = ReconciliationEngine.reconcile(sys_a, sys_b, {"LOC-1": "ORG-1"})

    item = report.items[0]
    assert item.disagreement_type == DisagreementType.VOIDED_RECORD
    assert item.is_voided is True
    assert item.is_value_blank is True
    assert item.system_b_recorded_amount is None
    assert item.difference is None
    assert len(item.system_b_entry_ids) == 2


def test_pure_voided_record_without_b_entries_remains_missing():
    sys_a = [{
        "record_id": "REC-0001",
        "location_id": "LOC-1",
        "event_date": date(2026, 3, 1),
        "total_value": Decimal("50.00"),
        "state": "VOIDED",
    }]

    report = ReconciliationEngine.reconcile(sys_a, [], {"LOC-1": "ORG-1"})

    item = report.items[0]
    assert item.disagreement_type == DisagreementType.MISSING_IN_B
    assert item.is_voided is True


def test_pure_date_and_location_mismatches():
    # Location mismatch
    sys_a = [{
        "record_id": "REC-0001",
        "location_id": "LOC-1",
        "event_date": date(2026, 3, 1),
        "total_value": Decimal("100.00"),
        "state": "CONFIRMED",
    }]
    sys_b_loc = [{
        "entry_id": "ENT/01",
        "record_ref": "REC-0001",
        "location_id": "LOC-2",
        "recorded_on": date(2026, 3, 1),
        "value": Decimal("100.00"),
    }]
    loc_map = {"LOC-1": "ORG-A", "LOC-2": "ORG-B"}

    report_loc = ReconciliationEngine.reconcile(sys_a, sys_b_loc, loc_map)
    assert report_loc.items[0].disagreement_type == DisagreementType.LOCATION_MISMATCH
    assert report_loc.items[0].has_location_mismatch is True

    same_tenant_map = {"LOC-1": "ORG-A", "LOC-2": "ORG-A"}
    report_same_tenant = ReconciliationEngine.reconcile(sys_a, sys_b_loc, same_tenant_map)
    assert report_same_tenant.items[0].disagreement_type == DisagreementType.LOCATION_MISMATCH
    assert report_same_tenant.items[0].organization_id_a == "ORG-A"
    assert report_same_tenant.items[0].organization_id_b == "ORG-A"

    sys_b_both_mismatches = [{
        "entry_id": "ENT/01",
        "record_ref": "REC-0001",
        "location_id": "LOC-2",
        "recorded_on": date(2026, 3, 5),
        "value": Decimal("100.00"),
    }]
    report_both = ReconciliationEngine.reconcile(sys_a, sys_b_both_mismatches, same_tenant_map)
    assert report_both.items[0].disagreement_type == DisagreementType.LOCATION_MISMATCH
    assert report_both.items[0].has_location_mismatch is True
    assert report_both.items[0].has_date_mismatch is True

    # Date mismatch
    sys_b_date = [{
        "entry_id": "ENT/01",
        "record_ref": "REC-0001",
        "location_id": "LOC-1",
        "recorded_on": date(2026, 3, 5),
        "value": Decimal("100.00"),
    }]
    report_date = ReconciliationEngine.reconcile(sys_a, sys_b_date, loc_map)
    assert report_date.items[0].disagreement_type == DisagreementType.DATE_MISMATCH
    assert report_date.items[0].has_date_mismatch is True


def test_tenant_filter_masks_cross_tenant_value_mismatch():
    sys_a = [{
        "record_id": "REC-0001",
        "location_id": "LOC-A",
        "event_date": date(2026, 3, 1),
        "total_value": Decimal("100.00"),
        "state": "CONFIRMED",
    }]
    sys_b = [{
        "entry_id": "ENT/01",
        "record_ref": "REC-0001",
        "location_id": "LOC-B",
        "recorded_on": date(2026, 3, 1),
        "value": Decimal("75.00"),
    }]
    loc_map = {"LOC-A": "ORG-A", "LOC-B": "ORG-B"}

    report_a = ReconciliationEngine.reconcile(
        sys_a, sys_b, loc_map, tenant_org_id="ORG-A"
    )
    item_a = report_a.items[0]
    assert item_a.disagreement_type == DisagreementType.MISSING_IN_B
    assert item_a.system_b_entry_ids == []
    assert item_a.system_b_recorded_amount is None
    assert item_a.difference == Decimal("-100.00")
    assert "ORG-B" not in item_a.message
    assert "LOC-B" not in item_a.message
    dict_a = item_a.to_dict(tenant_org_id="ORG-A")
    assert dict_a["system_b_entry_ids"] == []
    assert dict_a["system_b_recorded_amount"] is None

    report_b = ReconciliationEngine.reconcile(
        sys_a, sys_b, loc_map, tenant_org_id="ORG-B"
    )
    item_b = report_b.items[0]
    assert item_b.disagreement_type == DisagreementType.ORPHAN_IN_B
    assert item_b.system_a_record_id is None
    assert item_b.system_a_expected_amount is None
    assert item_b.system_b_entry_ids == ["ENT/01"]
    assert item_b.system_b_recorded_amount == Decimal("75.00")
    assert item_b.difference == Decimal("75.00")
    assert "ORG-A" not in item_b.message
    assert "LOC-A" not in item_b.message
    dict_b = item_b.to_dict(tenant_org_id="ORG-B")
    assert dict_b["system_a_record_id"] is None
    assert dict_b["system_a_expected_amount"] is None
    assert dict_b["date_a"] is None

    unmapped_a = ReconciliationEngine.reconcile(
        sys_a, sys_b, {"LOC-A": "ORG-A"}, tenant_org_id="ORG-A"
    ).items[0]
    assert unmapped_a.disagreement_type == DisagreementType.MISSING_IN_B
    assert unmapped_a.system_b_entry_ids == []

    unmapped_b = ReconciliationEngine.reconcile(
        sys_a, sys_b, {"LOC-B": "ORG-B"}, tenant_org_id="ORG-B"
    ).items[0]
    assert unmapped_b.disagreement_type == DisagreementType.ORPHAN_IN_B
    assert unmapped_b.system_a_record_id is None


# ============================================================================
# INTEGRATION TESTS AGAINST ACTUAL ASSESSMENT DATABASE
# ============================================================================

@pytest.mark.django_db
def test_engine_against_assessment_database():
    """Verify engine output against the actual assessment dataset."""
    from django.core.management import call_command
    call_command("import_csv_data", replace=True)

    report = ReconciliationEngine.reconcile_from_db()
    s = report.summary

    assert s.total_system_a == 120
    assert s.total_system_b == 121
    assert s.total_reconciled == 121
    assert s.matched_count == 108
    assert s.valid_split_count == 1
    assert s.duplicate_in_b_count == 1
    assert s.missing_in_b_count == 2
    assert s.orphan_in_b_count == 1
    assert s.value_mismatch_count == 1
    assert s.base_value_recorded_count == 3
    assert s.date_mismatch_count == 1
    assert s.location_mismatch_count == 1
    assert s.voided_record_count == 1
    assert s.blank_value_count == 1


@pytest.mark.django_db
def test_specific_assessment_cases():
    from django.core.management import call_command
    call_command("import_csv_data", replace=True)

    report = ReconciliationEngine.reconcile_from_db()
    by_ref = {item.normalized_record_ref: item for item in report.items}

    # 1. Missing in B: REC-1015, REC-1061
    assert by_ref["REC-1015"].disagreement_type == DisagreementType.MISSING_IN_B
    assert by_ref["REC-1061"].disagreement_type == DisagreementType.MISSING_IN_B

    # 2. Orphan in B: REC-1999
    assert by_ref["REC-1999"].disagreement_type == DisagreementType.ORPHAN_IN_B
    assert by_ref["REC-1999"].system_b_recorded_amount == Decimal("41250.00")

    # 3. Valid split: REC-1055
    rec1055 = by_ref["REC-1055"]
    assert rec1055.disagreement_type == DisagreementType.VALID_SPLIT
    assert rec1055.difference == Decimal("0.00")
    assert len(rec1055.system_b_entry_ids) == 2
    assert set(rec1055.system_b_entry_ids) == {"ENT/2026/4055", "ENT/2026/4903"}

    # 4. Duplicate in B: REC-1042
    rec1042 = by_ref["REC-1042"]
    assert rec1042.disagreement_type == DisagreementType.DUPLICATE_IN_B
    assert rec1042.system_b_recorded_amount == Decimal("225674.12")
    assert rec1042.system_a_expected_amount == Decimal("112837.06")
    assert rec1042.difference == Decimal("112837.06")

    # 5. Blank value: REC-1050
    rec1050 = by_ref["REC-1050"]
    assert rec1050.disagreement_type == DisagreementType.BLANK_VALUE
    assert rec1050.is_value_blank is True
    assert rec1050.difference is None

    # 6. Value mismatch (Indian Lakh parsed): REC-1064
    rec1064 = by_ref["REC-1064"]
    assert rec1064.disagreement_type == DisagreementType.VALUE_MISMATCH
    assert rec1064.system_b_recorded_amount == Decimal("125400.00")
    assert rec1064.system_a_expected_amount == Decimal("183244.16")
    assert rec1064.difference == Decimal("-57844.16")

    # 7. Base value recorded: REC-1003, REC-1027, REC-1088
    for r_id in ["REC-1003", "REC-1027", "REC-1088"]:
        item = by_ref[r_id]
        assert item.disagreement_type == DisagreementType.BASE_VALUE_RECORDED
        assert item.is_base_value_mismatch is True
        assert item.system_b_recorded_amount == item.system_a_base_amount

    # 8. Date mismatch: REC-1009
    rec1009 = by_ref["REC-1009"]
    assert rec1009.disagreement_type == DisagreementType.DATE_MISMATCH
    assert rec1009.has_date_mismatch is True
    assert rec1009.difference == Decimal("0.00")

    # 9. Location mismatch: REC-1077
    rec1077 = by_ref["REC-1077"]
    assert rec1077.disagreement_type == DisagreementType.LOCATION_MISMATCH
    assert rec1077.has_location_mismatch is True
    assert rec1077.location_id_a == "LOC-102"
    assert rec1077.location_id_b == "LOC-201"

    # 10. VOIDED record: REC-1019
    rec1019 = by_ref["REC-1019"]
    assert rec1019.disagreement_type == DisagreementType.VOIDED_RECORD
    assert rec1019.is_voided is True


# ============================================================================
# MULTI-TENANT ISOLATION TESTS
# ============================================================================

# ============================================================================
# MULTI-TENANT ISOLATION TESTS
# ============================================================================

@pytest.mark.django_db
def test_tenant_scoped_reconciliation_and_masking():
    """
    Exhaustive tenant-isolation audit for the cross-tenant transaction REC-1077.

    Facts:
    - System A: REC-1077 at LOC-102, owned by ORG-A.
    - System B: ENT/2026/4077, record_ref=REC-1077 at LOC-201, owned by ORG-B.
    - Amounts and dates match.

    Expected tenant-scoped behaviour
    ---------------------------------
    ORG-A scope:
      - REC-1077 must be MISSING_IN_B  (B-side invisible).
      - No foreign org IDs, entry IDs, B-amounts, or B-side dates must appear.
      - message must NOT contain LOC-201 or ORG-B.
      - summary.location_mismatch_count must be 0.

    ORG-B scope:
      - REC-1077 must be ORPHAN_IN_B  (A-side invisible).
      - No foreign org IDs, A-side amounts, A-side record_id, or A-side dates
        must appear.
      - message must NOT contain LOC-102 or ORG-A.
      - summary.location_mismatch_count must be 0.

    Global scope (no tenant_org_id):
      - REC-1077 is LOCATION_MISMATCH — both sides visible (admin/auditor view).
    """
    from django.core.management import call_command
    call_command("import_csv_data", replace=True)

    report_a = ReconciliationEngine.reconcile_from_db(tenant_org_id="ORG-A")
    report_b = ReconciliationEngine.reconcile_from_db(tenant_org_id="ORG-B")
    report_global = ReconciliationEngine.reconcile_from_db()

    # ---- Helpers ----
    def find(report, ref):
        return next((i for i in report.items if i.normalized_record_ref == ref), None)

    # =========================================================
    # 1. ORG-A: REC-1077 must be MISSING_IN_B
    # =========================================================
    item_a = find(report_a, "REC-1077")
    assert item_a is not None, "REC-1077 must appear in ORG-A report"
    assert item_a.disagreement_type == DisagreementType.MISSING_IN_B, (
        f"ORG-A must see MISSING_IN_B, got {item_a.disagreement_type.value}"
    )
    # A-side fields visible
    assert item_a.organization_id_a == "ORG-A"
    assert item_a.location_id_a == "LOC-102"
    assert item_a.system_a_record_id == "REC-1077"
    # B-side fields invisible
    assert item_a.organization_id_b is None, "ORG-B must not be visible to ORG-A"
    assert item_a.location_id_b is None,     "LOC-201 must not be visible to ORG-A"
    assert item_a.system_b_entry_ids == [],  "ENT/2026/4077 must not be visible to ORG-A"
    assert item_a.system_b_recorded_amount is None
    assert item_a.date_b is None
    # Difference = negative A amount
    assert item_a.difference is not None
    assert item_a.difference < 0
    # has_location_mismatch must NOT be surfaced to this tenant
    assert item_a.has_location_mismatch is False
    # Message must not leak ORG-B or LOC-201
    assert "ORG-B" not in item_a.message, "message must not leak ORG-B to ORG-A"
    assert "LOC-201" not in item_a.message, "message must not leak LOC-201 to ORG-A"

    # to_dict check for ORG-A
    dict_a = item_a.to_dict(tenant_org_id="ORG-A")
    assert dict_a["disagreement_type"] == "MISSING_IN_B"
    assert dict_a["location_a"] == "LOC-102"
    assert dict_a["location_b"] is None
    assert dict_a["organization_id_a"] == "ORG-A"
    assert dict_a["organization_id_b"] is None
    assert dict_a["system_b_entry_ids"] == []
    assert dict_a["system_b_recorded_amount"] is None
    assert dict_a["system_a_record_id"] == "REC-1077"
    assert dict_a["date_b"] is None
    # No foreign location in details
    for entry in dict_a["details"]:
        assert entry.get("location_id") != "LOC-201", "LOC-201 must not appear in details for ORG-A"
    assert "ORG-B" not in dict_a["message"]
    assert "LOC-201" not in dict_a["message"]

    # ORG-A summary: no location_mismatch items
    assert report_a.summary.location_mismatch_count == 0
    assert report_a.summary.missing_in_b_count >= 1   # includes REC-1077

    # =========================================================
    # 2. ORG-B: REC-1077 must be ORPHAN_IN_B
    # =========================================================
    item_b = find(report_b, "REC-1077")
    assert item_b is not None, "REC-1077 must appear in ORG-B report"
    assert item_b.disagreement_type == DisagreementType.ORPHAN_IN_B, (
        f"ORG-B must see ORPHAN_IN_B, got {item_b.disagreement_type.value}"
    )
    # B-side fields visible
    assert item_b.organization_id_b == "ORG-B"
    assert item_b.location_id_b == "LOC-201"
    assert "ENT/2026/4077" in item_b.system_b_entry_ids
    # A-side fields invisible
    assert item_b.organization_id_a is None, "ORG-A must not be visible to ORG-B"
    assert item_b.location_id_a is None,     "LOC-102 must not be visible to ORG-B"
    assert item_b.system_a_record_id is None, "REC-1077 A-side record_id must not be visible to ORG-B"
    assert item_b.system_a_expected_amount is None
    assert item_b.system_a_base_amount is None
    assert item_b.system_a_adjustment is None
    assert item_b.date_a is None
    assert item_b.state_a is None
    # Difference = positive B amount (orphan)
    assert item_b.difference is not None
    assert item_b.difference > 0
    # has_location_mismatch must NOT be surfaced to this tenant
    assert item_b.has_location_mismatch is False
    # Message must not leak ORG-A or LOC-102
    assert "ORG-A" not in item_b.message, "message must not leak ORG-A to ORG-B"
    assert "LOC-102" not in item_b.message, "message must not leak LOC-102 to ORG-B"

    # to_dict check for ORG-B
    dict_b = item_b.to_dict(tenant_org_id="ORG-B")
    assert dict_b["disagreement_type"] == "ORPHAN_IN_B"
    assert dict_b["location_b"] == "LOC-201"
    assert dict_b["location_a"] is None
    assert dict_b["organization_id_b"] == "ORG-B"
    assert dict_b["organization_id_a"] is None
    assert dict_b["system_a_record_id"] is None
    assert dict_b["system_a_expected_amount"] is None
    assert dict_b["system_a_base_amount"] is None
    assert dict_b["system_a_adjustment"] is None
    assert dict_b["date_a"] is None
    assert dict_b["state_a"] is None
    assert "ENT/2026/4077" in dict_b["system_b_entry_ids"]
    assert "ORG-A" not in dict_b["message"]
    assert "LOC-102" not in dict_b["message"]

    # ORG-B summary: no location_mismatch items
    assert report_b.summary.location_mismatch_count == 0
    assert report_b.summary.orphan_in_b_count >= 1   # includes REC-1077

    # =========================================================
    # 3. Global report: REC-1077 remains LOCATION_MISMATCH (admin view)
    # =========================================================
    item_global = find(report_global, "REC-1077")
    assert item_global is not None
    assert item_global.disagreement_type == DisagreementType.LOCATION_MISMATCH
    assert item_global.location_id_a == "LOC-102"
    assert item_global.location_id_b == "LOC-201"
    assert item_global.organization_id_a == "ORG-A"
    assert item_global.organization_id_b == "ORG-B"
    # Global to_dict (no tenant): full view
    dict_global = item_global.to_dict()
    assert dict_global["location_a"] == "LOC-102"
    assert dict_global["location_b"] == "LOC-201"
    assert dict_global["organization_id_a"] == "ORG-A"
    assert dict_global["organization_id_b"] == "ORG-B"


@pytest.mark.django_db
@pytest.mark.parametrize("tenant_org_id", ["ORG-A", "ORG-B"])
def test_tenant_reconciliation_location_map_query_is_scoped(tenant_org_id):
    from django.core.management import call_command
    from reconcile.models import SystemARecord, SystemBEntry

    call_command("import_csv_data", replace=True)

    with CaptureQueriesContext(connection) as queries:
        report = ReconciliationEngine.reconcile_from_db(tenant_org_id=tenant_org_id)

    location_queries = [
        query["sql"] for query in queries.captured_queries
        if 'FROM "reconcile_location"' in query["sql"]
        and 'JOIN "reconcile_organization"' in query["sql"]
    ]
    assert len(location_queries) == 1
    assert '"organization_id"' in location_queries[0]
    assert tenant_org_id in location_queries[0]
    assert report.summary.total_system_a == SystemARecord.objects.filter(
        location__organization_id=tenant_org_id
    ).count()
    assert report.summary.total_system_b == SystemBEntry.objects.filter(
        location__organization_id=tenant_org_id
    ).count()

    cross_tenant_item = next(
        item for item in report.items
        if item.normalized_record_ref == "REC-1077"
    )
    if tenant_org_id == "ORG-A":
        assert cross_tenant_item.disagreement_type == DisagreementType.MISSING_IN_B
        assert cross_tenant_item.location_id_b is None
        assert cross_tenant_item.organization_id_b is None
    else:
        assert cross_tenant_item.disagreement_type == DisagreementType.ORPHAN_IN_B
        assert cross_tenant_item.location_id_a is None
        assert cross_tenant_item.organization_id_a is None


@pytest.mark.django_db
def test_tenant_report_contains_only_own_records():
    """
    Verify that each tenant's report contains only records belonging to that
    tenant, and that no same-tenant items are incorrectly excluded.
    REC-1077 contributes one item to each tenant (not shared).
    """
    from django.core.management import call_command
    call_command("import_csv_data", replace=True)

    report_a = ReconciliationEngine.reconcile_from_db(tenant_org_id="ORG-A")
    report_b = ReconciliationEngine.reconcile_from_db(tenant_org_id="ORG-B")
    report_global = ReconciliationEngine.reconcile_from_db()

    # Every item in ORG-A report must own the A-side or B-side under ORG-A
    for item in report_a.items:
        assert item.organization_id_a == "ORG-A" or item.organization_id_b == "ORG-A", (
            f"Foreign item leaked into ORG-A report: ref={item.normalized_record_ref}, "
            f"orgA={item.organization_id_a}, orgB={item.organization_id_b}"
        )

    # Every item in ORG-B report must own the A-side or B-side under ORG-B
    for item in report_b.items:
        assert item.organization_id_a == "ORG-B" or item.organization_id_b == "ORG-B", (
            f"Foreign item leaked into ORG-B report: ref={item.normalized_record_ref}, "
            f"orgA={item.organization_id_a}, orgB={item.organization_id_b}"
        )

    # Total items across both tenant reports >= global total
    # (REC-1077 appears in both A and B reports; all others appear once)
    total_tenant = report_a.summary.total_reconciled + report_b.summary.total_reconciled
    assert total_tenant == report_global.summary.total_reconciled + 1, (
        "REC-1077 must contribute one extra item (once per tenant)"
    )


@pytest.mark.django_db
def test_same_tenant_items_not_masked():
    """
    Non-cross-tenant items (both sides in the same org) must not have their
    fields masked when calling to_dict(tenant_org_id).
    """
    from django.core.management import call_command
    call_command("import_csv_data", replace=True)

    report_a = ReconciliationEngine.reconcile_from_db(tenant_org_id="ORG-A")

    for item in report_a.items:
        if item.normalized_record_ref == "REC-1077":
            continue  # cross-tenant — already tested separately
        d = item.to_dict(tenant_org_id="ORG-A")
        # For items that have an A-side record, record_id must not be masked
        if item.system_a_record_id is not None:
            assert d["system_a_record_id"] is not None, (
                f"system_a_record_id masked incorrectly for {item.normalized_record_ref}"
            )
