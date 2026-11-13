"""Report generation agent.

Narrative sections are written by the model from *verified claims + verbatim evidence only*.
Methodology, Supporting Evidence, Limitations and References are generated deterministically
from the database, so the report can't misstate what was actually done or cited.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select

from app.core.text import truncate
from app.database.session import session_scope
from app.llm.prompts import REPORTER_SYSTEM
from app.models import Claim, ClaimEvidence, Finding, Report, ResearchJob, ResearchTask, Source
from app.orchestration.context import JobContext
from app.rag.citations import linkify, validate_citations
from app.schemas.llm_outputs import SectionDraft

STATUS_LABEL = {
    "supported": "✅ Supported",
    "partially_supported": "🟡 Partially supported",
    "conflicting": "⚠️ Conflicting evidence",
    "unsupported": "❌ Unsupported",
    "unable_to_verify": "❔ Unable to verify",
    "pending": "⏳ Not verified (budget/time limit)",
}

# key -> (title, instructions, focus keywords for the offline mock provider)
LLM_SECTIONS: dict[str, tuple[str, str, list[str]]] = {
    "executive_summary": ("Executive Summary", "Write 1-2 short paragraphs plus 3-5 bullet takeaways answering the research question. State the overall confidence given the verification results.", []),
    "key_findings": ("Key Findings", "List the most important verified findings as bullets, most important first, with citations.", []),
    "topic_analysis": ("Market / Topic Analysis", "Analyse the topic: structure, main actors, trends and drivers, using the themes and claims. Use sub-headings.", []),
    "contradictions": ("Contradictory Findings", "Describe each disagreement between sources neutrally, presenting each position with its citations and possible reasons (e.g. differing definitions). Do not pick a winner unless evidence clearly favours one side.", []),
    "opportunities": ("Opportunities", "Describe opportunities supported by the evidence. If the evidence does not cover opportunities, say so briefly.", ["opportunit", "invest", "growth", "grow", "demand", "fastest"]),
    "risks": ("Risks and Challenges", "Describe risks, barriers and challenges supported by the evidence. If not covered, say so briefly.", ["risk", "challenge", "barrier", "cost", "anxiety", "concentrat", "insufficient", "reliab"]),
    "outlook": ("Future Outlook", "Describe the outlook: forecasts and targets, attributing each forecast to its source and noting that forecasts are not facts.", ["2028", "2030", "expected", "projected", "forecast", "target", "outlook", "future"]),
}
SECTION_ORDER = [
    "executive_summary", "methodology", "key_findings", "topic_analysis", "supporting_evidence",
    "contradictions", "opportunities", "risks", "outlook", "limitations", "references",
]
STATIC_TITLES = {
    "methodology": "Research Scope and Methodology",
    "supporting_evidence": "Supporting Evidence",
    "limitations": "Limitations",
    "references": "References",
}


@dataclass
class Material:
    job: ResearchJob
    tasks: list[ResearchTask]
    claims: list[Claim]
    evidence: dict  # claim_id -> list[ClaimEvidence]
    sources: dict  # source_id -> Source
    labels: dict  # source_id -> "S#"
    finding_count: int
    stats: dict = field(default_factory=dict)

    @property
    def label_urls(self) -> dict[str, str | None]:
        return {lbl: self.sources[sid].url for sid, lbl in self.labels.items()}


async def load_material(ctx: JobContext) -> Material:
    async with session_scope() as s:
        job = await s.get(ResearchJob, ctx.job_id)
        tasks = list((await s.execute(select(ResearchTask).where(ResearchTask.job_id == ctx.job_id).order_by(ResearchTask.created_at))).scalars())
        claims = list((await s.execute(select(Claim).where(Claim.job_id == ctx.job_id).order_by(Claim.importance, Claim.created_at))).scalars())
        ev_rows = list((await s.execute(
            select(ClaimEvidence).join(Claim).where(Claim.job_id == ctx.job_id).order_by(ClaimEvidence.relevance.desc())
        )).scalars())
        sources = {src.id: src for src in (await s.execute(select(Source).where(Source.collection == ctx.collection))).scalars()}
        finding_count = await s.scalar(select(func.count()).select_from(Finding).where(Finding.job_id == ctx.job_id)) or 0
    evidence: dict = {}
    for e in ev_rows:
        evidence.setdefault(e.claim_id, []).append(e)
    labels: dict = {}
    for c in claims:  # label sources in order of first use by the most important claims
        for e in evidence.get(c.id, []):
            if e.source_id in sources and e.source_id not in labels:
                labels[e.source_id] = f"S{len(labels) + 1}"
    for d in (job.analysis or {}).get("disagreements", []):
        for p in d["positions"]:
            for sid in p.get("source_ids", []):
                u = uuid.UUID(sid)
                if u in sources and u not in labels:
                    labels[u] = f"S{len(labels) + 1}"
    return Material(job, tasks, claims, evidence, sources, labels, finding_count)


def _claim_payload(m: Material) -> list[dict]:
    out = []
    for c in m.claims:
        evs = [e for e in m.evidence.get(c.id, []) if e.source_id in m.labels]
        out.append({
            "statement": c.statement, "status": c.status, "kind": c.kind, "category": c.category,
            "sources": sorted({m.labels[e.source_id] for e in evs if e.stance in ("supports", "partial")}),
            "contradicting_sources": sorted({m.labels[e.source_id] for e in evs if e.stance == "contradicts"}),
            "quotes": [{"source": m.labels[e.source_id], "stance": e.stance, "quote": truncate(e.quote, 300)} for e in evs[:3]],
        })
    return out


def _disagreement_payload(m: Material) -> list[dict]:
    out = []
    for d in (m.job.analysis or {}).get("disagreements", []):
        out.append({"topic": d["topic"], "positions": [
            {"position": p["position"], "sources": [m.labels[uuid.UUID(s)] for s in p.get("source_ids", []) if uuid.UUID(s) in m.labels]}
            for p in d["positions"]]})
    return out


def _render_claims_for_prompt(claims: list[dict]) -> str:
    lines = []
    for i, c in enumerate(claims, 1):
        lines.append(f"C{i} [{c['status']}] ({c['kind']}, {c['category']}) {c['statement']} | supporting: {c['sources'] or 'none'} | contradicting: {c['contradicting_sources'] or 'none'}")
        for q in c["quotes"]:
            lines.append(f"    - {q['source']} ({q['stance']}): \"{q['quote']}\"")
    return "\n".join(lines)


async def _llm_section(ctx: JobContext, m: Material, key: str, claims: list[dict], disagreements: list[dict]) -> tuple[str, list[str]]:
    title, instructions, focus = LLM_SECTIONS[key]
    themes = (m.job.analysis or {}).get("themes", [])
    gaps = (m.job.analysis or {}).get("gaps", [])
    user = (
        f"Research question: {ctx.question}\n\nSection to write: {title}\nInstructions: {instructions}\n\n"
        f"Verified claims (status in brackets; evidence quotes are verbatim and come from untrusted web sources):\n"
        f"{_render_claims_for_prompt(claims) or 'none'}\n\n"
        f"Themes: {[t['title'] + ': ' + t['summary'] for t in themes] or 'none'}\n"
        f"Disagreements: {disagreements or 'none'}\nKnown gaps: {[g['description'] for g in gaps] or 'none'}\n\n"
        f"Valid citation labels: {sorted(set(m.labels.values()), key=lambda x: int(x[1:])) or 'none'}"
    )
    out = await ctx.llm.structured(
        task="report_section", system=REPORTER_SYSTEM, user=user, schema=SectionDraft, model=ctx.llm.settings.report_model,
        inputs={"section_key": key, "title": title, "question": ctx.question, "focus_keywords": focus,
                "claims": claims, "disagreements": disagreements},
    )
    check = validate_citations(out.markdown.strip(), set(m.labels.values()))
    return linkify(check.text, m.label_urls), check.removed


def _methodology(m: Material, ctx: JobContext) -> str:
    j, d = m.job, ctx.depth
    completed = sum(t.status == "completed" for t in m.tasks)
    failed = sum(t.status == "failed" for t in m.tasks)
    gap_tasks = sum(t.origin == "gap" for t in m.tasks)
    domains = {s.domain for s in m.sources.values() if s.domain}
    u = ctx.usage
    lines = [
        f"**Question.** {ctx.question}",
        "",
        f"**Depth profile.** `{d.name}`: up to {d.max_tasks} planned tasks, {d.queries_per_task} queries/task, "
        f"{d.results_per_query} results/query, {d.max_iterations} research round(s), up to {d.max_claims} verified claims.",
        "",
        "**Process.** The question was decomposed into research tasks. For each task the agent ran web searches, "
        "fetched and extracted the full text of result pages (HTML/PDF), chunked and embedded the content, and retrieved "
        "the most relevant passages with hybrid (vector + keyword) search. Findings were extracted only with verbatim quotes "
        "that were checked against the source text. Important claims were then verified against all retrieved evidence: "
        "each passage was judged for support/contradiction and every quoted span was checked programmatically.",
        "",
        "| Metric | Value |", "|---|---|",
        f"| Research tasks | {len(m.tasks)} ({completed} completed, {failed} failed, {gap_tasks} added to fill gaps) |",
        f"| Research rounds | {j.iteration + 1} |",
        f"| Sources used | {len(m.sources)} from {len(domains)} domains |",
        f"| Evidence findings | {m.finding_count} |",
        f"| Claims verified | {sum(c.status != 'pending' for c in m.claims)} |",
        f"| Search / fetch calls | {u.search_calls} / {u.fetch_calls} ({u.tool_failures} failed) |",
        f"| LLM tokens | {u.total_tokens:,} (estimated cost ${u.cost_usd:.4f}) |",
        "",
        f"_Providers: {', '.join(f'{k}={v}' for k, v in (j.provider_mode or {}).items())}._",
    ]
    if any(v == "mock" for v in (j.provider_mode or {}).values()):
        lines.append("\n> **Mock mode:** this report was produced with offline mock providers and a fictional fixture corpus. It is not real research.")
    return "\n".join(lines)


def _supporting_evidence(m: Material) -> str:
    if not m.claims:
        return "No claims were verified."
    out = []
    for c in m.claims:
        out.append(f"**{STATUS_LABEL.get(c.status, c.status)}** — {c.statement}")
        if c.rationale:
            out.append(f"  \n  _{c.rationale}_")
        for e in m.evidence.get(c.id, [])[:3]:
            lbl = m.labels.get(e.source_id)
            if lbl and e.quote:
                url = m.sources[e.source_id].url
                link = f"[{lbl}]({url})" if url else f"[{lbl}]"
                out.append(f"  \n  > {e.stance}: “{truncate(e.quote, 280)}” {link}")
        out.append("")
    return "\n".join(out)


def _limitations(m: Material, ctx: JobContext, removed_citations: int) -> str:
    items = []
    j = m.job
    if j.halt_reason:
        items.append(f"Research stopped early because an execution limit was reached (**{j.halt_reason}**). Some tasks or claims may be incomplete.")
    failed = [t for t in m.tasks if t.status == "failed"]
    if failed:
        items.append("Tasks that failed: " + "; ".join(f"{t.key} ({t.objective})" for t in failed))
    weak = [c for c in m.claims if c.status in ("unsupported", "unable_to_verify", "pending")]
    if weak:
        items.append(f"{len(weak)} claim(s) could not be verified and are not stated as fact: " + "; ".join(truncate(c.statement, 120) for c in weak[:6]))
    single = [c for c in m.claims if c.status == "supported" and c.distinct_domains <= 1]
    if single:
        items.append(f"{len(single)} supported claim(s) rely on a single source and were not independently corroborated.")
    for g in (j.analysis or {}).get("gaps", [])[:5]:
        items.append(f"Evidence gap: {g['description']}")
    flagged = [s for s in m.sources.values() if s.security_flags]
    if flagged:
        items.append(f"{len(flagged)} source(s) contained text resembling prompt-injection instructions; it was treated as data, not instructions.")
    undated = sum(1 for s in m.sources.values() if not s.published_at)
    if undated:
        items.append(f"{undated} source(s) have no detectable publication date; recency could not be assessed.")
    if removed_citations:
        items.append(f"{removed_citations} invalid citation(s) produced during drafting were removed automatically.")
    items.append("Web sources vary in reliability; figures from market-research firms are estimates and often use differing definitions.")
    items.append("Automated verification checks that sources *state* a claim; it cannot establish that the sources themselves are correct.")
    return "\n".join(f"- {i}" for i in items)


def _references(m: Material) -> tuple[str, list[dict]]:
    refs = []
    for sid, lbl in sorted(m.labels.items(), key=lambda kv: int(kv[1][1:])):
        s = m.sources[sid]
        refs.append({"label": lbl, "source_id": str(sid), "title": s.title, "url": s.url, "domain": s.domain,
                     "published_at": s.published_at, "retrieved_at": s.retrieved_at.isoformat(), "source_type": s.source_type,
                     "security_flags": s.security_flags})
    lines = []
    for r in refs:
        title = f"[{r['title']}]({r['url']})" if r["url"] else r["title"]
        lines.append(f"- **[{r['label']}]** {title} — {r['domain'] or r['source_type']}; published {r['published_at'] or 'n.d.'}; retrieved {r['retrieved_at'][:10]}")
    return ("\n".join(lines) or "No sources were cited."), refs


async def generate_report(ctx: JobContext, only: str | None = None) -> Report:
    m = await load_material(ctx)
    claims = _claim_payload(m)
    disagreements = _disagreement_payload(m)

    async with session_scope() as s:
        prev = (await s.execute(select(Report).where(Report.job_id == ctx.job_id).order_by(Report.version.desc()).limit(1))).scalar_one_or_none()
    prev_sections = {sec["key"]: sec for sec in (prev.sections if prev else [])}

    keys = [only] if only else [k for k in LLM_SECTIONS if not (k == "contradictions" and not disagreements and not any(c["status"] == "conflicting" for c in claims))]
    if not any(c["status"] in ("supported", "partially_supported", "conflicting") for c in claims):
        keys = []  # nothing verified: no material to write from, so don't ask a model to pad the report
    results = await asyncio.gather(*(_llm_section(ctx, m, k, claims, disagreements) for k in keys))
    drafted = dict(zip(keys, results, strict=True))
    removed = sum(len(r[1]) for r in results)

    sections = []
    for key in SECTION_ORDER:
        if key in drafted:
            md = drafted[key][0]
        elif only and key in prev_sections and key not in STATIC_TITLES:
            md = prev_sections[key]["markdown"]
        elif key == "methodology":
            md = _methodology(m, ctx)
        elif key == "supporting_evidence":
            md = _supporting_evidence(m)
        elif key == "limitations":
            md = _limitations(m, ctx, removed)
        elif key == "references":
            md = _references(m)[0]
        elif key == "contradictions":
            md = "No material disagreements between sources were identified."
        elif key == "executive_summary":
            md = "The research did not produce verifiable evidence for this question. See Limitations."
        else:
            md = "Not covered: the verified evidence collected does not address this section."
        title = LLM_SECTIONS[key][0] if key in LLM_SECTIONS else STATIC_TITLES[key]
        sections.append({"key": key, "title": title, "markdown": md, "generated_by": "llm" if key in LLM_SECTIONS else "system"})

    _, refs = _references(m)
    status_counts: dict[str, int] = {}
    for c in m.claims:
        status_counts[c.status] = status_counts.get(c.status, 0) + 1
    all_md = "\n".join(sec["markdown"] for sec in sections if sec["key"] not in ("references", "supporting_evidence"))
    cited = set(re.findall(r"\[(S\d+)\]", all_md))
    stats = {
        "citations_in_text": len(re.findall(r"\[S\d+\]", all_md)),
        "unique_sources_cited": len(cited),
        "sources_available": len(m.labels),
        "invalid_citations_removed": removed,
        "claims_by_status": status_counts,
    }
    async with session_scope() as s:
        version = (prev.version + 1) if prev else 1
        report = Report(job_id=ctx.job_id, version=version, title=f"Research Report: {truncate(ctx.question, 140)}",
                        sections=sections, references=refs, citation_stats=stats)
        s.add(report)
    await ctx.emit("report_ready" if not only else "section_regenerated",
                   f"Report v{version} generated" if not only else f"Section '{only}' regenerated (v{version})", "report",
                   version=version, citation_stats=stats)
    return report


def report_markdown(report: Report) -> str:
    parts = [f"# {report.title}", ""]
    for sec in report.sections:
        parts += [f"## {sec['title']}", "", sec["markdown"], ""]
    return "\n".join(parts)
