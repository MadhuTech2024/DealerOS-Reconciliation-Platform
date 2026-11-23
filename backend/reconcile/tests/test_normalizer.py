import pytest
from reconcile.reference_normalizer import (
    normalize_reference,
    is_canonical_reference,
    is_valid_reference,
)


def test_normalize_exact_spec_examples():
    """Verify the 3 specific examples required by the assessment prompt."""
    assert normalize_reference("rec1034") == "REC-1034"
    assert normalize_reference(" REC - 1070 ") == "REC-1070"
    assert normalize_reference("1112") == "REC-1112"


def test_normalize_canonical_references():
    """Standard format should remain unchanged."""
    assert normalize_reference("REC-1001") == "REC-1001"
    assert normalize_reference("REC-1120") == "REC-1120"
    assert normalize_reference("REC-1999") == "REC-1999"


def test_normalize_whitespace_and_casing():
    """Test varying case, spaces, tabs, and hyphens."""
    assert normalize_reference("rec-1005") == "REC-1005"
    assert normalize_reference("rEc-1005") == "REC-1005"
    assert normalize_reference("REC  1005") == "REC-1005"
    assert normalize_reference("  REC-1005  ") == "REC-1005"
    assert normalize_reference("rec_1005") == "REC-1005"


def test_normalize_bare_numbers():
    """Bare 4-digit numbers should be prefixed with REC-."""
    assert normalize_reference("1001") == "REC-1001"
    assert normalize_reference(" 1050 ") == "REC-1050"
    assert normalize_reference("9999") == "REC-9999"


def test_normalize_blank_and_none():
    """Blank or None inputs should safely return empty string."""
    assert normalize_reference(None) == ""
    assert normalize_reference("") == ""
    assert normalize_reference("   ") == ""


def test_normalize_unrelated_inputs():
    """Non-matching inputs should not be falsely coerced into REC- format."""
    assert normalize_reference("CAT-02") == "CAT-02"
    assert normalize_reference("UNKNOWN_REF") == "UNKNOWN_REF"
    assert normalize_reference("ENT/2026/4001") == "ENT/2026/4001"


def test_reference_validators():
    assert is_canonical_reference("REC-1001") is True
    assert is_canonical_reference("rec1001") is False
    assert is_canonical_reference("1001") is False
    assert is_canonical_reference("") is False
    assert is_canonical_reference(None) is False

    assert is_valid_reference("rec1034") is True
    assert is_valid_reference(" REC - 1070 ") is True
    assert is_valid_reference("1112") is True
    assert is_valid_reference("REC-1001") is True
    assert is_valid_reference("CAT-02") is False
    assert is_valid_reference("") is False
    assert is_valid_reference(None) is False
