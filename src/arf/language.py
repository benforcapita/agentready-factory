"""Pick the report language (he or en) from the site's declared language (LLD §3)."""

from __future__ import annotations

import re
from typing import Literal

Language = Literal["he", "en"]

_HTML_TAG = re.compile(r"<html\b[^>]*>", re.IGNORECASE)
_LANG_ATTR = re.compile(r"""\s(?:xml:)?lang\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", re.IGNORECASE)
_TAG = re.compile(r"[A-Za-z]{1,8}(?:[-_][A-Za-z0-9]{1,8})*")
_SUPPORTED = {"he": "he", "iw": "he", "en": "en"}


def html_lang(html: str | None) -> str | None:
    """The `lang` attribute of the document's `<html>` tag, if it holds a language tag."""
    tag = _HTML_TAG.search(html or "")
    match = _LANG_ATTR.search(tag.group(0)) if tag else None
    value = next((group for group in match.groups() if group is not None), "").strip() if match else ""
    return value if _TAG.fullmatch(value) else None


def _header_lang(content_language: str | None) -> str | None:
    first = (content_language or "").split(",")[0].strip()
    return first if _TAG.fullmatch(first) else None


def _from_declared(declared: str, source: str) -> tuple[Language, str]:
    primary = re.split(r"[-_]", declared)[0].lower()
    if primary in _SUPPORTED:
        return _SUPPORTED[primary], f"{source}={declared}"
    return "en", f"unsupported:{primary}"


def detect(html: str | None, content_language: str | None, host: str) -> tuple[Language, str, str | None]:
    """Return (language, reason, declared tag).

    Precedence: `<html lang>`, then the Content-Language header, then an Israeli host TLD, then en.
    A declared language other than he/en falls back to en with reason `unsupported:<code>`.
    """
    return detect_declared(html_lang(html), content_language, host)


def detect_declared(
    lang: str | None, content_language: str | None, host: str
) -> tuple[Language, str, str | None]:
    """`detect` for an already extracted `<html lang>` value (e.g. collect/site.json `html_lang`)."""
    lang = (lang or "").strip()
    lang = lang if _TAG.fullmatch(lang) else None
    for declared, source in ((lang, "html lang"), (_header_lang(content_language), "content-language")):
        if declared:
            language, reason = _from_declared(declared, source)
            return language, reason, declared
    name = (host or "").strip().rstrip(".").lower()
    if name == "il" or name.endswith(".il"):
        return "he", f"host {name} (.il)", None
    return "en", "default (no declared language)", None


__all__ = ["Language", "detect", "detect_declared", "html_lang"]
