from us_trader.pipeline.universe import load_universe


def test_load_universe_min_rows():
    uni = load_universe()
    assert len(uni) > 50, f"Expected >50 entries, got {len(uni)}"


def test_load_universe_fields():
    uni = load_universe()
    for row in uni:
        assert "ts_code" in row, "Missing ts_code field"
        assert "name" in row, "Missing name field"
        assert row["ts_code"], "ts_code should not be empty"
        assert row["name"], "name should not be empty"


def test_load_universe_no_duplicates():
    uni = load_universe()
    codes = [r["ts_code"] for r in uni]
    assert len(codes) == len(set(codes)), "Duplicate ts_codes found"
