import uuid

import pytest
from pydantic import ValidationError

from app.agents.common import dedupe_tasks
from app.agents.verifier import Judged, check_judgement, decide_status
from app.evaluation import metrics as M
from app.llm.embeddings import HashingEmbeddings
from app.schemas.api import CreateResearch
from app.schemas.llm_outputs import PlannedTask, ResearchPlan

SRC = "The Freedonia EV market grew 38 percent year on year in fiscal year 2024."


def J(stance, domain="a.example", verified=True):
    return Judged(uuid.uuid4(), uuid.uuid4(), domain, stance, "q", verified, True, 0.5)


# ---- verification rules -------------------------------------------------------------------

def test_check_judgement_discards_fabricated_quote():
    assert check_judgement("grew 38 percent", "supports", "The market grew 50 percent overall", SRC) == ("irrelevant", False, True)


def test_check_judgement_downgrades_number_mismatch():
    stance, verified, consistent = check_judgement("The market grew 45 percent in 2024", "supports", "grew 38 percent year on year in fiscal year 2024", SRC)
    assert (stance, verified, consistent) == ("partial", True, False)


def test_check_judgement_keeps_grounded_support():
    assert check_judgement("The market grew 38 percent in 2024", "supports", "grew 38 percent year on year in fiscal year 2024", SRC)[0] == "supports"


@pytest.mark.parametrize(
    "evidence,any_relevant,expected",
    [
        ([], False, "unable_to_verify"),
        ([J("irrelevant")], True, "unsupported"),
        ([J("supports")], True, "supported"),
        ([J("supports"), J("contradicts")], True, "conflicting"),
        ([J("partial")], True, "partially_supported"),
        ([J("partial"), J("contradicts")], True, "conflicting"),
        ([J("contradicts")], True, "unsupported"),
    ],
)
def test_decide_status(evidence, any_relevant, expected):
    assert decide_status(evidence, any_relevant)[0] == expected


def test_single_source_support_is_flagged():
    _, why = decide_status([J("supports", "a.example"), J("supports", "a.example")], True)
    assert "Single source" in why
    _, why2 = decide_status([J("supports", "a.example"), J("supports", "b.example")], True)
    assert "2 independent" in why2 and "Single" not in why2


# ---- planner ------------------------------------------------------------------------------

async def test_dedupe_tasks_removes_near_duplicates():
    t = lambda o: PlannedTask(objective=o, search_queries=[o], expected_evidence="", priority=1)  # noqa: E731
    out = await dedupe_tasks(
        [t("Estimate the market size of Indian EVs"), t("Estimate the Indian EV market size"), t("Identify charging infrastructure gaps")],
        ["Identify charging infrastructure gaps"],
        HashingEmbeddings(256),
    )
    assert [x.objective for x in out] == ["Estimate the market size of Indian EVs"]


# ---- schemas ------------------------------------------------------------------------------

def test_llm_output_schema_strict_compatible():
    # Every property is required (no defaults) - required by OpenAI strict structured outputs.
    schema = ResearchPlan.model_json_schema()
    task = schema["$defs"]["PlannedTask"]
    assert set(task["required"]) == set(task["properties"])


def test_create_research_validation():
    assert CreateResearch(question="  Analyse   the EV market  ").question == "Analyse the EV market"
    with pytest.raises(ValidationError):
        CreateResearch(question="hi")
    with pytest.raises(ValidationError):
        CreateResearch(question="Analyse the EV market", depth="ultra")


# ---- metrics ------------------------------------------------------------------------------

def test_retrieval_metrics():
    ranked, rel = ["a", "b", "c", "d"], {"b", "d"}
    assert M.precision_at_k(ranked, rel, 2) == 0.5
    assert M.recall_at_k(ranked, rel, 2) == 0.5 and M.recall_at_k(ranked, rel, 4) == 1.0
    assert M.mrr(ranked, rel) == 0.5 and M.mrr(["x"], rel) == 0
    assert 0 < M.ndcg_at_k(ranked, rel, 4) < 1 and M.ndcg_at_k(["b", "d"], rel, 2) == 1.0


def test_fact_recall_requires_all_keywords():
    facts = [{"fact": "a", "keywords": ["412,000"]}, {"fact": "b", "keywords": ["Voltra", "27 percent"]}]
    score, hit = M.fact_recall("Sales hit 412000 units; Voltra leads.", facts)
    assert score == 0.5 and hit == ["a"]


def test_answer_sentences_strip_links_and_headings():
    s = M.answer_sentences("## Heading\n- The market grew 38 percent in fiscal 2024 [S1](https://x.example). Ok.")
    assert s == ["The market grew 38 percent in fiscal 2024 [S1]."]
