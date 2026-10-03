"""Interview cost workbench: estimate (1), optimise (2), actuals (3), forecast (4). One profile drives all four."""
import json
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import actuals as act
import estimate as est
import forecast as fc
import report as rpt
import scan

st.set_page_config(page_title="Interview Cost Workbench", layout="wide")
ss = st.session_state
DARK = st.context.theme.type == "dark"
# Categorical slots 1-2 from the validated dataviz palette (light/dark steps), checked with validate_palette.js.
BLUE, ORANGE = ("#3987e5", "#d95926") if DARK else ("#2a78d6", "#eb6834")
SYNTHETIC = Path(__file__).parent / "tests" / "fixtures" / "usage_synthetic.jsonl"


def usd(x):
    """Escaped for Streamlit markdown, where a pair of $ signs would render as LaTeX."""
    return "—" if x is None else f"\\${x:,.4f}" if abs(x) < 100 else f"\\${x:,.0f}"


def inr(x):
    return None if x is None else f"₹{x * est.META['usd_inr']['rate']:,.2f}" if abs(x) < 100 else \
        f"₹{x * est.META['usd_inr']['rate']:,.0f}"


def tile(col, label, value, sub=None):
    """Metric with USD as the value and INR (or a note) underneath, never styled as a change."""
    col.metric(label, value, sub, delta_color="off", delta_arrow="off")


def money(x):
    return "—" if x is None else f"{usd(x)} · {inr(x)}"


st.title("Interview cost workbench")
st.caption("What one AI interview costs, where to cut it, what it actually cost, and what next year's hiring plan will cost.")

if "profile_text" not in ss:
    ss.profile_text = (est.DATA / "interview.yaml").read_text()
try:
    profile = est.load_profile(ss.profile_text)
    result = est.estimate(profile, ss.get("findings", []))
except Exception as exc:
    profile, result = None, None
    st.error(f"Profile error: {exc}")

t1, t2, t3, t4 = st.tabs(["1 · Estimate", "2 · Optimise", "3 · Actuals", "4 · Forecast"])

# ---------------------------------------------------------------- Phase 1
with t1:
    st.subheader("Repository")
    c1, c2, c3 = st.columns([4, 2, 1])
    source = c1.text_input("Local path or https://github.com/… URL", ss.get("source", ""))
    ref = c2.text_input("Commit / branch (optional)", "")
    if c3.button("Scan", type="primary", disabled=not source):
        with st.spinner("Running Semgrep + Trusera AI-BOM (target code is never executed)…"):
            try:
                ss.scan = scan.scan(source.strip(), ref.strip())
                ss.source = source
            except Exception as exc:
                st.error(f"Scan failed: {exc}")
    if "scan" in ss:
        st.caption(f"Scanned `{ss.scan['path']}` at commit `{ss.scan['commit'] or 'not a git repo'}`")
        st.markdown("**Detected call sites.** Set **billable** (and the estimate line in *call*), **covered** "
                    "(billed inside another line, e.g. a bundled voice vendor) or **ignore**. "
                    "*candidate* and *unresolved* rows keep the estimate partial.")
        df = pd.DataFrame(ss.scan["findings"], columns=["status", "call", "provider", "kind", "model",
                                                        "file", "line", "sources"])
        edited = st.data_editor(
            df, hide_index=True, width="stretch", key="findings_editor",
            disabled=["provider", "kind", "file", "line", "sources"],
            column_config={"status": st.column_config.SelectboxColumn(options=scan.STATUSES, required=True)})
        ss.findings = edited.to_dict("records")
        result = est.estimate(profile, ss.findings) if profile else None
    else:
        st.info("Scan a repository to list call sites, or estimate straight from the profile below.")

    st.subheader("Interview profile")
    st.caption("One operation. `rate` keys come from `data/rates.json` or the pinned LiteLLM snapshot. "
               "Mark numbers `source: measured` only when they come from a real interview log.")
    # Keyed widget: edits land in session state before the next run computes the estimate.
    st.text_area("Profile (YAML)", key="profile_text", height=380)

    if result:
        st.subheader("Cost of one interview")
        if stale := est.stale_inputs(date.today().isoformat()):
            st.warning(f"**Prices may be out of date** (older than {est.STALE_DAYS} days):\n\n"
                       + "\n".join(f"- {label}: {d} ({age} days)" for label, d, age in stale))
        if result["partial"]:
            st.warning("**Partial estimate.** Not in the total:\n\n" + "\n".join(f"- {g}" for g in result["gaps"]))
        m1, m2, m3 = st.columns(3)
        tile(m1, "Variable / interview" + (" (partial)" if result["partial"] else ""),
             usd(result["variable_usd"]), inr(result["variable_usd"]))
        tile(m2, "Monthly fixed (separate)", usd(result["monthly_fixed_usd"]), inr(result["monthly_fixed_usd"]))
        tile(m3, "Allocated fixed / interview", usd(result["allocated_fixed_usd"]), inr(result["allocated_fixed_usd"]))
        rate = result["usd_inr"]["rate"]
        rows = []
        for item in result["items"]:
            for line in item["lines"] or [{}]:
                cost = line.get("cost_usd")
                rows.append({"item": item["name"], "status": item["status"], "quantity": item["quantity_source"],
                             "unit": line.get("unit", ""), "qty": line.get("quantity"), "billed": line.get("billed"),
                             "USD / unit": line.get("unit_price_usd"), "USD": cost,
                             "INR": None if cost is None else cost * rate, "rate": item["rate"],
                             "rate source": item["rate_source"], "evidence": ", ".join(item["evidence"])})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                     column_config={"USD / unit": st.column_config.NumberColumn(format="%.8f"),
                                    "USD": st.column_config.NumberColumn(format="%.5f"),
                                    "INR": st.column_config.NumberColumn(format="%.3f")})
        for item in result["items"]:
            if item["note"]:
                st.caption(f"**{item['name']}:** {item['note']}")
        st.caption(f"USD→INR {rate} on {result['usd_inr']['date']} ({result['usd_inr']['source']}). "
                   f"Model prices: LiteLLM {result['price_snapshot']['commit'][:10]}, "
                   f"fetched {result['price_snapshot']['fetched']}.")

# ---------------------------------------------------------------- Phase 2
with t2:
    if not profile:
        st.stop()
    opt = est.levers(profile)
    st.caption("Every option is re-priced with the same calculator, so the savings are exact for the stated "
               "quantities. Whether quality holds is a separate question, answered by the eval in the risk column.")
    m1, m2, m3 = st.columns(3)
    tile(m1, "Now / interview", usd(opt["current_usd"]), inr(opt["current_usd"]))
    tile(m2, "Best option per line", usd(opt["optimised_usd"]), inr(opt["optimised_usd"]))
    tile(m3, "Saving", f"{100 * (1 - opt['optimised_usd'] / opt['current_usd']):.0f}%" if opt["current_usd"] else "—",
         "before quality checks")
    if result and result["partial"]:
        st.caption("Unpriced lines are excluded from both figures.")
    if opt["levers"]:
        lv = pd.DataFrame(opt["levers"])
        lv["status"] = lv["suspect"].map({True: "⚠ suspect price data", False: "✓ re-priced"})
        st.dataframe(lv[["call", "lever", "current_usd", "new_usd", "saving_pct", "status", "risk"]],
                     hide_index=True, width="stretch",
                     column_config={"current_usd": st.column_config.NumberColumn("now USD", format="%.4f"),
                                    "new_usd": st.column_config.NumberColumn("new USD", format="%.4f"),
                                    "saving_pct": st.column_config.NumberColumn("saving %", format="%.0f%%")})
    else:
        st.info("No cheaper option found. Add `alternatives:` under a call in the profile to test one.")
    st.caption("Add your own what-ifs under any call as `alternatives:` (rate, units or chat overrides).")

# ---------------------------------------------------------------- Phase 3
with t3:
    st.caption("Upload runtime usage: JSONL events, or an OTLP/JSON trace export with OpenTelemetry GenAI spans "
               "(Langfuse, OpenLIT and the OpenAI/Anthropic OTel instrumentations produce these). "
               "Tag spans with `interview.id` (or `session.id`).")
    c1, c2 = st.columns([3, 2])
    up = c1.file_uploader("Usage log", type=["jsonl", "json"])
    id_key = c2.text_input("Interview id attribute (OTLP)", "interview.id")
    demo = c2.toggle("Use the synthetic demo log", value=up is None)
    text = up.getvalue().decode() if up else SYNTHETIC.read_text() if demo else ""
    ss.actuals = None
    ss.usage_text, ss.usage_synthetic = text or None, bool(demo and not up)
    if text:
        if demo and not up:
            st.warning("**Synthetic data.** The demo log is generated from the same assumptions as the profile, "
                       "so its calibration shows only that the pipeline works, not that the estimate is accurate.")
        events, skipped = act.load(text, id_key)
        att = act.attribute(events, profile.get("usd_inr") if profile else None)
        s = act.summary(att)
        ss.actuals = s
        if skipped:
            st.caption(f"{skipped} spans skipped (no model or no interview id).")
        if att["unpriced"]:
            st.warning("**Unpriced usage** (affected interviews are left out of the averages):\n\n" +
                       "\n".join(f"- {k} ×{v}" for k, v in att["unpriced"].items()))
        m = st.columns(4)
        tile(m[0], "Completed interviews", f"{s['completed']:,}", f"{s['abandoned']} abandoned")
        tile(m[1], "Mean / completed", usd(s["mean_completed_usd"]), inr(s["mean_completed_usd"]))
        tile(m[2], "p10 / completed", usd(s["p10_completed_usd"]), f"p90 ${s['p90_completed_usd'] or 0:,.4f}")
        tile(m[3], "Per interview minute", usd(s["usd_per_minute"]), inr(s["usd_per_minute"]))

        per = pd.DataFrame([r for r in att["interviews"] if r["outcome"] == "completed" and not r["unpriced_events"]])
        if not per.empty:
            st.markdown("**Cost per completed interview**")
            hist = alt.Chart(per).mark_bar(color=BLUE, cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                alt.X("cost_usd:Q", bin=alt.Bin(maxbins=24), title="USD per completed interview"),
                alt.Y("count():Q", title="Interviews"),
                tooltip=[alt.Tooltip("count():Q", title="Interviews"),
                         alt.Tooltip("cost_usd:Q", bin=alt.Bin(maxbins=24), title="USD range", format=".3f")])
            st.altair_chart(hist, width="stretch")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**By role** (completed)")
            st.dataframe(pd.DataFrame([{"role": k, "interviews": v["n"], "mean USD": v["mean_usd"]}
                                       for k, v in s["by_role"].items()]),
                         hide_index=True, width="stretch",
                         column_config={"mean USD": st.column_config.NumberColumn(format="%.4f")})
        with c2:
            st.markdown("**By month**")
            st.dataframe(pd.DataFrame([{"month": k, **v} for k, v in s["by_month"].items()]),
                         hide_index=True, width="stretch",
                         column_config={"cost_usd": st.column_config.NumberColumn("USD", format="%.2f")})

        if result:
            cal = act.calibrate(att, result)
            st.markdown("**Calibration:** the estimate against the actual mean, for the same lines and "
                        "completed interviews only")
            if cal["lines"]:
                verdict = "within" if cal["within_target"] else "outside"
                st.write(f"Covered lines: estimate {money(cal['estimate_usd'])} vs actual {money(cal['actual_usd'])} "
                         f"→ **{cal['error_pct']:+.1f}%**, {verdict} the ±20% target.")
                st.dataframe(pd.DataFrame(cal["lines"]), hide_index=True, width="stretch",
                             column_config={"estimate_usd": st.column_config.NumberColumn(format="%.4f"),
                                            "actual_mean_usd": st.column_config.NumberColumn(format="%.4f"),
                                            "error_pct": st.column_config.NumberColumn(format="%+.1f%%")})
            else:
                st.caption("No usage event `call` names match the profile's line names, so nothing to compare.")

        st.markdown("**Invoice reconciliation:** enter the invoice totals for the same period as the log")
        inv = st.data_editor(pd.DataFrame({"provider": sorted(att["by_provider"]), "invoice_usd": [None] * len(att["by_provider"])}),
                             hide_index=True, num_rows="dynamic", key="invoices")
        invoices = {r["provider"]: r["invoice_usd"] for r in inv.to_dict("records") if r["provider"] and pd.notna(r["invoice_usd"])}
        if invoices:
            st.dataframe(pd.DataFrame(act.reconcile(att, invoices)), hide_index=True, width="stretch",
                         column_config={"gap_pct": st.column_config.NumberColumn(format="%+.1f%%")})
            st.caption("A positive gap means usage the log doesn't capture (uninstrumented calls, retries, "
                       "minimums) or a rate that's wrong for your contract.")

# ---------------------------------------------------------------- Phase 4
with t4:
    if not profile or "forecast" not in profile:
        st.info("Add a `forecast:` section to the profile.")
        st.stop()
    a = ss.get("actuals")
    choices = ["Phase 1 estimate", "Phase 2 optimised"] + (["Phase 3 actuals"] if a and a["mean_completed_usd"] else [])
    basis = st.radio("Cost per completed interview from", choices, index=len(choices) - 1, horizontal=True)
    band, share = None, None
    if basis == "Phase 3 actuals":
        per_iv, band, share = a["mean_completed_usd"], (a["p10_completed_usd"], a["p90_completed_usd"]), a["abandoned_cost_share"]
    elif basis == "Phase 2 optimised":
        per_iv = est.levers(profile)["optimised_usd"]
    else:
        per_iv = result["variable_usd"]
    rows = fc.forecast(profile["forecast"], per_iv, profile.get("monthly_fixed_usd") or 0, band, share)
    tot = fc.totals(rows)
    m = st.columns(4)
    tile(m[0], f"Next {len(rows)} months", usd(tot["total_usd"]), inr(tot["total_usd"]))
    tile(m[1], "Scenario range", usd(tot["low_usd"]), f"up to ${tot['high_usd']:,.0f}")
    tile(m[2], "Interview cost per hire", usd(tot["cost_per_hire_usd"]), inr(tot["cost_per_hire_usd"]))
    tile(m[3], "Completed interviews", f"{tot['completed']:,.0f}", f"{tot['abandoned']:,.0f} abandoned")

    df = pd.DataFrame(rows)
    df["series"] = "Forecast"
    hist = pd.DataFrame([{"month": k, "total_usd": v["cost_usd"], "series": "Actual"}
                         for k, v in (a or {}).get("by_month", {}).items()]) if a else pd.DataFrame()
    both = pd.concat([hist, df[["month", "total_usd", "series"]]], ignore_index=True)
    both["date"] = pd.to_datetime(both["month"])
    df["date"] = pd.to_datetime(df["month"])
    color = alt.Color("series:N", scale=alt.Scale(domain=["Forecast", "Actual"], range=[BLUE, ORANGE]),
                      legend=alt.Legend(title=None, orient="top-left"))
    hover = alt.selection_point(fields=["date"], nearest=True, on="pointerover", empty=False)
    band_ = alt.Chart(df).mark_area(color=BLUE, opacity=0.15).encode(
        alt.X("date:T", title=None), alt.Y("low_usd:Q", title="USD per month"), alt.Y2("high_usd:Q"))
    line = alt.Chart(both).mark_line(strokeWidth=2).encode(alt.X("date:T"), alt.Y("total_usd:Q"), color)
    pts = alt.Chart(both).mark_point(size=64, filled=True).encode(
        alt.X("date:T"), alt.Y("total_usd:Q"), color,
        opacity=alt.condition(hover, alt.value(1), alt.value(0)),
        tooltip=[alt.Tooltip("month:N"), alt.Tooltip("series:N"), alt.Tooltip("total_usd:Q", title="USD", format=",.0f")]
    ).add_params(hover)
    st.altair_chart((band_ + line + pts).properties(height=320), width="stretch")
    if a and a["by_month"]:
        last_m, last = list(a["by_month"].items())[-1]
        done_fc, done_act = rows[0]["completed"], last["completed"]
        if done_act and not 0.5 <= done_fc / done_act <= 2:
            st.warning(f"The funnel forecasts {done_fc:,.0f} completed interviews in {rows[0]['month']}, "
                       f"{done_fc / done_act:.1f}× the {done_act:,} completed in {last_m}. "
                       "Check `open_roles`, `applicants_per_role` and `invite_rate` against your ATS.")
    st.caption("Shaded band: volume ±{:.0%} × cost per interview {}. A scenario range, not a confidence interval."
               .format(profile["forecast"].get("volume_uncertainty", 0.25),
                       "p10–p90 from actuals" if band else f"±{profile['forecast'].get('cost_uncertainty', 0.2):.0%}"))
    st.dataframe(df.drop(columns=["series", "date"]), hide_index=True, width="stretch",
                 column_config={c: st.column_config.NumberColumn(format="%.0f") for c in
                                ("roles", "invited", "completed", "abandoned", "hires")} |
                 {c: st.column_config.NumberColumn(format="%.2f") for c in
                  ("variable_usd", "fixed_usd", "total_usd", "low_usd", "high_usd", "cost_per_hire_usd",
                   "cost_per_invited_usd")})
    st.caption("Change the funnel in the profile's `forecast:` section (Estimate tab).")

# ---------------------------------------------------------------- Export
if profile and result:
    report = {"scan": {k: v for k, v in ss.get("scan", {}).items() if k != "findings"},
              "findings": ss.get("findings", []), "profile": profile, "estimate": result,
              "optimise": est.levers(profile), "actuals_summary": ss.get("actuals")}
    st.sidebar.download_button("Export JSON", json.dumps(report, indent=2, default=str), "interview-cost.json",
                               "application/json")
    usage_text = ss.get("usage_text")
    html = rpt.render(rpt.build(profile, ss.get("findings", []), usage_text, None, ss.get("usage_synthetic", False),
                                {k: v for k, v in ss.get("scan", {}).items() if k != "findings"} or None))
    st.sidebar.download_button("Download HTML report", html, "interview-cost-report.html", "text/html",
                               help="Static, offline report. Open it in a browser; print to PDF if needed.")
