"""Phase 3: actual cost per interview from runtime usage, then calibration against the Phase 1 estimate.

Input is either JSONL (one event per line) or an OTLP/JSON trace export with OpenTelemetry GenAI spans
(what Langfuse, OpenLIT and the OpenAI/Anthropic OTel instrumentations emit). Event shapes:

  {"type": "session", "interview_id": "i-1", "ts": "2026-09-01T10:00:00Z", "role": "Backend Engineer",
   "outcome": "completed" | "abandoned", "duration_s": 1790}
  {"type": "usage", "interview_id": "i-1", "call": "Interviewer LLM", "rate": "gpt-4o-mini",
   "units": {"input_tokens": 1200, "cached_input_tokens": 3000, "output_tokens": 90}}
"""
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone

import estimate as est

GENAI_UNITS = {  # OTel GenAI semantic-convention usage attributes -> billable units
    "gen_ai.usage.input_tokens": "input_tokens",
    "gen_ai.usage.output_tokens": "output_tokens",
    "gen_ai.usage.cache_read.input_tokens": "cached_input_tokens",
    "gen_ai.usage.cache_creation.input_tokens": "cache_write_tokens",
}


def _attr_value(v):
    for k in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if k in v:
            return int(v[k]) if k == "intValue" else v[k]
    return None


def from_otlp(doc, id_key="interview.id"):
    """OTLP/JSON spans -> usage events. Spans without the interview id attribute are skipped, and counted."""
    events, skipped = [], 0
    for rs in doc.get("resourceSpans", []):
        res = {a["key"]: _attr_value(a["value"]) for a in rs.get("resource", {}).get("attributes", [])}
        for ss in rs.get("scopeSpans", []):
            for span in ss.get("spans", []):
                a = {**res, **{x["key"]: _attr_value(x["value"]) for x in span.get("attributes", [])}}
                model = a.get("gen_ai.response.model") or a.get("gen_ai.request.model")
                iid = a.get(id_key) or a.get("session.id")
                if not model or not iid:
                    skipped += 1
                    continue
                provider = a.get("gen_ai.provider.name") or a.get("gen_ai.system") or ""
                units = {u: a[k] for k, u in GENAI_UNITS.items() if a.get(k)}
                # GenAI convention: input_tokens includes cached and cache-write tokens; bill only the fresh rest.
                if "input_tokens" in units:
                    units["input_tokens"] -= units.get("cached_input_tokens", 0) + units.get("cache_write_tokens", 0)
                rate = model if model in est.PRICES or model in est.RATES else f"{provider}/{model}"
                ts = datetime.fromtimestamp(int(span.get("startTimeUnixNano", 0)) / 1e9, timezone.utc)
                events.append({"type": "usage", "interview_id": iid, "call": a.get("cost.call", rate),
                               "rate": a.get("cost.rate", rate), "units": units, "ts": ts.isoformat()})
    return events, skipped


def load(text, id_key="interview.id"):
    text = text.strip()
    if text.startswith("{") and '"resourceSpans"' in text[:2000]:
        return from_otlp(json.loads(text), id_key)
    return [json.loads(line) for line in text.splitlines() if line.strip()], 0


def attribute(events, usd_inr=None):
    """Price every usage event and roll up per interview. Unpriced usage is counted, never treated as zero."""
    usd_per = est.converter(usd_inr or est.META["usd_inr"])
    interviews = defaultdict(lambda: {"cost_usd": 0.0, "unpriced_events": 0, "by_call": defaultdict(float)})
    unpriced = defaultdict(int)
    by_provider = defaultdict(float)
    for e in events:
        iv = interviews[e["interview_id"]]
        if e.get("type") == "session":
            iv.update({k: e[k] for k in ("ts", "role", "outcome", "duration_s") if k in e})
            continue
        iv.setdefault("ts", e.get("ts"))
        item = est.price_call({"name": e.get("call", e["rate"]), "rate": e["rate"], "units": e["units"],
                               "requests": e.get("requests", 1)}, usd_per)
        if item["status"] != "priced":
            iv["unpriced_events"] += 1
            unpriced[f"{e['rate']}: {item['rate_source']}"] += 1
            continue
        iv["cost_usd"] += item["cost_usd"]
        iv["by_call"][item["name"]] += item["cost_usd"]
        by_provider[provider_of(e["rate"])] += item["cost_usd"]
    rows = []
    for iid, iv in interviews.items():
        rows.append({"interview_id": iid, "ts": iv.get("ts"), "month": (iv.get("ts") or "")[:7],
                     "role": iv.get("role", "unknown"), "outcome": iv.get("outcome", "unknown"),
                     "minutes": (iv.get("duration_s") or 0) / 60, "cost_usd": iv["cost_usd"],
                     "unpriced_events": iv["unpriced_events"], "by_call": dict(iv["by_call"])})
    rows.sort(key=lambda r: r["ts"] or "")
    return {"interviews": rows, "unpriced": dict(unpriced), "by_provider": dict(by_provider)}


def provider_of(rate):
    return (est.PRICES.get(rate) or {}).get("litellm_provider") or rate.split("/")[0]


def _pct(values, q):
    values = sorted(values)
    if len(values) < 2:
        return values[0] if values else 0.0
    return statistics.quantiles(values, n=100, method="inclusive")[q - 1]


def summary(att):
    """Cost per completed interview, per abandoned interview, per minute, per role."""
    done = [r for r in att["interviews"] if r["outcome"] == "completed" and not r["unpriced_events"]]
    gone = [r for r in att["interviews"] if r["outcome"] == "abandoned" and not r["unpriced_events"]]
    costs = [r["cost_usd"] for r in done]
    minutes = sum(r["minutes"] for r in done)
    by_role = defaultdict(list)
    for r in done:
        by_role[r["role"]].append(r["cost_usd"])
    by_month = defaultdict(lambda: {"completed": 0, "abandoned": 0, "cost_usd": 0.0})
    for r in att["interviews"]:
        m = by_month[r["month"]]
        m["cost_usd"] += r["cost_usd"]
        if r["outcome"] in ("completed", "abandoned"):
            m[r["outcome"]] += 1
    return {
        "completed": len(done), "abandoned": len(gone),
        "excluded_unpriced": sum(1 for r in att["interviews"] if r["unpriced_events"]),
        "mean_completed_usd": statistics.fmean(costs) if costs else None,
        "p10_completed_usd": _pct(costs, 10) if costs else None,
        "p90_completed_usd": _pct(costs, 90) if costs else None,
        "usd_per_minute": sum(costs) / minutes if minutes else None,
        "abandoned_cost_share": (statistics.fmean(r["cost_usd"] for r in gone) / statistics.fmean(costs))
        if gone and costs else None,
        "by_role": {k: {"n": len(v), "mean_usd": statistics.fmean(v)} for k, v in sorted(by_role.items())},
        "by_month": dict(sorted(by_month.items())),
    }


def calibrate(att, estimate_result):
    """Estimate vs actual mean per covered line, completed interviews only (the estimate is for a completed one)."""
    done = [r for r in att["interviews"] if r["outcome"] == "completed" and not r["unpriced_events"]]
    rows = []
    for item in estimate_result["items"]:
        if item["status"] != "priced" or not done:
            continue
        seen = [r["by_call"][item["name"]] for r in done if item["name"] in r["by_call"]]
        if not seen:
            continue
        actual = statistics.fmean(seen)
        rows.append({"line": item["name"], "estimate_usd": item["cost_usd"], "actual_mean_usd": actual,
                     "error_pct": 100 * (item["cost_usd"] - actual) / actual if actual else None,
                     "interviews": len(seen)})
    est_total = sum(r["estimate_usd"] for r in rows)
    act_total = sum(r["actual_mean_usd"] for r in rows)
    return {"lines": rows, "estimate_usd": est_total, "actual_usd": act_total,
            "error_pct": 100 * (est_total - act_total) / act_total if act_total else None,
            "within_target": abs(est_total - act_total) <= 0.2 * act_total if act_total else None}


def reconcile(att, invoices_usd):
    """Computed usage cost per provider vs what the invoice says, for the same period."""
    out = []
    for provider, billed in invoices_usd.items():
        computed = att["by_provider"].get(provider, 0.0)
        out.append({"provider": provider, "computed_usd": computed, "invoice_usd": billed,
                    "gap_usd": billed - computed, "gap_pct": 100 * (billed - computed) / billed if billed else None})
    return out
