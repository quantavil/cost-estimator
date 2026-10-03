# Interview Cost Workbench

What one AI interview costs (Phase 1), where to cut it (Phase 2), what it actually cost (Phase 3), and what the hiring plan will cost (Phase 4). One YAML profile (`data/interview.yaml`) drives all four.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
.venv/bin/python -m pytest -q
```

| Phase | File | What it does |
| --- | --- | --- |
| 1 Estimate | `scan.py`, `rules/`, `estimate.py` | Scans a local path or `https://github.com/` URL with Semgrep and Trusera `ai-bom`, without running the target code. You confirm the call sites; a pinned LiteLLM snapshot + `data/rates.json` price the profile. Unpriced ≠ free: gaps mark the result **partial**. |
| 2 Optimise | `estimate.levers` | Re-prices each what-if with the same calculator: same model on another billing path, prompt caching, history cap, plus your `alternatives:` (e.g. VAD-gated STT, a different TTS). Same-model prices >50% apart are flagged as suspect data, not savings. |
| 3 Actuals | `actuals.py` | Ingests JSONL usage events or OTLP/JSON GenAI spans (Langfuse, OpenLIT, OTel instrumentations) tagged `interview.id`. Reports cost per completed/abandoned interview, per minute and per role; calibrates against the estimate (±20% target); reconciles against provider invoices. |
| 4 Forecast | `forecast.py` | Drives cost from the hiring funnel (roles → applicants → invited → started → completed/abandoned). Reports monthly cost, a scenario range and interview cost per hire, and warns when the funnel disagrees with recent actuals by more than 2×. |

## Data

- `data/model_prices.json`: LiteLLM price file pinned at commit `5724117`, fetched 2026-10-03.
- `data/rates.json`: rates LiteLLM lacks, each with source URL, date and basis, plus the dated USD→INR rate. Rates marked `verified: false` stay unpriced.
- `tests/fixtures/usage_synthetic.jsonl`: **synthetic**, generated from the profile's own assumptions. It shows the pipeline works; it says nothing about estimate accuracy.

## Known gaps (fix with real inputs, not code)

- **Hiring-platform repo:** not scanned yet. The rules and default profile come from fixtures.
- **Twilio rate:** unverified, because the official page was blocked from the build environment.
- **Deepgram rate:** LiteLLM's `deepgram/nova-3` is the pre-recorded rate; streaming is billed differently.
- **ElevenLabs rate:** LiteLLM's ElevenLabs price is a list rate, so check it against your plan.
- **USD→INR:** indicative only.
- **Calibration:** needs a real interview log and the matching invoices.
- **Funnel drivers:** assumed. Replace them from your ATS.
