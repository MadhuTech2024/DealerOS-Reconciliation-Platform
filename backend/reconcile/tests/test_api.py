import pytest
from decimal import Decimal
from rest_framework.test import APIClient


@pytest.fixture
def imported_assessment_data(db):
    from django.core.management import call_command

    call_command("import_csv_data", replace=True)
    return APIClient()


def test_disagreements_require_a_supported_organization(imported_assessment_data):
    client = imported_assessment_data

    assert client.get("/api/disagreements/").status_code == 400
    response = client.get("/api/disagreements/?org=ORG-UNKNOWN")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_organization"
    assert "ORG-A" not in response.content.decode()


@pytest.mark.parametrize("org", ["ORG-A", "ORG-B"])
def test_disagreements_are_scoped_to_requested_organization(imported_assessment_data, org):
    response = imported_assessment_data.get(f"/api/disagreements/?org={org}")

    assert response.status_code == 200
    body = response.json()
    assert body["org"] == org
    assert body["count"] == len(body["results"])
    assert body["results"]
    assert all(row["record_id"] and row["reason"] for row in body["results"])


def test_summary_requires_a_supported_organization(imported_assessment_data):
    assert imported_assessment_data.get("/api/summary/").status_code == 400
    response = imported_assessment_data.get("/api/summary/?org=ORG-UNKNOWN")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_organization"


def test_api_rejects_non_get_requests_with_json(imported_assessment_data):
    response = imported_assessment_data.post("/api/disagreements/?org=ORG-A")

    assert response.status_code == 405
    assert response["Allow"] == "GET"
    assert response.json()["error"] == "method_not_allowed"


def test_reason_filter_is_validated_and_applied(imported_assessment_data):
    response = imported_assessment_data.get(
        "/api/disagreements/?org=ORG-A&reason=VALUE_MISMATCH"
    )
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["results"][0]["record_id"] == "REC-1064"

    invalid = imported_assessment_data.get(
        "/api/disagreements/?org=ORG-A&reason=NOT_A_REASON"
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"] == "invalid_reason"


@pytest.mark.parametrize(
    "sort,field,reverse",
    [
        ("value", "a_value", False),
        ("-value", "a_value", True),
        ("b_value", "b_value", False),
        ("-b_value", "b_value", True),
        ("difference", "difference", False),
        ("-difference", "difference", True),
    ],
)
def test_monetary_sorting_places_nulls_last(imported_assessment_data, sort, field, reverse):
    response = imported_assessment_data.get(f"/api/disagreements/?org=ORG-A&sort={sort}")
    assert response.status_code == 200
    values = [row[field] for row in response.json()["results"]]
    present = [value for value in values if value is not None]
    parsed = [Decimal(value) for value in present]

    assert parsed == sorted(parsed, reverse=reverse)
    assert values[len(present):] == [None] * (len(values) - len(present))


def test_sorting_validation_and_empty_results(imported_assessment_data):
    invalid = imported_assessment_data.get("/api/disagreements/?org=ORG-A&sort=secret")
    assert invalid.status_code == 400
    assert invalid.json()["error"] == "invalid_sort"

    empty = imported_assessment_data.get("/api/disagreements/?org=ORG-A&reason=MATCHED")
    assert empty.status_code == 200
    assert empty.json() == {"org": "ORG-A", "count": 0, "results": []}


def test_cross_tenant_location_details_are_not_serialized(imported_assessment_data):
    response_a = imported_assessment_data.get(
        "/api/disagreements/?org=ORG-A&reason=MISSING_IN_B"
    )
    body_a = response_a.content.decode()
    assert response_a.status_code == 200
    assert "REC-1077" in body_a
    assert "LOC-201" not in body_a
    assert "ORG-B" not in body_a
    assert "ENT/2026/4077" not in body_a

    all_a = imported_assessment_data.get("/api/disagreements/?org=ORG-A")
    assert "REC-1999" in all_a.content.decode()
    all_b = imported_assessment_data.get("/api/disagreements/?org=ORG-B")
    assert "REC-1999" not in all_b.content.decode()
    response_b = imported_assessment_data.get(
        "/api/disagreements/?org=ORG-B&reason=ORPHAN_IN_B"
    )
    body_b = response_b.content.decode()
    assert response_b.status_code == 200
    assert "REC-1077" in body_b
    assert "LOC-102" not in body_b
    assert "ORG-A" not in body_b


def test_summary_counts_are_tenant_scoped(imported_assessment_data):
    summary_a = imported_assessment_data.get("/api/summary/?org=ORG-A")
    summary_b = imported_assessment_data.get("/api/summary/?org=ORG-B")

    assert summary_a.status_code == summary_b.status_code == 200
    a = summary_a.json()
    b = summary_b.json()
    assert a["org"] == "ORG-A"
    assert b["org"] == "ORG-B"
    assert a["total_disagreements"] == sum(a["reasons"].values())
    assert b["total_disagreements"] == sum(b["reasons"].values())
    assert a["location_mismatches"] == 0
    assert b["location_mismatches"] == 0
    assert a["missing_in_b"] > 0
    assert b["orphan_in_b"] > 0


def test_unexpected_reconciliation_failure_returns_safe_error(
    imported_assessment_data, monkeypatch
):
    from reconcile.engine import ReconciliationEngine

    def fail(*args, **kwargs):
        raise RuntimeError("internal detail")

    monkeypatch.setattr(ReconciliationEngine, "reconcile_from_db", fail)
    response = imported_assessment_data.get("/api/disagreements/?org=ORG-A")

    assert response.status_code == 500
    assert response.json() == {
        "error": "reconciliation_unavailable",
        "message": "The reconciliation results could not be loaded.",
    }
    assert "internal detail" not in response.content.decode()
