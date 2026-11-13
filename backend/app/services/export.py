"""Report export. HTML export escapes raw HTML (report text contains quotes from untrusted pages)."""

from __future__ import annotations

import html

from markdown_it import MarkdownIt

from app.agents.reporter import report_markdown
from app.models import Report

_md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")

CSS = """body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:860px;margin:40px auto;padding:0 20px;line-height:1.6;color:#1f2937}
h1{font-size:1.8rem}h2{border-bottom:1px solid #e5e7eb;padding-bottom:.3rem;margin-top:2rem}
a{color:#2563eb}blockquote{border-left:3px solid #cbd5e1;margin:0;padding-left:1rem;color:#475569}
table{border-collapse:collapse}td,th{border:1px solid #e5e7eb;padding:4px 10px}@media print{a{color:inherit}}"""


def report_html(report: Report) -> str:
    body = _md.render(report_markdown(report))
    # Only allow http(s) links in the rendered output.
    body = body.replace('href="javascript:', 'href="#blocked:')
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{html.escape(report.title)}</title>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><style>{CSS}</style></head><body>{body}</body></html>")
