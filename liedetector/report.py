"""The Truth Report: a static HTML view rendered entirely from the receipt.

The report is a derived artifact.  It is rendered from the canonical
``verification_receipt.json`` plus the stored execution logs, and embeds the
receipt hash in its footer.  The receipt never references the report.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

_VERDICT_COLORS = {
    "PROVEN": "var(--proven)",
    "FALSE": "var(--false)",
    "INCONCLUSIVE": "var(--inconclusive)",
    "UNTESTABLE": "var(--untestable)",
}

_CSS = """
:root {
  --bg: #0b0e14;
  --panel: #131824;
  --panel-edge: #1f2736;
  --ink: #e6e9f0;
  --ink-dim: #97a0b3;
  --accent: #7aa2f7;
  --proven: #4ade80;
  --false: #f87171;
  --inconclusive: #fbbf24;
  --untestable: #94a3b8;
  --mono: "SFMono-Regular", "JetBrains Mono", Consolas, "Liberation Mono", monospace;
  --serif: Georgia, "Times New Roman", serif;
  --sans: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: var(--sans); line-height: 1.55;
}
main { max-width: 960px; margin: 0 auto; padding: 3rem 1.5rem 5rem; }
header.masthead { border-bottom: 1px solid var(--panel-edge); padding-bottom: 2rem; }
h1 {
  font-family: var(--serif); font-size: 2.6rem; margin: 0 0 .25rem;
  letter-spacing: -.02em;
}
h1 .tagline { display:block; font-size: 1rem; color: var(--ink-dim); font-family: var(--sans); }
.meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: .6rem 2rem; margin-top: 1.5rem; }
.meta div { font-size: .85rem; color: var(--ink-dim); }
.meta code { font-family: var(--mono); color: var(--ink); font-size: .8rem;
             word-break: break-all; }
.tally { display: flex; gap: .75rem; margin: 1.6rem 0 0; flex-wrap: wrap; }
.tally .pill {
  border: 1px solid var(--panel-edge); border-radius: 999px;
  padding: .35rem .9rem; font-size: .85rem; background: var(--panel);
}
.tally .pill b { font-family: var(--mono); }
section.claim {
  background: var(--panel); border: 1px solid var(--panel-edge);
  border-radius: 12px; margin-top: 2rem; padding: 1.5rem 1.75rem;
}
.claim .verdict-row { display: flex; align-items: baseline; gap: 1rem; flex-wrap: wrap; }
.verdict {
  font-family: var(--mono); font-weight: 700; font-size: .95rem;
  letter-spacing: .08em; padding: .15rem .7rem; border-radius: 6px;
  border: 1px solid currentColor;
}
.claim .loc { color: var(--ink-dim); font-size: .8rem; font-family: var(--mono); }
blockquote.quote {
  font-family: var(--serif); font-size: 1.15rem; font-style: italic;
  border-left: 3px solid var(--accent); margin: 1rem 0; padding: .2rem 0 .2rem 1rem;
}
.label { text-transform: uppercase; letter-spacing: .14em; font-size: .68rem;
         color: var(--ink-dim); margin: 1.1rem 0 .3rem; }
.hypothesis { color: var(--ink); }
pre {
  background: #0a0d13; border: 1px solid var(--panel-edge); border-radius: 8px;
  padding: .9rem 1rem; overflow-x: auto; font-family: var(--mono);
  font-size: .78rem; line-height: 1.5; color: #c9d3e8;
}
details > summary { cursor: pointer; color: var(--accent); font-size: .85rem; }
.hashes { font-family: var(--mono); font-size: .72rem; color: var(--ink-dim);
          word-break: break-all; }
.failcat { font-family: var(--mono); color: var(--inconclusive); font-size: .85rem; }
footer { margin-top: 4rem; border-top: 1px solid var(--panel-edge);
         padding-top: 1.5rem; color: var(--ink-dim); font-size: .8rem; }
footer code { font-family: var(--mono); word-break: break-all; color: var(--ink); }
"""


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _read_rel(base: Path, rel: str | None) -> str:
    if not rel:
        return ""
    path = base / rel
    if not path.is_file():
        return f"(missing artifact: {rel})"
    return path.read_text(encoding="utf-8", errors="replace")


def _claim_card(entry: dict[str, Any], base: Path, repo_url: str) -> str:
    verdict = str(entry["verdict"])
    color = _VERDICT_COLORS.get(verdict, "var(--untestable)")
    parts: list[str] = [
        '<section class="claim">',
        '<div class="verdict-row">',
        f'<span class="verdict" style="color:{color}">{_esc(verdict)}</span>',
        f'<span class="loc">{_esc(entry["source"]["file"])}:{_esc(entry["source"]["line"])}'
        f' &middot; {_esc(entry["id"])} &middot; {_esc(entry["claim_type"])}</span>',
        "</div>",
        f'<blockquote class="quote">&ldquo;{_esc(entry["source"]["quote"])}&rdquo;</blockquote>',
        '<p class="label">Interpreted hypothesis</p>',
        f'<p class="hypothesis">{_esc(entry["hypothesis"])}</p>',
        '<p class="label">Interpretation notes</p>',
        f'<p class="hypothesis">{_esc(entry["interpretation_notes"])}</p>',
        f'<p class="hashes">verdict confidence: {_esc(entry["verdict_confidence"])}'
        f' &middot; {_esc(entry["rationale"])}</p>',
    ]
    if entry.get("failure_category"):
        parts.append(
            f'<p class="failcat">failure category: {_esc(entry["failure_category"])}</p>'
        )
    if entry.get("suggested_strategy"):
        parts.append('<p class="label">Suggested verification strategy</p>')
        parts.append(f'<p class="hypothesis">{_esc(entry["suggested_strategy"])}</p>')
    if entry.get("harness_error"):
        parts.append('<p class="label">Harness generation error</p>')
        parts.append(f"<pre>{_esc(entry['harness_error'])}</pre>")
    if entry.get("harness_path"):
        harness = _read_rel(base, entry["harness_path"])
        parts.append("<details><summary>Generated harness</summary>")
        parts.append(f"<pre>{_esc(harness)}</pre></details>")
    for execution in entry.get("executions", []):
        log_text = _read_rel(base, execution["log_path"])
        parts.append(
            f"<details><summary>Execution log &mdash; run {_esc(execution['run_index'])}"
            f" (exit {_esc(execution['exit_code'])})</summary>"
        )
        parts.append(f"<pre>{_esc(log_text)}</pre></details>")
    parts.append('<p class="label">Reproduce</p>')
    parts.append(f"<pre>liedetector run {_esc(repo_url)}</pre>")
    hashes = [f"claim {entry['claim_sha256']}"]
    if entry.get("harness_sha256"):
        hashes.append(f"harness {entry['harness_sha256']}")
    for execution in entry.get("executions", []):
        hashes.append(f"run{execution['run_index']} log {execution['log_sha256']}")
    parts.append('<p class="label">Evidence hashes (SHA-256)</p>')
    parts.append('<p class="hashes">' + "<br>".join(_esc(h) for h in hashes) + "</p>")
    parts.append("</section>")
    return "\n".join(parts)


def render_report(
    receipt: dict[str, Any],
    receipt_hash: str,
    bundle_dir: Path,
    duration_seconds: float | None = None,
) -> str:
    """Render the Truth Report HTML from the receipt and stored logs.

    ``duration_seconds`` comes from the run log, never from the receipt (the
    receipt carries a single recorded timestamp and no other wall-clock data).
    """
    tally = receipt["verdict_tally"]
    duration = f"{duration_seconds:.1f}s" if duration_seconds is not None else "n/a"
    tally_html = "".join(
        f'<span class="pill" style="color:{_VERDICT_COLORS[v]}"><b>{tally.get(v, 0)}</b>'
        f" {v}</span>"
        for v in ("PROVEN", "FALSE", "INCONCLUSIVE", "UNTESTABLE")
    )
    cards = "\n".join(
        _claim_card(entry, bundle_dir, str(receipt["repo_url"]))
        for entry in receipt["claims"]
    )
    prompt_versions = ", ".join(
        f"{k}={v}" for k, v in sorted(receipt["prompt_versions"].items())
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Truth Report &mdash; {_esc(receipt["repo_url"])}</title>
<style>{_CSS}</style>
</head>
<body>
<main>
<header class="masthead">
  <h1>Truth Report
    <span class="tagline">The Lie Detector &mdash; turn READMEs into tests.</span></h1>
  <div class="meta">
    <div>Repository<br><code>{_esc(receipt["repo_url"])}</code></div>
    <div>Commit<br><code>{_esc(receipt["commit_sha"])}</code></div>
    <div>Timestamp (UTC)<br><code>{_esc(receipt["timestamp_utc"])}</code></div>
    <div>Runtime duration<br><code>{_esc(duration)}</code></div>
    <div>Environment fingerprint<br><code>{_esc(receipt["environment_fingerprint"])}</code></div>
    <div>Sandbox image<br><code>{_esc(receipt["docker_image_digest"])}</code></div>
    <div>Prompt versions<br><code>{_esc(prompt_versions)}</code></div>
    <div>Receipt hash<br><code>{_esc(receipt_hash)}</code></div>
  </div>
  <div class="tally">{tally_html}</div>
</header>
{cards}
<footer>
  Rendered from <code>verification_receipt.json</code> &mdash; the receipt is the
  root of trust; this report is a derived view.<br>
  Receipt SHA-256: <code>{_esc(receipt_hash)}</code><br>
  liedetector {_esc(receipt["tool_version"])} &middot; receipt v{_esc(receipt["receipt_version"])}
  &middot; schema v{_esc(receipt["schema_version"])}
</footer>
</main>
</body>
</html>
"""
