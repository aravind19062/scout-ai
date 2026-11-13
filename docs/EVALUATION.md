# Evaluation

Generating convincing reports is not the bar. The evaluation framework (`backend/app/evaluation`) measures
retrieval, answer, citation, agent and system quality, and compares three systems on the same questions.

## Systems compared

| System | What it does |
|---|---|
| `direct_llm` | The model answers from its own knowledge. No retrieval. |
| `single_pass_rag` | One web search → fetch + ingest top 5 → hybrid retrieval → grounded answer with citations. No planning, iteration or verification. |
| `autonomous_agent` | The full SCOUT AI workflow at a chosen depth; the answer scored is Executive Summary + Key Findings. |

## Metrics: what each one actually measures

| Group | Metric | Definition | Caveats |
|---|---|---|---|
| Retrieval | Precision@k | Share of the top-k retrieved *documents* that are labelled relevant | Document-level (chunks collapsed to their document in rank order) |
| | Recall@k | Share of labelled relevant documents found in the top-k | Needs ground-truth labels: only on the labelled set |
| | MRR | 1 / rank of the first relevant document | |
| | nDCG@5 | Binary-relevance discounted gain vs ideal ordering | |
| Answer | Fact recall | Share of reference facts whose keywords **all** appear in the answer | Lexical: misses paraphrases (under-counts), never credits missing facts |
| | Faithfulness | Share of substantive answer sentences that passages from the system's *own* corpus support (fully or partially) | Judged by the verifier prompt **and** a verbatim quote check in code; still an LLM judge (see below). Undefined for `direct_llm` |
| | Answer relevance | Cosine similarity of question and answer embeddings | Topicality proxy only. It does not measure correctness, and it rewards answers that echo the question |
| Citation | URL validity | Share of cited URLs that resolve (HTTP < 400 through the SSRF-safe fetcher) | Point-in-time; sites change |
| | Citation support rate | Share of (sentence, cited source) pairs where the *cited source itself* supports the sentence | Same judge caveat as faithfulness |
| Agent | Task completion / failure rate | From the job's tasks | |
| | Research rounds | 1 + gap-filling iterations | |
| | Tool-call success rate | 1 − failed tool calls / tool calls | Unreachable pages count as failures |
| System | Latency, tokens, estimated cost, error rate | From the per-run usage tracker | Cost uses configured prices (`PRICE_*`), so it is an estimate. The LLM cache is disabled during evaluation |

**Judge bias.** Faithfulness and citation support use the model to label stance. In live mode, if the judge is the
same model family as the generator, scores may be optimistic. Mitigations: a positive verdict needs a verbatim quote
that code finds in the passage, and numbers in the sentence must appear in that quote. For a stricter evaluation, set
`OPENAI_MODEL` to a different (stronger) model when running the evaluator than the one used to generate. We did not
use RAGAS: its metrics overlap these, it pulls a large dependency tree, and owning the definitions keeps them auditable.
The dataset format is simple enough to export to RAGAS if needed.

## Datasets

| File | Mode | Content |
|---|---|---|
| `evaluation/datasets/retrieval_fixture.json` | offline or live embeddings | 15 queries with hand-labelled relevant documents over the fixture corpus |
| `evaluation/datasets/freedonia_mock.json` | mock | 3 research questions, 12 reference facts copied verbatim from the *fictional* fixture corpus |
| `evaluation/datasets/real_world.json` | live | 5 questions with long-established, stable reference facts (Paris Agreement, CRISPR Nobel, JWST, Transformer paper, India FAME) |

## How to run

```bash
cd backend
uv run python -m app.evaluation.run retrieval --dataset ../evaluation/datasets/retrieval_fixture.json
uv run python -m app.evaluation.run e2e --dataset ../evaluation/datasets/freedonia_mock.json --depth standard
uv run python -m app.evaluation.run e2e --dataset ../evaluation/datasets/real_world.json --depth quick   # needs keys
```

Each run writes `evaluation/results/<timestamp>-<name>.json` (per-question rows) and `.md` (summary), recording the provider mode.

## Results actually measured (2026-11-06)

> **All results below are from MOCK mode** (deterministic lexical "LLM", hashing embeddings, fictional fixture corpus).
> They verify that the pipeline and metrics work end to end. **They say nothing about real model quality.**
> No live OpenAI/Tavily evaluation has been run yet because no API keys were available in the build environment.

### Retrieval: 15 labelled queries, hashing embeddings (deterministic across 3 repeated runs)

| mode | P@1 | P@3 | P@5 | R@1 | R@3 | R@5 | MRR | nDCG@5 |
|---|---|---|---|---|---|---|---|---|
| vector | 0.933 | 0.422 | 0.253 | 0.822 | 1.000 | 1.000 | **0.956** | **0.967** |
| hybrid (RRF) | 0.867 | 0.422 | 0.253 | 0.756 | 1.000 | 1.000 | 0.933 | 0.945 |
| keyword | 0.800 | 0.378 | 0.253 | 0.722 | 0.900 | 1.000 | 0.872 | 0.907 |

**Reading:** on this set, hybrid does **not** improve on vector-only. That is expected here, because the offline
"vector" retriever is itself lexical (hashed unigrams and bigrams), so keyword search adds little and RRF mostly adds
noise. The test cannot tell us whether hybrid helps real dense embeddings. Default stays `hybrid` on the reasoning
that exact figures and names matter in research, but this is **unverified**. Re-run with real embeddings before relying
on it, and set `RETRIEVAL_MODE` accordingly. While building this, the evaluation also exposed two real bugs, both fixed:
non-deterministic RRF tie ordering, and pgvector HNSW silently dropping filtered results (now
`hnsw.iterative_scan = strict_order`).

### End-to-end: 3 questions, `standard` depth, mock providers

Ranges are over 4 repeated runs. The agent is not bit-for-bit deterministic even with mock providers: research tasks run concurrently and race to fetch shared URLs, which changes which task owns a source and which copy of a duplicated page is kept.

| system | fact recall | faithfulness | citation support | URL validity | answer relevance | error rate | tokens/run | tool calls/run |
|---|---|---|---|---|---|---|---|---|
| autonomous_agent | 0.133–0.150 | 1.000 | 0.845–0.956 | 1.000 | 0.29–0.33 | 0 | 19.3k–25.7k | 15.3–19.7 |
| single_pass_rag | 0.167 | 0.333 | 0.333 | 1.000 | 0.313 | 0 | 540 | 2.7 |
| direct_llm | 0.000 | — | — | — | 0.742 | 0 | 111 | 0 |

Agent: task completion 1.00, failed tasks 0.00, 1.67 research rounds on average, tool-call success 0.959 (the
fixture corpus deliberately contains an unreachable page).

**Reading (mechanics only):**
- The agent's sentences are grounded in its corpus (faithfulness 1.0) and its citations point to sources that support
  the cited sentence (0.85–0.96); single-pass RAG's mock answers are far less grounded (0.333). This reflects the
  verify-then-write design, where only verified claims reach the report.
- **Fact recall is low for every system.** The lexical mock "writer" lists a handful of claims, so the scored answer
  (summary + key findings) covers few of the 12 reference facts. Do not read this as a real-model result.
- `answer_relevance` ranks the mock direct answer highest because it restates the question. This shows why that
  metric is a weak proxy and should not be used alone.
- The agent costs ~40× the tokens of single-pass RAG. Whether that buys proportionally better answers can only be
  answered by a live run.

## Next steps for a credible live evaluation

1. Add keys, run `real_world.json` at `quick` and `standard`, and record results here with date, model and cost.
2. Extend the labelled retrieval set with live web pages from your domain; compare `RETRIEVAL_MODE`s.
3. Use a different judge model from the generator; spot-check 20 judged sentences by hand to estimate judge accuracy.
