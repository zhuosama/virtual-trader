from us_trader.config import load_config


def test_defaults_present():
    c = load_config()
    assert c["holdings_n"] == 8
    assert c["stop_loss_pct"] == -0.10
    assert c["universe"]["mcap_min"] == 300_000_000
    assert c["notify_target"] == "wecom"


def test_override(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"holdings_n": 5}')
    c = load_config(str(p))
    assert c["holdings_n"] == 5           # 覆盖项
    assert c["max_position_pct"] == 0.18  # 未覆盖回落默认
