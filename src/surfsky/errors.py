from collections.abc import Mapping
from typing import Any


class SurfskyError(Exception): ...


class ConfigurationError(SurfskyError):
    """No API token, or another unusable client setting."""


class APIError(SurfskyError):
    """An error response from the API, or a request that never got one."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: Any = None,
        request_id: str | None = None,
        headers: Mapping[str, str] | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.body = body
        self.code: str | None = body.get("code") if isinstance(body, dict) else None
        self.request_id = request_id
        self.headers = headers
        self.retry_after = retry_after

    def __str__(self) -> str:
        if self.status_code is None:
            return self.message
        return f"[{self.status_code}] {self.message}"


class APIConnectionError(APIError):
    """The request never reached the API."""


class APITimeoutError(APIConnectionError):
    """No response within the timeout."""


class BadRequestError(APIError):
    """400 or 422."""


class AuthenticationError(APIError):
    """401, or a 422 that rejects the token header."""


class PaymentRequiredError(APIError):
    """402."""


class ForbiddenError(APIError):
    """403."""


class NotFoundError(APIError):
    """404."""


class ConflictError(APIError):
    """409."""


class RateLimitError(APIError):
    """429. ``retry_after`` is the server's hint in seconds."""


class SharedTrafficLimitError(APIError):
    """429 with ``shared_traffic_limit_reached``"""


class PremiumTrafficLimitError(APIError):
    """429 with ``premium_traffic_limit_reached``"""


class MonthlySessionLimitError(APIError):
    """429 with ``monthly_session_limit_reached``"""


class ServerError(APIError):
    """5xx."""


class BrowserTimeoutError(SurfskyError, TimeoutError):
    """A browser wait or command ran out of time."""


class PageClosedError(SurfskyError, RuntimeError):
    """The page is gone."""
