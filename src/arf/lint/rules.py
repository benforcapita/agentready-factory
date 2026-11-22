"""Deterministic rules implementing LLD §6.4 (no model judging)."""

from html import unescape
import re
import unicodedata
from urllib.parse import parse_qsl, urlsplit

from .document import HTMLDocument, headings, plain, sections, tables
from .markers import DRAFT_COMMENT, DRAFT_MARKER

WARNINGS = {"currency-outside-offer", "generic-price", "pdf-length", "too-many-findings", "public-side-effect"}
MESSAGES = {
    "price-in-offer": "Service pricing phrases or currency amounts in proposal/private notes are forbidden.",
    "generic-price": "Acknowledge generic price/cost language with its context.",
    "currency-outside-offer": "Acknowledge currency amounts outside the proposal.",
    "code-block": "Customer reports must not contain code.",
    "implementation-detail": "Remove implementation tokens from the customer report.",
    "extra-offer-steps": "The offer must contain at most four pillars.",
    "detailed-offer-step": "Offer pillars must be at most 40 words and not step-by-step instructions.",
    "ben-role": "Do not describe Ben as a development team manager.",
    "employer-mention": "Do not mention Ben's configured employer.",
    "prepared-by": "The prepared-by line must equal Factory Config text.",
    "sent-status": "Drafts must not have a sent status.",
    "recipient-email": "Only Ben's configured contact email is permitted, on his contact line.",
    "tracking-pixel": "Remove scripts, remote images, pixels and analytics media.",
    "tracking-param": "Remove tracking URL parameters.",
    "link-shortener": "Link shorteners are forbidden.",
    "non-evidence-url": "Third-party URLs must be evidence sources.",
    "missing-appendix": "Include an appendix table with at least three data rows.",
    "appendix-evidence": "Every appendix row must map to an evidence.json entry.",
    "required-headings": "Include executive summary, sections 1–5 and appendix in that order.",
    "findings-table": "The findings table needs at least three rows and exactly three columns.",
    "too-many-findings": "The report has more than five findings.",
    "israel-time": "Every time block needs an Israel-time label in the block or heading.",
    "missing-draft-marker": "Shadow/reviewed artifacts need the run comment and HTML footer marker unless final.",
    "secret": "Remove credential-looking content.",
    "uncited-finding": "Findings must cite existing evidence files.",
    "blocked-single-source": "Blocking claims need two independent sources or an explicit uncertainty qualification.",
    "pdf-length": "The report exceeds four PDF pages.",
    "public-side-effect": "Acknowledge the recorded public side effect.",
}
SOURCE_ALIASES = {
    "direct": r"HTTP\s+\d{3}|probes\.csv|בקשות ישירות|direct requests?",
    "is-agentic": r"is-agentic", "webfetch": r"WebFetch", "tinyfish": r"TinyFish", "jina": r"Jina",
    "validator": r"validator\.schema\.org", "sitemap": r"sitemap|מפת האתר|מפת אתר", "robots": r"robots\.txt",
}
CURRENCY = r"(?:₪|\$|€|£|NIS\b|ILS\b|USD\b|ש[\"״']ח)"
AMOUNT = r"\d[\d,.]*(?:\s*[–-]\s*\d[\d,.]*)?"
MONEY = re.compile(rf"(?:{AMOUNT}\s*{CURRENCY}|{CURRENCY}\s*{AMOUNT})", re.I)
PRICING = re.compile(r"הצעת\s+מחיר|תמחור|עלות\s+השירות|מחירון|\b(?:price\s+lists?|pricing|quotes?|fees?|per\s+hour|hourly\s+rates?|discounts?|retainers?)\b", re.I)
GENERIC_PRICE = re.compile(r"מחיר|\b(?:price|cost)\b", re.I)
ROLE = re.compile(r"ראש\s+צו{1,2}ת(?:\s+פי?תוח)?|מנהל\s+צו{1,2}ת\s+פי?תוח|מנהל\s+פי?תוח|\b(?:dev(?:elopment)?[\s-]+team[\s-]+manager|engineering[\s-]+manager|R&D[\s-]+manager|team[\s-]+lead)\b", re.I)
IMPLEMENTATION = re.compile(r"<script\b|application/ld\+json|openingHoursSpecification|@type\b|Disallow:|User-agent:|\.htaccess\b|wp-config\b|add the following|הוסיפו את הקוד", re.I)
SECRET = re.compile(
    r"\bAKIA[A-Z0-9]{16,}|\b(?:sk_live_|sk-)[A-Za-z0-9_-]{16,}"
    r"|\b(?:ghp_|github_pat_)[A-Za-z0-9_]{16,}|\bxox[abpr]-[A-Za-z0-9-]{16,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*"
    r"|\b(?:api_key|token|password|secret)\s*=\s*[\"']?[^\s\"'<>]{16,}"
    r"|(?<![\w/])[A-Za-z0-9+/]{80,}={0,2}(?![\w/])", re.I
)
URL = re.compile(r"(?:https?:)?//[^\s<>\"'`)\]]+", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
SHORTENERS = {"bit.ly", "t.co", "tinyurl.com", "goo.gl", "ow.ly", "buff.ly", "is.gd", "rebrand.ly", "shorturl.at"}
TRACKERS = ("google-analytics.com", "googletagmanager.com", "doubleclick.net", "mixpanel.com",
            "segment.com", "segment.io", "hotjar.com", "hotjar.io", "analytics.google.com")
BLOCKED = re.compile(r"חסום|נחסם|נחסמת|חסימה|\bblock(?:ed|ing)\b", re.I)
UNCERTAIN = re.compile(r"לא עקבי|אינה עקבית|לא הוכח|לא מוכח|לא הוכחה|not (?:consistent|proven)|inconsistent", re.I)


def normalize_for_matching(text):
    return "".join(char for char in unicodedata.normalize("NFKC", text)
                   if unicodedata.category(char) != "Cf" and not (
                       "\u0591" <= char <= "\u05c7" and unicodedata.category(char) == "Mn"))


def host(url):
    return (urlsplit(normalize_for_matching(unescape(url))).hostname or "").lower().removeprefix("www.")


def tracker(url):
    name = host(url)
    return any(name == domain or name.endswith("." + domain) for domain in TRACKERS) or (
        (name == "facebook.com" or name.endswith(".facebook.com")) and urlsplit(url).path.startswith("/tr"))


def inspect_artifact(file, content, phase, *, final=False, config=None, allowed_hosts=None):
    content = normalize_for_matching(content)
    if config:
        config = {key: normalize_for_matching(value) for key, value in config.items()}
    findings = []

    def add(rule, line, excerpt):
        findings.append((rule, line, excerpt))

    html = HTMLDocument()
    is_html = file.endswith(".html")
    customer = file in {"report.md", "report.html"}
    if is_html:
        html.feed(content)
    text = normalize_for_matching(plain(html.text if is_html else content))
    time_context = bool(re.search(r"כל הבדיקות:[^\n]*שעון ישראל|All (?:checks|times)[^\n]*Israel time", text, re.I))
    blocks = list(re.finditer(r"\S[^\n]*(?:\n(?!\s*\n)[^\n]*)*", text))
    section = ""
    heading_times = False
    prepared = []
    for block in blocks:
        raw = block[0]
        line = text.count("\n", 0, block.start()) + 1
        found_headings = headings(raw)
        if found_headings:
            section = found_headings[-1][2]
            heading_times = bool(re.search(r"שעון ישראל|Israel time", section, re.I))
        numbered = re.match(r"([1-5])\.", section)
        proposal = bool(numbered and numbered[1] in {"4", "5"}) or (
            bool(re.search(r"תקציר מנהלים|Executive summary", section, re.I)) and
            bool(re.search(r"(?:ההצעה|proposal|our offer)\s*:", raw, re.I)))
        pricing = list(PRICING.finditer(raw))
        for match in pricing:
            add("price-in-offer", line, match[0])
        for match in MONEY.finditer(raw):
            add("price-in-offer" if proposal or file == "notes-for-ben.md" else "currency-outside-offer", line, match[0])
        for match in GENERIC_PRICE.finditer(raw):
            if not any(m.start() <= match.start() < m.end() for m in pricing):
                row = raw.count("\n", 0, match.start())
                add("generic-price", line + row, raw.splitlines()[row])
        for match in ROLE.finditer(raw):
            add("ben-role", line, match[0])
        if config:
            if re.search(r"(?<!\w)" + re.escape(config["employer"]) + r"(?!\w)", raw, re.I):
                add("employer-mention", line, config["employer"])
            prepared.extend((line + raw.count("\n", 0, m.start()), m[1].strip()) for m in re.finditer(
                r"^(?:הוכן על ידי|Prepared by)\s*:\s*(.+)$", raw, re.I | re.M))
            for match in EMAIL.finditer(raw):
                contact_line = raw[:match.start()].rsplit("\n", 1)[-1]
                if match[0].casefold() != config["contact_email"].casefold() or not re.search(
                        r"Ben|בן|contact|קשר|Prepared by|הוכן על ידי", contact_line, re.I):
                    add("recipient-email", line, match[0])
        if re.search(r"^\s*(?:(?:status|סטטוס|מצב)\s*:\s*)?(?:sent|נשלח)(?:\s|[.:—-]|$)", raw, re.I | re.M):
            add("sent-status", line, raw)
        if re.search(r"\b\d{1,2}:\d{2}\b", raw) and not time_context and not heading_times and not re.search(r"שעון ישראל|Israel time", raw, re.I):
            add("israel-time", line, raw)
        if customer:
            for match in IMPLEMENTATION.finditer(raw):
                add("implementation-detail", line, match[0])
    if config and customer and (len(prepared) != 1 or prepared[0][1] != config["prepared_by"]):
        add("prepared-by", prepared[0][0] if prepared else 1, prepared[0][1] if prepared else "Missing prepared-by line")
    for number, raw in enumerate(content.splitlines(), 1):
        decoded = normalize_for_matching(unescape(raw))
        for match in SECRET.finditer(decoded):
            add("secret", number, match[0])
        for match in URL.finditer(decoded):
            url = match[0].rstrip(".,;")
            if any(key.lower().startswith("utm_") or key.lower() in {"gclid", "fbclid", "mc_eid", "msclkid"}
                   for key, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)):
                add("tracking-param", number, url)
            if host(url) in SHORTENERS:
                add("link-shortener", number, url)
            elif allowed_hosts is not None and host(url) not in allowed_hosts:
                add("non-evidence-url", number, url)
        if file == "report.md" and (re.match(r" {0,3}(?:`{3,}|~{3,})|(?: {4}|\t)\S", raw) or re.search(r"`+[^`]+`+", raw)):
            add("code-block", number, raw)
    if not is_html:
        html.feed(content)
    if customer:
        if not final and phase in {"shadow", "reviewed"} and (
                not re.search(DRAFT_COMMENT, content) or (is_html and DRAFT_MARKER not in "".join(html.footer))):
            add("missing-draft-marker", 1, file)
        for number, excerpt in html.code:
            add("code-block", number, excerpt)
    for number, tag, attrs, excerpt in html.media:
        attrs = {key: normalize_for_matching(value) for key, value in attrs.items()}
        dimensions = {key: attrs.get(key, "") for key in ("width", "height")}
        for key, value in re.findall(r"(width|height)\s*:\s*([\d.]+(?:px)?)", attrs.get("style", ""), re.I):
            dimensions[key.lower()] = value
        tiny = all(re.fullmatch(r"[01](?:\.0+)?(?:px)?", value.strip(), re.I) for value in dimensions.values())
        if tag == "script" or (tag == "img" and (tiny or re.match(r"(?:https?:)?//", attrs.get("src", ""), re.I))) or tracker(attrs.get("src", "")):
            add("tracking-pixel", number, excerpt)
    for match in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)(?:\{([^}]+)\})?", content):
        tiny = all(re.search(rf"\b{key}\s*[=:]\s*[\"']?[01](?:px)?\b", match[2] or "") for key in ("width", "height"))
        if tiny or re.match(r"(?:https?:)?//", match[1], re.I):
            add("tracking-pixel", content.count("\n", 0, match.start()) + 1, match[0])
    if customer:
        structural = []
        report_structure(text, lambda rule, line, excerpt: structural.append((rule, line, excerpt)))
        if is_html:
            findings = [(rule, html.source_line(line), excerpt) if rule not in {
                "tracking-pixel", "code-block", "tracking-param", "link-shortener", "non-evidence-url", "secret", "missing-draft-marker"
            } else (rule, line, excerpt) for rule, line, excerpt in findings]
        findings.extend((rule, html.source_line(line) if is_html else line, excerpt) for rule, line, excerpt in structural)
    return findings


def report_structure(text, add):
    sequence = []
    for offset, title, body in sections(text):
        number = text.count("\n", 0, offset) + 1
        numbered = re.match(r"([1-5])\.\s+", title)
        if re.search(r"תקציר מנהלים|Executive summary", title, re.I):
            sequence.append("summary")
        elif re.search(r"נספח|Appendix", title, re.I):
            sequence.append("appendix")
            table = tables(body)
            if not table or len(table[0][1]) < 3:
                add("missing-appendix", number, title)
        elif numbered:
            section = numbered[1]
            sequence.append(section)
            if section == "2":
                table = tables(body)
                if not table or len(table[0][0]) != 3 or len(table[0][1]) < 3 or any(len(row[1]) != 3 for row in table[0][1]):
                    add("findings-table", number, title)
                if table and len(table[0][1]) > 5:
                    add("too-many-findings", number, str(len(table[0][1])))
                if table:
                    for row_line, _, row in table[0][1]:
                        sources = sum(bool(re.search(alias, row, re.I)) for alias in SOURCE_ALIASES.values())
                        if sources < 2 and BLOCKED.search(row) and not UNCERTAIN.search(row):
                            add("blocked-single-source", number + row_line - 1, row)
            if section == "4":
                items = list(re.finditer(r"^ {0,3}(?:\d+[.)]|[-+*])\s+(.+(?:\n(?!\s*\n| {0,3}(?:\d+[.)]|[-+*])\s)[^\n]+)*)", body, re.M))
                if len(items) > 4:
                    add("extra-offer-steps", number + body.count("\n", 0, items[4].start()), items[4][0])
                for item in items:
                    nested = re.search(r"^\s{2,}\d+[.)]\s", item[1], re.M)
                    instructions = re.search(r"step[ -]by[ -]step|שלב אחר שלב|\b(?:first|then|next|finally)\s*[:,]|(?:ראשית|לאחר מכן|לבסוף)[,:]?", item[1], re.I)
                    if len(item[1].split()) > 40 or nested or instructions:
                        add("detailed-offer-step", number + body.count("\n", 0, item.start()), item[0])
    if sequence != ["summary", "1", "2", "3", "4", "5", "appendix"]:
        add("required-headings", 1, " > ".join(sequence))
    if "appendix" not in sequence:
        add("missing-appendix", 1, "Missing appendix")
