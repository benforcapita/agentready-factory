"""Deterministic, offline guardrails for customer drafts."""

import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import parse_qsl, urlsplit

from .markers import DRAFT_MARKER

SOURCE_ALIASES = {
    "direct": r"HTTP\s+\d{3}|probes\.csv|בקשות ישירות|direct request",
    "is-agentic": r"is-agentic",
    "webfetch": r"WebFetch",
    "tinyfish": r"TinyFish",
    "jina": r"Jina",
    "validator": r"validator\.schema\.org",
    "sitemap": r"sitemap|מפת האתר|במפת האתר|מפת אתר",
    "robots": r"robots\.txt",
}
WARNINGS = {"uncited-finding", "placeholder-left"}
MESSAGES = {
    "price-in-offer": "Remove price or cost language from the offer.",
    "code-block": "Customer reports must not contain code.",
    "ben-role": "Do not describe Ben as a development team manager.",
    "tracking-pixel": "Remove tracking media or analytics resources.",
    "tracking-param": "Remove tracking URL parameters.",
    "missing-appendix": "Include an appendix heading followed by a Markdown table.",
    "missing-draft-marker": "Shadow artifacts must contain a draft marker.",
    "secret": "Remove credential-looking content.",
    "blocked-single-source": "Blocking claims need at least two distinct sources.",
    "extra-offer-steps": "The offer must contain at most four numbered steps.",
    "uncited-finding": "Cite a source for this finding.",
    "placeholder-left": "Fill or explicitly acknowledge this placeholder.",
}
PRICE = re.compile(r"[₪$€£]|ש[\"״']ח|מחיר|עלות|תמחור|\b(?:NIS|ILS|USD|price|pricing|cost|fee|quote)\b", re.I)
ROLE = re.compile(r"ראש צוות|מנהל צוות פיתוח|\b(?:dev(?:elopment)? team manager|team lead)\b", re.I)
SECRET = re.compile(
    r"\bAKIA[A-Z0-9]{16,}|\b(?:sk_live_|sk-)[A-Za-z0-9_-]{16,}"
    r"|\b(?:ghp_|github_pat_)[A-Za-z0-9_]{16,}|\bxox[abpr]-[A-Za-z0-9-]{16,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*"
    r"|\b(?:api_key|token|password|secret)\s*=\s*[\"']?[^\s\"'<>]{16,}", re.I
)
URL = re.compile(r"(?:https?:)?//[^\s<>\"'`)\]]+", re.I)
TRACKERS = ("google-analytics.com", "googletagmanager.com", "doubleclick.net",
            "mixpanel.com", "segment.com", "segment.io", "hotjar.com", "hotjar.io",
            "analytics.google.com", "facebook.com")


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).split())


def fingerprint(rule, file, excerpt):
    return hashlib.sha256(f"{rule}\0{file}\0{normalized(excerpt)}".encode()).hexdigest()[:16]


def tracker(url):
    parsed = urlsplit(unescape(url))
    host = (parsed.hostname or "").lower()
    return any(host == name or host.endswith("." + name) for name in TRACKERS
               if name != "facebook.com") or (
                   (host == "facebook.com" or host.endswith(".facebook.com"))
                   and parsed.path.startswith("/tr"))


class MediaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.matches = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in {"img", "script", "iframe", "beacon"}:
            return
        dimensions = {key: attrs.get(key, "") for key in ("width", "height")}
        for key, value in re.findall(r"(width|height)\s*:\s*([\d.]+(?:px)?)", attrs.get("style", ""), re.I):
            dimensions[key.lower()] = value
        tiny = all(re.fullmatch(r"[01](?:\.0+)?(?:px)?", value.strip(), re.I)
                   for value in dimensions.values())
        if (tag == "img" and tiny) or tracker(attrs.get("src", "")):
            self.matches.append((self.getpos()[0], self.get_starttag_text()))

    handle_startendtag = handle_starttag


def inspect_artifact(file, content, phase):
    findings = []

    def add(rule, line, excerpt):
        excerpt = normalized(excerpt)
        item = dict(rule=rule, level="warning" if rule in WARNINGS else "hard_fail",
                    file=file, line=line, excerpt=excerpt, message=MESSAGES[rule],
                    fingerprint=fingerprint(rule, file, excerpt), acknowledged=False,
                    ack_reason=None)
        if item not in findings:
            findings.append(item)

    lines = content.splitlines()
    section = None
    appendix = False
    appendix_table = False
    steps = []
    for number, line in enumerate(lines, 1):
        heading = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            title = heading[2]
            if len(heading[1]) <= 2:
                numbered = re.match(r"(\d+)\.\s*", title) if len(heading[1]) == 2 else None
                section = int(numbered[1]) if numbered else None
                appendix = bool(re.search(r"נספח|Appendix", title, re.I))
        for rule, pattern in (("ben-role", ROLE), ("secret", SECRET),
                              ("placeholder-left", re.compile(r"\[[A-Z][A-Z0-9_]*\]"))):
            for match in pattern.finditer(line):
                add(rule, number, match[0])
        for match in URL.finditer(unescape(line)):
            if any(key.lower().startswith("utm_") or key.lower() in
                   {"gclid", "fbclid", "mc_eid", "msclkid"}
                   for key, _ in parse_qsl(urlsplit(match[0]).query, keep_blank_values=True)):
                add("tracking-param", number, match[0])
        if file != "report.md":
            continue
        if section in {4, 5} and PRICE.search(line):
            add("price-in-offer", number, line)
        if re.match(r" {0,3}(?:`{3,}|~{3,})|(?: {4}|\t)\S", line) or re.search(r"`+[^`]+`+", line):
            add("code-block", number, line)
        if section == 4 and re.match(r"^ {0,3}\d+[.)]\s+", line):
            steps.append((number, line))
        if section == 2 and re.match(r"^\s*\|\s*\d+\s*\|", line):
            sources = sum(bool(re.search(alias, line, re.I)) for alias in SOURCE_ALIASES.values())
            if not sources:
                add("uncited-finding", number, line)
            if sources < 2 and re.search(r"חסום|נחסם|נחסמת|חסימה|\bblock(?:ed|ing)\b", line, re.I):
                add("blocked-single-source", number, line)
        if appendix and re.match(r"^\s*\|?\s*:?-{3,}:?\s*\|", line) and number > 1 and "|" in lines[number - 2]:
            appendix_table = True
    if file == "report.md":
        if not appendix_table:
            add("missing-appendix", 1, "report.md")
        if len(steps) > 4:
            add("extra-offer-steps", *steps[4])
    if file in {"report.md", "report.html"}:
        if phase == "shadow" and DRAFT_MARKER not in content:
            add("missing-draft-marker", 1, file)
        parser = MediaParser()
        parser.feed(content)
        for number, excerpt in parser.matches:
            add("tracking-pixel", number, excerpt)
        for match in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)(?:\{([^}]+)\})?", content):
            size = match[2] or ""
            tiny = all(re.search(rf"\b{key}\s*[=:]\s*[\"']?[01](?:px)?\b", size)
                       for key in ("width", "height"))
            if tiny or tracker(match[1].split()[0]):
                add("tracking-pixel", content.count("\n", 0, match.start()) + 1, match[0])
    return findings


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
        if ack["file"] not in {"report.md", "report.html", "notes-for-ben.md"}:
            raise ValueError("ack file names must be relative artifact names")
    return data["acks"]


def lint(draft_dir, phase="shadow", ack_path=None):
    if phase not in {"shadow", "reviewed", "volume"}:
        raise ValueError("invalid phase")
    root = Path(draft_dir)
    if not (root / "report.md").is_file():
        raise ValueError("DRAFT_DIR must contain report.md")
    if ack_path is None and (root / "lint-ack.json").exists():
        ack_path = root / "lint-ack.json"
    acks = load_acks(ack_path)
    inputs, findings = [], []
    for file in ("notes-for-ben.md", "report.html", "report.md"):
        path = root / file
        if path.exists():
            raw = path.read_bytes()
            inputs.append(dict(file=file, sha256=hashlib.sha256(raw).hexdigest()))
            findings.extend(inspect_artifact(file, raw.decode("utf-8"), phase))
    findings.sort(key=lambda item: (item["file"], item["line"], item["rule"], item["fingerprint"]))
    unused = []
    for ack in acks:
        matches = [f for f in findings if all(f[key] == ack[key] for key in ("rule", "file", "fingerprint"))]
        warnings = [f for f in matches if f["level"] == "warning" and not f["acknowledged"]]
        if warnings:
            for finding in warnings:
                finding.update(acknowledged=True, ack_reason=ack["reason"])
        else:
            unused.append({**ack, "status": "hard-fail-ack-ignored" if matches and matches[0]["level"] == "hard_fail" else "unused"})
    summary = dict(hard_fail=sum(f["level"] == "hard_fail" for f in findings),
                   warning=sum(f["level"] == "warning" for f in findings),
                   acknowledged=sum(f["acknowledged"] for f in findings),
                   unacknowledged_warning=sum(f["level"] == "warning" and not f["acknowledged"] for f in findings))
    return dict(version=1, phase=phase, inputs=inputs, findings=findings,
                unused_acks=sorted(unused, key=lambda a: (a["file"], a["rule"], a["fingerprint"], a["reason"])),
                summary=summary, passed=not (summary["hard_fail"] or summary["unacknowledged_warning"]))


def serialize(result):
    return json.dumps(result, ensure_ascii=False, indent=2) + "\n"
