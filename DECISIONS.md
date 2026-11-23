# Architectural & Engineering Decisions (DECISIONS.md)

This document records the foundational architectural, engineering, and data-integrity decisions implemented in **Phase 2: Database Models and CSV Importer**.

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

## 4. Non-Destructive Ingestion for Unresolved Location References

* **Context:** A source row in System A or System B might reference an unknown or invalid `location_id`.
* **Decision:** The foreign key `location` is nullable with `on_delete=models.SET_NULL`. When an unresolved location is encountered, `raw_location_id` stores the string, `location` is set to `None`, an `import_warning` is logged, and the row is successfully saved.
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
    * The incoming `locations.csv` is pre-scanned to extract the exact `location_id`s and `org_id`s owned by the import.
    * Replacement deletes only matching locations: `Location.objects.filter(location_id__in=incoming_loc_ids).delete()`.
    * Clean up only organizations from this incoming import that have no remaining locations attached: `Organization.objects.filter(org_id__in=incoming_org_ids, locations__isnull=True).delete()`.
* **Rationale:** Fully preserves unrelated organizations and locations already present in the database, eliminates accidental data wiping, and guarantees clean, idempotent reloads.
