import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import anyio
import httpx
from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    Retrying,
    retry_if_exception,
    retry_if_result,
    stop_after_attempt,
)

from .errors import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    MonthlySessionLimitError,
    NotFoundError,
    PaymentRequiredError,
    PremiumTrafficLimitError,
    RateLimitError,
    ServerError,
    SharedTrafficLimitError,
)

logger = logging.getLogger("surfsky")

AUTH_HEADER = "X-Cloud-Api-Token"
MAX_BACKOFF = 30.0

STATUS_ERRORS: dict[int, type[APIError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    402: PaymentRequiredError,
    403: ForbiddenError,
    404: NotFoundError,
    409: ConflictError,
    422: BadRequestError,
    429: RateLimitError,
}

# 429s with these codes are exhausted quotas, not rate limits: never retried
QUOTA_ERRORS: dict[str, type[APIError]] = {
    "shared_traffic_limit_reached": SharedTrafficLimitError,
    "premium_traffic_limit_reached": PremiumTrafficLimitError,
    "monthly_session_limit_reached": MonthlySessionLimitError,
}
# The plan's parallel-browser cap: a 429 the pool treats as backpressure
PLAN_FULL = "parallel_browsers_limit_reached"
NO_RETRY_CODES = frozenset(QUOTA_ERRORS) | {PLAN_FULL}


@dataclass
class Spec[T]:
    method: str
    path: str
    json: Any = None
    params: dict[str, Any] | None = None
    files: Any = None
    data: dict[str, Any] | None = None
    parse: Callable[[Any], T] = lambda data: data
    # an error status this call treats as a normal reply (e.g. batch delete partials)
    accept_error: Callable[[int, Any], bool] | None = None
    timeout: float | None = None
    shield: bool = False

    def result(self, response: httpx.Response) -> T:
        status, body = response.status_code, parse_body(response)
        if status >= 400 and not (self.accept_error and self.accept_error(status, body)):
            raise api_error(response, body)
        data = body
        if isinstance(body, dict) and "data" in body:  # {success, msg, data}
            if body.get("success") is False and self.accept_error is None:
                raise APIError(str(body.get("msg") or "request failed"), body=body)
            data = body["data"]
        try:
            return self.parse(data)
        except (ValidationError, TypeError, AttributeError, LookupError) as exc:
            raise APIError(
                f"unexpected response from {self.method} {self.path}: {exc}",
                status_code=status,
                body=body,
                request_id=request_id(response),
                headers=dict(response.headers),
            ) from exc


def dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_none=True, by_alias=True)


def list_of[M: BaseModel](model: type[M]) -> Callable[[Any], list[M]]:
    return lambda data: [model.model_validate(item) for item in data or []]


def send(
    http: httpx.Client,
    spec: Spec[Any],
    *,
    retries: int,
    backoff: float,
    timeout: float | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    def attempt() -> httpx.Response:
        return http.request(
            spec.method,
            spec.path,
            json=spec.json,
            params=spec.params,
            files=spec.files,
            data=spec.data,
            headers=headers,
            timeout=httpx.USE_CLIENT_DEFAULT if timeout is None else timeout,
        )

    try:
        return Retrying(**retry_policy(spec, retries, backoff))(attempt)
    except httpx.TransportError as exc:
        raise network_error(spec.path, exc) from exc


async def asend(
    http: httpx.AsyncClient,
    spec: Spec[Any],
    *,
    retries: int,
    backoff: float,
    timeout: float | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    async def attempt() -> httpx.Response:
        with anyio.CancelScope(shield=spec.shield):
            return await http.request(
                spec.method,
                spec.path,
                json=spec.json,
                params=spec.params,
                files=spec.files,
                data=spec.data,
                headers=headers,
                timeout=httpx.USE_CLIENT_DEFAULT if timeout is None else timeout,
            )

    policy = retry_policy(spec, retries, backoff)
    try:
        return await AsyncRetrying(sleep=anyio.sleep, **policy)(attempt)
    except httpx.TransportError as exc:
        raise network_error(spec.path, exc) from exc


def retry_policy(spec: Spec[Any], retries: int, backoff: float) -> dict[str, Any]:
    idempotent = spec.method.upper() in {"GET", "HEAD", "PUT", "DELETE", "OPTIONS"}
    never_sent = (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.PoolTimeout,
        httpx.ProxyError,
    )

    def retry_response(response: httpx.Response) -> bool:
        if response.status_code == 429:
            return not never_retried(parse_body(response))
        return idempotent and response.status_code in {500, 502, 503, 504}

    def retry_exception(exc: BaseException) -> bool:
        if isinstance(exc, never_sent):
            return True
        return idempotent and isinstance(exc, httpx.TransportError)

    def wait(state: RetryCallState) -> float:
        if state.outcome is not None and not state.outcome.failed:
            after = retry_after_seconds(state.outcome.result())
            if after is not None:
                return min(after, MAX_BACKOFF)
        delay = min(backoff * 2 ** (state.attempt_number - 1), MAX_BACKOFF)
        return delay + random.uniform(0, backoff)

    def log(state: RetryCallState) -> None:
        outcome, action = state.outcome, state.next_action
        if outcome is None or action is None:
            return
        got = outcome.exception() if outcome.failed else outcome.result().status_code
        logger.warning(
            "%s %s -> %s, retry in %.1fs", spec.method, spec.path, got, action.sleep
        )

    return {
        "retry": retry_if_result(retry_response) | retry_if_exception(retry_exception),
        "stop": stop_after_attempt(retries + 1),
        "wait": wait,
        "before_sleep": log,
        "retry_error_callback": lambda state: state.outcome.result(),
    }


def parse_body(response: httpx.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text


def never_retried(body: Any) -> bool:
    return isinstance(body, dict) and body.get("code") in NO_RETRY_CODES


def retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After", "").strip()
    if not value:
        return None
    if value.isdecimal():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)  # -0000 parses naive and means UTC
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def request_id(response: httpx.Response) -> str | None:
    return response.headers.get("cf-ray")


def token_rejected(body: Any) -> bool:
    data = body.get("data") if isinstance(body, dict) else None
    errors = data.get("errors") if isinstance(data, dict) else None
    return isinstance(errors, list) and any(
        isinstance(error, dict)
        and any(str(part).lower() == AUTH_HEADER.lower() for part in error.get("loc", ()))
        for error in errors
    )


def ref(value: str) -> str:
    if not value:
        raise ValueError("an empty id would address a different endpoint")
    if value in (".", ".."):
        raise ValueError(f"{value!r} is not a path segment")
    return quote(value, safe="")


def api_error(response: httpx.Response, body: Any) -> APIError:
    status = response.status_code
    message = f"HTTP {status}"
    if isinstance(body, dict):
        message = str(
            body.get("msg")
            or body.get("message")
            or body.get("detail")
            or body.get("error")
            or message
        )
        data = body.get("data")
        details = data.get("errors") if isinstance(data, dict) else body.get("errors")
        if isinstance(details, list):  # a validation error, field by field
            fields = "; ".join(
                f"{'.'.join(str(part) for part in e.get('loc', ()))}: {e.get('msg')}"
                for e in details
                if isinstance(e, dict)
            )
        elif isinstance(details, dict):
            fields = "; ".join(f"{name}: {why}" for name, why in details.items())
        else:
            fields = ""
        if fields:
            message += f": {fields}"
    elif isinstance(body, str) and body:
        message = body[:500]

    request = response.request
    message = f"{request.method} {request.url.path}: {message}"
    error = STATUS_ERRORS.get(status, ServerError if status >= 500 else APIError)
    retry_after = None
    if status == 422 and token_rejected(body):
        error = AuthenticationError
    if status == 429:
        code = body.get("code") if isinstance(body, dict) else None
        error = QUOTA_ERRORS.get(code, RateLimitError)
        if error is RateLimitError:
            retry_after = retry_after_seconds(response)
    return error(
        message,
        status_code=status,
        body=body,
        request_id=request_id(response),
        headers=dict(response.headers),
        retry_after=retry_after,
    )


def network_error(path: str, exc: httpx.HTTPError) -> APIError:
    if isinstance(exc, httpx.TimeoutException):
        return APITimeoutError(f"request to {path} timed out")
    return APIConnectionError(f"request to {path} failed: {exc}")
