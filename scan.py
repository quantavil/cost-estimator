"""Read-only checkout, the two scanners, and one merged finding list.

Never runs anything from the target repo: no installs, builds, tests or hooks.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
BIN = Path(sys.executable).parent
ALLOWED_PREFIXES = ("https://github.com/",)
MAX_CHECKOUT_MB = 500
MODEL_RE = re.compile(r"""\bmodel(?:_id|Id)?["']?\s*[=:]\s*["']([^"']+)["']""")
# candidate/unresolved keep the estimate partial; inventory = import/config only.
STATUSES = ["candidate", "unresolved", "billable", "covered", "ignore", "inventory"]


def _tool(name):
    return str(BIN / name) if (BIN / name).exists() else shutil.which(name) or name


def _git(cwd, *args):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"}
    return subprocess.run(["git", "-c", "core.symlinks=false", *args], cwd=cwd, env=env,
                          capture_output=True, text=True, check=True, timeout=300).stdout.strip()


def checkout(source, ref=""):
    """Local directory or allowed git URL -> (path, commit sha). Pins `ref` (sha/branch/tag) if given."""
    local = Path(source).expanduser()
    if local.is_dir():
        try:
            sha = _git(local, "rev-parse", "HEAD")
        except (subprocess.CalledProcessError, FileNotFoundError):
            sha = ""
        return local.resolve(), sha
    if not source.startswith(ALLOWED_PREFIXES) or any(c in source for c in " ;|&$`"):
        raise ValueError(f"Only local paths or URLs starting with {ALLOWED_PREFIXES} are allowed")
    dest = Path(tempfile.mkdtemp(prefix="cost-estimator-"))
    try:
        _git(dest, "init", "-q")
        _git(dest, "fetch", "-q", "--depth", "1", "--no-tags", source, ref or "HEAD")
        _git(dest, "checkout", "-q", "FETCH_HEAD")
        # ponytail: size is checked after the shallow fetch, not streamed; fine for a local demo tool.
        mb = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1e6
        if mb > MAX_CHECKOUT_MB:
            raise ValueError(f"Checkout is {mb:.0f} MB, limit is {MAX_CHECKOUT_MB} MB")
        return dest, _git(dest, "rev-parse", "HEAD")
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise


def _model_at(path, start, end):
    try:
        text = "\n".join(Path(path).read_text(errors="ignore").splitlines()[start - 1:end])
    except OSError:
        return ""
    m = MODEL_RE.search(text)
    return m.group(1) if m else ""


def semgrep(repo):
    out = subprocess.run([_tool("semgrep"), "scan", "--metrics=off", "--disable-version-check", "--quiet",
                          "--json", "--config", str(ROOT / "rules"), str(repo)],
                         capture_output=True, text=True, timeout=900)
    data = json.loads(out.stdout or "{}")
    if "results" not in data:
        raise RuntimeError(f"semgrep failed: {out.stderr[-500:]}")
    return [{
        "file": str(Path(r["path"]).resolve().relative_to(repo)),
        "line": r["start"]["line"],
        "end": r["end"]["line"],
        "provider": r["extra"]["metadata"]["provider"],
        "kind": r["extra"]["metadata"]["kind"],
        "model": _model_at(r["path"], r["start"]["line"], r["end"]["line"]),
        "sources": ["semgrep:" + r["check_id"].rsplit(".", 1)[-1]],
    } for r in data["results"]]


def trusera(repo):
    """AI inventory from Trusera ai-bom, LLM enrichment and telemetry off. Missing tool -> no findings."""
    with tempfile.TemporaryDirectory() as tmp:
        out_file = Path(tmp) / "aibom.json"
        try:
            subprocess.run([_tool("ai-bom"), "scan", str(repo), "-f", "cyclonedx", "-o", str(out_file),
                            "-q", "--no-telemetry"], capture_output=True, text=True, timeout=900, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            return []
        bom = json.loads(out_file.read_text())
    found = []
    for c in bom.get("components", []):
        props = {p["name"]: p["value"] for p in c.get("properties", [])}
        loc = props.get("trusera:source_location", "")
        file, _, line = loc.rpartition(":")
        if not line.isdigit():
            continue
        try:
            rel = str(Path(file).resolve().relative_to(repo))
        except ValueError:
            continue
        found.append({"file": rel, "line": int(line), "end": int(line),
                      "provider": props.get("trusera:provider", c.get("name", "")).lower(),
                      "kind": props.get("trusera:usage_type", "ai"),
                      "model": props.get("trusera:model_name", ""),
                      "sources": ["trusera:" + c.get("name", "")]})
    return found


def merge(findings):
    """One row per call site. Trusera rows inside a Semgrep match span join that row; never summed."""
    rows = []
    for f in sorted(findings, key=lambda f: (not f["sources"][0].startswith("semgrep"), f["file"], f["line"])):
        same = next((r for r in rows if r["file"] == f["file"] and r["line"] <= f["line"] <= r["end"]), None)
        if same:
            same["sources"] = sorted(set(same["sources"] + f["sources"]))
            same["model"] = same["model"] or f["model"]
            if same["provider"] == "unknown":
                same["provider"] = f["provider"]
            continue
        rows.append(dict(f))
    for r in rows:
        is_call = any(s.startswith("semgrep") for s in r["sources"])
        r["status"] = "unresolved" if r["provider"] == "unknown" else "candidate" if is_call else "inventory"
        r["call"] = ""
        r["sources"] = ", ".join(r["sources"])
    return sorted(rows, key=lambda r: (r["file"], r["line"]))


def scan(source, ref=""):
    repo, sha = checkout(source, ref)
    repo = repo.resolve()
    return {"source": source, "path": str(repo), "commit": sha,
            "findings": merge(semgrep(repo) + trusera(repo))}


if __name__ == "__main__":
    print(json.dumps(scan(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""), indent=2))
