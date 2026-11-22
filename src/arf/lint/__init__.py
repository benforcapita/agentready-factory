"""Deterministic, offline guardrails for customer drafts."""

import csv
import hashlib
import io
import json
from pathlib import Path
import re
import unicodedata

from .document import sections, tables
from .pdf import has_draft_marker
from .rules import MESSAGES, SECRET, SOURCE_ALIASES, URL, WARNINGS, host, normalize_for_matching
from .rules import inspect_artifact as scan_artifact


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).split())


def fingerprint(rule, file, excerpt):
    return hashlib.sha256(f"{rule}\0{file}\0{normalized(excerpt)}".encode()).hexdigest()[:16]


def finding(rule, file, line, excerpt):
    excerpt = normalized(excerpt)
    return dict(rule=rule, level="warning" if rule in WARNINGS else "hard_fail", file=file,
                line=line, excerpt=excerpt, message=MESSAGES[rule],
                fingerprint=fingerprint(rule, file, excerpt), acknowledged=False, ack_reason=None)


def inspect_artifact(file, content, phase, **kwargs):
    result = []
    for rule, line, excerpt in scan_artifact(file, content, phase, **kwargs):
        item = finding(rule, file, line, excerpt)
        if item not in result:
            result.append(item)
    return result


def load_acks(path):
    if path is None:
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1 or not isinstance(data.get("acks"), list):
        raise ValueError("ack file must have version 1 and an acks list")
    for ack in data["acks"]:
        if not isinstance(ack, dict) or any(not isinstance(ack.get(key), str) or not ack[key].strip()
                                            for key in ("rule", "file", "fingerprint", "reason")):
            raise ValueError("each ack needs nonempty rule, file, fingerprint and reason")
        if Path(ack["file"]).is_absolute() or ".." in Path(ack["file"]).parts:
            raise ValueError("ack file names must be relative artifact names")
    return data["acks"]


def load_config(path):
    """Accept JSON or a flat YAML mapping of the three required string scalars."""
    raw = Path(path).read_text(encoding="utf-8")
    if raw.lstrip().startswith("{"):
        data = json.loads(raw)
    else:
        data = {}
        for line in raw.splitlines():
            if not line.strip() or line.lstrip().startswith("#") or line.strip() in {"---", "..."}:
                continue
            match = re.fullmatch(r"([a-z_]+):\s*(.*?)\s*", line)
            if not match or match[1] in data:
                raise ValueError("Factory Config YAML must be a flat mapping of unique string scalars")
            value = match[2]
            if value.startswith('"'):
                value = json.loads(value)
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1].replace("''", "'")
            elif value.startswith(("|", ">", "[", "{", "&", "*", "!")):
                raise ValueError("Factory Config YAML requires inline string scalars")
            else:
                value = value.split(" #", 1)[0].strip()
            data[match[1]] = value
    required = {"prepared_by", "contact_email", "employer"}
    if not isinstance(data, dict) or set(data) != required or any(not isinstance(v, str) or not v.strip() for v in data.values()):
        raise ValueError("Factory Config needs prepared_by, contact_email, employer strings")
    return data


def safe_file(root, name):
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        return None
    resolved = (root / path).resolve()
    return resolved if resolved.is_relative_to(root.resolve()) and resolved.is_file() else None


def load_findings(raw):
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise ValueError("findings.json must contain findings[].id and evidence (a list of paths)")
    ids = set()
    for item in data["findings"]:
        if not isinstance(item, dict) or type(item.get("id")) not in {str, int} or not normalize_for_matching(str(item["id"])).strip():
            raise ValueError("each findings.json finding needs a nonempty string or integer id")
        identity = normalize_for_matching(str(item["id"])).strip()
        if identity in ids:
            raise ValueError("findings.json ids must be unique")
        ids.add(identity)
        if not isinstance(item.get("evidence"), list) or any(not isinstance(path, str) for path in item["evidence"]):
            raise ValueError("each findings.json finding needs evidence: a list of paths")
    return data["findings"]


def lint(draft_dir, phase="shadow", ack_path=None, *, final=False, factory_config=None):
    if phase not in {"shadow", "reviewed", "volume"}:
        raise ValueError("invalid phase")
    root = Path(draft_dir)
    if not (root / "report.md").is_file():
        raise ValueError("DRAFT_DIR must contain report.md")
    if ack_path is None and (root / "lint-ack.json").exists():
        ack_path = root / "lint-ack.json"
    acks = load_acks(ack_path)
    inputs, findings, skipped = {}, [], []

    def read(file):
        raw = (root / file).read_bytes()
        inputs[file] = dict(file=file, sha256=hashlib.sha256(raw).hexdigest())
        return raw

    def skip(rule, reason):
        item = dict(rule=rule, reason=reason)
        if item not in skipped:
            skipped.append(item)

    config = load_config(factory_config) if factory_config else None
    if factory_config:
        raw = Path(factory_config).read_bytes()
        inputs["factory-config"] = dict(file="factory-config", sha256=hashlib.sha256(raw).hexdigest())
    else:
        for rule in ("employer-mention", "prepared-by", "recipient-email"):
            skip(rule, "Factory Config is absent; supply --factory-config PATH.")
    manifest = None
    allowed_hosts = None
    files = []
    if (root / "evidence.json").exists():
        manifest = json.loads(read("evidence.json"))
        if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list) or any(
                not isinstance(entry, dict) or not isinstance(entry.get("path"), str) for entry in manifest["files"]):
            raise ValueError("evidence.json must use the collector manifest format: files[].path")
        files = manifest["files"]
        allowed_hosts = set()
        for entry in files:
            path = safe_file(root, entry["path"])
            if path:
                raw = read(entry["path"])
                if entry["path"] == "discovery.csv":
                    rows = csv.DictReader(io.StringIO(raw.decode("utf-8")))
                    allowed_hosts.update(host(row["url"]) for row in rows if row.get("url"))
        if isinstance(manifest.get("site"), str):
            allowed_hosts.add(host(manifest["site"]))
        allowed_hosts.update(name.lower().removeprefix("www.") for name in manifest.get("hosts", []) if isinstance(name, str))
    else:
        for rule in ("appendix-evidence", "non-evidence-url"):
            skip(rule, "evidence.json collector manifest is absent.")
    markdown = normalize_for_matching(read("report.md").decode("utf-8"))
    for file in ("notes-for-ben.md", "report.html", "report.md"):
        if (root / file).exists():
            content = read(file).decode("utf-8")
            findings.extend(inspect_artifact(file, content, phase, final=final, config=config, allowed_hosts=allowed_hosts))
    if manifest is not None:
        for offset, title, body in sections(markdown):
            if not re.search(r"נספח|Appendix", title, re.I):
                continue
            table = tables(body)
            for number, _, row in table[0][1] if table else []:
                if not any(entry["path"] in row for entry in files):
                    findings.append(finding("appendix-evidence", "report.md", markdown.count("\n", 0, offset) + number, row))
    for offset, title, body in sections(markdown):
        if not re.match(r"2\.\s", title):
            continue
        table = tables(body)
        for number, _, row in table[0][1] if table else []:
            for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", row):
                target = match[1].split("#", 1)[0]
                if target and not re.match(r"(?:https?:)?//", target) and not safe_file(root, target):
                    findings.append(finding("uncited-finding", "report.md", markdown.count("\n", 0, offset) + number, match[0]))
    if (root / "findings.json").exists():
        entries = load_findings(read("findings.json"))
        manifest_paths = {entry["path"] for entry in files}
        for item in entries:
            evidence = item["evidence"]
            invalid = []
            for path in evidence:
                if not safe_file(root, path) or (manifest is not None and path not in manifest_paths):
                    invalid.append(path)
                else:
                    read(path)
            if not evidence or invalid:
                excerpt = json.dumps(dict(id=item["id"], evidence=invalid if invalid else evidence), ensure_ascii=False, sort_keys=True)
                findings.append(finding("uncited-finding", "findings.json", 1, excerpt))
    else:
        skip("uncited-finding", "findings.json is absent; explicit report evidence links are checked when present.")
    skip("public-side-effect", "No run/public-side-effect record format exists in this repository; no event schema invented.")
    if (root / "report.pdf").exists():
        pdf = read("report.pdf")
        if phase in {"shadow", "reviewed"} and not final and not has_draft_marker(pdf):
            item = finding("missing-draft-marker", "report.pdf", 1, "PDF marker unverifiable")
            item["message"] = "PDF marker unverifiable"
            findings.append(item)
        pages = len(re.findall(rb"/Type\s*/Page\b", pdf))
        if pages > 4:
            findings.append(finding("pdf-length", "report.pdf", 1, f"{pages} pages"))
        elif not pages:
            skip("pdf-length", "report.pdf has no readable /Type /Page objects; compressed object streams are not decoded.")
    else:
        skip("pdf-length", "report.pdf is absent.")
    skip("secret", "Binary PDF streams and raw evidence captures are not text artifacts; scan report/notes and structured artifact text only. Notion pre-write is outside this command.")
    for file in ("findings.json", "evidence.json"):
        if (root / file).exists():
            text = read(file).decode("utf-8")
            for number, line in enumerate(text.splitlines(), 1):
                for match in SECRET.finditer(normalize_for_matching(line)):
                    findings.append(finding("secret", file, number, match[0]))
    findings = [dict(items) for items in dict.fromkeys(tuple(f.items()) for f in findings)]
    findings.sort(key=lambda item: (item["file"], item["line"], item["rule"], item["fingerprint"]))
    unused = []
    for ack in acks:
        matches = [f for f in findings if all(f[key] == ack[key] for key in ("rule", "file", "fingerprint"))]
        warnings = [f for f in matches if f["level"] == "warning" and not f["acknowledged"]]
        if warnings:
            for item in warnings:
                item.update(acknowledged=True, ack_reason=ack["reason"])
        else:
            unused.append({**ack, "status": "hard-fail-ack-ignored" if matches and matches[0]["level"] == "hard_fail" else "unused"})
    summary = dict(hard_fail=sum(f["level"] == "hard_fail" for f in findings),
                   warning=sum(f["level"] == "warning" for f in findings),
                   acknowledged=sum(f["acknowledged"] for f in findings),
                   unacknowledged_warning=sum(f["level"] == "warning" and not f["acknowledged"] for f in findings))
    return dict(version=1, phase=phase, final=final, inputs=[inputs[key] for key in sorted(inputs)], findings=findings,
                unused_acks=sorted(unused, key=lambda a: (a["file"], a["rule"], a["fingerprint"], a["reason"])),
                skipped_checks=sorted(skipped, key=lambda item: (item["rule"], item["reason"])),
                summary=summary, passed=not (summary["hard_fail"] or summary["unacknowledged_warning"]))


def serialize(result):
    return json.dumps(result, ensure_ascii=False, indent=2) + "\n"
