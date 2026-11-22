"""Scores: Musy passthrough, is-agentic stored-report lookup and the gated public scan.

PRO-424 policy: a new is-agentic public scan (``npx is-agentic``) runs only when the request sets
``allow_public_scan: true`` and ``ben_ok`` quotes Ben's OK for that run. Anything else is refused
before any process is spawned.
"""

import json
import os
import subprocess
import urllib.parse
from pathlib import Path

from ..readers.adapters import ReaderError, urllib_fetch

ISAG_API = "https://is-agentic.com/api/v1/report"
ISAG_CLI_PACKAGE = "is-agentic@1.0.1"
SCAN_TIMEOUT = 300


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def musy_passthrough(request):
    """Musy's own scores, copied as received; never rescored."""
    if request.musy is None:
        return None
    musy = request.musy
    return {
        "source": "musy",
        "agent_ready": musy.scores.agent_ready,
        "poppy": musy.scores.poppy,
        "scan_date": musy.scan_date.isoformat(),
        "claimed_gaps": list(musy.claimed_gaps),
        "message_id": musy.message_id,
    }


def _decode(body):
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return None


def api_lookup(site, *, fetch=urllib_fetch):
    """Read the latest stored is-agentic report. 404 and other failures are recorded, not raised."""
    url = f"{ISAG_API}?{urllib.parse.urlencode({'url': site})}"
    try:
        status, body = fetch("GET", url, {"Accept": "application/json"})
    except ReaderError as error:
        return {"source": "is-agentic-api", "url": url, "status": None, "found": False, "error": str(error)}
    data = _decode(body)
    result = {"source": "is-agentic-api", "url": url, "status": status, "found": False, "response": data}
    if status == 200 and isinstance(data, dict) and "score" in data:
        result.update(found=True, score=data["score"], score_label=data.get("score_label"),
                      report_url=data.get("report_url"), scanned_at=data.get("scanned_at"))
    elif status == 404:
        result["error"] = (data or {}).get("code", "not_found") if isinstance(data, dict) else "not_found"
    else:
        result["error"] = f"unexpected status {status}"
    return result


def scan_gate(request):
    """Decide whether a new public scan may run for this request."""
    if not request.allow_public_scan:
        return {"allowed": False, "reason": "allow_public_scan is false (default)"}
    ben_ok = (request.ben_ok or "").strip()
    if not ben_ok:
        return {"allowed": False, "reason": "refused: allow_public_scan is true but ben_ok is missing"}
    return {"allowed": True, "reason": "allow_public_scan is true and ben_ok is present", "ben_ok": ben_ok}


def scan_command(site):
    host = urllib.parse.urlsplit(site).hostname or site
    return ["npx", "--yes", ISAG_CLI_PACKAGE, host, "--json"]


def public_scan(request, *, runner=subprocess.run):
    """Run ``npx is-agentic --json`` only when the gate allows it; never spawns otherwise."""
    gate = scan_gate(request)
    if not gate["allowed"]:
        return {"source": "is-agentic-cli", "ran": False, "gate": gate}
    command = scan_command(request.site)
    try:
        completed = runner(command, capture_output=True, text=True, timeout=SCAN_TIMEOUT,
                           check=False, env={**os.environ, "NO_COLOR": "1"})
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"source": "is-agentic-cli", "ran": True, "gate": gate, "command": command, "error": str(error)}
    try:
        data = json.loads(completed.stdout or "")
    except ValueError:
        data = None
    result = {"source": "is-agentic-cli", "ran": True, "gate": gate, "command": command,
              "exit_code": completed.returncode, "response": data}
    if completed.returncode == 0 and isinstance(data, dict) and "score" in data:
        result.update(score=data["score"], score_label=data.get("score_label"))
    else:
        result["error"] = (completed.stderr or "").strip()[:500] or "scan failed"
    return result


def collect_scores(request, out, *, fetch=urllib_fetch, runner=subprocess.run):
    """Write score.json: Musy passthrough, stored is-agentic report, and the gated scan outcome."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    api = api_lookup(request.site, fetch=fetch)
    write_json(out / "isag_api.json", api)
    if api["found"]:
        scan = {"source": "is-agentic-cli", "ran": False, "gate": scan_gate(request),
                "skipped": "stored report found"}
    else:
        scan = public_scan(request, runner=runner)
        if scan["ran"]:
            write_json(out / "isag_cli.json", scan)
    isag = api if api["found"] else scan if "score" in scan else None
    summary = {
        "site": request.site,
        "musy": musy_passthrough(request),
        "is_agentic": {
            "score": isag["score"] if isag else None,
            "score_label": isag.get("score_label") if isag else None,
            "from": isag["source"] if isag else None,
            "api": {"status": api["status"], "found": api["found"], "error": api.get("error")},
            "public_scan": {k: scan[k] for k in ("ran", "gate", "skipped", "error") if k in scan},
        },
    }
    write_json(out / "score.json", summary)
    return summary
