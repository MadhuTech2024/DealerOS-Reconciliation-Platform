import re


# Regular expression matching transaction references:
# Optional case-insensitive 'REC' prefix, optional hyphens/underscores/spaces, followed by 3-6 digits.
# Examples: 'REC-1001', 'rec1034', ' REC - 1070 ', '1112', 'REC 1050'
REFERENCE_PATTERN = re.compile(r"^(?:REC)?[\s\-_]*(\d{3,6})$", re.IGNORECASE)


def normalize_reference(raw_ref: str | None) -> str:
    """
    Normalizes a transaction reference into the canonical format 'REC-<digits>'.

    Handles:
    - Whitespace trimming and internal whitespace removal around delimiters.
    - Case insensitivity (e.g. 'rec1034' -> 'REC-1034').
    - Optional or missing prefixes (e.g. '1112' -> 'REC-1112').
    - Irregular delimiters (e.g. ' REC - 1070 ' -> 'REC-1070').

    Safety:
    - Returns empty string '' for None, empty, or whitespace-only inputs.
    - If the input does not match a recognizable reference pattern (e.g. 'UNKNOWN', 'CAT-01'),
      returns the stripped uppercase string without falsely fabricating a 'REC-' identifier.
    """
    if raw_ref is None:
        return ""

    cleaned = raw_ref.strip()
    if not cleaned:
        return ""

    match = REFERENCE_PATTERN.match(cleaned)
    if match:
        digits = match.group(1)
        return f"REC-{digits}"

    # Return uppercase representation if not matching canonical numeric pattern
    return cleaned.upper()


def is_canonical_reference(ref: str | None) -> bool:
    """Check if reference is already in canonical 'REC-XXXX' format."""
    if not ref:
        return False
    return bool(re.match(r"^REC-\d{3,6}$", ref))


def is_valid_reference(raw_ref: str | None) -> bool:
    """Check if raw reference can be normalized into a canonical reference."""
    if not raw_ref:
        return False
    return bool(REFERENCE_PATTERN.match(raw_ref.strip()))
