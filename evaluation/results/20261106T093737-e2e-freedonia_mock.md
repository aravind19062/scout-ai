# Evaluation: 20261106T093737-e2e-freedonia_mock

- Providers: `{'llm': 'mock', 'embeddings': 'mock', 'search': 'mock', 'model': 'mock'}`  **(MOCK MODE: pipeline mechanics only, not model quality)**
- Embeddings: `mock-hashing-v1`
- Duration: 2.6s

| system | fact_recall | faithfulness | citation_support_rate | citation_url_validity | answer_relevance | error_rate | latency_s | tokens | cost_usd | tool_calls |
|---|---|---|---|---|---|---|---|---|---|---|
| autonomous_agent | 0.150 | 1.000 | 0.860 | 1.000 | 0.331 | 0.000 | 0.417 | 19272.333 | 0.027 | 15.333 |
| direct_llm | 0.000 | — | — | — | 0.742 | 0.000 | 0.017 | 0 | 0.000 | 0 |
| single_pass_rag | 0.167 | 0.333 | 0.333 | 1.000 | 0.313 | 0.000 | 0.037 | 0 | 0.000 | 2.667 |

Agent metrics:

| system | task_completion_rate | failed_task_rate | research_rounds | tool_call_success_rate |
|---|---|---|---|---|
| autonomous_agent | 1.000 | 0.000 | 1.667 | 0.959 |
