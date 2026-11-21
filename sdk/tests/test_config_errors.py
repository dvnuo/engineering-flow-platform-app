"""Settings from the environment (section 8) and the typed errors (section 9)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from efp_app import config, errors
from conftest import PORTAL, TOKEN


# --- config ---------------------------------------------------------------------


def test_a_developers_machine_has_no_app_id(credentials):
    settings = config.load()
    assert settings.token == TOKEN
    assert settings.portal_url == PORTAL
    assert settings.deployed is False
    assert settings.base_path == ""
    assert config.is_deployed() is False


def test_deployed_means_the_app_id_is_set():
    env = {
        "EFP_APP_ID": "0b0f3a4e-0000-4000-8000-000000000001",
        "EFP_APP_SLUG": "mindmap",
        "EFP_APP_TOKEN": TOKEN,
        "EFP_APP_BASE_PATH": "/mindmap/",
        "EFP_APP_PUBLIC_URL": "https://app.example.com/mindmap/",
        "EFP_PORTAL_URL": "http://portal.efp.svc:8000/",
        "EFP_VISITOR_KEY": "a2V5",
        "EFP_APP_DATA_DIR": "/data",
        "PORT": "20003",
    }
    settings = config.load(env)
    assert settings.deployed is True
    assert settings.slug == "mindmap"
    assert settings.base_path == "/mindmap"
    assert settings.portal_url == "http://portal.efp.svc:8000"
    assert settings.data_dir == "/data"
    assert settings.storage_socket is None
    assert settings.port == 20003
    assert config.is_deployed(env) is True


@pytest.mark.parametrize("value, expected", [("", ""), ("/", ""), ("mindmap", "/mindmap"), ("/a/b/", "/a/b")])
def test_base_path_has_a_leading_slash_and_no_trailing_one(value, expected):
    assert config.load({"EFP_APP_BASE_PATH": value}).base_path == expected


@pytest.mark.parametrize("value", ["", "abc", "0", "70000"])
def test_an_unusable_port_is_no_port(value):
    assert config.load({"PORT": value}).port is None


def test_a_missing_token_names_the_variable():
    with pytest.raises(errors.ConfigurationError) as caught:
        config.load({"EFP_PORTAL_URL": PORTAL}).require_token()
    assert "EFP_APP_TOKEN" in caught.value.message
    assert ".env" in caught.value.message
    assert caught.value.code == "not_configured"
    assert caught.value.status is None


def test_a_missing_token_on_the_host_points_at_the_host():
    with pytest.raises(errors.ConfigurationError) as caught:
        config.load({"EFP_APP_ID": "x"}).require_token()
    assert "EFP_APP_TOKEN" in caught.value.message
    assert "redeploy" in caught.value.message


def test_a_missing_portal_url_names_the_variable():
    with pytest.raises(errors.ConfigurationError) as caught:
        config.load({"EFP_APP_TOKEN": TOKEN}).require_portal_url()
    assert "EFP_PORTAL_URL" in caught.value.message


def test_a_portal_url_without_a_scheme_is_refused():
    with pytest.raises(errors.ConfigurationError) as caught:
        config.load({"EFP_PORTAL_URL": "efp.example.com"}).require_portal_url()
    assert "EFP_PORTAL_URL" in caught.value.message


def test_secrets_stay_out_of_repr():
    settings = config.load({"EFP_APP_TOKEN": TOKEN, "EFP_VISITOR_KEY": "c2VjcmV0LWtleQ"})
    assert TOKEN not in repr(settings)
    assert "c2VjcmV0LWtleQ" not in repr(settings)


# --- errors ---------------------------------------------------------------------

SECTION_9 = [
    (400, "invalid_request", errors.InvalidRequestError),
    (400, "model_not_allowed", errors.ModelNotAllowedError),
    (400, "invalid_path", errors.InvalidPathError),
    (401, "invalid_token", errors.InvalidTokenError),
    (403, "app_disabled", errors.AppDisabledError),
    (403, "llm_disabled", errors.LlmDisabledError),
    (403, "connector_disabled", errors.ConnectorDisabledError),
    (403, "method_not_allowed", errors.MethodNotAllowedError),
    (424, "provider_auth_failed", errors.ProviderAuthFailedError),
    (424, "connector_not_configured", errors.ConnectorNotConfiguredError),
    (429, "rate_limited", errors.RateLimitedError),
    (429, "budget_exceeded", errors.BudgetExceededError),
    (502, "provider_error", errors.ProviderError),
    (502, "upstream_error", errors.UpstreamError),
    (504, "upstream_timeout", errors.UpstreamTimeoutError),
]


def test_every_code_of_section_9_has_its_own_class():
    assert sorted(errors.PORTAL_ERRORS) == sorted(code for _status, code, _cls in SECTION_9)
    assert len({cls for _status, _code, cls in SECTION_9}) == len(SECTION_9)


@pytest.mark.parametrize("status, code, cls", SECTION_9)
def test_the_envelope_becomes_the_typed_error(status, code, cls):
    body = ('{"error": {"code": "%s", "message": "the detail"}}' % code).encode("utf-8")
    error = errors.from_response(status, body, {})
    assert type(error) is cls
    assert isinstance(error, errors.EfpError)
    assert (error.code, error.message, error.status) == (code, "the detail", status)
    assert cls.default_status == status
    assert str(error) == "%s: the detail" % code
    assert error.user_message and "the detail" not in error.user_message


def test_budget_exceeded_carries_retry_after():
    body = {"error": {"code": "budget_exceeded", "message": "daily budget used up"}}
    error = errors.from_response(429, body, {"Retry-After": "7200"})
    assert isinstance(error, errors.BudgetExceededError)
    assert error.retry_after == 7200.0
    assert "about 2 hours" in error.user_message


def test_retry_after_is_none_when_the_portal_does_not_say():
    error = errors.from_response(429, {"error": {"code": "budget_exceeded", "message": "x"}}, {})
    assert error.retry_after is None
    assert error.user_message == "This app has used up its AI budget for today."


def test_rate_limited_has_retry_after_too():
    error = errors.from_response(429, {"error": {"code": "rate_limited", "message": "x"}}, {"retry-after": "3"})
    assert isinstance(error, errors.RateLimitedError)
    assert error.retry_after == 3.0


def test_retry_after_as_a_date():
    now = datetime(2026, 11, 15, 8, 0, 0, tzinfo=timezone.utc)
    headers = {"Retry-After": "Sun, 15 Nov 2026 08:30:00 GMT"}
    assert errors.parse_retry_after(headers, now=now) == 1800.0
    assert errors.parse_retry_after({"Retry-After": "soon"}) is None
    assert errors.parse_retry_after({"Retry-After": "-5"}) == 0.0
    assert errors.parse_retry_after(None) is None


@pytest.mark.parametrize("seconds, text", [(30, "about a minute"), (600, "about 10 minutes"), (3 * 3600, "about 3 hours")])
def test_the_wait_is_put_in_words(seconds, text):
    assert text in errors.BudgetExceededError("x", retry_after=seconds).user_message


def test_an_unknown_code_is_a_plain_efp_error():
    error = errors.from_response(418, {"error": {"code": "teapot", "message": "short and stout"}})
    assert type(error) is errors.EfpError
    assert (error.code, error.message, error.status) == ("teapot", "short and stout", 418)


@pytest.mark.parametrize("body", [b"<html>Bad gateway</html>", b"", b'{"detail": "Not Found"}', b'{"error": "text"}', b"[1]"])
def test_a_body_without_the_envelope_is_an_unexpected_response(body):
    error = errors.from_response(502, body)
    assert isinstance(error, errors.UnexpectedResponseError)
    assert error.status == 502
    assert "EFP_PORTAL_URL" in error.message


def test_a_code_without_a_message_still_has_text():
    error = errors.for_code("llm_disabled")
    assert isinstance(error, errors.LlmDisabledError)
    assert error.message
    assert error.status == 403


def test_storage_codes_have_classes():
    assert isinstance(errors.for_storage_code("not_found", "x"), errors.StorageNotFoundError)
    assert isinstance(errors.for_storage_code("invalid_key", "x"), errors.InvalidKeyError)
    assert isinstance(errors.for_storage_code("too_large", "x"), errors.TooLargeError)
    assert isinstance(errors.for_storage_code("quota_exceeded", "x"), errors.QuotaExceededError)
    other = errors.for_storage_code("bad_request", "x")
    assert type(other) is errors.StorageError and other.code == "bad_request"
    assert all(issubclass(cls, errors.StorageError) for cls in errors.STORAGE_ERRORS.values())
    assert issubclass(errors.StorageError, errors.EfpError)
