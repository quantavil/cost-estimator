"""Single-screen Streamlit app: scan -> review call sites -> edit one operation -> itemised estimate."""
import json

import pandas as pd
import streamlit as st
import yaml

import estimate as est
import scan

st.set_page_config(page_title="Cost Estimator", layout="wide")
st.title("Per-operation cost estimator")
ss = st.session_state


def money(usd, inr):
    return "—" if usd is None else f"${usd:,.4f} · ₹{inr:,.2f}"


# 1. Repository
st.header("1. Repository")
c1, c2, c3 = st.columns([4, 2, 1])
source = c1.text_input("Local path or https://github.com/… URL", ss.get("source", ""))
ref = c2.text_input("Commit / branch (optional)", "")
if c3.button("Scan", type="primary", disabled=not source):
    with st.spinner("Running Semgrep + Trusera AI-BOM (target code is never executed)…"):
        try:
            ss.scan = scan.scan(source.strip(), ref.strip())
            ss.source = source
        except Exception as exc:  # show the reason, keep the app usable
            st.error(f"Scan failed: {exc}")
if "scan" in ss:
    st.caption(f"Scanned `{ss.scan['path']}` at commit `{ss.scan['commit'] or 'not a git repo'}`")

# 2. Detected costs
st.header("2. Detected call sites")
findings = []
if "scan" in ss:
    st.caption("Set status per row: **billable** (map it to an estimate line in *call*), **covered** "
               "(billed inside another line, e.g. a bundled voice vendor), **ignore**. "
               "*candidate* and *unresolved* rows keep the estimate marked partial.")
    df = pd.DataFrame(ss.scan["findings"], columns=["status", "call", "provider", "kind", "model",
                                                    "file", "line", "sources"])
    edited = st.data_editor(
        df, hide_index=True, use_container_width=True, disabled=["provider", "kind", "file", "line", "sources"],
        column_config={"status": st.column_config.SelectboxColumn(options=scan.STATUSES, required=True)})
    findings = edited.to_dict("records")
else:
    st.info("Scan a repository to list call sites. You can still estimate from the profile below.")

# 3. Operation assumptions
st.header("3. Operation profile")
st.caption("One operation. `rate` keys come from `data/rates.json` or the pinned LiteLLM snapshot. "
           "Mark numbers `source: measured` only when they come from a real usage log.")
profile_text = st.text_area("Profile (YAML)", ss.get("profile_text", (est.DATA / "interview.yaml").read_text()),
                            height=420)
ss.profile_text = profile_text

# 4. Estimate
st.header("4. Estimate")
try:
    result = est.estimate(est.load_profile(profile_text), findings)
except Exception as exc:
    st.error(f"Profile error: {exc}")
    st.stop()

if result["partial"]:
    st.warning("**Partial estimate** — the items below are missing from the total:\n\n" +
               "\n".join(f"- {g}" for g in result["gaps"]))
m1, m2, m3 = st.columns(3)
m1.metric("Variable cost / operation" + (" (partial)" if result["partial"] else ""),
          money(result["variable_usd"], result["variable_inr"]))
m2.metric("Monthly fixed (separate)", money(result["monthly_fixed_usd"], result["monthly_fixed_inr"]))
m3.metric("Allocated fixed / operation", money(result["allocated_fixed_usd"], result["allocated_fixed_inr"]))

rate = result["usd_inr"]["rate"]
rows = []
for item in result["items"]:
    for line in item["lines"] or [{}]:
        cost = line.get("cost_usd")
        rows.append({"item": item["name"], "status": item["status"], "quantity source": item["quantity_source"],
                     "unit": line.get("unit", ""), "quantity": line.get("quantity"), "billed": line.get("billed"),
                     "USD / unit": line.get("unit_price_usd"),
                     "USD": cost, "INR": None if cost is None else cost * rate,
                     "rate": item["rate"], "rate source": item["rate_source"],
                     "evidence": ", ".join(item["evidence"])})
st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
             column_config={"USD / unit": st.column_config.NumberColumn(format="%.8f"),
                            "USD": st.column_config.NumberColumn(format="%.5f"),
                            "INR": st.column_config.NumberColumn(format="%.3f")})
for item in result["items"]:
    if item["note"]:
        st.caption(f"**{item['name']}:** {item['note']}")
st.caption(f"USD→INR {rate} on {result['usd_inr']['date']} ({result['usd_inr']['source']}). "
           f"Model prices: LiteLLM snapshot {result['price_snapshot']['commit'][:10]}, "
           f"fetched {result['price_snapshot']['fetched']}.")

report = {"scan": {k: v for k, v in ss.get("scan", {}).items() if k != "findings"},
          "findings": findings, "profile": est.load_profile(profile_text), "estimate": result}
st.download_button("Export JSON", json.dumps(report, indent=2, default=str), "cost-estimate.json",
                   "application/json")
