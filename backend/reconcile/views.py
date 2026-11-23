import logging
from decimal import Decimal

from django.http import JsonResponse

from reconcile.engine import DisagreementType, ReconciliationEngine
from reconcile.models import Organization

logger = logging.getLogger(__name__)

NON_DISAGREEMENT_TYPES = {
    DisagreementType.MATCHED.value,
    DisagreementType.VALID_SPLIT.value,
}
SORT_FIELDS = {"value", "a_value", "b_value", "difference", "record_id", "reason"}
REASON_TYPES = {reason.value for reason in DisagreementType}


def _error(message, code, status):
    return JsonResponse({"error": code, "message": message}, status=status)


def _require_get(request):
    if request.method == "GET":
        return None
    response = _error("Only GET requests are supported.", "method_not_allowed", 405)
    response["Allow"] = "GET"
    return response


def _get_organization(request):
    org_values = request.GET.getlist("org")
    if len(org_values) != 1 or not org_values[0].strip():
        return None, _error(
            "The org query parameter is required exactly once.",
            "invalid_organization",
            400,
        )

    org_id = org_values[0].strip()
    try:
        organization_exists = Organization.objects.filter(org_id=org_id).exists()
    except Exception:
        logger.exception("Failed to validate requested organization")
        return None, _error(
            "The requested organization could not be validated.",
            "organization_unavailable",
            500,
        )
    if not organization_exists:
        return None, _error(
            "The requested organization is not supported.",
            "invalid_organization",
            400,
        )
    return org_id, None


def _get_disagreement_results(org_id):
    report = ReconciliationEngine.reconcile_from_db(tenant_org_id=org_id)
    return [
        item.to_dict(tenant_org_id=org_id)
        for item in report.items
        if item.disagreement_type.value not in NON_DISAGREEMENT_TYPES
    ]


def _sort_results(results, sort):
    descending = sort.startswith("-")
    field = sort[1:] if descending else sort
    internal_fields = {
        "value": "system_a_expected_amount",
        "a_value": "system_a_expected_amount",
        "b_value": "system_b_recorded_amount",
        "difference": "difference",
        "record_id": "record_id",
        "reason": "disagreement_type",
    }
    internal_field = internal_fields[field]

    if field in {"value", "a_value", "b_value", "difference"}:
        present = [row for row in results if row[internal_field] is not None]
        missing = [row for row in results if row[internal_field] is None]
        present.sort(
            key=lambda row: (Decimal(row[internal_field]), row["record_id"]),
            reverse=descending,
        )
        return present + missing

    return sorted(
        results,
        key=lambda row: row[internal_field] or "",
        reverse=descending,
    )


def disagreements(request):
    method_error = _require_get(request)
    if method_error:
        return method_error

    org_id, error_response = _get_organization(request)
    if error_response:
        return error_response

    reason_values = request.GET.getlist("reason")
    if len(reason_values) > 1:
        return _error("The reason parameter may be specified only once.", "invalid_reason", 400)
    reason = reason_values[0].strip().upper() if reason_values else ""
    if reason and reason not in REASON_TYPES:
        return _error("The requested disagreement reason is not supported.", "invalid_reason", 400)

    sort_values = request.GET.getlist("sort")
    if len(sort_values) > 1:
        return _error("The sort parameter may be specified only once.", "invalid_sort", 400)
    sort = sort_values[0].strip() if sort_values else ""
    if sort:
        sort_field = sort.removeprefix("-")
        if sort_field not in SORT_FIELDS or sort in {"-", ""}:
            return _error("The requested sort field is not supported.", "invalid_sort", 400)

    try:
        results = _get_disagreement_results(org_id)
        if reason:
            results = [row for row in results if row["disagreement_type"] == reason]
        if sort:
            results = _sort_results(results, sort)

        payload = [
            {
                "reason": row["disagreement_type"],
                "record_id": row["record_id"],
                "location": row["location_a"] or row["location_b"],
                "a_value": row["system_a_expected_amount"],
                "b_value": row["system_b_recorded_amount"],
                "difference": row["difference"],
                "message": row["message"],
            }
            for row in results
        ]
    except Exception:
        logger.exception("Failed to build tenant-scoped disagreement response for %s", org_id)
        return _error(
            "The reconciliation results could not be loaded.",
            "reconciliation_unavailable",
            500,
        )

    return JsonResponse({"org": org_id, "count": len(payload), "results": payload})


def summary(request):
    method_error = _require_get(request)
    if method_error:
        return method_error

    org_id, error_response = _get_organization(request)
    if error_response:
        return error_response

    try:
        results = _get_disagreement_results(org_id)
        counts = {reason: 0 for reason in REASON_TYPES}
        for row in results:
            counts[row["disagreement_type"]] += 1
    except Exception:
        logger.exception("Failed to build tenant-scoped summary response for %s", org_id)
        return _error(
            "The reconciliation summary could not be loaded.",
            "reconciliation_unavailable",
            500,
        )

    return JsonResponse(
        {
            "org": org_id,
            "total_disagreements": len(results),
            "missing_in_b": counts[DisagreementType.MISSING_IN_B.value],
            "orphan_in_b": counts[DisagreementType.ORPHAN_IN_B.value],
            "duplicate_in_b": counts[DisagreementType.DUPLICATE_IN_B.value],
            "value_mismatches": (
                counts[DisagreementType.VALUE_MISMATCH.value]
                + counts[DisagreementType.BASE_VALUE_RECORDED.value]
            ),
            "location_mismatches": counts[DisagreementType.LOCATION_MISMATCH.value],
            "date_mismatches": counts[DisagreementType.DATE_MISMATCH.value],
            "reasons": counts,
        }
    )
