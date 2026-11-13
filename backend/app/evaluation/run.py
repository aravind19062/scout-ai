"""Evaluation runner.

    uv run python -m app.evaluation.run retrieval --dataset ../evaluation/datasets/retrieval_fixture.json
    uv run python -m app.evaluation.run e2e --dataset ../evaluation/datasets/freedonia_mock.json --depth quick

Results are written to ../evaluation/results/<timestamp>-<name>.{json,md}. Every result file
records the provider mode, so mock-mode numbers can never be mistaken for real model quality.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database.session import dispose_engine, init_engine, session_scope
from app.evaluation import metrics as M
from app.evaluation.systems import SystemOutput, run_agent, run_direct, run_rag
from app.llm.factory import get_embedder, get_llm
from app.llm.usage import UsageTracker, bind_usage, unbind_usage
from app.models import Source
from app.rag.ingestion import IngestionService
from app.rag.retriever import HybridRetriever, RetrievalFilters
from app.retrieval.fetcher import load_fixture_corpus

RESULTS = Path(__file__).resolve().parents[3] / "evaluation" / "results"


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 4) if xs else None


async def eval_retrieval(dataset: dict, ks=(1, 3, 5), rerankers: tuple[str, ...] = ("none",)) -> dict:
    """Retrieval quality of vector vs keyword vs hybrid, each with and without second-stage
    reranking, over the labelled fixture corpus. Also records retrieval latency per query."""
    coll = "eval:retrieval"
    emb = get_embedder()
    async with session_scope() as s:
        await s.execute(delete(Source).where(Source.collection == coll))
    ingest = IngestionService(emb)
    url_to_id: dict[str, str] = {}
    async with session_scope() as s:
        for d in load_fixture_corpus():
            # include duplicates: ingest each doc under its own collection-unique text by url-tagging
            r = await ingest.ingest(s, collection=coll, text=d["content"] + f"\n\n(Source: {d['id']})", title=d["title"], source_type="web", url=d["url"])
            url_to_id[r.source.url] = d["id"]
    retriever = HybridRetriever(emb)
    out = {}
    for mode, rr in [(m, r) for r in rerankers for m in ("vector", "keyword", "hybrid")]:
        rows = {f"P@{k}": [] for k in ks} | {f"R@{k}": [] for k in ks} | {"MRR": [], f"nDCG@{max(ks)}": [], "ms": []}
        for q in dataset["queries"]:
            t0 = time.perf_counter()
            async with session_scope() as s:
                hits = await retriever.search(s, q["query"], RetrievalFilters(collections=[coll]), k=20, mode=mode, reranker=rr)
            rows["ms"].append((time.perf_counter() - t0) * 1000)
            ranked = list(dict.fromkeys(url_to_id[h.url] for h in hits))  # unique documents in rank order
            rel = set(q["relevant"])
            for k in ks:
                rows[f"P@{k}"].append(M.precision_at_k(ranked, rel, k))
                rows[f"R@{k}"].append(M.recall_at_k(ranked, rel, k))
            rows["MRR"].append(M.mrr(ranked, rel))
            rows[f"nDCG@{max(ks)}"].append(M.ndcg_at_k(ranked, rel, max(ks)))
        out[mode if rr == "none" else f"{mode}+{rr}"] = {k: _mean(v) for k, v in rows.items()}
    return {"queries": len(dataset["queries"]), "by_mode": out}


async def _score(q: dict, o: SystemOutput) -> dict:
    llm, emb = get_llm(), get_embedder()
    retr = HybridRetriever(emb)
    judge_usage = UsageTracker()
    tok = bind_usage(judge_usage)
    try:
        fr, hits = M.fact_recall(o.answer, q.get("reference_facts", []))
        row = {
            "system": o.system, "question_id": q["id"], "error": o.error, "latency_s": round(o.latency_s, 2),
            "tokens": o.usage.get("total_tokens", 0), "cost_usd": round(o.usage.get("cost_usd", 0.0), 5),
            "tool_calls": o.usage.get("tool_calls", 0), "llm_cache_hits": o.usage.get("llm_cache_hits", 0), "fact_recall": round(fr, 4), "facts_matched": hits,
            "answer_chars": len(o.answer),
        }
        if o.error or not o.answer:
            return row
        row["answer_relevance"] = round(await M.answer_relevance(emb, q["question"], o.answer), 4)
        async with session_scope() as s:
            if o.collection:
                f, n = await M.faithfulness(s, llm, retr, o.answer, RetrievalFilters(collections=[o.collection]))
                row["faithfulness"], row["faithfulness_n"] = (round(f, 4) if f is not None else None), n
                cs, n = await M.citation_support(s, llm, retr, o.answer, o.label_to_source, o.collection)
                row["citation_support_rate"], row["citation_pairs"] = (round(cs, 4) if cs is not None else None), n
        valid = [await M.url_valid(u) for u in o.cited_urls]
        row["citation_url_validity"] = round(sum(valid) / len(valid), 4) if valid else None
        row["citations"] = len(o.cited_urls)
        if o.agent:
            row["agent"] = o.agent
        if "expected_domains" in q and o.cited_urls:
            from urllib.parse import urlsplit

            got = {urlsplit(u).hostname for u in o.cited_urls}
            row["expected_domain_recall"] = round(len(got & set(q["expected_domains"])) / len(q["expected_domains"]), 4)
    finally:
        unbind_usage(tok)
    row["eval_judge_tokens"] = judge_usage.total_tokens
    return row


async def eval_e2e(dataset: dict, systems: list[str], depth: str) -> dict:
    rows = []
    for q in dataset["questions"]:
        for sysname in systems:
            print(f"  · {q['id']} / {sysname} …", flush=True)
            if sysname == "direct":
                o = await run_direct(q["question"])
            elif sysname == "rag":
                o = await run_rag(q["question"])
            else:
                o = await run_agent(q["question"], depth)
            rows.append(await _score(q, o))
    summary = {}
    for sysname in {r["system"] for r in rows}:
        rs = [r for r in rows if r["system"] == sysname]
        summary[sysname] = {
            "runs": len(rs),
            "error_rate": round(sum(1 for r in rs if r["error"]) / len(rs), 4),
            **{m: _mean([r.get(m) for r in rs]) for m in (
                "fact_recall", "faithfulness", "citation_support_rate", "citation_url_validity", "answer_relevance",
                "expected_domain_recall", "latency_s", "tokens", "cost_usd", "tool_calls")},
        }
        ag = [r["agent"] for r in rs if r.get("agent")]
        if ag:
            summary[sysname]["agent"] = {m: _mean([a.get(m) for a in ag]) for m in (
                "task_completion_rate", "failed_task_rate", "research_rounds", "tool_call_success_rate")}
    return {"depth": depth, "systems": systems, "summary": summary, "rows": rows}


def _md_table(d: dict[str, dict], cols: list[str]) -> str:
    fmt = lambda v: "—" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))  # noqa: E731
    lines = ["| system | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
    for name, vals in sorted(d.items()):
        lines.append(f"| {name} | " + " | ".join(fmt(vals.get(c)) for c in cols) + " |")
    return "\n".join(lines)


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("kind", choices=["retrieval", "e2e"])
    p.add_argument("--dataset", required=True)
    p.add_argument("--systems", default="direct,rag,agent")
    p.add_argument("--depth", default="quick", choices=["quick", "standard", "deep"])
    p.add_argument("--name", default=None)
    p.add_argument("--rerankers", default="none,lexical,cross_encoder",
                   help="retrieval eval: comma-separated rerankers to compare (none, lexical, cross_encoder)")
    a = p.parse_args()
    s = get_settings()
    configure_logging("WARNING")
    init_engine()
    # Measure real cost/latency: never serve system answers from the LLM response cache during evaluation.
    get_llm().use_cache = False
    # Measure the reranker itself, never its timeout fallback.
    s.rerank_timeout_s = max(s.rerank_timeout_s, 600.0)
    dataset = json.loads(Path(a.dataset).read_text())  # noqa: ASYNC240 (CLI startup)
    if dataset.get("mode") == "live" and s.is_mock:
        raise SystemExit("This dataset needs real providers (OPENAI/TAVILY); mock mode is configured.")
    started = datetime.now(UTC)
    print(f"Running {a.kind} evaluation on {a.dataset} (providers: {s.mode_summary})")
    result = await eval_retrieval(dataset, rerankers=tuple(a.rerankers.split(","))) if a.kind == "retrieval" else await eval_e2e(dataset, a.systems.split(","), a.depth)
    meta = {"kind": a.kind, "dataset": Path(a.dataset).name, "started_at": started.isoformat(),
            "duration_s": round((datetime.now(UTC) - started).total_seconds(), 1), "providers": s.mode_summary,
            "mock_mode": s.is_mock, "embedding_model": get_embedder().model}
    out = {"meta": meta, **result}
    RESULTS.mkdir(parents=True, exist_ok=True)
    stem = f"{started:%Y%m%dT%H%M%S}-{a.name or a.kind + '-' + Path(a.dataset).stem}"
    (RESULTS / f"{stem}.json").write_text(json.dumps(out, indent=2, default=str))
    md = [f"# Evaluation: {stem}", "", f"- Providers: `{meta['providers']}`" + ("  **(MOCK MODE: pipeline mechanics only, not model quality)**" if s.is_mock else ""), f"- Embeddings: `{meta['embedding_model']}`", f"- Duration: {meta['duration_s']}s", ""]
    if a.kind == "retrieval":
        cols = list(next(iter(result["by_mode"].values())).keys())
        md.append(f"Retrieval over {result['queries']} labelled queries (document-level, unique docs in rank order):\n")
        md.append(_md_table(result["by_mode"], cols))
    else:
        md.append(_md_table(result["summary"], ["fact_recall", "faithfulness", "citation_support_rate", "citation_url_validity",
                                                "answer_relevance", "error_rate", "latency_s", "tokens", "cost_usd", "tool_calls"]))
        ag = {k: v["agent"] for k, v in result["summary"].items() if "agent" in v}
        if ag:
            md += ["", "Agent metrics:", "", _md_table(ag, ["task_completion_rate", "failed_task_rate", "research_rounds", "tool_call_success_rate"])]
    (RESULTS / f"{stem}.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))
    print(f"\nWritten: {RESULTS / stem}.json/.md")
    from app.orchestration.runner import job_manager

    await job_manager.shutdown()
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
