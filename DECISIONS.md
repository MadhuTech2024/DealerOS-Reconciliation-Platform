# Architectural & Engineering Decisions (DECISIONS.md)

This document records architectural, engineering, and data-integrity decisions implemented across the data model, importer, reconciliation engine, API, and dashboard.

---

## 1. Organization Ownership Exclusively from `locations.csv`

* **Context:** In multi-tenant systems, tenant hierarchy can accidentally be inferred from transaction records, creating circular dependencies or data corruption if a transaction has a mistyped location.
* **Decision:** `Organization` instances and their ownership over `Location` instances are populated exclusively from `locations.csv`. Neither System A nor System B can create organizations or alter location-organization relationships.
* **Rationale:** Establishes a single source of truth for corporate hierarchy, fulfilling the tenant isolation requirement.

---

## 2. Safe Source-Row Identity vs. Business Reference Uniqueness

* **Context:** Database models often place unique constraints on external identifiers (e.g. `unique=True` on `record_id` or `record_ref`).
* **Decision:** Uniqueness constraints are strictly defined on `(source_file, source_row_number)` rather than business references.
* **Rationale:**
  * System B contains legitimate duplicate references (`REC-1042` appears twice) and valid split payments (`REC-1055` appears twice). A unique constraint on `record_ref` would reject these valid business records during ingestion.
  * Composite identity `(source_file, source_row_number)` prevents accidental double-imports of the same file while allowing valid duplicate and split records to be stored.

---

## 3. Preserving Raw Source Values Alongside Typed Representations

* **Context:** Parsing source data into strict types (dates, decimals) often discards the original formatting, casing, and whitespace.
* **Decision:** Models store both the parsed/typed attributes (`value`, `location`, `recorded_on`) and the raw source strings (`raw_value`, `raw_record_ref`, `raw_location_id`, `raw_data` JSONField).
* **Rationale:**
  * Financial audits require non-repudiation and traceability back to source files.
  * If a transaction has a non-standard reference (`rec1034`, ` REC - 1070 `), the exact syntax is preserved for auditing and debugging.

---

## 4. Non-Destructive Ingestion for Unresolved or Malformed Rows

* **Context:** A source row in System A or System B might reference an unknown or invalid `location_id`.
* **Decision:** The foreign key `location` is nullable with `on_delete=models.SET_NULL`. When an unresolved location is encountered, `raw_location_id` and the raw source row are retained, `location` is set to `None`, and an `import_warning` is saved. Location rows with missing ownership keys or duplicate IDs are retained in `ImportIssue` rather than assigned synthetic ownership. Short or otherwise malformed rows do not abort the import; rows with enough ownership data can still update the location, while a blank name does not erase an existing name.
* **Rationale:** Violating foreign key constraints must never cause silent row dropping. All operational and ledger rows must be preserved.

---

## 5. Arbitrary-Precision `DecimalField` and Indian Lakh Currency Parsing

* **Context:** System B row 63 contains `"1,25,400.00"` using the Indian numbering system (comma after thousands and then every two digits). Binary `float` introduces rounding inaccuracies.
* **Decision:** All monetary values are parsed with `parse_decimal_safe` into Python `decimal.Decimal` and stored in `DecimalField(max_digits=14, decimal_places=2)`. Commas are sanitized in a locale-agnostic manner.
* **Rationale:** Exact decimal arithmetic eliminates floating-point precision errors (e.g. `0.1 + 0.2 != 0.3`) and supports regional financial formatting without host OS locale dependencies.

---

## 6. Strict Blank Monetary Value Preservation (Never Coerced to Zero)

* **Context:** System B row 50 (`ENT/2026/4050`) has an empty string `""` in the `value` column.
* **Decision:** Blank monetary inputs are stored as `None` (SQL `NULL`) with `is_value_blank=True`. They are never converted to `Decimal('0.00')`.
  * For a non-voided matched System A transaction, any blank System B amount is classified as `BLANK_VALUE`; its numeric difference is unknown.
  * For an orphan System B group, the aggregate amount and difference are `None` if any entry is blank. Per-entry details retain known values and null entries, so a partial sum is never presented as a complete total.
* **Rationale:** In accounting, blank signifies omitted or unrecorded data, whereas `0.00` represents a zero-dollar transaction. Coercing blank to zero creates phantom transactions and distorts ledger reconciliations.

---

## 7. Deferring Reference Normalization to Phase 3

* **Context:** References in System B have messy variations (`rec1034`, `1112`, ` REC - 1070 `).
* **Decision:** The importer stores the raw references as-is without altering casing, spacing, or prefixes. Reference normalization is strictly deferred to the reconciliation engine in Phase 3.
* **Rationale:** Separation of concerns: the ingestion layer must remain an objective, faithful record of source input, leaving interpretation and reconciliation to the domain engine.

---

## 8. Atomic Transactions and Safely Scoped CSV Replacement (`--replace`)

* **Context:** Network interruptions or database errors during multi-file imports can leave partial, corrupt datasets. Re-running imports could silently duplicate data. Furthermore, an unconstrained `.all().delete()` on `Location` and `Organization` risked deleting unrelated multi-tenant organizations or locations present in the database.
* **Decision:**
  * Ingestion runs inside a single `transaction.atomic()` block. Any error rolls back all changes.
  * If records already exist, the importer halts with a `CommandError` unless `--replace` is explicitly provided.
  * When `--replace` is provided:
    * System A records are deleted selectively by `source_file=system_a_filename`.
    * System B entries are deleted selectively by `source_file=system_b_filename`.
    * The incoming `locations.csv` is read row-by-row; malformed rows are retained as `ImportIssue` records, and repeated IDs are treated as duplicate issues.
    * Existing locations are updated in place from `locations.csv`; they are not deleted during replacement, preserving foreign keys from independently sourced A/B records.
    * Organizations and locations not present in the incoming file are retained; replacement does not infer that they are safe to delete.
* **Rationale:** Re-imports source-owned organization/location data idempotently without detaching unrelated records or deleting unrelated tenant data.

---

## 9. Reference Normalization and Total-Value Reconciliation

* **Context:** System B contains formatting variants (`rec1034`, ` REC - 1070 `, `1112`), and comparing only System A's base value would miss adjustments.
* **Decision:** `normalize_reference` uses `^(?:REC)?[\s\-_]*(\d{3,6})$` to canonicalize references. The decoupled reconciliation engine compares System B amounts with System A `total_value`, and distinguishes valid splits from duplicates.
* **Rationale:**
  * Canonicalizes references to uppercase `REC-<digits>` for fast hash map lookups.
  * Returns original uppercase strings for non-transaction strings (`CAT-02`, `UNKNOWN`) to avoid false positive matching.
  * Pure-Python reconciliation accepts record sequences rather than Django queries, keeping business rules independently testable.
  * An amount equal to `base_value` with a non-zero adjustment is classified as `BASE_VALUE_RECORDED` for diagnosis, while the expected amount remains `total_value`.
  * For multiple nonblank B entries, a sum equal to `total_value` is a valid split; any other sum is `DUPLICATE_IN_B`. A blank in a matched group is classified as `BLANK_VALUE` instead of split/duplicate.
  * A `VOIDED` A record with one or more B entries is classified as `VOIDED_RECORD` before blank, split, duplicate, amount, date, or location classification. A voided record with no B entry remains `MISSING_IN_B` and carries the voided flag.
  * For non-voided matched records, blank values take precedence, followed by split/duplicate, base-value-recorded, amount mismatch, location mismatch, then date mismatch. Location mismatch therefore takes precedence over date mismatch when both flags are true; the flags are still retained.
  * A date mismatch is classified only after a single B amount matches A's total and the locations match. For multi-entry groups, split/duplicate classification takes precedence while date/location flags are retained.

---

## 10. Tenant-Scoped Classification and Defence-in-Depth Masking (`REC-1077`)

* **Context:** `REC-1077` was recorded at `LOC-102` (`ORG-A`) in System A, but at `LOC-201` (`ORG-B`) in System B. Amounts and dates match. This is a genuine cross-tenant location mismatch.

* **Global (admin/auditor) view:** `disagreement_type = LOCATION_MISMATCH`. Both `location_id_a`, `organization_id_a`, `location_id_b`, and `organization_id_b` are populated. Full cross-tenant detail is available to administrators.
* **Same-tenant location mismatch:** Different A/B location IDs are classified as `LOCATION_MISMATCH` even when both locations belong to the same organization. If values also match but the dates differ, the location mismatch is the classification and both mismatch flags remain set.
* **Orphan B ownership:** The first B entry in a normalized-reference group supplies the displayed location and organization, which are resolved from that B location. Tenant-specific database reconciliation filters B entries and location mappings to that organization before grouping, so the tenant sees only its own orphan details.

* **Tenant-scoped classification — the agreed business rule:**
  When `tenant_org_id` is supplied to `ReconciliationEngine.reconcile()` or `filter_by_tenant()`, each cross-tenant `LOCATION_MISMATCH` item is **re-classified** using the following rule:
  - **Tenant owns the A-side only** (ORG-A for REC-1077):
    The corresponding System B entry lives in a foreign organisation and is therefore invisible to this tenant.  The item is reclassified as **`MISSING_IN_B`** — from ORG-A's perspective, its System A transaction has no matching entry in System B within its own domain.
    Fields cleared: `organization_id_b`, `location_id_b`, `system_b_entry_ids`, `system_b_recorded_amount`, `date_b`.
    `difference` = negative A amount.
    `has_location_mismatch` = `False` (the mismatch detail is not surfaced to the tenant).
  - **Tenant owns the B-side only** (ORG-B for REC-1077):
    The corresponding System A record lives in a foreign organisation and is invisible to this tenant. The item is reclassified as **`ORPHAN_IN_B`** — from ORG-B's perspective, its System B entry has no corresponding System A transaction within its own domain.
    Fields cleared: `organization_id_a`, `location_id_a`, `system_a_record_id`, `system_a_expected_amount`, `system_a_base_amount`, `system_a_adjustment`, `date_a`, `state_a`.
    `difference` = positive B amount.
    `has_location_mismatch` = `False`.

* **Defence-in-depth serialization masking in `to_dict(tenant_org_id)`:**
  Even after correct classification, the serializer applies a second masking layer:
  1. **Location masking** — foreign `location_id` → `None`.
  2. **Org-ID masking** — foreign `organization_id` → `None`.
  3. **Side masking** — for `MISSING_IN_B`: all B-side fields are cleared; for `ORPHAN_IN_B`: all A-side fields are cleared.
  4. **Details masking** — per-entry `location_id` values belonging to the foreign organisation → `None`.
  5. **Message masking** — tenant-safe message is used; it must not contain the foreign org ID or location ID.

* **Rationale:**
  * *Classification correctness*: Exposing `LOCATION_MISMATCH` to a tenant would implicitly reveal the existence of a foreign transaction and organisation. Re-classifying to `MISSING_IN_B` or `ORPHAN_IN_B` reflects the actual business reality from each tenant's information domain.
  * *Defence in depth*: `to_dict()` masking is a second safety net at serialization time. The primary protection is the upfront classification transformation in `filter_by_tenant()`.
  * REST API views enforce tenant filtering at the **database query level** (`SystemARecord.objects.filter(location__organization_id=tenant)`) in addition to engine-level classification. Response masking is an additional safeguard, not a replacement for query-level isolation.
