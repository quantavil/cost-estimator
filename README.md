# Cost Estimator (Phase 1)

Static scan of a repo → user-confirmed call sites → one operation profile → itemised, labelled per-operation cost.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py          # UI
.venv/bin/python scan.py <path|github-url> [ref]   # findings as JSON
.venv/bin/python estimate.py            # estimate for data/interview.yaml
.venv/bin/python -m pytest -q
```

| File | Role |
| --- | --- |
| `scan.py` | Local path or `https://github.com/` URL (shallow fetch, optional pinned ref, symlinks off, 500 MB cap). Runs Semgrep (`rules/`) + Trusera `ai-bom` (no LLM, no telemetry) and merges them into one row per call site. Target code is never executed. |
| `rules/paid_calls.yaml` | Billable SDK calls: OpenAI, Anthropic, Gemini, Deepgram, ElevenLabs, Twilio voice, plus raw HTTP as *unresolved*. |
| `estimate.py` | Price lookup (`data/rates.json` first, then the pinned LiteLLM snapshot), multi-turn chat token model (history, truncation, prompt caching), per-request billing increments, USD + INR. |
| `data/interview.yaml` | The one operation (30 min interview). All numbers are **assumed** until replaced from a real log. |
| `data/rates.json` | Non-LiteLLM rates with source URL/date/basis, the LiteLLM snapshot commit, and the dated USD→INR rate. Entries with `verified: false` stay **unpriced**. |

Rules the estimate enforces: unpriced ≠ free; any unpriced line, unreviewed `candidate`/`unresolved` call site, or `billable` site not mapped to a line marks the result **partial**. Variable, monthly fixed and allocated fixed are kept separate. Each billed vendor appears as exactly one line; mark sites billed inside another vendor as `covered`.

Known gaps:
- No hiring-platform repo yet. The rules and default profile come from `tests/fixtures`, so they need re-checking against the real code.
- Twilio's rate is unverified. Set `verified: true` after checking the official page.
- LiteLLM's `deepgram/nova-3` is the pre-recorded rate, not the streaming rate.
- Calibration against real bills (gate 5) needs one interview log and the matching invoices.
- Semgrep skips `tests/` directories by default, and cloned checkouts stay in the temp directory until the OS cleans it.
