"""Price lookup and the deterministic per-operation cost calculation."""
import json
import math
from pathlib import Path

import yaml

DATA = Path(__file__).parent / "data"
RATES = json.loads((DATA / "rates.json").read_text())
META = RATES.pop("_meta")
PRICES = json.loads((DATA / "model_prices.json").read_text())

# Billable unit in the operation profile -> price field. Field names follow LiteLLM's schema.
UNIT_FIELDS = {
    "input_tokens": "input_cost_per_token",
    "cached_input_tokens": "cache_read_input_token_cost",
    "cache_write_tokens": "cache_creation_input_token_cost",
    "output_tokens": "output_cost_per_token",
    "input_audio_tokens": "input_cost_per_audio_token",
    "output_audio_tokens": "output_cost_per_audio_token",
    "audio_seconds": "input_cost_per_second",
    "output_audio_seconds": "output_cost_per_second",
    "characters": "input_cost_per_character",
    "minutes": "cost_per_minute",
}
ROUNDED_UNITS = {"audio_seconds", "output_audio_seconds", "minutes"}


def lookup(key):
    """Official-rate file first, then the pinned LiteLLM snapshot. Returns (rate, source) or (None, reason)."""
    if key in RATES:
        r = RATES[key]
        if not r.get("verified", False):
            return None, f"rate in rates.json not verified against {r.get('source_url')}"
        return r, f"{r['source_url']} (checked {r['checked']}, {r['basis']})"
    if key in PRICES:
        snap = META["litellm_snapshot"]
        return PRICES[key], f"LiteLLM {snap['commit'][:10]} (fetched {snap['fetched']})"
    return None, f"no rate for '{key}'"


def chat_tokens(turns, system_tokens, user_tokens, assistant_tokens, history="full",
                max_history_tokens=None, prompt_caching=False, min_cache_tokens=1024, cache_writes=False):
    """Token totals for a multi-turn conversation that resends history each turn."""
    t = {"input_tokens": 0, "cached_input_tokens": 0, "cache_write_tokens": 0, "output_tokens": 0}
    prev_prompt = 0
    for k in range(turns):
        hist = k * (user_tokens + assistant_tokens) if history == "full" else 0
        if max_history_tokens is not None:
            hist = min(hist, max_history_tokens)
        prompt = system_tokens + hist + user_tokens
        # The previous request's prompt is the reusable prefix, unless truncation shifted it.
        cached = prev_prompt if prompt_caching and prev_prompt >= min_cache_tokens and prompt > prev_prompt else 0
        fresh = prompt - cached
        t["cached_input_tokens"] += cached
        t["cache_write_tokens" if prompt_caching and cache_writes else "input_tokens"] += fresh
        t["output_tokens"] += assistant_tokens
        prev_prompt = prompt
    return t


def _billable(unit, qty, requests, rate):
    """Apply per-request minimums and increments (in the unit's own size) for time-billed units."""
    if unit not in ROUNDED_UNITS or not requests:
        return qty
    inc, minimum = rate.get("billing_increment", 0), rate.get("billing_minimum", 0)
    per = qty / requests
    if inc:
        per = math.ceil(per / inc - 1e-9) * inc
    return max(per, minimum) * requests


def price_call(call, usd_per_unit_currency):
    rate, source = lookup(call["rate"])
    units = dict(call.get("units") or {})
    if "chat" in call:
        chat = dict(call["chat"])
        if rate and chat.get("prompt_caching"):
            chat.setdefault("cache_writes", "cache_creation_input_token_cost" in rate)
        units.update({k: v for k, v in chat_tokens(**chat).items() if v})
    item = {"name": call["name"], "rate": call["rate"], "quantity_source": call.get("source", "assumed"),
            "evidence": call.get("evidence", []), "rate_source": source, "lines": [], "cost_usd": None,
            "status": "unpriced", "note": call.get("note", "")}
    if rate is None:
        item["lines"] = [{"unit": u, "quantity": q} for u, q in units.items()]
        return item
    fx = usd_per_unit_currency(rate.get("currency", "USD"))
    missing = []
    for unit, qty in units.items():
        field = UNIT_FIELDS.get(unit)
        if field not in rate:
            missing.append(unit)
            continue
        billed = _billable(unit, qty, call.get("requests", 1), rate)
        item["lines"].append({"unit": unit, "quantity": qty, "billed": billed, "unit_price_usd": rate[field] * fx,
                              "cost_usd": billed * rate[field] * fx})
    if missing:
        item["rate_source"] += f"; no price field for {', '.join(missing)}"
        return item
    item["cost_usd"] = sum(line["cost_usd"] for line in item["lines"])
    item["status"] = "priced"
    return item


def estimate(profile, findings=()):
    """Profile dict (see data/interview.yaml) + reviewed findings -> itemised, labelled estimate."""
    usd_inr = profile.get("usd_inr") or META["usd_inr"]
    to_usd = {"USD": 1.0, "INR": 1 / usd_inr["rate"]}

    def usd_per(currency):
        if currency not in to_usd:
            raise ValueError(f"No conversion for {currency}")
        return to_usd[currency]

    items = [price_call(c, usd_per) for c in profile["calls"]]
    variable = sum(i["cost_usd"] for i in items if i["cost_usd"] is not None)
    gaps = [f"Unpriced: {i['name']} ({i['rate_source']})" for i in items if i["status"] == "unpriced"]
    names = {c["name"] for c in profile["calls"]}
    for f in findings:
        where = f"{f['file']}:{f['line']}"
        if f["status"] in ("candidate", "unresolved"):
            gaps.append(f"Unreviewed {f['status']} call site: {where} ({f['provider']})")
        elif f["status"] == "billable" and f.get("call") not in names:
            gaps.append(f"Billable call site {where} is not mapped to any estimate line")
    fixed = profile.get("monthly_fixed_usd") or 0
    n = profile.get("operations_per_month") or 0
    allocated = fixed / n if fixed and n else None
    inr = usd_inr["rate"]
    return {
        "operation": profile["operation"],
        "partial": bool(gaps),
        "gaps": gaps,
        "items": items,
        "variable_usd": variable,
        "variable_inr": variable * inr,
        "monthly_fixed_usd": fixed,
        "monthly_fixed_inr": fixed * inr,
        "allocated_fixed_usd": allocated,
        "allocated_fixed_inr": allocated * inr if allocated is not None else None,
        "usd_inr": usd_inr,
        "price_snapshot": META["litellm_snapshot"],
    }


def load_profile(text=None):
    return yaml.safe_load(text if text is not None else (DATA / "interview.yaml").read_text())


if __name__ == "__main__":
    print(json.dumps(estimate(load_profile()), indent=2))
