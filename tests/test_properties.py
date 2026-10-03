"""Invariants over many generated inputs: money is never negative, totals add up, currencies agree."""
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import estimate as e
import forecast as f

counts = st.integers(min_value=0, max_value=5000)


@settings(max_examples=150, deadline=None)
@given(turns=st.integers(1, 60), system=counts, user=counts, assistant=counts,
       history=st.sampled_from(["full", "none"]), cap=st.one_of(st.none(), counts), caching=st.booleans())
def test_chat_tokens_conserve_prompt_tokens(turns, system, user, assistant, history, cap, caching):
    t = e.chat_tokens(turns, system, user, assistant, history=history, max_history_tokens=cap, prompt_caching=caching)
    expected = 0
    for k in range(turns):
        hist = k * (user + assistant) if history == "full" else 0
        expected += system + (min(hist, cap) if cap is not None else hist) + user
    assert t["input_tokens"] + t["cached_input_tokens"] + t["cache_write_tokens"] == expected
    assert t["output_tokens"] == turns * assistant and min(t.values()) >= 0


@settings(max_examples=100, deadline=None)
@given(turns=st.integers(1, 40), system=counts, user=counts, assistant=counts)
def test_caching_never_costs_more_on_openai(turns, system, user, assistant):
    def cost(caching):
        return e.estimate({"operation": "x", "calls": [{"name": "l", "rate": "gpt-4o-mini", "chat": {
            "turns": turns, "system_tokens": system, "user_tokens": user, "assistant_tokens": assistant,
            "prompt_caching": caching}}]})["variable_usd"]
    assert cost(True) <= cost(False) + 1e-12


@settings(max_examples=100, deadline=None)
@given(seconds=st.floats(0, 1e5), chars=st.integers(0, 10 ** 6), fx=st.floats(1, 500))
def test_total_is_sum_of_lines_and_inr_matches(seconds, chars, fx):
    out = e.estimate({"operation": "x", "usd_inr": {"rate": fx, "date": "d", "source": "s"}, "calls": [
        {"name": "stt", "rate": "deepgram/nova-3", "units": {"audio_seconds": seconds}},
        {"name": "tts", "rate": "elevenlabs/eleven_multilingual_v2", "units": {"characters": chars}}]})
    lines = [l["cost_usd"] for i in out["items"] for l in i["lines"]]
    assert min(lines) >= 0 and out["variable_usd"] == pytest.approx(sum(lines))
    assert out["variable_inr"] == pytest.approx(out["variable_usd"] * fx)


@settings(max_examples=100, deadline=None)
@given(roles=st.floats(0, 500), growth=st.floats(-20, 20), apps=st.floats(0, 1000), rates=st.tuples(
    st.floats(0, 1), st.floats(0, 1), st.floats(0, 1)), cost=st.floats(0, 50), share=st.floats(0, 1))
def test_forecast_never_negative_and_band_holds(roles, growth, apps, rates, cost, share):
    d = {"start": "2026-11", "months": 6, "open_roles": roles, "roles_growth_pct": growth, "applicants_per_role": apps,
         "invite_rate": rates[0], "start_rate": rates[1], "completion_rate": rates[2]}
    for r in f.forecast(d, cost, abandoned_share=share):
        assert r["total_usd"] >= 0 and r["low_usd"] <= r["total_usd"] + 1e-9 <= r["high_usd"] + 2e-9
