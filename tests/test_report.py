import re
from pathlib import Path

import estimate as e
import report as r

USAGE = (Path(__file__).parent / "fixtures" / "usage_synthetic.jsonl").read_text()


def test_full_report_is_deterministic_and_complete():
    html = r.render(r.build(e.load_profile(), usage_text=USAGE, synthetic=True, generated="2026-10-03",
                            invoices={"deepgram": 20.0}))
    assert html == r.render(r.build(e.load_profile(), usage_text=USAGE, synthetic=True, generated="2026-10-03",
                                    invoices={"deepgram": 20.0}))
    for text in ("Synthetic usage data", "Partial estimate", "1 · Cost of one interview", "2 · Where to cut it",
                 "3 · What interviews actually cost", "Calibration", "Invoice reconciliation",
                 "4 · What the hiring plan will cost", "<svg", "Provenance", "Suspect price data"):
        assert text in html, text


def test_report_loads_nothing_from_the_network():
    html = r.render(r.build(e.load_profile(), usage_text=USAGE, generated="2026-10-03"))
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", html)
    assert "<script" not in html and "@import" not in html


def test_profile_text_is_escaped():
    p = e.load_profile()
    p["operation"] = "<script>alert(1)</script>"
    html = r.render(r.build(p, generated="2026-10-03"))
    assert "<script>alert" not in html and "&lt;script&gt;" in html


def test_minimal_profile_without_forecast_or_usage():
    p = {"operation": "x", "calls": [{"name": "stt", "rate": "deepgram/nova-3", "units": {"audio_seconds": 60}}]}
    html = r.render(r.build(p, generated="2026-10-03"))
    assert "All detected costs priced" in html and "hiring plan" not in html and "actually cost" not in html


def test_axis_ticks_cover_the_data():
    assert r._ticks(0) == [0, 1]
    for hi in (0.4, 7, 999, 2083.5, 1e6):
        t = r._ticks(hi)
        assert t[0] == 0 and t[-1] >= hi and t == sorted(t)


def test_cli_writes_report_and_hashes_inputs(tmp_path):
    out = tmp_path / "r.html"
    usage = tmp_path / "u.jsonl"
    usage.write_text(USAGE)
    r.main(["--usage", str(usage), "--synthetic", "--date", "2026-10-03", "--out", str(out)])
    html = out.read_text()
    assert "u.jsonl (sha256 " in html and "interview.yaml (sha256 " in html and str(tmp_path) not in html


def test_report_warns_when_prices_are_stale():
    profile = e.load_profile((e.DATA / "interview.yaml").read_text())
    fresh = r.render(r.build(profile, generated="2026-10-03"))
    old = r.render(r.build(profile, generated="2027-06-01"))
    assert "Prices may be out of date" not in fresh
    assert "Prices may be out of date" in old
