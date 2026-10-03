import copy

import pytest

import estimate as e


def test_chat_tokens_full_history_and_caching():
    t = e.chat_tokens(turns=3, system_tokens=2000, user_tokens=100, assistant_tokens=50, prompt_caching=True)
    # prompts: 2100, 2250, 2400; turns 2 and 3 reuse the previous prompt as a cached prefix
    assert t == {"input_tokens": 2100 + 150 + 150, "cached_input_tokens": 2100 + 2250,
                 "cache_write_tokens": 0, "output_tokens": 150}


def test_chat_tokens_truncation_and_no_history():
    capped = e.chat_tokens(turns=4, system_tokens=0, user_tokens=10, assistant_tokens=10, max_history_tokens=20)
    assert capped["input_tokens"] == 10 + 30 + 30 + 30
    assert e.chat_tokens(turns=4, system_tokens=5, user_tokens=10, assistant_tokens=1, history="none")["input_tokens"] == 60


def test_minute_rounding_per_request():
    rate = {"cost_per_minute": 0.01, "billing_increment": 1}
    assert e._billable("minutes", 10.2, 2, rate) == 12  # two calls of 5.1 min -> 6 + 6
    assert e._billable("characters", 10.2, 2, rate) == 10.2


def test_estimate_is_itemised_partial_and_never_free():
    profile = e.load_profile()
    out = e.estimate(profile)
    assert out["partial"]  # Twilio rate is unverified -> unpriced, not zero
    twilio = next(i for i in out["items"] if i["rate"].startswith("twilio/"))
    assert twilio["status"] == "unpriced" and twilio["cost_usd"] is None
    priced = sum(i["cost_usd"] for i in out["items"] if i["status"] == "priced")
    assert out["variable_usd"] == pytest.approx(priced)
    assert out["variable_inr"] == pytest.approx(priced * out["usd_inr"]["rate"])
    assert e.estimate(profile) == out  # deterministic


def test_unknown_rate_and_fixed_allocation_and_findings():
    profile = {"operation": "x", "monthly_fixed_usd": 300, "operations_per_month": 100,
               "calls": [{"name": "stt", "rate": "deepgram/nova-3", "units": {"audio_seconds": 60}},
                         {"name": "mystery", "rate": "nope/nope", "units": {"minutes": 1}}]}
    findings = [{"file": "a.py", "line": 3, "provider": "unknown", "status": "unresolved"},
                {"file": "b.py", "line": 4, "provider": "openai", "status": "billable", "call": "llm"},
                {"file": "c.py", "line": 5, "provider": "deepgram", "status": "billable", "call": "stt"}]
    out = e.estimate(profile, findings)
    assert out["allocated_fixed_usd"] == 3
    assert out["variable_usd"] == pytest.approx(60 * e.PRICES["deepgram/nova-3"]["input_cost_per_second"])
    assert len(out["gaps"]) == 3  # unpriced mystery, unresolved a.py, unmapped b.py


def test_missing_price_field_is_unpriced():
    p = {"operation": "x", "calls": [{"name": "stt", "rate": "deepgram/nova-3", "units": {"characters": 5}}]}
    assert e.estimate(p)["items"][0]["status"] == "unpriced"


def test_verified_rate_is_priced(monkeypatch):
    rates = copy.deepcopy(e.RATES)
    rates["twilio/voice-inbound-us-local"]["verified"] = True
    monkeypatch.setattr(e, "RATES", rates)
    p = {"operation": "x", "calls": [{"name": "phone", "rate": "twilio/voice-inbound-us-local",
                                      "units": {"minutes": 29.5}, "requests": 1}]}
    assert e.estimate(p)["variable_usd"] == pytest.approx(30 * 0.0085)
