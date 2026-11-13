# Security

## Threat model

SCOUT AI reads untrusted content (arbitrary web pages, user uploads) and feeds it to an LLM that controls a
research workflow. The main risks: prompt injection, SSRF through fetching, malicious uploads, secret
leakage, cost abuse, and XSS through rendered reports.

## Controls

| Risk | Control | Where |
|---|---|---|
| Secrets | Only from env / `.env` (git-ignored); `SecretStr` in settings; structlog processor redacts `sk-…`, `tvly-…`, bearer tokens and secret-named keys | `core/config.py`, `core/logging.py` |
| Prompt injection | All retrieved/uploaded text wrapped in `<untrusted_passage>` (closing tags escaped); every system prompt states passages are data and must never be followed; heuristic detector flags injection-like text (stored, shown in UI, listed in Limitations) | `llm/prompts.py`, `retrieval/safety.py` |
| Injection to actions | The model has **no** free tool access: our code runs search with planner queries and fetches only URLs returned by the search provider (never URLs found in page text). There are no consequential external actions (no email, posting, purchases, code execution). Any such feature would need explicit user approval | `agents/researcher.py` |
| Fabricated evidence | Quotes must exist verbatim in sources; invalid citation labels stripped; references built from the DB; numeric consistency checks | researcher, verifier, `rag/citations.py` |
| SSRF | http/https only, no credentials in URL, ports 80/443/8080/8443, blocks `localhost`/`.internal`/`.local`; DNS resolved and **all** addresses must be globally routable (blocks private, loopback, link-local incl. 169.254.169.254, IPv4-mapped IPv6); re-checked on every redirect (manual redirect handling, max 5) | `retrieval/url_safety.py`, `retrieval/fetcher.py` |
| Resource abuse (fetch) | 15 s timeout, 8 MB streaming cap, content-type allow-list | fetcher |
| Uploads | Extension allow-list (.pdf .txt .md .html); size cap; PDF magic-byte check; binary check for text types; parsed in memory with pypdf/trafilatura; raw files not stored; nothing executed; PDFs limited to 200 pages | `api/documents.py` |
| Input validation | Pydantic models with length limits on all bodies; UUID path params | `schemas/api.py` |
| Rate limiting | Sliding window per IP on mutating `/api` requests (429 + `Retry-After`) | `core/rate_limit.py` |
| Cost abuse | Per-job token, cost, tool-call and time budgets; bounded concurrent jobs | `orchestration/depth.py`, `llm/usage.py` |
| XSS | React escapes by default; react-markdown renders no raw HTML and sanitises link protocols; HTML export uses markdown-it with `html=False`; links `rel="noopener noreferrer nofollow"`; nginx sets `nosniff`, `X-Frame-Options: DENY` | frontend, `services/export.py`, `nginx.conf` |
| Errors | Domain errors map to safe messages; unhandled exceptions return a generic 500, details only in logs | `main.py` |
| DB integrity | Transactions per unit of work (`session_scope` commits/rolls back); unique constraints for dedupe | `database/session.py` |
| Container | Backend runs as non-root user; DB password via env | `docker/` |

## Known residual risks

- **DNS rebinding:** we resolve and validate before each request, but httpx resolves again when connecting. A hostile
  DNS server could return a public IP to us and a private one to httpx. Mitigation for hardened deployments: egress
  firewall / proxy that blocks private ranges, or pin the connection to the validated IP.
- **Heuristic injection detection** can be evaded. It is defence in depth; the architectural controls above are the real protection.
- **No authentication.** Expose only on trusted networks or behind an authenticating proxy.
- **In-memory rate limiter** is per process; use a shared store (e.g. Redis) behind multiple replicas.
