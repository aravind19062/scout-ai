# Evaluation: 20261106T094030-e2e-freedonia_mock

- Providers: `{'llm': 'mock', 'embeddings': 'mock', 'search': 'mock', 'model': 'mock'}`  **(MOCK MODE: pipeline mechanics only, not model quality)**
- Embeddings: `mock-hashing-v1`
- Duration: 1.8s

| system | fact_recall | faithfulness | citation_support_rate | citation_url_validity | answer_relevance | error_rate | latency_s | tokens | cost_usd | tool_calls |
|---|---|---|---|---|---|---|---|---|---|---|
| autonomous_agent | 0.150 | 1.000 | 0.956 | 1.000 | 0.293 | 0.000 | 0.323 | 22559 | 0.033 | 15.333 |
| direct_llm | 0.000 | — | — | — | 0.742 | 0.000 | 0.000 | 111.333 | 0.000 | 0 |
| single_pass_rag | 0.167 | 0.333 | 0.333 | 1.000 | 0.313 | 0.000 | 0.037 | 539.667 | 0.001 | 2.667 |

Agent metrics:

| system | task_completion_rate | failed_task_rate | research_rounds | tool_call_success_rate |
|---|---|---|---|---|
| autonomous_agent | 1.000 | 0.000 | 1.667 | 0.959 |
