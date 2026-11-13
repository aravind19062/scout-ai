"""Structured-output schemas for LLM calls.

These are sent to the model as strict JSON schemas, so they deliberately avoid defaults and
numeric constraints (not all are supported in strict mode). Bounds are enforced in code.
Identifiers the model refers to (P#, F#, S#) are short labels assigned by our code in the prompt
and mapped back to database ids afterwards; the model never invents database ids or URLs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FindingKind = Literal["fact", "interpretation", "forecast"]
Stance = Literal["supports", "partial", "contradicts", "irrelevant"]


class PlannedTask(BaseModel):
    objective: str = Field(description="One focused research objective")
    search_queries: list[str] = Field(description="1-3 distinct web search queries")
    expected_evidence: str = Field(description="What evidence would answer this objective")
    priority: int = Field(description="1 = critical, 2 = important, 3 = nice to have")


class ResearchPlan(BaseModel):
    restated_objective: str
    scope_notes: str = Field(description="Assumptions, time frame, geography, exclusions")
    tasks: list[PlannedTask]


class ExtractedFinding(BaseModel):
    passage_id: str = Field(description="The P# label of the passage the evidence comes from")
    statement: str = Field(description="Self-contained finding in your own words")
    evidence_quote: str = Field(description="Verbatim quote copied exactly from that passage")
    kind: FindingKind


class FindingsExtraction(BaseModel):
    findings: list[ExtractedFinding]
    task_summary: str = Field(description="2-3 sentence summary of what the passages establish")


class ProposedClaim(BaseModel):
    statement: str
    category: str = Field(description="Short topic label, e.g. 'market size', 'policy'")
    importance: int = Field(description="1 = central to the answer, 2 = supporting, 3 = minor")
    kind: FindingKind
    finding_ids: list[str] = Field(description="F# labels of findings this claim is based on")


class Position(BaseModel):
    position: str
    finding_ids: list[str]


class Disagreement(BaseModel):
    topic: str
    positions: list[Position]


class Theme(BaseModel):
    title: str
    summary: str
    finding_ids: list[str]


class Gap(BaseModel):
    description: str
    suggested_queries: list[str]


class AnalysisResult(BaseModel):
    themes: list[Theme]
    claims: list[ProposedClaim]
    disagreements: list[Disagreement]
    gaps: list[Gap]


class EvidenceJudgement(BaseModel):
    passage_id: str
    stance: Stance
    quote: str = Field(description="Verbatim span from the passage that justifies the stance; empty if irrelevant")
    explanation: str = Field(description="One sentence; no speculation beyond the passage")


class EvidenceJudgements(BaseModel):
    judgements: list[EvidenceJudgement]


class GapPlan(BaseModel):
    tasks: list[PlannedTask]


class SectionDraft(BaseModel):
    markdown: str


class GroundedAnswer(BaseModel):
    answer_markdown: str
    insufficient_evidence: bool = Field(description="True if the passages do not adequately answer the question")


class DirectAnswer(BaseModel):
    answer_markdown: str
