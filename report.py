"""Static, offline HTML report (Jinja2). Same inputs + same --date -> byte-identical output.

  python report.py --profile data/interview.yaml [--findings findings.json] [--usage log.jsonl] \
                   [--invoices invoices.json] [--date 2026-10-03] --out report.html
"""
import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

import actuals as act
import estimate as est
import forecast as fc

ROOT = Path(__file__).parent
ENV = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html", "j2"]),
                  undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
ENV.filters["usd"] = lambda x, d=4: "—" if x is None else f"${x:,.{d}f}"
ENV.filters["num"] = lambda x, d=0: "—" if x is None else f"{x:,.{d}f}"


def _ticks(hi, n=4):
    """Round axis ticks from 0 to just above hi."""
    if hi <= 0:
        return [0, 1]
    raw = hi / n
    mag = 10 ** (len(str(int(raw))) - 1) if raw >= 1 else 1
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    return [step * i for i in range(n + 1) if step * (i - 1) < hi]


def forecast_svg(rows, history, w=720, h=260, pad=(16, 16, 28, 56)):
    """Geometry for the forecast chart: band polygon, forecast + actual polylines, axis ticks."""
    top, right, bottom, left = pad
    months = [m for m, _ in history] + [r["month"] for r in rows]
    hi = max([r["high_usd"] for r in rows] + [v for _, v in history] + [0])
    ticks = _ticks(hi)
    ymax = ticks[-1]
    x = {m: left + i * (w - left - right) / max(len(months) - 1, 1) for i, m in enumerate(months)}

    def y(v):
        return top + (h - top - bottom) * (1 - v / ymax)

    def pts(pairs):
        return " ".join(f"{x[m]:.1f},{y(v):.1f}" for m, v in pairs)
    band = [(r["month"], r["high_usd"]) for r in rows] + [(r["month"], r["low_usd"]) for r in reversed(rows)]
    return {"w": w, "h": h, "left": left, "right": w - right, "bottom": h - bottom,
            "band": pts(band), "forecast": pts([(r["month"], r["total_usd"]) for r in rows]),
            "actual": pts(history), "last_forecast": (x[rows[-1]["month"]], y(rows[-1]["total_usd"])) if rows else None,
            "yticks": [(y(t), t) for t in ticks],
            "xticks": [(x[m], m) for i, m in enumerate(months) if i % 3 == 0 or i == len(months) - 1]}


def build(profile, findings=(), usage_text=None, invoices=None, synthetic=False, scan_meta=None,
          generated=None, sources=None):
    result = est.estimate(profile, findings)
    opt = est.levers(profile)
    ctx = {"generated": generated or date.today().isoformat(), "profile": profile, "estimate": result,
           "optimise": opt, "scan": scan_meta or {}, "findings": list(findings), "synthetic": synthetic,
           "sources": sources or {}, "stale": None, "actuals": None, "calibration": None, "reconcile": None, "forecast": None}
    ctx["stale"] = est.stale_inputs(ctx["generated"])
    history, band, share, per_iv, basis = [], None, None, result["variable_usd"], "Phase 1 estimate"
    if usage_text:
        events, skipped = act.load(usage_text)
        att = act.attribute(events, profile.get("usd_inr"))
        s = act.summary(att)
        ctx["actuals"] = {**s, "skipped": skipped, "unpriced": att["unpriced"]}
        ctx["calibration"] = act.calibrate(att, result)
        ctx["reconcile"] = act.reconcile(att, invoices) if invoices else None
        history = [(m, v["cost_usd"]) for m, v in s["by_month"].items()]
        if s["mean_completed_usd"]:
            per_iv, band, share = s["mean_completed_usd"], (s["p10_completed_usd"], s["p90_completed_usd"]), \
                s["abandoned_cost_share"]
            basis = "Phase 3 actuals"
    if profile.get("forecast"):
        rows = fc.forecast(profile["forecast"], per_iv, profile.get("monthly_fixed_usd") or 0, band, share)
        ctx["forecast"] = {"rows": rows, "totals": fc.totals(rows), "basis": basis, "chart": forecast_svg(rows, history),
                           "has_history": bool(history)}
    priced = [i for i in result["items"] if i["status"] == "priced"]
    top = max((i["cost_usd"] for i in priced), default=0)
    ctx["bars"] = [{"name": i["name"], "usd": i["cost_usd"], "pct": 100 * i["cost_usd"] / top if top else 0,
                    "share": 100 * i["cost_usd"] / result["variable_usd"] if result["variable_usd"] else 0}
                   for i in sorted(priced, key=lambda i: -i["cost_usd"])]
    return ctx


def render(ctx):
    return ENV.get_template("report.html.j2").render(**ctx, inr_rate=ctx["estimate"]["usd_inr"]["rate"],
                                                       stale_days=est.STALE_DAYS)


def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", default=str(est.DATA / "interview.yaml"))
    ap.add_argument("--findings", help="scan.py JSON output, or a list of reviewed findings")
    ap.add_argument("--usage", help="JSONL usage events or OTLP/JSON GenAI spans")
    ap.add_argument("--invoices", help='JSON object {"provider": invoice_usd}')
    ap.add_argument("--synthetic", action="store_true", help="label the usage log as synthetic")
    ap.add_argument("--date", help="report date (default today); fix it for reproducible output")
    ap.add_argument("--out", default="report.html")
    a = ap.parse_args(argv)
    profile_text = Path(a.profile).read_text()
    sources = {"profile": f"{Path(a.profile).name} (sha256 {_digest(profile_text)})"}
    findings, scan_meta = [], None
    if a.findings:
        raw = json.loads(Path(a.findings).read_text())
        findings = raw["findings"] if isinstance(raw, dict) else raw
        scan_meta = {k: v for k, v in raw.items() if k != "findings"} if isinstance(raw, dict) else None
        sources["findings"] = Path(a.findings).name
    usage = None
    if a.usage:
        usage = Path(a.usage).read_text()
        sources["usage"] = f"{Path(a.usage).name} (sha256 {_digest(usage)})"
    invoices = json.loads(Path(a.invoices).read_text()) if a.invoices else None
    ctx = build(est.load_profile(profile_text), findings, usage, invoices, a.synthetic, scan_meta, a.date, sources)
    Path(a.out).write_text(render(ctx))
    print(f"wrote {a.out}" + (" (partial estimate)" if ctx["estimate"]["partial"] else ""))


if __name__ == "__main__":
    main()
