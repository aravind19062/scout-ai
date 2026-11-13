# Evaluation: 20261106T093319-retrieval-retrieval_fixture

- Providers: `{'llm': 'mock', 'embeddings': 'mock', 'search': 'mock', 'model': 'mock'}`  **(MOCK MODE: pipeline mechanics only, not model quality)**
- Embeddings: `mock-hashing-v1`
- Duration: 0.7s

Retrieval over 15 labelled queries (document-level, unique docs in rank order):

| system | P@1 | P@3 | P@5 | R@1 | R@3 | R@5 | MRR | nDCG@5 |
|---|---|---|---|---|---|---|---|---|
| hybrid | 0.867 | 0.400 | 0.253 | 0.756 | 0.967 | 1.000 | 0.933 | 0.943 |
| keyword | 0.800 | 0.356 | 0.253 | 0.722 | 0.867 | 1.000 | 0.867 | 0.904 |
| vector | 0.933 | 0.422 | 0.253 | 0.822 | 1.000 | 1.000 | 0.956 | 0.967 |
