"""Regenerate seeded drafts and expected results: python fixtures/lint/regenerate.py."""

import json
from pathlib import Path

from arf.lint import lint, serialize
from arf.lint.markers import DRAFT_MARKER_EN

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "fixtures/lint"
GOLDEN = ROOT / "fixtures/golden/lehamim"


def generate():
    original = (GOLDEN / "report.md").read_text(encoding="utf-8")
    draft = DRAFT_MARKER_EN + "\n\n" + original
    result = lint(GOLDEN, "reviewed")
    reasons = {
        "placeholder-left": "Sanitized golden fixture deliberately retains contact and meeting placeholders; fill before sending.",
        "uncited-finding": "Golden narrative finding retained verbatim; supporting structured-data observations appear in the appendix and private notes.",
    }
    acks = {"version": 1, "acks": [
        {**{key: f[key] for key in ("rule", "file", "fingerprint")}, "reason": reasons[f["rule"]]}
        for f in result["findings"] if f["level"] == "warning"
    ]}
    acks["acks"] = list({(a["rule"], a["file"], a["fingerprint"]): a for a in acks["acks"]}.values())
    ack_text = json.dumps(acks, ensure_ascii=False, indent=2) + "\n"
    (BASE / "lehamim").mkdir(parents=True, exist_ok=True)
    ack_path = BASE / "lehamim/lint-ack.json"
    ack_path.write_text(ack_text, encoding="utf-8")
    expected = BASE / "expected"
    expected.mkdir(exist_ok=True)
    (expected / "lehamim.json").write_text(serialize(lint(GOLDEN, "reviewed", ack_path)), encoding="utf-8")
    mutations = [
        ("price-in-offer", draft.replace("## 5. הצעד הבא", "## 5. הצעד הבא\n\nPrice: 50 USD")),
        ("code-block", draft + "\n```python\nprint('example')\n```\n"),
        ("ben-role", draft + "\nבן הוא ראש צוות.\n"),
        ("ben-role", draft + "\nBen is a dev team manager.\n"),
        ("tracking-pixel", draft + '\n<img src="https://example.com/p.gif" width="1" height="1">\n'),
        ("tracking-param", draft + "\n[Example](https://example.com/?utm_source=example)\n"),
        ("missing-appendix", draft.replace("## נספח", "## מקורות")),
        ("missing-draft-marker", original),
        ("secret", draft + "\nAKIAEXAMPLE0000000000\n"),
        ("uncited-finding", draft.replace("## 3.", "| 6 | New observation | Needs evidence |\n\n## 3.", 1)),
        ("blocked-single-source", draft.replace("## 3.", "| 6 | blocked: HTTP 403 | Cannot read |\n\n## 3.", 1)),
        ("extra-offer-steps", draft.replace("## 5.", "5. Another step.\n\n## 5.", 1)),
    ]
    for index, (rule, content) in enumerate(mutations, 1):
        name = f"{index:02}-{rule}"
        directory = BASE / "seeded" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "report.md").write_text(content, encoding="utf-8")
        (directory / "lint-ack.json").write_text(ack_text, encoding="utf-8")
        (expected / f"{name}.json").write_text(serialize(lint(directory)), encoding="utf-8")


if __name__ == "__main__":
    generate()
