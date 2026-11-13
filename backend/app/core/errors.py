"""Domain exceptions. API layer maps these to HTTP responses."""


class ScoutError(Exception):
    """Base class for expected, user-presentable errors."""

    status_code = 400
    code = "scout_error"


class NotFoundError(ScoutError):
    status_code = 404
    code = "not_found"


class ConflictError(ScoutError):
    status_code = 409
    code = "conflict"


class ValidationFailed(ScoutError):
    status_code = 422
    code = "validation_failed"


class UnsafeURLError(ScoutError):
    code = "unsafe_url"


class FetchError(ScoutError):
    status_code = 502
    code = "fetch_failed"


class ExtractionError(ScoutError):
    code = "extraction_failed"


class ProviderError(ScoutError):
    """An external provider (LLM, embeddings, search) failed after retries."""

    status_code = 502
    code = "provider_error"


class BudgetExceeded(ScoutError):
    """An execution limit (tokens, cost, tool calls, time) was reached."""

    code = "budget_exceeded"

    def __init__(self, limit: str, detail: str = ""):
        self.limit = limit
        super().__init__(f"Execution limit reached: {limit}. {detail}".strip())


class JobCancelled(ScoutError):
    code = "cancelled"
