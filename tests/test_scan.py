import shutil
import subprocess
from pathlib import Path

import pytest

import scan

FIXTURES = Path(__file__).parent / "fixtures"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                               "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(cwd), "PATH": "/usr/bin:/bin"}).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "a.py").write_text("x = 1\n")
    git(r, "add", ".")
    git(r, "commit", "-qm", "one")
    return r


@pytest.fixture
def scanned(tmp_path_factory):
    # Copy out of tests/: Semgrep ignores test directories by default.
    root = tmp_path_factory.mktemp("scan") / "repo"
    shutil.copytree(FIXTURES, root, ignore=shutil.ignore_patterns("*.json", "*.jsonl"))
    return {(r["file"], r["line"]): r for r in scan.scan(str(root))["findings"]}


def test_fixture_call_sites(scanned):
    llm = scanned[("interview_app/app.py", 12)]
    assert (llm["provider"], llm["model"], llm["status"]) == ("openai", "gpt-4o-mini", "candidate")
    assert "trusera" in llm["sources"] and "semgrep" in llm["sources"]  # merged, not double-counted
    assert scanned[("interview_app/app.py", 16)]["provider"] == "deepgram"
    assert scanned[("interview_app/app.py", 20)]["model"] == "eleven_flash_v2_5"
    assert scanned[("interview_app/app.py", 24)]["status"] == "unresolved"
    assert scanned[("ts_app/agent.ts", 8)]["model"] == "claude-sonnet-4-5"
    assert scanned[("ts_app/agent.ts", 12)]["model"] == "tts-1"
    assert scanned[("ts_app/agent.ts", 16)]["status"] == "unresolved"


def test_negative_fixture_has_no_billable_calls(scanned):
    # Twilio SMS must not look like an Anthropic call; imports are inventory only.
    assert all(r["status"] == "inventory" for k, r in scanned.items() if k[0] == "negative/notify.py")


def test_wrapper_called_three_times_is_one_call_site(scanned):
    calls = [r for k, r in scanned.items() if k[0] == "wrapper_app/llm.py" and r["status"] == "candidate"]
    assert len(calls) == 1 and calls[0]["model"] == "gpt-4o-mini"


def test_head_commit_matches_git(repo):
    assert scan.head_commit(repo) == git(repo, "rev-parse", "HEAD")


def test_head_commit_packed_detached_worktree_and_empty(repo, tmp_path):
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "pack-refs", "--all")
    assert not (repo / ".git/refs/heads/main").exists() and scan.head_commit(repo) == sha
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "side")
    assert scan.head_commit(wt) == sha  # .git is a file pointing at the main repo
    git(repo, "checkout", "-q", "--detach")
    assert scan.head_commit(repo) == sha
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-q")
    assert scan.head_commit(empty) == "" and scan.head_commit(tmp_path) == ""


def test_url_checkout_pins_ref_and_disables_symlinks(repo, monkeypatch):
    first = git(repo, "rev-parse", "HEAD")
    (repo / "link").symlink_to("/etc/passwd")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "two")
    monkeypatch.setattr(scan, "ALLOWED_PREFIXES", ("file://",))
    path, sha = scan.checkout(f"file://{repo}", first)
    assert sha == first and not (path / "link").exists()
    path, sha = scan.checkout(f"file://{repo}")
    assert sha != first and not (path / "link").is_symlink()  # stored as a plain file, never followed
    shutil.rmtree(path)


def test_size_cap_rejects_and_cleans_up(repo, monkeypatch):
    monkeypatch.setattr(scan, "ALLOWED_PREFIXES", ("file://",))
    monkeypatch.setattr(scan, "MAX_CHECKOUT_MB", 0)
    before = set(Path(scan.tempfile.gettempdir()).glob("cost-estimator-*"))
    with pytest.raises(ValueError, match="limit"):
        scan.checkout(f"file://{repo}")
    assert set(Path(scan.tempfile.gettempdir()).glob("cost-estimator-*")) == before


@pytest.mark.parametrize("url", ["https://evil.example/repo.git", "http://github.com/a/b",
                                 "https://github.com/a/b;rm -rf /", "https://github.com/a/b`id`", "/no/such/dir"])
def test_rejects_unapproved_sources(url):
    with pytest.raises(ValueError):
        scan.checkout(url)


def test_missing_trusera_degrades_to_semgrep_only(monkeypatch, tmp_path):
    monkeypatch.setattr(scan, "_tool", lambda name: "/nonexistent/" + name)
    assert scan.trusera(tmp_path) == []


def test_semgrep_failure_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(scan, "_tool", lambda name: "false")
    with pytest.raises(RuntimeError, match="semgrep failed"):
        scan.semgrep(tmp_path)


def test_merge_keeps_both_sources_and_never_sums():
    rows = scan.merge([
        {"file": "a.py", "line": 10, "end": 12, "provider": "openai", "kind": "llm", "model": "", "sources": ["semgrep:x"]},
        {"file": "a.py", "line": 11, "end": 11, "provider": "openai", "kind": "completion", "model": "gpt-4o",
         "sources": ["trusera:OpenAI"]},
        {"file": "a.py", "line": 1, "end": 1, "provider": "openai", "kind": "completion", "model": "",
         "sources": ["trusera:OpenAI"]},
    ])
    assert [(r["line"], r["status"]) for r in rows] == [(1, "inventory"), (10, "candidate")]
    assert rows[1]["model"] == "gpt-4o" and rows[1]["sources"] == "semgrep:x, trusera:OpenAI"
