"""Phase 4: monthly interview cost driven by the hiring funnel, not by a generic growth curve.

roles opened -> applicants -> invited to the AI interview -> started -> completed | abandoned.
Cost per completed interview comes from Phase 3 actuals when available, otherwise the Phase 1 estimate.
"""


def _add_months(ym, n):
    y, m = map(int, ym.split("-"))
    y, m = divmod(y * 12 + m - 1 + n, 12)
    return f"{y:04d}-{m + 1:02d}"


def forecast(drivers, cost_per_completed_usd, monthly_fixed_usd=0.0, cost_band=None, abandoned_share=None):
    """Return one row per month with base/low/high totals. cost_band = (p10, p90) per completed interview."""
    d = drivers
    unc_v = d.get("volume_uncertainty", 0.25)
    low_c, high_c = cost_band or (cost_per_completed_usd * (1 - d.get("cost_uncertainty", 0.2)),
                                  cost_per_completed_usd * (1 + d.get("cost_uncertainty", 0.2)))
    share = abandoned_share if abandoned_share is not None else d.get("abandoned_cost_share", 0.35)
    rows = []
    for i in range(d.get("months", 12)):
        roles = d["open_roles"] * (1 + d.get("roles_growth_pct", 0) / 100) ** i
        invited = roles * d["applicants_per_role"] * d["invite_rate"]
        started = invited * d["start_rate"]
        completed = started * d["completion_rate"]
        abandoned = started - completed
        equiv = completed + abandoned * share  # abandoned interviews cost a share of a completed one
        hires = roles * d.get("hires_per_role", 1)
        base = equiv * cost_per_completed_usd + monthly_fixed_usd
        rows.append({
            "month": _add_months(d["start"], i), "roles": roles, "invited": invited, "completed": completed,
            "abandoned": abandoned, "hires": hires,
            "variable_usd": equiv * cost_per_completed_usd, "fixed_usd": monthly_fixed_usd, "total_usd": base,
            "low_usd": equiv * (1 - unc_v) * low_c + monthly_fixed_usd,
            "high_usd": equiv * (1 + unc_v) * high_c + monthly_fixed_usd,
            "cost_per_hire_usd": base / hires if hires else None,
            "cost_per_invited_usd": base / invited if invited else None,
        })
    return rows


def totals(rows):
    keys = ("completed", "abandoned", "hires", "variable_usd", "fixed_usd", "total_usd", "low_usd", "high_usd")
    t = {k: sum(r[k] for r in rows) for k in keys}
    t["cost_per_hire_usd"] = t["total_usd"] / t["hires"] if t["hires"] else None
    return t
