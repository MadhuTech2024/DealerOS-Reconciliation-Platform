# AdosX – DealerOS Full-Stack Financial Reconciliation Platform

AdosX is an enterprise-grade financial transaction reconciliation platform designed for multi-tenant automotive dealership networks (DealerOS). The system reconciles operational dealership transactions (System A) against accounting ledger entries (System B) across geographically distributed dealership locations and organizational entities.

---

## 1. Project Overview

In dealership operations, financial activities (sales, service orders, parts billing) originate in operational management systems (System A) and are synchronized with accounting and financial systems (System B). During synchronization, real-world data discrepancies emerge:
* Split payments where a single transaction is settled across multiple payments.
* Omission of adjustments where only base values are transferred.
* Formatting and data entry variations (casing differences, spacing errors, missing prefixes).
* Regional number formatting (such as Indian Lakh comma grouping: `1,25,400.00`).
* Missing or duplicate records.
* Timing delays resulting in recording date differences.
* Misallocated store locations across corporate boundaries.

AdosX provides an automated, deterministic reconciliation engine, a robust REST API, and an intuitive dashboard to identify, classify, and audit all transaction discrepancies while strictly preserving tenant boundaries.

---

## 2. Technology Stack

* **Backend Framework:** Django 5.2 (Python 3.11+)
* **API Layer:** Django REST Framework (DRF)
* **Database:** SQLite
* **Cross-Origin Handling:** django-cors-headers
* **Testing Framework:** Pytest & pytest-django
* **Frontend:** React + Vite
* **Version Control:** Git

---

## 3. Database Model Architecture (Phase 2)

The database models in `backend/reconcile/models.py` model the source datasets without data loss or premature assumptions:

1. **`Organization`**:
   * Represents the legal dealership entity (`ORG-A`, `ORG-B`).
   * **Rule:** Ownership is derived exclusively from `locations.csv`. Never inferred from transaction records.
2. **`Location`**:
   * Represents physical dealership facilities (`LOC-101`, `LOC-201`, etc.).
   * Foreign key to `Organization`. Retains source fields and raw data dictionary.
3. **`SystemARecord`**:
   * Stores operational transactions from `system_a.csv` (`REC-1001` through `REC-1120`).
   * Captures `base_value`, `adjustment`, and `total_value` as `DecimalField(max_digits=14, decimal_places=2)`.
   * Preserves `actor_id` as nullable (supporting missing actors like row 51 / `REC-1050`).
   * Foreign key to `Location` is nullable (`SET_NULL`) with `raw_location_id` preserved to guarantee rows with unresolved locations are never dropped.
   * Enforces composite row identity `(source_file, source_row_number)` to prevent accidental duplicate imports without constraining business references.
4. **`SystemBEntry`**:
   * Stores accounting/ledger entries from `system_b.csv` (`ENT/2026/4001` through `ENT/2026/4903`).
   * Stores original `record_ref` verbatim (`rec1034`, ` REC - 1070 `, `1112`) without premature normalization.
   * Captures `raw_value` as string and `value` as `DecimalField` (or `None` when blank).
   * Flags `is_value_blank=True` when monetary input is omitted.
   * Allows duplicate business references (`REC-1042`, `REC-1055`) without constraint collisions.
5. **`ImportIssue`**:
   * Retains raw rows and messages when a location row has no ownership keys or repeats a location ID that cannot be represented without ambiguity.

---

## 4. CSV Import Command

The custom Django management command ingests all source CSV files atomically:

```powershell
# Standard import (fails safely if data has already been imported)
.venv\Scripts\python.exe backend\manage.py import_csv_data

# Replace existing data (safe idempotent reload)
.venv\Scripts\python.exe backend\manage.py import_csv_data --replace

# Custom directory or custom file overrides (useful for testing)
.venv\Scripts\python.exe backend\manage.py import_csv_data --data-dir path/to/csvs
.venv\Scripts\python.exe backend\manage.py import_csv_data --locations-file path/to/loc.csv --system-a-file path/to/a.csv --system-b-file path/to/b.csv
```

### Key Importer Behaviors:
* **Zero Data Loss:** Never skips rows with missing fields or corrupt values. Unresolved references and unparseable values are flagged in `import_warning` while the row is stored.
* **Malformed Location Rows:** Rows that cannot become a `Location` are persisted in `ImportIssue` with their original data and a row-specific reason; duplicate location IDs do not abort the import.
* **Indian Lakh & Western Comma Parsing:** Converts `"1,25,400.00"` to `Decimal('125400.00')`.
* **Blank Preservation:** Leaves blank monetary values as `None`; never coerces them to `0.00`.
* **Atomic Transactions:** Entire import runs inside `transaction.atomic()`. Any unexpected failure triggers an immediate rollback, leaving no partial records.
* **Accidental Duplicate Protection:** Re-running without `--replace` halts with an informative `CommandError`.
* **Safe Replacement:** When `--replace` is used, System A and System B records are replaced transactionally by source file. Incoming locations are updated in place rather than deleted, preserving existing foreign-key relationships. Organizations and locations not present in the incoming file are retained.

---

## 5. Verification & Testing

### Run Django System Check
```powershell
.venv\Scripts\python.exe backend\manage.py check
```

### Run Migrations
```powershell
.venv\Scripts\python.exe backend\manage.py migrate
```

### Run Pytest Suite
```powershell
.venv\Scripts\python.exe -m pytest -v
```

### Verify Imported Database Record Counts
```powershell
.venv\Scripts\python.exe -c "
import os, django; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import sys; sys.path.insert(0, 'backend'); django.setup()
from reconcile.models import Organization, Location, SystemARecord, SystemBEntry
print('Organizations:', Organization.objects.count())  # 2
print('Locations:    ', Location.objects.count())      # 5
print('System A:     ', SystemARecord.objects.count())  # 120
print('System B:     ', SystemBEntry.objects.count())   # 121
print('Total rows:   ', Location.objects.count() + SystemARecord.objects.count() + SystemBEntry.objects.count()) # 246
"
```

---

## 6. Domain Reconciliation Engine & Reference Normalizer (Phase 3)

### A. Reference Normalizer (`backend/reconcile/reference_normalizer.py`)
Deterministic reference normalizer standardizing messy incoming references into canonical uppercase `REC-<digits>`:
* `rec1034` &rarr; `REC-1034`
* ` REC - 1070 ` &rarr; `REC-1070`
* `1112` &rarr; `REC-1112`
* Preserves original raw references in the database; non-matching inputs (`CAT-02`, `UNKNOWN`) are safely preserved without synthetic prefixing.

### B. Reconciliation Engine (`backend/reconcile/engine.py`)
Pure-Python, decoupled domain engine operating on abstract collections of records:
* **Comparison Rule:** Always compares System B values against System A's `total_value` (`base_value + adjustment`).
* **Decimal Precision:** All calculations use Python `Decimal`. Floats are strictly prohibited.
* **10 Discrepancy Categories Detected:**
  1. `MISSING_IN_B`: `REC-1015`, `REC-1061`.
  2. `ORPHAN_IN_B`: `REC-1999` (`ENT/2026/4901`).
  3. `DUPLICATE_IN_B`: `REC-1042` (2 full payments summing to double expected amount).
  4. `VALID_SPLIT`: `REC-1055` (2 split payments summing exactly to `179,877.32`; not flagged as duplicate).
  5. `VALUE_MISMATCH`: `REC-1064` (Indian Lakh `1,25,400.00` parsed as `125400.00` vs expected `183244.16`).
  6. `BASE_VALUE_RECORDED`: `REC-1003`, `REC-1027`, `REC-1088` (System B omitted adjustment).
  7. `DATE_MISMATCH`: `REC-1009` (A: `2026-03-31` vs B: `2026-04-02`).
  8. `LOCATION_MISMATCH`: `REC-1077` (A: `LOC-102` [`ORG-A`] vs B: `LOC-201` [`ORG-B`]).
  9. `VOIDED_RECORD`: `REC-1019` (System A transaction is voided, but System B has active entry).
  10. `BLANK_VALUE`: `REC-1050` (System B value is blank; preserved as unknown, never 0.00).

### C. Multi-Tenant Safety & Cross-Tenant Masking
* For cross-tenant transaction `REC-1077`:
  * When queried in `ORG-A` context: Location A is visible (`LOC-102`), Location B (`LOC-201`) is **masked as `None`**.
  * When queried in `ORG-B` context: Location B is visible (`LOC-201`), Location A (`LOC-102`) is **masked as `None`**.
  * Zero foreign tenant location details are leaked into tenant views.

---

## 7. REST API

The API is read-only and requires an `org` query parameter on every endpoint. Organization IDs are validated against the `Organization` table. The organization selector is an assessment-level tenant filter; it is **not authentication or authorization**.

### `GET /api/disagreements/`

Returns disagreement rows for one organization. `MATCHED` and `VALID_SPLIT` records are not included.

Supported query parameters:

* `org` (required): `ORG-A` or `ORG-B`.
* `reason` (optional): a supported disagreement type, such as `VALUE_MISMATCH` or `MISSING_IN_B`.
* `sort` (optional): `value`, `-value`, `a_value`, `-a_value`, `b_value`, `-b_value`, `difference`, `-difference`, `record_id`, `-record_id`, `reason`, or `-reason`. Null monetary values remain last in either direction.

Example:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/disagreements/?org=ORG-A&reason=VALUE_MISMATCH&sort=-value"
```

Response shape:

```json
{
  "org": "ORG-A",
  "count": 1,
  "results": [
    {
      "reason": "VALUE_MISMATCH",
      "record_id": "REC-1064",
      "location": "LOC-101",
      "a_value": "183244.16",
      "b_value": "125400.00",
      "difference": "-57844.16",
      "message": "..."
    }
  ]
}
```

Amounts are serialized as decimal strings; missing values are JSON `null`. Invalid/missing parameters return HTTP 400 with `error` and `message`. Unexpected reconciliation failures are logged and return a generic HTTP 500 response.

### `GET /api/summary/`

Returns counts scoped to the requested organization, including `total_disagreements`, missing/orphan/duplicate counts, grouped value mismatches, and location/date mismatch counts. The `reasons` object includes counts for each disagreement type. The value-mismatch card combines `VALUE_MISMATCH` and `BASE_VALUE_RECORDED`; blank monetary values remain a separate reason.

Both endpoints scope System A and System B records in database queries using the related location's organization before reconciliation. A cross-tenant mismatch is surfaced as `MISSING_IN_B` to the A-side organization and `ORPHAN_IN_B` to the B-side organization; the foreign side's location and organization are not serialized.

## 8. React Dashboard

The Vite application in `frontend/` loads the selected organization's summary and disagreements from the API. It supports switching between ORG-A and ORG-B, reason filters, refresh, and ascending/descending monetary sorting. It includes loading, error, and empty states, responsive table cards on narrow screens, keyboard-focus indicators, and accessible input labels. The client never requests an unscoped report or filters tenant records in the browser.

The API base URL is configurable with `VITE_API_BASE_URL` in `frontend/.env`; see [frontend/.env.example](frontend/.env.example). Only the public API URL belongs in frontend environment variables. Do not put backend secrets in Vite variables.

## 9. Project Structure

```text
backend/
  config/                 Django settings and URL routing
  data/                   Original assessment CSV files (read-only inputs)
  reconcile/
    engine.py             Pure-Python reconciliation rules
    reference_normalizer.py
    models.py             Source records, tenants, locations, and import issues
    management/commands/  CSV import command
    tests/                Importer, engine, and API regression tests
frontend/
  src/                    React dashboard, API client, and styles
  package.json            Vite scripts and frontend dependencies
  .env.example            Public API URL template
DECISIONS.md              Implemented engineering decisions
requirements.txt          Backend dependencies
```

## 10. Clean-Clone Setup and Run (Windows PowerShell)

Prerequisites: Python 3.11 or newer, Node.js/npm, and Git.

From the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python backend\manage.py check
python backend\manage.py migrate
python backend\manage.py import_csv_data
```

`import_csv_data` refuses to overwrite an existing import unless `--replace` is supplied. To intentionally reload the supplied data, use `python backend\manage.py import_csv_data --replace`.

Run the backend in one PowerShell window:

```powershell
python backend\manage.py runserver 127.0.0.1:8000
```

In a second window, from the repository root:

```powershell
Set-Location frontend
Copy-Item .env.example .env
npm ci
npm run dev
```

Open the Vite URL printed in the terminal (normally `http://127.0.0.1:5173`). CORS is configured for localhost and 127.0.0.1 on that development port.

Backend settings can be overridden with `DJANGO_ENV`, `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, and `DJANGO_ALLOWED_HOSTS`. `.env.example` documents these variables; Django does not load that file automatically. For a local PowerShell session, for example:

```powershell
$env:DJANGO_ENV = "development"
$env:DJANGO_SECRET_KEY = "replace-with-a-random-local-value"
$env:DJANGO_DEBUG = "True"
$env:DJANGO_ALLOWED_HOSTS = "localhost,127.0.0.1"
```

Local development has a built-in fallback secret when none is set. For production, set `DJANGO_ENV=production` and provide a private `DJANGO_SECRET_KEY` of at least 50 characters. Production defaults `DEBUG` to false and rejects an explicit true value; it will not start without the secret. Never use the example placeholder or local fallback in a deployed environment.

## 11. Verification

From the repository root:

```powershell
python backend\manage.py check
python backend\manage.py makemigrations --check --dry-run
python backend\manage.py migrate
python -m pytest -q
Push-Location frontend
npm ci
npm run build
Pop-Location
```

The regression suite verifies the supplied data totals (5 locations, 120 System A rows, 121 System B rows), idempotent replacement, Decimal parsing, blank values, split-versus-duplicate behavior, mismatch rules, tenant-specific summaries, API validation/sorting, and cross-tenant response masking.

There are no committed screenshots.

## 12. Known Limitations and Deliberate Exclusions

* The organization selector is not a security boundary; authentication and authorization are intentionally out of scope for this assessment. Do not expose this service as a multi-tenant production API without real identity and access controls.
* SQLite and the in-memory reconciliation report are appropriate for the supplied assessment dataset, not a high-volume production ledger.
* The API is read-only and unpaginated. It does not expose a transaction-detail or import-history UI.
* Malformed location rows are retained as `ImportIssue` records rather than tenant-owned `Location` records because their ownership cannot safely be inferred.
* No AI-generated screenshots or deployment configuration are included.

## 13. AI Agent Reflection

1. **One thing the agent got wrong and how it was detected:** The first API sorting implementation indexed its internal reconciliation rows with public response field names. The focused API tests raised a `KeyError` for `sort=value`; the sorter was corrected to map the API fields to engine fields, and the same tests then passed.
2. **Least-confident implementation:** The summary groups `BASE_VALUE_RECORDED` into the value-mismatch card while keeping `BLANK_VALUE` separate. That grouping is explicit and tested against the dataset, but should be confirmed with a product owner if card definitions change.
3. **First improvement with another day:** Add a transaction-detail view that presents the retained per-entry provenance and import warnings in the tenant-scoped UI, with focused browser accessibility tests.

| Phase | Description | Status |
| :--- | :--- | :--- |
| **Phase 0–3** | CSV audit, Django models/importer, reconciliation engine and normalizer | Completed |
| **Phase 4** | Tenant-scoped REST API and API regression tests | Completed |
| **Phase 5** | React + Vite reconciliation dashboard | Completed |
| **Phase 6** | Integration verification and interview documentation | Completed |
