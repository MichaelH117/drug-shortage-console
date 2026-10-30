"""DE-06: the SYNTHETIC hospital generator (AC-3.2, AC-3.3, AC-3.4)."""

from __future__ import annotations

import pandas as pd
import pytest

from generator.hospital import generate, leading_zero_error, ndc_to_542, write
from pipeline.config import ROOT

VARIANTS = {"ndc_as_listed", "ndc_11digit", "ndc_5_4_2_hyphen", "ndc_other_package", "ndc_leading_zero_error",
            "name_brand", "name_salt", "name_case_whitespace", "alt_manufacturer", "diff_strength", "diff_route",
            "filler"}


@pytest.fixture(scope="module")
def tables():
    return generate(42)


def test_same_seed_gives_byte_identical_csvs(tmp_path):
    a = write(generate(42), tmp_path / "a")
    b = write(generate(42), tmp_path / "b")
    assert a == b
    for name in a:
        assert (tmp_path / "a" / f"{name}.csv").read_bytes() == (tmp_path / "b" / f"{name}.csv").read_bytes()


def test_different_seed_differs(tmp_path):
    assert write(generate(42), tmp_path / "a") != write(generate(7), tmp_path / "b")


def test_committed_seed_csvs_match_the_generator(tmp_path):
    hashes = write(generate(42), tmp_path)
    for name in hashes:
        committed = ROOT / "data" / "demo" / "seed" / f"{name}.csv"
        assert committed.read_bytes() == (tmp_path / f"{name}.csv").read_bytes(), name


def test_sizes(tables):
    assert len(tables["facility"]) == 3
    assert 150 <= len(tables["formulary_item"]) <= 300
    users = tables["app_user"]
    assert len(users) == 8
    assert users["role"].value_counts().to_dict() == {"pharmacist": 3, "buyer": 3, "viewer": 2}
    assert (~users["active"]).sum() == 1
    named = dict(zip(users["display_name"].str.split(",").str[0], users["role"], strict=True))
    assert named["Dana Ruiz"] == named["Sam Okafor"] == "pharmacist"
    assert named["Lee Chen"] == named["Priya Nair"] == "buyer"
    assert named["Jordan Blake"] == "viewer"


def test_every_table_is_marked_synthetic(tables):
    for name, df in tables.items():
        assert df["is_synthetic"].eq(True).all(), name
    assert tables["facility"]["name"].str.contains("SYNTHETIC").all()


def test_at_least_30_percent_of_items_come_from_the_shortage_list(tables):
    m = tables["variant_manifest"]
    assert (m["expected_match"] == 1).mean() >= 0.30
    assert m.loc[m["expected_match"] == 1, "source_package_ndc"].notna().all()


def test_every_variant_type_is_present_at_least_3_times(tables):
    counts = tables["variant_manifest"]["variant_type"].value_counts()
    assert set(counts.index) == VARIANTS
    assert (counts >= 3).all(), counts.to_dict()


def test_manifest_covers_every_item_once(tables):
    m, f = tables["variant_manifest"], tables["formulary_item"]
    assert list(m["item_id"]) == list(f["item_id"]) and f["item_id"].is_unique


def test_ndc_formats_are_mixed(tables):
    ndc = tables["formulary_item"]["ndc_raw"].dropna()
    shapes = set(ndc.str.replace(r"\d", "9", regex=True))
    assert {"9999-9999-99", "99999-999-99", "99999-9999-9", "99999-9999-99", "99999999999"} <= shapes


def test_shortage_items_are_stocked_somewhere(tables):
    m, stock = tables["variant_manifest"], tables["facility_stock"]
    positives = set(m.loc[m["expected_match"] == 1, "item_id"])
    assert positives <= set(stock["item_id"])
    assert stock["days_on_hand"].between(0, 45).all()
    assert not stock.duplicated(["facility_id", "item_id"]).any()


def test_ndc_helpers():
    assert ndc_to_542("0409-1893-01") == "00409-1893-01"
    assert ndc_to_542("63323-452-01") == "63323-0452-01"
    assert ndc_to_542("70121-2537-6") == "70121-2537-06"
    assert leading_zero_error("63323-452-01") == "06332345201"   # differs from the right 63323045201
    assert leading_zero_error("0409-1893-01") is None


def test_name_variants_carry_no_ndc(tables):
    df = tables["formulary_item"].merge(tables["variant_manifest"], on="item_id")
    names = df[df["variant_type"].isin(["name_brand", "name_salt", "name_case_whitespace"])]
    assert names["ndc_raw"].isna().all()
    assert pd.Series(names["name_raw"]).str.len().gt(0).all()
