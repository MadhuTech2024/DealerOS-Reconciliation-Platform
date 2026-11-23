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

* **Backend Framework:** Django 5.2 (Python 3.13)
* **API Layer:** Django REST Framework (DRF)
* **Database:** SQLite
* **Cross-Origin Handling:** django-cors-headers
* **Testing Framework:** Pytest & pytest-django
* **Frontend:** React + Vite (planned for subsequent phase)
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
* **Indian Lakh & Western Comma Parsing:** Converts `"1,25,400.00"` to `Decimal('125400.00')`.
* **Blank Preservation:** Leaves blank monetary values as `None`; never coerces them to `0.00`.
* **Atomic Transactions:** Entire import runs inside `transaction.atomic()`. Any unexpected failure triggers an immediate rollback, leaving no partial records.
* **Accidental Duplicate Protection:** Re-running without `--replace` halts with an informative `CommandError`.
* **Safely Scoped Replacement:** When `--replace` is used, replacement of organizations and locations is strictly scoped to the incoming `locations.csv` identifiers. Unrelated organizations and locations already present in the database are protected and preserved.

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

## 6. Important Assumptions & Limitations in Phase 2

1. **Reference Normalization Deferred:** References in System B (`rec1034`, ` REC - 1070 `, `1112`) are preserved as raw strings. Reference normalization and fuzzy cleaning are implemented in Phase 3.
2. **Reconciliation Engine Deferred:** Matching algorithms, split payment aggregations, and difference classifications belong to Phase 3.
3. **API & UI Deferred:** REST endpoints and React frontend components are scheduled for Phases 4 and 5.

---

## 7. Development Progress

| Phase | Description | Status |
| :--- | :--- | :--- |
| **Phase 0** | CSV Data Audit & Empirical Anomaly Analysis | Completed |
| **Phase 1** | Project Initialization, Git, Docs, Virtualenv & Django Setup | Completed |
| **Phase 2** | Django Database Models, Migrations & Robust CSV Importer | **Completed** |
| **Phase 3** | Domain Reconciliation Engine, Reference Normalizer & Unit Tests | Pending Approval |
| **Phase 4** | REST API Serializers, Scoped Viewsets & API Tests | Pending Approval |
| **Phase 5** | React + Vite Frontend (Tenant Switcher, Dashboard, Modals) | Pending Approval |
| **Phase 6** | End-to-End Validation & Interview Documentation | Pending Approval |
