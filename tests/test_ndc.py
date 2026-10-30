"""ML-01 tests for pipeline.ndc (AC-4.1)."""
import pytest

from pipeline.ndc import (
    extract_ndc_from_presentation,
    normalize_ndc,
    normalize_product_ndc,
)


@pytest.mark.parametrize(
    "raw, ndc11",
    [
        ("0378-6860-77", "00378686077"),  # 4-4-2
        ("43066-013-10", "43066001310"),  # 5-3-2
        ("50242-0040-6", "50242004006"),  # 5-4-1
        ("00378-6860-77", "00378686077"),  # already 5-4-2
        ("00378686077", "00378686077"),  # 11 digits plain
        ("  NDC 0378-6860-77 ", "00378686077"),  # prefix and whitespace
    ],
)
def test_formats(raw, ndc11):
    r = normalize_ndc(raw)
    assert r.ndc11 == ndc11
    assert r.product_ndc9 == ndc11[:9]
    assert r.reason is None


def test_ten_digit_unhyphenated_is_ambiguous_not_guessed():
    r = normalize_ndc("0378686077")
    assert r.ndc11 is None and r.product_ndc9 is None
    assert r.reason == "ambiguous_10_digit_unhyphenated"


@pytest.mark.parametrize(
    "raw, reason_prefix",
    [
        ("0378-68A0-77", "non_numeric"),
        ("abc", "non_numeric"),
        ("378-6860-77", "bad_segment_lengths"),  # leading zero dropped
        ("1234-5678", "bad_segment_count"),
        ("123456789", "wrong_length"),
        ("123456789012", "wrong_length"),
        ("", "empty"),
        ("   ", "empty"),
        (None, "empty"),
        (float("nan"), "empty"),
        ([1, 2], "empty"),
    ],
)
def test_invalid_inputs_return_reason(raw, reason_prefix):
    r = normalize_ndc(raw)
    assert r.ndc11 is None
    assert r.reason.startswith(reason_prefix)


def test_product_ndc():
    assert normalize_product_ndc("0378-6860") == "003786860"
    assert normalize_product_ndc("43066-013") == "430660013"
    assert normalize_product_ndc("43066-0013") == "430660013"
    assert normalize_product_ndc("bad") is None
    assert normalize_product_ndc(None) is None


def test_extract_from_presentation():
    text = "Rocuronium Bromide, Injection, 100 mg/10 mL (NDC 43066-013-10)"
    assert extract_ndc_from_presentation(text) == "43066-013-10"
    assert extract_ndc_from_presentation("no ndc here") is None
    assert extract_ndc_from_presentation(None) is None
    assert extract_ndc_from_presentation("Item 0378-6860-77 only") == "0378-6860-77"
