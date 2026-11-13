"""Verification agent.

For each important claim:
  1. Retrieve candidate evidence from the job's *own retrieved sources* (hybrid search), plus the
     chunks the claim was derived from.
  2. Gate by relevance; if nothing relevant exists the claim is `unable_to_verify` (no LLM call).
  3. Ask the model for a per-passage stance AND a verbatim quote.
  4. Check programmatically: the quote must exist in the passage, and numbers in the claim must
     appear in a supporting quote. Unverifiable judgements are discarded; numeric mismatches are
     downgraded to `partial`.
  5. Decide the status with deterministic rules (`decide_status`), not with model confidence.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select

from app.core.text import extract_numbers, overlap, quote_in_text
from app.database.session import session_scope
from app.llm.prompts import VERIFIER_SYSTEM, passage
from app.models import Chunk, Claim, ClaimEvidence, Finding, Source
from app.orchestration.context import JobContext
from app.rag.retriever import RetrievedChunk
from app.schemas.llm_outputs import EvidenceJudgements

LEXICAL_GATE = 0.3
VECTOR_GATE = 0.45


@dataclass
class Judged:
    chunk_id: uuid.UUID
    source_id: uuid.UUID
    domain: str | None
    stance: str  # supports | partial | contradicts | irrelevant
    quote: str
    quote_verified: bool
    numbers_consistent: bool
    relevance: float


def is_relevant(claim: str, ch: RetrievedChunk) -> bool:
    return overlap(claim, ch.text) >= LEXICAL_GATE or (ch.vector_similarity or 0) >= VECTOR_GATE


def check_judgement(claim: str, stance: str, quote: str, chunk_text: str) -> tuple[str, bool, bool]:
    """Returns (effective_stance, quote_verified, numbers_consistent)."""
    if stance == "irrelevant":
        return "irrelevant", False, True
    verified = quote_in_text(quote, chunk_text)
    if not verified:
        return "irrelevant", False, True  # model judgement not grounded in the passage: discard
    claim_nums = extract_numbers(claim)
    consistent = not claim_nums or claim_nums <= extract_numbers(quote)
    if stance == "supports" and not consistent:
        return "partial", True, False
    return stance, True, consistent


def decide_status(evidence: list[Judged], any_relevant: bool) -> tuple[str, str]:
    sup = [e for e in evidence if e.stance == "supports"]
    par = [e for e in evidence if e.stance == "partial"]
    con = [e for e in evidence if e.stance == "contradicts"]
    domains = {e.domain or str(e.source_id) for e in sup}
    if not any_relevant:
        return "unable_to_verify", "No relevant evidence was found among the retrieved sources."
    if sup and con:
        return "conflicting", f"{len(sup)} passage(s) support and {len(con)} contradict the claim."
    if sup:
        note = "" if len(domains) > 1 else " Single source only; not independently corroborated."
        return "supported", f"Supported by {len(sup)} passage(s) from {len(domains)} independent source(s).{note}"
    if par and con:
        return "conflicting", f"Evidence is mixed: {len(par)} partial support, {len(con)} contradicting."
    if par:
        return "partially_supported", f"{len(par)} passage(s) support only part of the claim or differ in detail."
    if con:
        return "unsupported", f"Retrieved evidence contradicts the claim ({len(con)} passage(s)) and none supports it."
    return "unsupported", "Relevant passages were retrieved but none of them states the claim."


async def _candidate_evidence(ctx: JobContext, claim: Claim) -> list[RetrievedChunk]:
    async with session_scope() as s:
        hits = await ctx.retriever.search(s, claim.statement, ctx.filters(), k=ctx.depth.verify_passages)
        # Make sure the chunks the claim was derived from are considered too.
        fids = [uuid.UUID(f) for f in claim.finding_ids]
        rows = (await s.execute(
            select(Chunk, Source).join(Finding, Finding.chunk_id == Chunk.id).join(Source, Source.id == Chunk.source_id)
            .where(Finding.id.in_(fids))
        )).all() if fids else []
    have = {h.chunk_id for h in hits}
    for c, src in rows:
        if c.id not in have:
            have.add(c.id)
            hits.append(RetrievedChunk(
                chunk_id=c.id, source_id=src.id, text=c.text, score=0, vector_similarity=None, keyword_rank=None,
                vector_rank=None, title=src.title, url=src.url, domain=src.domain, source_type=src.source_type,
                published_at=src.published_at, retrieved_at=src.retrieved_at.isoformat(), security_flags=list(src.security_flags or []),
            ))
    return hits


async def verify_claim(ctx: JobContext, claim_id: uuid.UUID) -> str:
    async with session_scope() as s:
        claim = await s.get(Claim, claim_id)
    candidates = await _candidate_evidence(ctx, claim)
    relevant = [c for c in candidates if is_relevant(claim.statement, c)][: ctx.depth.verify_passages + 2]

    judged: list[Judged] = []
    if relevant:
        labels = {f"P{i}": c for i, c in enumerate(relevant, 1)}
        body = "\n\n".join(passage(lbl, c.text, f"{c.title} | {c.domain or c.source_type}") for lbl, c in labels.items())
        out = await ctx.llm.structured(
            task="judge_evidence", system=VERIFIER_SYSTEM,
            user=f"Claim: {claim.statement}\n\nPassages:\n{body}\n\nJudge every passage.",
            schema=EvidenceJudgements,
            inputs={"claim": claim.statement, "passages": [{"id": lbl, "text": c.text} for lbl, c in labels.items()]},
        )
        seen = set()
        for j in out.judgements:
            c = labels.get(j.passage_id.strip())
            if c is None or c.chunk_id in seen:
                continue
            seen.add(c.chunk_id)
            stance, qv, nc = check_judgement(claim.statement, j.stance, j.quote, c.text)
            judged.append(Judged(c.chunk_id, c.source_id, c.domain, stance, j.quote if qv else "", qv, nc,
                                 round(overlap(claim.statement, c.text), 3)))

    status, rationale = decide_status(judged, bool(relevant))
    sup = [e for e in judged if e.stance == "supports"]
    async with session_scope() as s:
        await s.execute(delete(ClaimEvidence).where(ClaimEvidence.claim_id == claim_id))
        for e in judged:
            if e.stance != "irrelevant":
                s.add(ClaimEvidence(claim_id=claim_id, chunk_id=e.chunk_id, source_id=e.source_id, stance=e.stance, quote=e.quote,
                                    quote_verified=e.quote_verified, numbers_consistent=e.numbers_consistent, relevance=e.relevance))
        c = await s.get(Claim, claim_id)
        c.status, c.rationale = status, rationale
        c.support_count = len(sup)
        c.contradict_count = sum(1 for e in judged if e.stance == "contradicts")
        c.distinct_domains = len({e.domain or str(e.source_id) for e in sup})
        c.verify_attempts += 1
    await ctx.emit("claim_verified", f"{status.replace('_', ' ').title()}: {claim.statement}", "verify",
                   claim_id=str(claim_id), status=status, rationale=rationale, evidence=len([e for e in judged if e.stance != "irrelevant"]))
    return status


async def verify_pending(ctx: JobContext) -> dict[str, int]:
    async with session_scope() as s:
        ids = list((await s.execute(
            select(Claim.id).where(Claim.job_id == ctx.job_id, Claim.status == "pending").order_by(Claim.importance, Claim.created_at)
        )).scalars())
    counts: dict[str, int] = {}
    for cid in ids:
        await ctx.checkpoint()
        st = await verify_claim(ctx, cid)
        counts[st] = counts.get(st, 0) + 1
    return counts
