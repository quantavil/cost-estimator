from pathlib import Path

import pytest

import actuals as a
import estimate as e
import forecast as f

FX = Path(__file__).parent / "fixtures"


def test_levers_reprice_and_quarantine_suspect_data():
    r = e.levers(e.load_profile())
    tts = [x for x in r["levers"] if x["call"] == "Interviewer text-to-speech"]
    assert any(x["suspect"] for x in tts)  # runwayml resale price is a unit error, not a saving
    assert all(not x["suspect"] for x in r["best_per_call"])
    stt = next(x for x in r["levers"] if x["call"] == "Candidate speech-to-text")
    assert stt["new_usd"] == pytest.approx(1080 * e.PRICES["deepgram/nova-3"]["input_cost_per_second"])
    assert r["optimised_usd"] == pytest.approx(r["current_usd"] - sum(x["saving_usd"] for x in r["best_per_call"]))


def test_openai_style_cache_creation_zero_is_not_free_input():
    p = {"operation": "x", "calls": [{"name": "llm", "rate": "vercel_ai_gateway/openai/gpt-4o-mini",
                                      "chat": {"turns": 2, "system_tokens": 2000, "user_tokens": 10,
                                               "assistant_tokens": 10, "prompt_caching": True}}]}
    units = {l["unit"]: l["quantity"] for l in e.estimate(p)["items"][0]["lines"]}
    assert "cache_write_tokens" not in units and units["input_tokens"] == 2010 + 20


def test_otlp_genai_import():
    events, skipped = a.load((FX / "otlp_genai.json").read_text())
    assert skipped == 1 and [x["interview_id"] for x in events] == ["i-1", "i-2"]
    assert events[0]["units"] == {"input_tokens": 952, "cached_input_tokens": 2048, "output_tokens": 100}
    att = a.attribute(events)
    p = e.PRICES["gpt-4o-mini"]
    i1 = next(r for r in att["interviews"] if r["interview_id"] == "i-1")
    assert i1["cost_usd"] == pytest.approx(952 * p["input_cost_per_token"] + 2048 * p["cache_read_input_token_cost"]
                                           + 100 * p["output_cost_per_token"])


def test_unpriced_usage_is_counted_not_zero():
    att = a.attribute([{"type": "usage", "interview_id": "x", "rate": "nope/nope", "units": {"minutes": 3}}])
    assert att["interviews"][0]["unpriced_events"] == 1 and att["unpriced"]
    assert a.summary(att)["excluded_unpriced"] == 1


def test_attribution_summary_and_calibration_on_synthetic_log():
    events, _ = a.load((FX / "usage_synthetic.jsonl").read_text())
    att = a.attribute(events)
    s = a.summary(att)
    assert s["completed"] + s["abandoned"] == len(att["interviews"])
    assert s["p10_completed_usd"] <= s["mean_completed_usd"] <= s["p90_completed_usd"]
    c = a.calibrate(att, e.estimate(e.load_profile()))
    assert {l["line"] for l in c["lines"]} == {"Interviewer LLM", "Candidate speech-to-text", "Interviewer text-to-speech"}
    rec = a.reconcile(att, {"deepgram": att["by_provider"]["deepgram"] + 10})
    assert rec[0]["gap_usd"] == pytest.approx(10)


def test_funnel_forecast():
    d = {"start": "2026-11", "months": 3, "open_roles": 10, "roles_growth_pct": 0, "applicants_per_role": 100,
         "invite_rate": 0.5, "start_rate": 0.8, "completion_rate": 0.75, "volume_uncertainty": 0, "hires_per_role": 1}
    rows = f.forecast(d, 2.0, monthly_fixed_usd=100, cost_band=(2.0, 2.0), abandoned_share=0.5)
    assert [r["month"] for r in rows] == ["2026-11", "2026-12", "2027-01"]
    # 500 invited -> 400 started -> 300 completed + 100 abandoned at half cost = 350 equivalents
    assert rows[0]["total_usd"] == pytest.approx(350 * 2 + 100) == rows[0]["low_usd"] == rows[0]["high_usd"]
    assert rows[0]["cost_per_hire_usd"] == pytest.approx(80)
    assert f.totals(rows)["total_usd"] == pytest.approx(3 * 800)
