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


@pytest.fixture
def rates(monkeypatch):
    """Isolated copy of rates.json the test can add entries to."""
    r = copy.deepcopy(e.RATES)
    monkeypatch.setattr(e, "RATES", r)
    return r


def _rate(**kw):
    return {"source_url": "https://example.invalid", "checked": "2026-10-03", "basis": "test", "verified": True, **kw}


def test_inr_billed_rate_converts_once(rates):
    rates["exotel/voice"] = _rate(cost_per_minute=0.95, currency="INR", billing_increment=1)
    out = e.estimate({"operation": "x", "usd_inr": {"rate": 95.0, "date": "d", "source": "s"},
                      "calls": [{"name": "phone", "rate": "exotel/voice", "units": {"minutes": 2}}]})
    assert out["variable_usd"] == pytest.approx(0.02) and out["variable_inr"] == pytest.approx(1.9)


def test_billing_minimum_per_request(rates):
    rates["v/stt"] = _rate(input_cost_per_second=0.01, billing_minimum=15, billing_increment=1)
    out = e.estimate({"operation": "x", "calls": [{"name": "s", "rate": "v/stt", "units": {"audio_seconds": 20},
                                                   "requests": 4}]})
    assert out["items"][0]["lines"][0]["billed"] == 60  # 4 x max(5, 15)


def test_unknown_currency_is_an_error(rates):
    rates["eu/x"] = _rate(cost_per_minute=1, currency="EUR")
    with pytest.raises(ValueError, match="EUR"):
        e.estimate({"operation": "x", "calls": [{"name": "a", "rate": "eu/x", "units": {"minutes": 1}}]})


@pytest.mark.parametrize("status,partial", [("covered", False), ("ignore", False), ("inventory", False),
                                            ("candidate", True), ("unresolved", True)])
def test_only_unreviewed_findings_make_it_partial(status, partial):
    p = {"operation": "x", "calls": [{"name": "stt", "rate": "deepgram/nova-3", "units": {"audio_seconds": 1}}]}
    f = [{"file": "a.py", "line": 1, "provider": "deepgram", "status": status, "call": ""}]
    assert e.estimate(p, f)["partial"] is partial


def test_anthropic_cache_writes_bill_at_premium():
    p = {"operation": "x", "calls": [{"name": "llm", "rate": "claude-haiku-4-5", "chat": {
        "turns": 2, "system_tokens": 2000, "user_tokens": 100, "assistant_tokens": 50, "prompt_caching": True}}]}
    units = {l["unit"]: l for l in e.estimate(p)["items"][0]["lines"]}
    assert "input_tokens" not in units and units["cache_write_tokens"]["quantity"] == 2100 + 150
    assert units["cache_write_tokens"]["unit_price_usd"] > e.PRICES["claude-haiku-4-5"]["input_cost_per_token"]


def test_truncation_that_shrinks_the_prompt_breaks_the_cache():
    t = e.chat_tokens(turns=3, system_tokens=2000, user_tokens=500, assistant_tokens=500, max_history_tokens=1000,
                      prompt_caching=True)
    # prompts 2500, 3500, 3500: turn 2 reuses 2500; turn 3 shifted the window so nothing is cached
    assert t["cached_input_tokens"] == 2500 and t["input_tokens"] == 2500 + 1000 + 3500


def test_below_minimum_cache_size_is_not_cached():
    t = e.chat_tokens(turns=3, system_tokens=100, user_tokens=10, assistant_tokens=10, prompt_caching=True)
    assert t["cached_input_tokens"] == 0


def test_lookup_order_and_reasons(rates):
    rates["gpt-4o-mini"] = _rate(input_cost_per_token=1.0)  # rates.json wins over LiteLLM
    assert e.lookup("gpt-4o-mini")[0]["input_cost_per_token"] == 1.0
    rates["gpt-4o-mini"]["verified"] = False
    assert e.lookup("gpt-4o-mini")[0] is None and "not verified" in e.lookup("gpt-4o-mini")[1]
    assert e.lookup("nope/nope") == (None, "no rate for 'nope/nope'")


def test_stale_inputs_flags_old_prices_only():
    assert e.stale_inputs("2026-10-03") == []
    old = e.stale_inputs("2027-06-01")
    labels = {label for label, _, _ in old}
    assert {"LiteLLM price snapshot", "USD→INR rate"} <= labels
    assert all(age > e.STALE_DAYS for _, _, age in old)
