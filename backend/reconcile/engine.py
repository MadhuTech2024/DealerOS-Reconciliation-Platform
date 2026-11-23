from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Sequence

from reconcile.reference_normalizer import normalize_reference, is_valid_reference


class DisagreementType(str, Enum):
    MATCHED = "MATCHED"
    VALID_SPLIT = "VALID_SPLIT"
    DUPLICATE_IN_B = "DUPLICATE_IN_B"
    MISSING_IN_B = "MISSING_IN_B"
    ORPHAN_IN_B = "ORPHAN_IN_B"
    VALUE_MISMATCH = "VALUE_MISMATCH"
    BASE_VALUE_RECORDED = "BASE_VALUE_RECORDED"
    DATE_MISMATCH = "DATE_MISMATCH"
    LOCATION_MISMATCH = "LOCATION_MISMATCH"
    VOIDED_RECORD = "VOIDED_RECORD"
    BLANK_VALUE = "BLANK_VALUE"
    INVALID_REFERENCE = "INVALID_REFERENCE"


@dataclass
class ReconciliationItem:
    disagreement_type: DisagreementType
    normalized_record_ref: str
    system_a_record_id: str | None = None
    system_b_entry_ids: list[str] = field(default_factory=list)
    system_a_expected_amount: Decimal | None = None  # System A total_value
    system_a_base_amount: Decimal | None = None      # System A base_value
    system_a_adjustment: Decimal | None = None       # System A adjustment
    system_b_recorded_amount: Decimal | None = None  # System B value or sum
    difference: Decimal | None = None                # B - A expected
    organization_id_a: str | None = None
    organization_id_b: str | None = None
    location_id_a: str | None = None
    location_id_b: str | None = None
    date_a: date | None = None
    date_b: date | None = None
    state_a: str | None = None
    has_date_mismatch: bool = False
    has_location_mismatch: bool = False
    is_voided: bool = False
    is_value_blank: bool = False
    is_base_value_mismatch: bool = False
    details: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""

    def to_dict(self, tenant_org_id: str | None = None) -> dict[str, Any]:
        """
        Serializes reconciliation result to dictionary for API exposure.

        Applies layered tenant isolation when tenant_org_id is provided:

        1. Location masking  — foreign location IDs are replaced with None.
        2. Org-ID masking    — the foreign organization ID is replaced with None.
        3. Side masking      — for MISSING_IN_B: B-side fields are cleared;
                               for ORPHAN_IN_B: A-side fields are cleared.
        4. Details masking   — entries whose location belongs to a foreign org
                               have their location_id replaced with None.
        5. Message masking   — tenant-safe message is used instead of the
                               global cross-tenant message.

        When tenant_org_id is None (admin / auditor scope) the full global
        view is returned with no masking.
        """
        if not tenant_org_id:
            return {
                "disagreement_type": self.disagreement_type.value,
                "normalized_record_ref": self.normalized_record_ref,
                "record_id": self.system_a_record_id or self.normalized_record_ref,
                "system_a_record_id": self.system_a_record_id,
                "system_b_entry_ids": self.system_b_entry_ids,
                "system_a_expected_amount": str(self.system_a_expected_amount) if self.system_a_expected_amount is not None else None,
                "system_a_base_amount": str(self.system_a_base_amount) if self.system_a_base_amount is not None else None,
                "system_a_adjustment": str(self.system_a_adjustment) if self.system_a_adjustment is not None else None,
                "system_b_recorded_amount": str(self.system_b_recorded_amount) if self.system_b_recorded_amount is not None else None,
                "difference": str(self.difference) if self.difference is not None else None,
                "location_a": self.location_id_a,
                "location_b": self.location_id_b,
                "organization_id_a": self.organization_id_a,
                "organization_id_b": self.organization_id_b,
                "date_a": self.date_a.isoformat() if self.date_a else None,
                "date_b": self.date_b.isoformat() if self.date_b else None,
                "state_a": self.state_a,
                "has_date_mismatch": self.has_date_mismatch,
                "has_location_mismatch": self.has_location_mismatch,
                "is_voided": self.is_voided,
                "is_value_blank": self.is_value_blank,
                "is_base_value_mismatch": self.is_base_value_mismatch,
                "details": self.details,
                "message": self.message,
            }

        # ------------------------------------------------------------------ #
        # Tenant-scoped serialization                                         #
        # ------------------------------------------------------------------ #
        tenant_owns_a = (self.organization_id_a == tenant_org_id)
        tenant_owns_b = (self.organization_id_b == tenant_org_id)

        # 1. Location masking
        loc_a = self.location_id_a if tenant_owns_a else None
        loc_b = self.location_id_b if tenant_owns_b else None

        # 2. Org-ID masking
        org_id_a = self.organization_id_a if tenant_owns_a else None
        org_id_b = self.organization_id_b if tenant_owns_b else None

        # 3. Side masking for cross-tenant MISSING_IN_B / ORPHAN_IN_B
        #    (items that were originally LOCATION_MISMATCH but have been
        #    re-classified by filter_by_tenant)
        if self.disagreement_type == DisagreementType.MISSING_IN_B:
            # Tenant owns the A side; B side must be completely invisible
            rec_id = self.system_a_record_id
            entry_ids: list[str] = []
            amt_a = str(self.system_a_expected_amount) if self.system_a_expected_amount is not None else None
            base_a = str(self.system_a_base_amount) if self.system_a_base_amount is not None else None
            adj_a = str(self.system_a_adjustment) if self.system_a_adjustment is not None else None
            amt_b = None
            diff = str(self.difference) if self.difference is not None else None
            details_out: list[dict[str, Any]] = []
            msg = self.message
        elif self.disagreement_type == DisagreementType.ORPHAN_IN_B:
            # Tenant owns the B side; A side must be completely invisible
            rec_id = self.normalized_record_ref
            entry_ids = self.system_b_entry_ids
            amt_a = None
            base_a = None
            adj_a = None
            amt_b = str(self.system_b_recorded_amount) if self.system_b_recorded_amount is not None else None
            diff = str(self.difference) if self.difference is not None else None
            # Mask foreign location_id inside each detail entry
            details_out = [
                {**e, "location_id": e.get("location_id") if tenant_owns_b else None}
                for e in self.details
            ]
            msg = self.message
        else:
            # Same-tenant item or already-safe global classification
            rec_id = self.system_a_record_id or self.normalized_record_ref
            entry_ids = self.system_b_entry_ids
            amt_a = str(self.system_a_expected_amount) if self.system_a_expected_amount is not None else None
            base_a = str(self.system_a_base_amount) if self.system_a_base_amount is not None else None
            adj_a = str(self.system_a_adjustment) if self.system_a_adjustment is not None else None
            amt_b = str(self.system_b_recorded_amount) if self.system_b_recorded_amount is not None else None
            diff = str(self.difference) if self.difference is not None else None
            # 4. Details masking: replace foreign location_ids in entries
            details_out = []
            for e in self.details:
                entry_copy = dict(e)
                # Only reveal location_id if the entry belongs to this tenant
                # (we cannot know per-entry org here, so mask if either side is foreign)
                if not tenant_owns_b:
                    entry_copy["location_id"] = None
                details_out.append(entry_copy)
            msg = self.message

        return {
            "disagreement_type": self.disagreement_type.value,
            "normalized_record_ref": self.normalized_record_ref,
            "record_id": rec_id,
            "system_a_record_id": self.system_a_record_id if tenant_owns_a else None,
            "system_b_entry_ids": entry_ids,
            "system_a_expected_amount": amt_a,
            "system_a_base_amount": base_a,
            "system_a_adjustment": adj_a,
            "system_b_recorded_amount": amt_b,
            "difference": diff,
            "location_a": loc_a,
            "location_b": loc_b,
            "organization_id_a": org_id_a,
            "organization_id_b": org_id_b,
            "date_a": self.date_a.isoformat() if (self.date_a and tenant_owns_a) else None,
            "date_b": self.date_b.isoformat() if (self.date_b and tenant_owns_b) else None,
            "state_a": self.state_a if tenant_owns_a else None,
            "has_date_mismatch": self.has_date_mismatch,
            "has_location_mismatch": self.has_location_mismatch,
            "is_voided": self.is_voided,
            "is_value_blank": self.is_value_blank,
            "is_base_value_mismatch": self.is_base_value_mismatch,
            "details": details_out,
            "message": msg,
        }


@dataclass
class ReconciliationSummary:
    total_system_a: int = 0
    total_system_b: int = 0
    total_reconciled: int = 0
    matched_count: int = 0
    valid_split_count: int = 0
    duplicate_in_b_count: int = 0
    missing_in_b_count: int = 0
    orphan_in_b_count: int = 0
    value_mismatch_count: int = 0
    base_value_recorded_count: int = 0
    date_mismatch_count: int = 0
    location_mismatch_count: int = 0
    voided_record_count: int = 0
    blank_value_count: int = 0
    invalid_reference_count: int = 0


@dataclass
class ReconciliationReport:
    items: list[ReconciliationItem]
    summary: ReconciliationSummary

    def filter_by_tenant(self, tenant_org_id: str) -> "ReconciliationReport":
        """
        Returns a tenant-scoped report where:

        - Only items relevant to tenant_org_id are included.
        - Any cross-tenant item with both sides represented is re-classified:
          * If the tenant owns the A-side  → MISSING_IN_B  (B side invisible).
          * If the tenant owns the B-side  → ORPHAN_IN_B   (A side invisible).
        - Recalculated summary reflects only tenant-visible records.

        This is called by reconcile() when tenant_org_id is supplied via the
        post-hoc global-report path.  The primary path (reconcile() with upfront
        tenant-scoped inputs) produces the same classification naturally.
        """
        filtered_items: list[ReconciliationItem] = []
        for item in self.items:
            owns_a = item.organization_id_a == tenant_org_id
            owns_b = item.organization_id_b == tenant_org_id
            is_relevant = owns_a or owns_b
            if not is_relevant:
                continue

            # A global match can be classified before its cross-tenant
            # ownership is considered. Treat any unowned counterpart as
            # invisible; a missing organization mapping is not proof of access.
            if owns_a and not owns_b and item.system_b_entry_ids:
                # Tenant A: the B-side entry is not proven tenant-owned.
                new_item = ReconciliationItem(
                    disagreement_type=DisagreementType.MISSING_IN_B,
                    normalized_record_ref=item.normalized_record_ref,
                    system_a_record_id=item.system_a_record_id,
                    system_b_entry_ids=[],           # B-side invisible
                    system_a_expected_amount=item.system_a_expected_amount,
                    system_a_base_amount=item.system_a_base_amount,
                    system_a_adjustment=item.system_a_adjustment,
                    system_b_recorded_amount=None,   # B-side invisible
                    difference=-item.system_a_expected_amount if item.system_a_expected_amount is not None else None,
                    organization_id_a=item.organization_id_a,
                    organization_id_b=None,          # B-side invisible
                    location_id_a=item.location_id_a,
                    location_id_b=None,              # B-side invisible
                    date_a=item.date_a,
                    date_b=None,                     # B-side invisible
                    state_a=item.state_a,
                    has_date_mismatch=False,
                    has_location_mismatch=False,     # not surfaced to this tenant
                    is_voided=item.is_voided,
                    details=[],                      # B-side invisible
                    message=(
                        f"Transaction {item.normalized_record_ref} is present in System A "
                        f"but has no corresponding entry in System B for your organisation."
                    ),
                )
                filtered_items.append(new_item)
            elif owns_b and not owns_a and item.system_a_record_id is not None:
                # Tenant B: the A-side record is not proven tenant-owned.
                new_item = ReconciliationItem(
                    disagreement_type=DisagreementType.ORPHAN_IN_B,
                    normalized_record_ref=item.normalized_record_ref,
                    system_a_record_id=None,         # A-side invisible
                    system_b_entry_ids=item.system_b_entry_ids,
                    system_a_expected_amount=None,   # A-side invisible
                    system_a_base_amount=None,       # A-side invisible
                    system_a_adjustment=None,        # A-side invisible
                    system_b_recorded_amount=item.system_b_recorded_amount,
                    difference=item.system_b_recorded_amount,  # orphan diff = B amount
                    organization_id_a=None,          # A-side invisible
                    organization_id_b=item.organization_id_b,
                    location_id_a=None,              # A-side invisible
                    location_id_b=item.location_id_b,
                    date_a=None,                     # A-side invisible
                    date_b=item.date_b,
                    state_a=None,                    # A-side invisible
                    has_date_mismatch=False,
                    has_location_mismatch=False,     # not surfaced to this tenant
                    is_voided=False,
                    is_value_blank=item.is_value_blank,
                    details=item.details,
                    message=(
                        f"System B entry {item.normalized_record_ref} has no corresponding "
                        f"transaction in System A for your organisation."
                    ),
                )
                filtered_items.append(new_item)
            else:
                filtered_items.append(item)

        # Recalculate summary for tenant-visible items only
        summary = ReconciliationSummary(
            total_reconciled=len(filtered_items),
            matched_count=sum(1 for i in filtered_items if i.disagreement_type == DisagreementType.MATCHED),
            valid_split_count=sum(1 for i in filtered_items if i.disagreement_type == DisagreementType.VALID_SPLIT),
            duplicate_in_b_count=sum(1 for i in filtered_items if i.disagreement_type == DisagreementType.DUPLICATE_IN_B),
            missing_in_b_count=sum(1 for i in filtered_items if i.disagreement_type == DisagreementType.MISSING_IN_B),
            orphan_in_b_count=sum(1 for i in filtered_items if i.disagreement_type == DisagreementType.ORPHAN_IN_B),
            value_mismatch_count=sum(1 for i in filtered_items if i.disagreement_type == DisagreementType.VALUE_MISMATCH),
            base_value_recorded_count=sum(1 for i in filtered_items if i.disagreement_type == DisagreementType.BASE_VALUE_RECORDED),
            date_mismatch_count=sum(1 for i in filtered_items if i.has_date_mismatch),
            location_mismatch_count=sum(1 for i in filtered_items if i.has_location_mismatch),
            voided_record_count=sum(1 for i in filtered_items if i.is_voided),
            blank_value_count=sum(1 for i in filtered_items if i.is_value_blank),
        )
        return ReconciliationReport(items=filtered_items, summary=summary)


def _get_field(obj: Any, name: str, default: Any = None) -> Any:
    """Helper to read attribute from dict, model instance, or object."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class ReconciliationEngine:
    """
    Core Domain Reconciliation Engine.
    Completely decoupled from database and HTTP frameworks.
    Operates on plain collections of records.
    """

    @classmethod
    def reconcile(
        cls,
        system_a_records: Sequence[Any],
        system_b_entries: Sequence[Any],
        location_org_map: dict[str, str],
        tenant_org_id: str | None = None,
    ) -> ReconciliationReport:
        """
        Reconciles System B entries against System A records.

        Args:
            system_a_records: Collection of System A records or dicts.
            system_b_entries: Collection of System B entries or dicts.
            location_org_map: Mapping of location_id -> org_id (from locations.csv).
            tenant_org_id: Optional organization ID to scope and mask results.

        Returns:
            ReconciliationReport containing items and summary.
        """
        # 1. Index System A records by normalized record_id
        a_map: dict[str, Any] = {}
        for rec in system_a_records:
            raw_id = _get_field(rec, "record_id", "")
            norm_id = normalize_reference(raw_id)
            a_map[norm_id] = rec

        # 2. Group System B entries by normalized reference
        b_grouped: dict[str, list[Any]] = {}
        invalid_b_entries: list[Any] = []

        for entry in system_b_entries:
            raw_ref = _get_field(entry, "record_ref", "")
            norm_ref = normalize_reference(raw_ref)

            if not norm_ref or not is_valid_reference(raw_ref):
                invalid_b_entries.append(entry)
                continue

            if norm_ref not in b_grouped:
                b_grouped[norm_ref] = []
            b_grouped[norm_ref].append(entry)

        items: list[ReconciliationItem] = []

        # 3. Process all System A records against System B groupings
        for norm_id, a_rec in a_map.items():
            loc_a = _get_field(a_rec, "raw_location_id") or (_get_field(a_rec, "location_id")) or ""
            if not loc_a and hasattr(a_rec, "location") and getattr(a_rec, "location"):
                loc_a = a_rec.location.location_id
            org_a = location_org_map.get(loc_a)

            tot_val_a = _get_field(a_rec, "total_value")
            if tot_val_a is not None and not isinstance(tot_val_a, Decimal):
                tot_val_a = Decimal(str(tot_val_a))

            base_val_a = _get_field(a_rec, "base_value")
            if base_val_a is not None and not isinstance(base_val_a, Decimal):
                base_val_a = Decimal(str(base_val_a))

            adj_val_a = _get_field(a_rec, "adjustment")
            if adj_val_a is not None and not isinstance(adj_val_a, Decimal):
                adj_val_a = Decimal(str(adj_val_a))

            date_a = _get_field(a_rec, "event_date")
            state_a = _get_field(a_rec, "state", "CONFIRMED")
            is_voided = (state_a == "VOIDED")

            b_entries = b_grouped.get(norm_id, [])

            # Case: MISSING_IN_B
            if not b_entries:
                item = ReconciliationItem(
                    disagreement_type=DisagreementType.MISSING_IN_B,
                    normalized_record_ref=norm_id,
                    system_a_record_id=_get_field(a_rec, "record_id", norm_id),
                    system_a_expected_amount=tot_val_a,
                    system_a_base_amount=base_val_a,
                    system_a_adjustment=adj_val_a,
                    system_b_recorded_amount=None,
                    difference=-tot_val_a if tot_val_a is not None else None,
                    organization_id_a=org_a,
                    location_id_a=loc_a,
                    date_a=date_a,
                    state_a=state_a,
                    is_voided=is_voided,
                    message=f"Transaction {norm_id} is present in System A but completely missing in System B.",
                )
                items.append(item)
                continue

            # System B entries exist for this reference
            entry_ids = [_get_field(e, "entry_id", "") for e in b_entries]

            # Build entry detail breakdowns
            b_details: list[dict[str, Any]] = []
            has_blank_val = False
            b_sum = Decimal("0.00")
            all_b_have_values = True

            first_e = b_entries[0]
            loc_b = _get_field(first_e, "raw_location_id") or (_get_field(first_e, "location_id")) or ""
            if not loc_b and hasattr(first_e, "location") and getattr(first_e, "location"):
                loc_b = first_e.location.location_id
            org_b = location_org_map.get(loc_b)
            date_b = _get_field(first_e, "recorded_on")

            for e in b_entries:
                raw_v = _get_field(e, "raw_value", "")
                val_b = _get_field(e, "value")
                is_blank = _get_field(e, "is_value_blank", False) or (val_b is None)

                if is_blank or val_b is None:
                    has_blank_val = True
                    all_b_have_values = False
                else:
                    if not isinstance(val_b, Decimal):
                        val_b = Decimal(str(val_b))
                    b_sum += val_b

                e_loc = _get_field(e, "raw_location_id") or (_get_field(e, "location_id")) or ""
                b_details.append({
                    "entry_id": _get_field(e, "entry_id", ""),
                    "raw_value": raw_v,
                    "value": str(val_b) if val_b is not None else None,
                    "location_id": e_loc,
                    "recorded_on": _get_field(e, "recorded_on").isoformat() if _get_field(e, "recorded_on") else None,
                    "label": _get_field(e, "label", ""),
                })

            # Check secondary discrepancies
            has_location_mismatch = (loc_a != loc_b)
            has_date_mismatch = (date_a != date_b)

            b_recorded_amount = b_sum if all_b_have_values else None
            diff = (b_recorded_amount - tot_val_a) if (b_recorded_amount is not None and tot_val_a is not None) else None

            # A voided System A transaction takes precedence whenever any B
            # entries exist, including split entries or entries with blanks.
            if is_voided:
                item = ReconciliationItem(
                    disagreement_type=DisagreementType.VOIDED_RECORD,
                    normalized_record_ref=norm_id,
                    system_a_record_id=_get_field(a_rec, "record_id", norm_id),
                    system_b_entry_ids=entry_ids,
                    system_a_expected_amount=tot_val_a,
                    system_a_base_amount=base_val_a,
                    system_a_adjustment=adj_val_a,
                    system_b_recorded_amount=b_recorded_amount,
                    difference=diff,
                    organization_id_a=org_a,
                    organization_id_b=org_b,
                    location_id_a=loc_a,
                    location_id_b=loc_b,
                    date_a=date_a,
                    date_b=date_b,
                    state_a=state_a,
                    has_date_mismatch=has_date_mismatch,
                    has_location_mismatch=has_location_mismatch,
                    is_voided=True,
                    is_value_blank=has_blank_val,
                    details=b_details,
                    message=(
                        f"System A transaction {norm_id} is marked VOIDED, but System B "
                        f"has {len(b_entries)} active ledger entr{'y' if len(b_entries) == 1 else 'ies'}."
                    ),
                )
                items.append(item)
                continue

            # Case: BLANK_VALUE
            if has_blank_val:
                item = ReconciliationItem(
                    disagreement_type=DisagreementType.BLANK_VALUE,
                    normalized_record_ref=norm_id,
                    system_a_record_id=_get_field(a_rec, "record_id", norm_id),
                    system_b_entry_ids=entry_ids,
                    system_a_expected_amount=tot_val_a,
                    system_a_base_amount=base_val_a,
                    system_a_adjustment=adj_val_a,
                    system_b_recorded_amount=b_recorded_amount,
                    difference=None,  # Blank value cannot compute numeric difference
                    organization_id_a=org_a,
                    organization_id_b=org_b,
                    location_id_a=loc_a,
                    location_id_b=loc_b,
                    date_a=date_a,
                    date_b=date_b,
                    state_a=state_a,
                    has_date_mismatch=has_date_mismatch,
                    has_location_mismatch=has_location_mismatch,
                    is_voided=is_voided,
                    is_value_blank=True,
                    details=b_details,
                    message=f"System B entry for {norm_id} contains a blank monetary value. Expected total_value is {tot_val_a}.",
                )
                items.append(item)
                continue

            # Case: Multiple entries in B (Split vs Duplicate)
            if len(b_entries) > 1:
                if b_recorded_amount == tot_val_a:
                    # Valid Split Entry
                    item = ReconciliationItem(
                        disagreement_type=DisagreementType.VALID_SPLIT,
                        normalized_record_ref=norm_id,
                        system_a_record_id=_get_field(a_rec, "record_id", norm_id),
                        system_b_entry_ids=entry_ids,
                        system_a_expected_amount=tot_val_a,
                        system_a_base_amount=base_val_a,
                        system_a_adjustment=adj_val_a,
                        system_b_recorded_amount=b_recorded_amount,
                        difference=Decimal("0.00"),
                        organization_id_a=org_a,
                        organization_id_b=org_b,
                        location_id_a=loc_a,
                        location_id_b=loc_b,
                        date_a=date_a,
                        date_b=date_b,
                        state_a=state_a,
                        has_date_mismatch=has_date_mismatch,
                        has_location_mismatch=has_location_mismatch,
                        is_voided=is_voided,
                        details=b_details,
                        message=f"Transaction {norm_id} is split across {len(b_entries)} System B entries summing exactly to total_value ({tot_val_a}).",
                    )
                else:
                    # Duplicate in B
                    item = ReconciliationItem(
                        disagreement_type=DisagreementType.DUPLICATE_IN_B,
                        normalized_record_ref=norm_id,
                        system_a_record_id=_get_field(a_rec, "record_id", norm_id),
                        system_b_entry_ids=entry_ids,
                        system_a_expected_amount=tot_val_a,
                        system_a_base_amount=base_val_a,
                        system_a_adjustment=adj_val_a,
                        system_b_recorded_amount=b_recorded_amount,
                        difference=diff,
                        organization_id_a=org_a,
                        organization_id_b=org_b,
                        location_id_a=loc_a,
                        location_id_b=loc_b,
                        date_a=date_a,
                        date_b=date_b,
                        state_a=state_a,
                        has_date_mismatch=has_date_mismatch,
                        has_location_mismatch=has_location_mismatch,
                        is_voided=is_voided,
                        details=b_details,
                        message=f"Transaction {norm_id} has {len(b_entries)} duplicate System B entries summing to {b_recorded_amount} (expected {tot_val_a}).",
                    )
                items.append(item)
                continue

            # Case: Exactly 1 System B entry
            single_e = b_entries[0]
            val_b = _get_field(single_e, "value")
            if val_b is not None and not isinstance(val_b, Decimal):
                val_b = Decimal(str(val_b))

            # Sub-case: BASE_VALUE_RECORDED (B value matches A base_value, missing adjustment)
            if val_b == base_val_a and (adj_val_a is not None and adj_val_a != Decimal("0.00")):
                item = ReconciliationItem(
                    disagreement_type=DisagreementType.BASE_VALUE_RECORDED,
                    normalized_record_ref=norm_id,
                    system_a_record_id=_get_field(a_rec, "record_id", norm_id),
                    system_b_entry_ids=entry_ids,
                    system_a_expected_amount=tot_val_a,
                    system_a_base_amount=base_val_a,
                    system_a_adjustment=adj_val_a,
                    system_b_recorded_amount=val_b,
                    difference=diff,
                    organization_id_a=org_a,
                    organization_id_b=org_b,
                    location_id_a=loc_a,
                    location_id_b=loc_b,
                    date_a=date_a,
                    date_b=date_b,
                    state_a=state_a,
                    has_date_mismatch=has_date_mismatch,
                    has_location_mismatch=has_location_mismatch,
                    is_voided=is_voided,
                    is_base_value_mismatch=True,
                    details=b_details,
                    message=f"System B recorded base_value ({val_b}) instead of total_value ({tot_val_a}), omitting adjustment of {adj_val_a}.",
                )
                items.append(item)
                continue

            # Sub-case: Standard VALUE_MISMATCH
            if val_b != tot_val_a:
                item = ReconciliationItem(
                    disagreement_type=DisagreementType.VALUE_MISMATCH,
                    normalized_record_ref=norm_id,
                    system_a_record_id=_get_field(a_rec, "record_id", norm_id),
                    system_b_entry_ids=entry_ids,
                    system_a_expected_amount=tot_val_a,
                    system_a_base_amount=base_val_a,
                    system_a_adjustment=adj_val_a,
                    system_b_recorded_amount=val_b,
                    difference=diff,
                    organization_id_a=org_a,
                    organization_id_b=org_b,
                    location_id_a=loc_a,
                    location_id_b=loc_b,
                    date_a=date_a,
                    date_b=date_b,
                    state_a=state_a,
                    has_date_mismatch=has_date_mismatch,
                    has_location_mismatch=has_location_mismatch,
                    is_voided=is_voided,
                    details=b_details,
                    message=f"Value mismatch for {norm_id}: System A total_value is {tot_val_a}, System B recorded {val_b} (difference: {diff}).",
                )
                items.append(item)
                continue

            # Values match! Now check location and date discrepancies
            if has_location_mismatch:
                item = ReconciliationItem(
                    disagreement_type=DisagreementType.LOCATION_MISMATCH,
                    normalized_record_ref=norm_id,
                    system_a_record_id=_get_field(a_rec, "record_id", norm_id),
                    system_b_entry_ids=entry_ids,
                    system_a_expected_amount=tot_val_a,
                    system_a_base_amount=base_val_a,
                    system_a_adjustment=adj_val_a,
                    system_b_recorded_amount=val_b,
                    difference=Decimal("0.00"),
                    organization_id_a=org_a,
                    organization_id_b=org_b,
                    location_id_a=loc_a,
                    location_id_b=loc_b,
                    date_a=date_a,
                    date_b=date_b,
                    state_a=state_a,
                    has_date_mismatch=has_date_mismatch,
                    has_location_mismatch=True,
                    is_voided=is_voided,
                    details=b_details,
                    message=f"Location mismatch for {norm_id}: System A is at {loc_a} ({org_a}), while System B is at {loc_b} ({org_b}).",
                )
                items.append(item)
                continue

            if has_date_mismatch:
                item = ReconciliationItem(
                    disagreement_type=DisagreementType.DATE_MISMATCH,
                    normalized_record_ref=norm_id,
                    system_a_record_id=_get_field(a_rec, "record_id", norm_id),
                    system_b_entry_ids=entry_ids,
                    system_a_expected_amount=tot_val_a,
                    system_a_base_amount=base_val_a,
                    system_a_adjustment=adj_val_a,
                    system_b_recorded_amount=val_b,
                    difference=Decimal("0.00"),
                    organization_id_a=org_a,
                    organization_id_b=org_b,
                    location_id_a=loc_a,
                    location_id_b=loc_b,
                    date_a=date_a,
                    date_b=date_b,
                    state_a=state_a,
                    has_date_mismatch=True,
                    has_location_mismatch=False,
                    is_voided=is_voided,
                    details=b_details,
                    message=f"Date mismatch for {norm_id}: System A date is {date_a}, System B recorded_on is {date_b}.",
                )
                items.append(item)
                continue

            # Fully clean match
            item = ReconciliationItem(
                disagreement_type=DisagreementType.MATCHED,
                normalized_record_ref=norm_id,
                system_a_record_id=_get_field(a_rec, "record_id", norm_id),
                system_b_entry_ids=entry_ids,
                system_a_expected_amount=tot_val_a,
                system_a_base_amount=base_val_a,
                system_a_adjustment=adj_val_a,
                system_b_recorded_amount=val_b,
                difference=Decimal("0.00"),
                organization_id_a=org_a,
                organization_id_b=org_b,
                location_id_a=loc_a,
                location_id_b=loc_b,
                date_a=date_a,
                date_b=date_b,
                state_a=state_a,
                has_date_mismatch=False,
                has_location_mismatch=False,
                is_voided=False,
                details=b_details,
                message=f"Transaction {norm_id} is cleanly reconciled.",
            )
            items.append(item)

        # 4. Process Orphan entries in System B (normalized ref not in System A)
        for norm_ref, b_entries in b_grouped.items():
            if norm_ref not in a_map:
                entry_ids = [_get_field(e, "entry_id", "") for e in b_entries]
                first_e = b_entries[0]
                loc_b = _get_field(first_e, "raw_location_id") or (_get_field(first_e, "location_id")) or ""
                if not loc_b and hasattr(first_e, "location") and getattr(first_e, "location"):
                    loc_b = first_e.location.location_id
                org_b = location_org_map.get(loc_b)
                date_b = _get_field(first_e, "recorded_on")

                b_sum = Decimal("0.00")
                all_b_have_values = True
                b_details = []
                for e in b_entries:
                    val_b = _get_field(e, "value")
                    is_blank = _get_field(e, "is_value_blank", False) or val_b is None
                    if not is_blank:
                        if not isinstance(val_b, Decimal):
                            val_b = Decimal(str(val_b))
                        b_sum += val_b
                    else:
                        all_b_have_values = False
                    b_details.append({
                        "entry_id": _get_field(e, "entry_id", ""),
                        "raw_value": _get_field(e, "raw_value", ""),
                        "value": str(val_b) if val_b is not None else None,
                        "location_id": _get_field(e, "raw_location_id") or _get_field(e, "location_id"),
                        "recorded_on": _get_field(e, "recorded_on").isoformat() if _get_field(e, "recorded_on") else None,
                        "label": _get_field(e, "label", ""),
                    })

                b_recorded_amount = b_sum if all_b_have_values else None
                item = ReconciliationItem(
                    disagreement_type=DisagreementType.ORPHAN_IN_B,
                    normalized_record_ref=norm_ref,
                    system_a_record_id=None,
                    system_b_entry_ids=entry_ids,
                    system_a_expected_amount=None,
                    system_b_recorded_amount=b_recorded_amount,
                    difference=b_recorded_amount,
                    organization_id_b=org_b,
                    location_id_b=loc_b,
                    date_b=date_b,
                    is_value_blank=not all_b_have_values,
                    details=b_details,
                    message=(
                        f"System B entry {norm_ref} has no corresponding transaction in System A."
                        if all_b_have_values else
                        f"System B entry {norm_ref} has no corresponding transaction in System A; "
                        "its aggregate amount is unknown because at least one entry is blank."
                    ),
                )
                items.append(item)

        # 5. Process any invalid reference entries
        for inv_entry in invalid_b_entries:
            raw_ref = _get_field(inv_entry, "record_ref", "")
            loc_b = _get_field(inv_entry, "raw_location_id") or _get_field(inv_entry, "location_id")
            val_b = _get_field(inv_entry, "value")
            item = ReconciliationItem(
                disagreement_type=DisagreementType.INVALID_REFERENCE,
                normalized_record_ref=raw_ref,
                system_b_entry_ids=[_get_field(inv_entry, "entry_id", "")],
                system_b_recorded_amount=val_b,
                organization_id_b=location_org_map.get(loc_b),
                location_id_b=loc_b,
                date_b=_get_field(inv_entry, "recorded_on"),
                message=f"System B entry has missing or invalid reference '{raw_ref}'.",
            )
            items.append(item)

        # 6. Build summary counts
        # NOTE: total_system_a and total_system_b reflect the input sequences
        # passed to this call.  When called globally they equal 120/121.  When
        # called via filter_by_tenant() post-hoc the totals are recalculated
        # inside filter_by_tenant using only tenant-visible items.
        summary = ReconciliationSummary(
            total_system_a=len(system_a_records),
            total_system_b=len(system_b_entries),
            total_reconciled=len(items),
            matched_count=sum(1 for i in items if i.disagreement_type == DisagreementType.MATCHED),
            valid_split_count=sum(1 for i in items if i.disagreement_type == DisagreementType.VALID_SPLIT),
            duplicate_in_b_count=sum(1 for i in items if i.disagreement_type == DisagreementType.DUPLICATE_IN_B),
            missing_in_b_count=sum(1 for i in items if i.disagreement_type == DisagreementType.MISSING_IN_B),
            orphan_in_b_count=sum(1 for i in items if i.disagreement_type == DisagreementType.ORPHAN_IN_B),
            value_mismatch_count=sum(1 for i in items if i.disagreement_type == DisagreementType.VALUE_MISMATCH),
            base_value_recorded_count=sum(1 for i in items if i.disagreement_type == DisagreementType.BASE_VALUE_RECORDED),
            date_mismatch_count=sum(1 for i in items if i.has_date_mismatch),
            location_mismatch_count=sum(1 for i in items if i.has_location_mismatch),
            voided_record_count=sum(1 for i in items if i.is_voided),
            blank_value_count=sum(1 for i in items if i.is_value_blank),
            invalid_reference_count=sum(1 for i in items if i.disagreement_type == DisagreementType.INVALID_REFERENCE),
        )

        report = ReconciliationReport(items=items, summary=summary)

        if tenant_org_id:
            return report.filter_by_tenant(tenant_org_id)

        return report

    @classmethod
    def reconcile_from_db(cls, tenant_org_id: str | None = None) -> ReconciliationReport:
        """
        Execute reconciliation directly from Django models.

        When tenant_org_id is supplied, database queries are scoped to that
        organisation's locations BEFORE passing records to the engine.  This
        guarantees that:
          - summary.total_system_a counts only the tenant's own A records.
          - summary.total_system_b counts only the tenant's own B entries.
          - No foreign records influence the tenant's reconciliation results.

        Tenant-specific location mappings are scoped to that tenant. Cross-
        tenant records are excluded by the source queries and therefore appear
        as tenant-local missing/orphan items without foreign location metadata.
        Global calls continue to load every location.

        When tenant_org_id is None (admin / auditor scope), all records from
        both organisations are passed to the engine and no post-hoc filtering
        is applied.
        """
        from reconcile.models import Location, SystemARecord, SystemBEntry

        locations_query = Location.objects.select_related("organization")
        if tenant_org_id:
            locations_query = locations_query.filter(organization_id=tenant_org_id)
        locations = locations_query.all()
        loc_map = {loc.location_id: loc.organization.org_id for loc in locations}

        if tenant_org_id:
            # Scope both source queries by tenant in SQL before reconciliation.
            sys_a = list(
                SystemARecord.objects
                .select_related("location")
                .filter(location__organization_id=tenant_org_id)
            )
            sys_b = list(
                SystemBEntry.objects
                .select_related("location")
                .filter(location__organization_id=tenant_org_id)
            )
        else:
            sys_a = list(SystemARecord.objects.select_related("location").all())
            sys_b = list(SystemBEntry.objects.select_related("location").all())

        # Pass tenant_org_id=None here: inputs are already tenant-scoped, so
        # filter_by_tenant() is NOT needed again.  The engine will naturally
        # produce MISSING_IN_B for REC-1077 from ORG-A's perspective and
        # ORPHAN_IN_B from ORG-B's perspective because the cross-tenant
        # counterpart simply doesn't exist in the pre-filtered input sets.
        # summary.total_system_a and summary.total_system_b will correctly
        # reflect the tenant-scoped record counts.
        return cls.reconcile(sys_a, sys_b, loc_map, tenant_org_id=None)
