"""Deterministic, offline LLM provider for tests, CI and demos.

Only active when LLM_PROVIDER=mock. It never pretends to be a model: outputs are produced by
simple lexical rules applied to the same structured `inputs` the real prompts are rendered from,
so the whole pipeline (planning -> research -> verification -> report) can run end-to-end without
paid API calls. Quality of mock output says nothing about real model quality.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.core.text import (
    content_words,
    extract_numbers,
    idf_weights,
    overlap,
    split_sentences,
    weighted_overlap,
)
from app.llm.base import LLMRequest, LLMResponse, T
from app.schemas.llm_outputs import (
    AnalysisResult,
    DirectAnswer,
    Disagreement,
    EvidenceJudgement,
    EvidenceJudgements,
    ExtractedFinding,
    FindingsExtraction,
    Gap,
    GapPlan,
    GroundedAnswer,
    PlannedTask,
    Position,
    ProposedClaim,
    ResearchPlan,
    SectionDraft,
    Theme,
)

GENERIC_ASPECTS = [
    "market size and key statistics",
    "leading companies and competitors",
    "growth trends",
    "government policy and regulation",
    "challenges and barriers",
    "future outlook and opportunities",
]
_LEAD_VERBS = re.compile(
    r"^(please\s+)?(analy[sz]e|identify|examine|evaluate|describe|explain|assess|research|investigate|review|compare)\s+",
    re.I,
)


def _topic_and_aspects(question: str) -> tuple[str, list[str]]:
    sentences = [s.strip() for s in re.split(r"(?<=[.?!])\s+", question.strip()) if s.strip()]
    topic = _LEAD_VERBS.sub("", sentences[0]).rstrip(".?! ") if sentences else question
    aspects: list[str] = []
    for s in sentences[1:]:
        body = _LEAD_VERBS.sub("", s).rstrip(".?! ")
        for part in re.split(r",|;|\band\b", body):
            part = part.strip()
            if len(content_words(part)) >= 1:
                aspects.append(part)
    return topic, aspects or list(GENERIC_ASPECTS)


def _dedupe_ci(items: list[str]) -> list[str]:
    seen, out = set(), []
    for it in items:
        k = it.lower()
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


def _plan(inp: dict) -> ResearchPlan:
    topic, aspects = _topic_and_aspects(inp["question"])
    tasks = [
        PlannedTask(
            objective=f"Determine the {a} for {topic}",
            search_queries=[f"{topic} {a}"],
            expected_evidence=f"Sourced statements about {a}",
            priority=1 if i < 3 else 2,
        )
        for i, a in enumerate(_dedupe_ci(aspects)[: inp.get("max_tasks", 6)])
    ]
    return ResearchPlan(restated_objective=topic, scope_notes="Mock planner: aspects parsed from the question.", tasks=tasks)


def _extract(inp: dict) -> FindingsExtraction:
    objective = inp["objective"]
    idf = idf_weights([s for p in inp["passages"] for s in split_sentences(p["text"])])
    scored = []
    for p in inp["passages"]:
        for sent in split_sentences(p["text"]):
            if len(sent) < 40 or len(sent) > 400:
                continue
            rel = weighted_overlap(objective, sent, idf)
            score = rel + (0.15 if extract_numbers(sent) else 0)
            if rel >= 0.2:
                scored.append((score, p["id"], sent))
    scored.sort(key=lambda x: -x[0])
    findings, seen = [], set()
    for _, pid, sent in scored:
        if sent in seen:
            continue
        seen.add(sent)
        low = sent.lower()
        kind = "forecast" if any(w in low for w in ("expected", "projected", "forecast", "will ", "by 20")) else (
            "interpretation" if any(w in low for w in ("believe", "argue", "likely", "suggest")) else "fact"
        )
        findings.append(ExtractedFinding(passage_id=pid, statement=sent, evidence_quote=sent, kind=kind))
        if len(findings) >= inp.get("max_findings", 8):
            break
    return FindingsExtraction(findings=findings, task_summary=f"Mock summary: {len(findings)} findings extracted.")


def _analyze(inp: dict) -> AnalysisResult:
    findings = inp["findings"]
    by_task: dict[str, list[dict]] = {}
    for f in findings:
        by_task.setdefault(f.get("task_key", "?"), []).append(f)
    claims, themes = [], []
    for task_key, fs in by_task.items():
        fs_sorted = sorted(fs, key=lambda f: (-len(extract_numbers(f["statement"])), f["id"]))
        for i, f in enumerate(fs_sorted[:2]):
            claims.append(
                ProposedClaim(
                    statement=f["statement"], category=task_key, importance=1 if i == 0 else 2,
                    kind=f.get("kind", "fact"), finding_ids=[f["id"]],
                )
            )
        themes.append(Theme(title=f"Theme {task_key}", summary=fs_sorted[0]["statement"], finding_ids=[f["id"] for f in fs_sorted[:3]]))
    # Disagreement: findings from different sources about the same thing with different numbers.
    disagreements = []
    for i, a in enumerate(findings):
        for b in findings[i + 1 :]:
            if a.get("source_label") == b.get("source_label"):
                continue
            na, nb = extract_numbers(a["statement"]), extract_numbers(b["statement"])
            if na and nb and na != nb and overlap(a["statement"], b["statement"]) >= 0.5:
                disagreements.append(
                    Disagreement(
                        topic=" ".join(content_words(a["statement"])[:5]),
                        positions=[Position(position=a["statement"], finding_ids=[a["id"]]),
                                   Position(position=b["statement"], finding_ids=[b["id"]])],
                    )
                )
    gaps = [
        Gap(description=f"No evidence found for: {obj}", suggested_queries=[obj])
        for obj in inp.get("tasks_without_findings", [])
    ]
    return AnalysisResult(themes=themes, claims=claims, disagreements=disagreements[:5], gaps=gaps)


def _judge(inp: dict) -> EvidenceJudgements:
    claim = inp["claim"]
    claim_nums = extract_numbers(claim)
    out = []
    for p in inp["passages"]:
        best, best_sent = 0.0, ""
        for sent in split_sentences(p["text"]):
            sc = overlap(claim, sent)
            if sc > best:
                best, best_sent = sc, sent
        if best >= 0.6:
            sent_nums = extract_numbers(best_sent)
            if claim_nums and sent_nums and not claim_nums <= sent_nums:
                stance = "contradicts" if claim_nums.isdisjoint(sent_nums) else "partial"
            else:
                stance = "supports"
        elif best >= 0.5 and (not claim_nums or claim_nums & extract_numbers(best_sent)):
            stance = "partial"
        else:
            stance, best_sent = "irrelevant", ""
        out.append(EvidenceJudgement(passage_id=p["id"], stance=stance, quote=best_sent, explanation=f"Mock lexical overlap {best:.2f}"))
    return EvidenceJudgements(judgements=out)


def _gap_plan(inp: dict) -> GapPlan:
    existing = {o.lower() for o in inp.get("existing_objectives", [])}
    tasks = []
    for g in inp.get("gaps", []):
        obj = f"Find evidence: {g['description']}"
        if obj.lower() in existing:
            continue
        tasks.append(PlannedTask(objective=obj, search_queries=g.get("suggested_queries", [])[:2] or [g["description"]],
                                 expected_evidence="Direct sourced statements", priority=1))
    for c in inp.get("weak_claims", []):
        obj = f"Verify: {c}"
        if obj.lower() not in existing:
            tasks.append(PlannedTask(objective=obj, search_queries=[c[:120]], expected_evidence="Independent confirmation", priority=2))
    return GapPlan(tasks=tasks[: inp.get("max_tasks", 3)])


def _section(inp: dict) -> SectionDraft:
    claims = inp.get("claims", [])
    key = inp["section_key"]
    lines = []
    if key == "contradictions":
        for d in inp.get("disagreements", []):
            lines.append(f"- **{d['topic']}**: " + " vs. ".join(f"{p['position']} {''.join(f'[{s}]' for s in p['sources'])}" for p in d["positions"]))
        return SectionDraft(markdown="\n".join(lines) or "No material disagreements were found between sources.")
    usable = [c for c in claims if c["status"] in ("supported", "partially_supported", "conflicting")]
    focus = inp.get("focus_keywords") or []
    if focus:
        usable = [c for c in usable if any(k in c["statement"].lower() for k in focus)]
    if not usable:
        return SectionDraft(markdown="The verified evidence collected does not cover this section.")
    for c in usable[:8]:
        cites = "".join(f"[{s}]" for s in c["sources"] + c.get("contradicting_sources", []))
        hedge = "" if c["status"] == "supported" else " (evidence is partial or conflicting)"
        lines.append(f"- {c['statement']}{hedge} {cites}")
    return SectionDraft(markdown="\n".join(lines))


def _answer(inp: dict) -> GroundedAnswer:
    q = inp["question"]
    hits = []
    for p in inp["passages"]:
        for sent in split_sentences(p["text"]):
            sc = overlap(q, sent)
            if sc >= 0.3:
                hits.append((sc, p["id"], sent))
    hits.sort(key=lambda x: -x[0])
    if not hits:
        return GroundedAnswer(answer_markdown="The available sources do not contain enough information to answer this question.", insufficient_evidence=True)
    return GroundedAnswer(answer_markdown=" ".join(f"{s} [{pid}]" for _, pid, s in hits[:3]), insufficient_evidence=False)


def _direct(inp: dict) -> DirectAnswer:
    return DirectAnswer(answer_markdown=f"(mock direct answer without retrieval) No grounded information available for: {inp['question']}")


HANDLERS: dict[str, Callable[[dict], Any]] = {
    "plan": _plan,
    "extract_findings": _extract,
    "analyze": _analyze,
    "judge_evidence": _judge,
    "gap_plan": _gap_plan,
    "report_section": _section,
    "grounded_answer": _answer,
    "direct_answer": _direct,
}


class MockProvider:
    name = "mock"

    def __init__(self, fail_tasks: set[str] | None = None):
        self.fail_tasks = fail_tasks or set()  # for failure-injection tests
        self.calls: list[str] = []

    async def structured(self, req: LLMRequest, schema: type[T]) -> LLMResponse:
        import asyncio

        from app.core.config import get_settings
        from app.core.errors import ProviderError

        self.calls.append(req.task)
        if latency := get_settings().mock_latency_ms:  # simulate model latency (offline demos, crash tests)
            await asyncio.sleep(latency / 1000)
        if req.task in self.fail_tasks:
            raise ProviderError(f"mock failure injected for task '{req.task}'")
        handler = HANDLERS.get(req.task)
        if handler is None:
            raise ProviderError(f"mock provider has no handler for task '{req.task}'")
        parsed = handler(req.inputs)
        if not isinstance(parsed, schema):
            parsed = schema.model_validate(parsed.model_dump())
        # Approximate token usage so budgets and metrics behave realistically in mock mode.
        pt = (len(req.system) + len(req.user)) // 4
        ct = len(parsed.model_dump_json()) // 4
        return LLMResponse(parsed=parsed, prompt_tokens=pt, completion_tokens=ct, model="mock")
