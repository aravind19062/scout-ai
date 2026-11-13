"""Analysis agent: synthesise findings into themes, claims to verify, disagreements and gaps."""

from __future__ import annotations

from sqlalchemy import select

from app.core.text import normalize, overlap
from app.database.session import session_scope
from app.llm.prompts import ANALYST_SYSTEM, passage
from app.models import Claim, Finding, ResearchJob, ResearchTask, Source
from app.orchestration.context import JobContext
from app.schemas.llm_outputs import AnalysisResult

MAX_FINDINGS_IN_PROMPT = 150


def _is_duplicate_claim(statement: str, existing: list[str]) -> bool:
    n = normalize(statement)
    return any(n == normalize(e) or (overlap(statement, e) >= 0.85 and overlap(e, statement) >= 0.85) for e in existing)


async def analyze(ctx: JobContext, iteration: int) -> dict:
    async with session_scope() as s:
        rows = (await s.execute(
            select(Finding, ResearchTask.key, ResearchTask.priority, Source.domain, Source.title)
            .join(ResearchTask, ResearchTask.id == Finding.task_id)
            .join(Source, Source.id == Finding.source_id)
            .where(Finding.job_id == ctx.job_id)
            .order_by(ResearchTask.priority, Finding.created_at)
        )).all()
        tasks = list((await s.execute(select(ResearchTask).where(ResearchTask.job_id == ctx.job_id))).scalars())
        existing_claims = list((await s.execute(select(Claim).where(Claim.job_id == ctx.job_id))).scalars())

    rows = rows[:MAX_FINDINGS_IN_PROMPT]
    labels = {f"F{i}": r for i, r in enumerate(rows, 1)}
    with_findings = {r.key for r in rows}
    tasks_without = [t.objective for t in tasks if t.key not in with_findings and t.status in ("completed", "failed")]

    if not rows:
        result = AnalysisResult(themes=[], claims=[], disagreements=[], gaps=[])
        await ctx.emit("analysis_done", "No findings to analyse yet", "analyze")
    else:
        findings_text = "\n".join(
            passage(lbl, f"{r.Finding.statement}\nQuote: \"{r.Finding.evidence_quote}\"",
                    f"task {r.key} | {r.Finding.kind} | {r.domain or r.title}")
            for lbl, r in labels.items()
        )
        user = (
            f"Research question:\n{ctx.question}\n\nFindings:\n{findings_text}\n\n"
            f"Tasks that produced no findings: {tasks_without or 'none'}\n\n"
            f"Propose at most {ctx.depth.max_claims} claims (most important first)."
        )
        result = await ctx.llm.structured(
            task="analyze", system=ANALYST_SYSTEM, user=user, schema=AnalysisResult,
            inputs={
                "question": ctx.question,
                "findings": [{"id": lbl, "statement": r.Finding.statement, "kind": r.Finding.kind, "task_key": r.key,
                              "source_label": str(r.Finding.source_id)} for lbl, r in labels.items()],
                "tasks_without_findings": tasks_without,
            },
            max_output_tokens=8000,
        )

    def ids(fids: list[str]) -> list[str]:
        return [str(labels[f].Finding.id) for f in fids if f in labels]

    def srcs(fids: list[str]) -> list[str]:
        return sorted({str(labels[f].Finding.source_id) for f in fids if f in labels})

    # Claims: must be traceable to at least one real finding; skip duplicates of earlier claims.
    existing_statements = [c.statement for c in existing_claims]
    budget = max(0, ctx.depth.max_claims - len(existing_claims))
    new_claims, untraceable = [], 0
    for pc in sorted(result.claims, key=lambda c: c.importance):
        fids = ids(pc.finding_ids)
        if not fids:
            untraceable += 1
            continue
        if _is_duplicate_claim(pc.statement, existing_statements) or len(new_claims) >= budget:
            continue
        existing_statements.append(pc.statement)
        new_claims.append(Claim(
            job_id=ctx.job_id, statement=pc.statement.strip(), normalized=normalize(pc.statement), category=pc.category[:60],
            importance=min(max(pc.importance, 1), 3), kind=pc.kind, finding_ids=fids, iteration=iteration,
        ))

    disagreements, seen_pairs = [], set()
    for d in result.disagreements:
        positions = [{"position": p.position, "finding_ids": ids(p.finding_ids), "source_ids": srcs(p.finding_ids)} for p in d.positions]
        if sum(1 for p in positions if p["finding_ids"]) < 2:  # a disagreement needs two evidenced sides
            continue
        key = frozenset(normalize(p["position"]) for p in positions)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        disagreements.append({"topic": d.topic, "positions": positions})

    analysis = {
        "iteration": iteration,
        "finding_count": len(rows),
        "themes": [{"title": t.title, "summary": t.summary, "finding_ids": ids(t.finding_ids)} for t in result.themes],
        "disagreements": disagreements,
        "gaps": [{"description": g.description, "suggested_queries": g.suggested_queries[:3]} for g in result.gaps],
        "untraceable_claims_dropped": untraceable,
    }
    async with session_scope() as s:
        s.add_all(new_claims)
        job = await s.get(ResearchJob, ctx.job_id)
        job.analysis = analysis
    await ctx.emit(
        "analysis_done",
        f"{len(new_claims)} new claims to verify, {len(analysis['disagreements'])} disagreements, {len(analysis['gaps'])} gaps identified",
        "analyze",
        themes=[t["title"] for t in analysis["themes"]], disagreements=[d["topic"] for d in analysis["disagreements"]],
        gaps=[g["description"] for g in analysis["gaps"]], claims=[c.statement for c in new_claims],
    )
    return analysis
