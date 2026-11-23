"""Run a bounded regression suite against the real arf lint CLI."""

import argparse
import json
import random
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from pathlib import Path


SEEDS = Path(__file__).resolve().parents[2] / "fixtures/lint/seeded"
KNOWN_SURVIVOR = "offer-amount/mixed-language-title"


@dataclass(frozen=True)
class Probe:
    name: str
    fixture: str
    anchor: str
    copy: str
    rule: str
    family: str = "copy"


PROBES = (
    Probe("offer-amount", "01-price-in-offer", "50 USD", "50 USD", "price-in-offer"),
    Probe("pricing-phrase", "01-price-in-offer", "50 USD", "price list", "price-in-offer"),
    Probe("role-he", "03-ben-role", "בן הוא ראש צוות.", "בן הוא ראש צוות", "ben-role"),
    Probe("role-en", "04-ben-role", "Ben is a dev team manager.", "Ben is a dev team manager", "ben-role"),
    Probe("tracking-param", "06-tracking-param", "[Example](https://example.com/?utm_source=example)",
          "[Example](https://example.com/?utm_source=example)", "tracking-param", "url"),
    Probe("tracking-pixel", "05-tracking-pixel", '<img src="https://example.com/p.gif" width="1" height="1">',
          '<img src="https://example.com/p.gif" width="1" height="1">', "tracking-pixel", "media"),
)


def url_variants(copy, rng):
    yield "baseline", copy
    key = rng.choice(("UTM_SOURCE", "Utm_Campaign", "utm_medium"))
    yield "key-case", f"[Example](https://example.com/?ref=1&{key}=example)"
    click = rng.choice(("gclid", "fbclid", "msclkid", "mc_eid"))
    yield "click-id", f"[Example](https://example.com/page?{click}=abc123)"
    yield "html-link", f'<a href="https://example.com/?{rng.choice(("utm_source", "gclid"))}=example">Example</a>'
    yield "autolink", "<https://example.com/?utm_campaign=example>"


def media_variants(copy, rng):
    yield "baseline", copy
    yield "tag-case", '<IMG SRC="https://example.com/p.gif">'
    yield "protocol-relative", f'<img src="//example.com/{rng.choice(("p.gif", "t.png", "1x1.gif"))}" alt="">'
    yield "markdown-image", f"![{rng.choice(('', 'logo'))}](https://example.com/p.gif)"
    unit = rng.choice(("1px", "0px", "1"))
    yield "style-tiny", f'<img src="p.gif" style="width:{unit};height:{unit}">'


def variants(probe, rng):
    copy = probe.copy
    if probe.family == "url":
        yield from url_variants(copy, rng)
        return
    if probe.family == "media":
        yield from media_variants(copy, rng)
        return
    yield "baseline", copy
    yield "unicode-space", copy.replace(" ", rng.choice(("\u00a0", "\u2009", "\u202f")))
    if probe.name == "offer-amount":
        left, right = rng.choice((("(", ")"), ("“", "”"), ("«", "»")))
        yield "punctuation", left + copy + right
    else:
        yield "punctuation", copy.replace(" ", rng.choice(("-", "—", "·")))
    emphasis = rng.choice(("*", "**"))
    yield "markdown", f"{emphasis}{copy}{emphasis}"
    tag = rng.choice(("span", "abbr"))
    yield "rendered-title", f'<{tag} title="{copy}">פרטים</{tag}>'
    if probe.name == "offer-amount":
        yield "mixed-language-title", f'<span title="{copy}">More details</span>'


def planned_cases(seed):
    rng = random.Random(seed)
    controls = set()
    for probe in PROBES:
        source = SEEDS / probe.fixture
        draft = (source / "report.md").read_text(encoding="utf-8")
        anchor = f"\n{probe.anchor}\n"
        if draft.count(anchor) != 1:
            raise ValueError(f"{probe.fixture}: expected one intact anchor line")
        cases = list(variants(probe, rng))
        if (source, anchor) not in controls:
            cases.insert(0, ("control", "More details."))
            controls.add((source, anchor))
        if len({copy for _, copy in cases}) != len(cases):
            raise ValueError(f"{probe.name}: mutation made no change")
        for kind, copy in cases:
            yield (
                dict(
                    id=f"{probe.name}/{kind}",
                    fixture=probe.fixture,
                    replacement=copy,
                    expected_rule=None if kind == "control" else probe.rule,
                    require_rendered=kind in {"rendered-title", "mixed-language-title"},
                ),
                draft.replace(anchor, f"\n{copy}\n"),
                source / "lint-ack.json",
            )


def run_case(case, draft, ack, directory, deadline):
    directory.mkdir()
    (directory / "report.md").write_text(draft, encoding="utf-8")
    output = directory / "lint.json"
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired("arf lint", 0)
    process = subprocess.run(
        [sys.executable, "-m", "arf.lint", str(directory), "--ack", str(ack), "--out", str(output)],
        capture_output=True, encoding="utf-8", timeout=remaining,
    )
    if process.returncode not in (0, 1):
        print(process.stderr.rstrip(), file=sys.stderr)
        return dict(case, status="error", exit_code=process.returncode)
    try:
        lint = json.loads(output.read_text(encoding="utf-8"))
        hard = sorted({(f["rule"], f["file"]) for f in lint["findings"] if f["level"] == "hard_fail"})
        if lint["passed"] is not (process.returncode == 0) or (lint["passed"] and hard):
            raise ValueError("lint exit status and report disagree")
    except (OSError, TypeError, ValueError, KeyError) as error:
        print(f'{case["id"]}: invalid lint report: {error}', file=sys.stderr)
        return dict(case, status="error", exit_code=process.returncode)
    rule = case["expected_rule"]
    if rule is None:
        status = "control-passed" if process.returncode == 0 else "control-failed"
    else:
        caught = any(r == rule and (not case["require_rendered"] or f.startswith("rendered:")) for r, f in hard)
        status = "caught" if process.returncode == 1 and caught else "survived"
        if case["id"] == KNOWN_SURVIVOR and process.returncode == 0 and not hard:
            status = "known-survivor"
    return dict(case, status=status, exit_code=process.returncode, hard_findings=hard)


def summarize(args, cases, results, started):
    return dict(
        seed=args.seed,
        budget_seconds=args.budget_seconds,
        elapsed_seconds=round(time.monotonic() - started, 3),
        total_cases=len(cases),
        caught=sum(r["status"] == "caught" for r in results),
        new_survivors=[r["id"] for r in results if r["status"] == "survived"],
        known_survivors=[r["id"] for r in results if r["status"] == "known-survivor"],
        errors=[r["id"] for r in results if r["status"] in {"error", "control-failed", "timed-out"}],
        unrun=[case["id"] for case, _, _ in cases[len(results):]],
        cases=results,
    )


def main():
    raise SystemExit("PRO-651 CI crash simulation: exit before any JSON (revert me)")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=567)
    parser.add_argument("--budget-seconds", type=int, default=90)
    args = parser.parse_args()
    if args.budget_seconds < 1:
        parser.error("--budget-seconds must be positive")
    started = time.monotonic()
    deadline = started + args.budget_seconds
    cases, results = [], []
    try:
        cases = list(planned_cases(args.seed))
        with tempfile.TemporaryDirectory(prefix="arf-lint-mutation-") as temp:
            for index, (case, draft, ack) in enumerate(cases):
                try:
                    result = run_case(case, draft, ack, Path(temp) / str(index), deadline)
                except subprocess.TimeoutExpired:
                    results.append(dict(case, status="timed-out"))
                    break
                results.append(result)
    except Exception as error:
        traceback.print_exc()
        report = summarize(args, cases, results, started)
        report["errors"].append("crash")
        report["crash"] = f"{type(error).__name__}: {error}"
        print(json.dumps(report, indent=2))
        return 2

    report = summarize(args, cases, results, started)
    print(json.dumps(report, indent=2))
    return int(bool(report["new_survivors"] or report["errors"] or report["unrun"]))


if __name__ == "__main__":
    raise SystemExit(main())
