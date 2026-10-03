import shutil
from pathlib import Path

import scan

FIXTURES = Path(__file__).parent / "fixtures"


def test_fixtures(tmp_path):
    # Copy out of tests/: Semgrep ignores test directories by default.
    shutil.copytree(FIXTURES, tmp_path / "repo")
    rows = {(r["file"], r["line"]): r for r in scan.scan(str(tmp_path / "repo"))["findings"]}

    llm = rows[("interview_app/app.py", 12)]
    assert (llm["provider"], llm["model"], llm["status"]) == ("openai", "gpt-4o-mini", "candidate")
    assert "trusera" in llm["sources"] and "semgrep" in llm["sources"]  # merged, not double-counted
    assert rows[("interview_app/app.py", 16)]["provider"] == "deepgram"
    assert rows[("interview_app/app.py", 20)]["model"] == "eleven_flash_v2_5"
    assert rows[("interview_app/app.py", 24)]["status"] == "unresolved"
    assert rows[("ts_app/agent.ts", 8)]["model"] == "claude-sonnet-4-5"
    assert rows[("ts_app/agent.ts", 12)]["model"] == "tts-1"
    assert rows[("ts_app/agent.ts", 16)]["status"] == "unresolved"
    # Twilio SMS must not look like an Anthropic call; imports are inventory only.
    assert all(r["status"] == "inventory" for k, r in rows.items() if k[0] == "negative/notify.py")


def test_rejects_unapproved_url():
    import pytest
    with pytest.raises(ValueError):
        scan.checkout("https://evil.example/repo.git")
