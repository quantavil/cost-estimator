"""Price lookup and the deterministic per-operation cost calculation."""
import copy
import json
import math
from datetime import date
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
STALE_DAYS = 90


def stale_inputs(today, max_days=STALE_DAYS):
    """Dated price inputs older than max_days on `today` (ISO date): [(label, date, age_days)]."""
    t = date.fromisoformat(today)
    dated = [("LiteLLM price snapshot", META["litellm_snapshot"]["fetched"]), ("USD→INR rate", META["usd_inr"]["date"])]
    dated += [(f"{k} rate", v["checked"]) for k, v in RATES.items() if v.get("checked")]
    return [(label, d, (t - date.fromisoformat(d)).days) for label, d in dated
            if (t - date.fromisoformat(d)).days > max_days]


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
            # Only Anthropic-style caching charges a premium to write the cache; OpenAI-style writes bill as input.
            chat.setdefault("cache_writes", rate.get("cache_creation_input_token_cost", 0) > rate.get("input_cost_per_token", 0))
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


def converter(usd_inr):
    """Billing currency -> USD multiplier, from the one recorded dated rate."""
    to_usd = {"USD": 1.0, "INR": 1 / usd_inr["rate"]}

    def usd_per(currency):
        if currency not in to_usd:
            raise ValueError(f"No conversion for {currency}")
        return to_usd[currency]
    return usd_per


def estimate(profile, findings=()):
    """Profile dict (see data/interview.yaml) + reviewed findings -> itemised, labelled estimate."""
    usd_inr = profile.get("usd_inr") or META["usd_inr"]
    usd_per = converter(usd_inr)
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


LEVER_KEYS = ("rate", "units", "chat", "requests")


def _with(profile, idx, change):
    p = copy.deepcopy(profile)
    call = p["calls"][idx]
    for k, v in change.items():
        if k == "chat":
            call[k] = {**call.get(k, {}), **v}
        elif k in LEVER_KEYS:
            call[k] = v
    return p


def levers(profile):
    """Phase 2: what-if savings per call. Each lever is re-priced with the same calculator, never guessed."""
    base = estimate(profile)
    out = []
    for idx, (call, item) in enumerate(zip(profile["calls"], base["items"])):
        if item["status"] != "priced":
            continue
        options = []
        model = call["rate"].split("/")[-1]
        mode = PRICES.get(call["rate"], {}).get("mode")
        # Same model, different billing path (direct, cloud, reseller). Quality-neutral; terms are not.
        for key in PRICES:
            if key != call["rate"] and key.split("/")[-1] == model and PRICES[key].get("mode") == mode:
                options.append((f"Bill via {key}", {"rate": key, "_same_model": True},
                                "Same model, different billing path: check contract, region and where candidate data is processed."))
        chat = call.get("chat")
        if chat and not chat.get("prompt_caching"):
            options.append(("Turn on prompt caching", {"chat": {"prompt_caching": True}},
                            "Keep the system prompt and history as a stable prefix; no quality change."))
        if chat and chat.get("history", "full") == "full" and not chat.get("max_history_tokens"):
            options.append(("Cap history at 4,000 tokens", {"chat": {"max_history_tokens": 4000}},
                            "Interviewer can lose early answers; keep a short rolling summary of the candidate."))
        for alt in call.get("alternatives", []):
            options.append((alt.get("label", alt.get("rate", "alternative")), alt,
                            alt.get("risk", "Quality not verified: run your interview eval set before switching.")))
        for label, change, risk in options:
            new = estimate(_with(profile, idx, change))["items"][idx]
            if new["status"] != "priced" or new["cost_usd"] >= item["cost_usd"] - 1e-12:
                continue
            pct = 100 * (1 - new["cost_usd"] / item["cost_usd"])
            # The same model resold rarely differs by half; a bigger gap is a unit or data error in the price file.
            suspect = change.get("_same_model") and pct > 50
            out.append({"call": call["name"], "lever": label, "current_usd": item["cost_usd"],
                        "new_usd": new["cost_usd"], "saving_usd": item["cost_usd"] - new["cost_usd"],
                        "saving_pct": pct, "suspect": bool(suspect),
                        "risk": "Suspect price data (same model, >50% cheaper): verify the unit before acting." if suspect
                        else risk, "change": {k: v for k, v in change.items() if not k.startswith("_")}})
    out.sort(key=lambda r: -r["saving_usd"])
    # ponytail: best single lever per call; stacking levers within one call needs a re-price of the combination.
    best = {}
    for r in out:
        if not r["suspect"]:
            best.setdefault(r["call"], r)
    return {"levers": out, "best_per_call": list(best.values()),
            "current_usd": base["variable_usd"],
            "optimised_usd": base["variable_usd"] - sum(r["saving_usd"] for r in best.values())}


def load_profile(text=None):
    return yaml.safe_load(text if text is not None else (DATA / "interview.yaml").read_text())


if __name__ == "__main__":
    print(json.dumps(estimate(load_profile()), indent=2))
