"""Typed errors of the SDK.

There is one class per error code of the Portal's SDK API (docs/CONTRACTS.md,
section 9) and of the storage service (section 7.1). All of them derive from
:class:`EfpError`, which carries ``code``, ``message`` and ``status``. Catch
the class you can do something about and ``EfpError`` for the rest::

    try:
        answer = llm.complete("Hello")
    except BudgetExceededError as exc:
        show(exc.user_message)          # a sentence that is safe to show a visitor
    except EfpError as exc:
        log.warning("%s: %s", exc.code, exc.message)
        show(exc.user_message)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Mapping, Optional, Type


class EfpError(Exception):
    """Base of every error the SDK raises.

    ``code``     the machine-readable code (``budget_exceeded``, ``invalid_key`` ...)
    ``message``  the detail, written for the developer
    ``status``   the HTTP status that goes with the error: the one the Portal
                 answered with, or the status the contract gives the code when
                 the error was found before the call or arrived inside a
                 stream; ``None`` for errors without one (configuration,
                 connection, storage)
    """

    code: str = "error"
    default_status: Optional[int] = None
    _user_message: str = "Something went wrong. Please try again."

    def __init__(self, message: Optional[str] = None, *, code: Optional[str] = None, status: Optional[int] = None):
        self.code = code or type(self).code
        self.message = message or type(self)._user_message
        self.status = status if status is not None else type(self).default_status
        super().__init__(self.message)

    @property
    def user_message(self) -> str:
        """A short sentence that can be shown to a visitor as it is."""
        return type(self)._user_message

    def __str__(self) -> str:
        return "%s: %s" % (self.code, self.message)

    def __repr__(self) -> str:
        return "%s(code=%r, status=%r, message=%r)" % (type(self).__name__, self.code, self.status, self.message)


class _RetryAfter(EfpError):
    """An error that may say when to try again (the ``Retry-After`` header)."""

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        code: Optional[str] = None,
        status: Optional[int] = None,
        retry_after: Optional[float] = None,
    ):
        super().__init__(message, code=code, status=status)
        #: Seconds to wait before trying again, or ``None`` when the Portal did not say.
        self.retry_after = retry_after

    @property
    def user_message(self) -> str:
        return type(self)._user_message + _wait_hint(self.retry_after)


def _wait_hint(seconds: Optional[float]) -> str:
    if not seconds or seconds <= 0:
        return ""
    if seconds < 90:
        return " Try again in about a minute."
    minutes = seconds / 60.0
    if minutes < 90:
        return " Try again in about %d minutes." % round(minutes)
    hours = int(round(minutes / 60.0))
    return " Try again in about %d hours." % hours


# --- SDK-side errors ---------------------------------------------------------


class ConfigurationError(EfpError):
    """A setting the SDK needs is missing or malformed; the message names it."""

    code = "not_configured"
    _user_message = "This app is not set up yet. Its owner needs to finish the configuration."


class PortalUnreachableError(EfpError):
    """The Portal could not be reached (DNS, connection, TLS)."""

    code = "portal_unreachable"
    _user_message = "The app cannot reach the EFP Portal right now. Please try again in a moment."


class PortalTimeoutError(PortalUnreachableError):
    """The Portal did not answer in time."""

    code = "portal_timeout"
    _user_message = "The EFP Portal took too long to answer. Please try again."


class UnexpectedResponseError(EfpError):
    """The Portal answered with something that is not part of the contract."""

    code = "unexpected_response"
    _user_message = "The EFP Portal gave an unexpected answer. Please try again."


class StreamInterruptedError(EfpError):
    """A streamed answer ended before its final event."""

    code = "stream_interrupted"
    _user_message = "The answer was cut off. Please try again."


# --- Portal SDK API (section 9) ----------------------------------------------


class InvalidRequestError(EfpError):
    code = "invalid_request"
    default_status = 400
    _user_message = "The app sent a request the EFP Portal could not accept."


class ModelNotAllowedError(EfpError):
    code = "model_not_allowed"
    default_status = 400
    _user_message = "The requested model is not available to this app."


class InvalidPathError(EfpError):
    code = "invalid_path"
    default_status = 400
    _user_message = "The app asked for an address that is not allowed."


class InvalidTokenError(EfpError):
    code = "invalid_token"
    default_status = 401
    _user_message = "This app is not connected to the EFP Portal: its token is missing or no longer valid."


class AppDisabledError(EfpError):
    code = "app_disabled"
    default_status = 403
    _user_message = "This app has been disabled."


class LlmDisabledError(EfpError):
    code = "llm_disabled"
    default_status = 403
    _user_message = "The owner has not turned on the AI model for this app."


class ConnectorDisabledError(EfpError):
    code = "connector_disabled"
    default_status = 403
    _user_message = "The owner has not turned on this connector for the app."


class MethodNotAllowedError(EfpError):
    code = "method_not_allowed"
    default_status = 403
    _user_message = "This app is only allowed to read from that system."


class ProviderAuthFailedError(EfpError):
    code = "provider_auth_failed"
    default_status = 424
    _user_message = "The owner's sign-in to the AI provider has expired. The owner needs to reconnect it in the Portal."


class ConnectorNotConfiguredError(EfpError):
    code = "connector_not_configured"
    default_status = 424
    _user_message = "The owner has not set up this connector in the Portal."


class RateLimitedError(_RetryAfter):
    code = "rate_limited"
    default_status = 429
    _user_message = "Too many requests right now. Please wait a moment and try again."

    @property
    def user_message(self) -> str:
        return type(self)._user_message


class BudgetExceededError(_RetryAfter):
    """The app's daily budget is used up. ``retry_after`` is the wait in seconds."""

    code = "budget_exceeded"
    default_status = 429
    _user_message = "This app has used up its AI budget for today."


class ProviderError(EfpError):
    code = "provider_error"
    default_status = 502
    _user_message = "The AI provider returned an error. Please try again."


class UpstreamError(EfpError):
    code = "upstream_error"
    default_status = 502
    _user_message = "The other system returned an error. Please try again."


class UpstreamTimeoutError(EfpError):
    code = "upstream_timeout"
    default_status = 504
    _user_message = "The other system took too long to answer. Please try again."


PORTAL_ERRORS: Dict[str, Type[EfpError]] = {
    cls.code: cls
    for cls in (
        InvalidRequestError,
        ModelNotAllowedError,
        InvalidPathError,
        InvalidTokenError,
        AppDisabledError,
        LlmDisabledError,
        ConnectorDisabledError,
        MethodNotAllowedError,
        ProviderAuthFailedError,
        ConnectorNotConfiguredError,
        RateLimitedError,
        BudgetExceededError,
        ProviderError,
        UpstreamError,
        UpstreamTimeoutError,
    )
}


# --- Storage (section 7.1) ---------------------------------------------------


class StorageError(EfpError):
    """Base of the storage errors."""

    code = "storage_error"
    _user_message = "The app could not read or save its data. Please try again."


class StorageNotFoundError(StorageError):
    code = "not_found"
    _user_message = "That item does not exist."


class InvalidKeyError(StorageError):
    code = "invalid_key"


class TooLargeError(StorageError):
    code = "too_large"
    _user_message = "That is too large to save."


class QuotaExceededError(StorageError):
    code = "quota_exceeded"
    _user_message = "This app has run out of storage space."


class StorageUnavailableError(StorageError):
    """No storage backend can be used in this environment."""

    code = "storage_unavailable"


class DataDirUnavailableError(StorageError):
    """``storage.data_dir()`` was called where apps have no directory of their own."""

    code = "data_dir_unavailable"


STORAGE_ERRORS: Dict[str, Type[StorageError]] = {
    cls.code: cls for cls in (StorageNotFoundError, InvalidKeyError, TooLargeError, QuotaExceededError)
}


# --- Mapping -----------------------------------------------------------------


def for_code(
    code: str,
    message: Optional[str] = None,
    *,
    status: Optional[int] = None,
    retry_after: Optional[float] = None,
) -> EfpError:
    """The exception for an error code of the SDK API; unknown codes give ``EfpError``."""
    cls = PORTAL_ERRORS.get(code)
    if cls is None:
        return EfpError(message, code=code, status=status)
    if issubclass(cls, _RetryAfter):
        return cls(message, status=status, retry_after=retry_after)
    return cls(message, status=status)


def for_storage_code(code: str, message: Optional[str] = None) -> StorageError:
    """The exception for an error code of the storage service."""
    cls = STORAGE_ERRORS.get(code)
    if cls is None:
        return StorageError(message, code=code or StorageError.code)
    return cls(message)


def parse_retry_after(headers: Optional[Mapping[str, Any]], *, now: Optional[datetime] = None) -> Optional[float]:
    """Seconds from a ``Retry-After`` header (a number or an HTTP date), else ``None``."""
    if not headers:
        return None
    value = None
    try:
        value = headers.get("retry-after") or headers.get("Retry-After")
    except Exception:
        value = None
    if value is None:
        try:
            for name, item in headers.items():
                if str(name).lower() == "retry-after":
                    value = item
                    break
        except Exception:
            return None
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - (now or datetime.now(timezone.utc))).total_seconds()
    if seconds != seconds or seconds in (float("inf"), float("-inf")):
        return None
    return max(0.0, seconds)


def from_response(status: int, body: Any, headers: Optional[Mapping[str, Any]] = None) -> EfpError:
    """The exception for a failed response: the error envelope when there is one."""
    payload = body
    if isinstance(body, (bytes, bytearray)):
        try:
            payload = json.loads(bytes(body).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            payload = None
    elif isinstance(body, str):
        try:
            payload = json.loads(body)
        except ValueError:
            payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        code = error.get("code")
        if isinstance(code, str) and code:
            message = error.get("message")
            return for_code(
                code,
                message if isinstance(message, str) and message else None,
                status=status,
                retry_after=parse_retry_after(headers),
            )
    return UnexpectedResponseError(
        "The Portal answered HTTP %d without the error body of the SDK API. Check EFP_PORTAL_URL." % status,
        status=status,
    )
