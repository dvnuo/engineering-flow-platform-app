"""The manager API for the Portal (docs/CONTRACTS.md, section 4).

Every request carries ``Authorization: Bearer <host API token>``. Every error
is ``{"error": {"code": "...", "message": "..."}}``, also the ones FastAPI
would word differently (a malformed body, an unknown path below ``/v1``).
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.routing import APIRoute
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from .applog import MAX_TAIL_LINES
from .deploy import DeployInProgress
from .jobs import is_job_id
from .manifest import is_safe_relative_path
from .models import VISIBILITIES, is_valid_app_id, is_valid_slug
from .registry import AppNotFound, SlugTaken
from .runtime import AppBusy, HostRuntime, ReleaseNotFound
from .state import HostState

logger = logging.getLogger(__name__)

MAX_BUNDLE_BYTES = 200 * 1024 * 1024
MAX_SPEC_BYTES = 64 * 1024
# The whole request of a deployment: the bundle, the spec and what
# multipart/form-data adds around them.
MAX_RELEASE_BODY_BYTES = MAX_BUNDLE_BYTES + MAX_SPEC_BYTES + 64 * 1024
MAX_ENV_VARIABLES = 200
MAX_ENV_VALUE_CHARS = 32 * 1024
_UPLOAD_CHUNK = 1024 * 1024
# Used with fullmatch: "$" would let a value end in a line break.
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


class EnvelopeRoute(APIRoute):
    """Checks the bearer token and turns every failure into the error envelope."""

    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def enveloped(request: Request) -> Response:
            try:
                authorize(request)
                return await handler(request)
            except ApiError as exc:
                return error_response(exc.status, exc.code, exc.message)
            except AppNotFound:
                return error_response(404, "app_not_found", "no app with this id is registered on the host")
            except RequestValidationError as exc:
                return error_response(400, "invalid_request", "; ".join(str(item.get("msg")) for item in exc.errors())[:500])
            except StarletteHTTPException as exc:
                return error_response(exc.status_code, "invalid_request" if exc.status_code < 500 else "internal_error", str(exc.detail))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the Portal must get the envelope, the log gets the trace
                logger.exception("%s %s failed", request.method, request.url.path)
                return error_response(500, "internal_error", "the host could not handle the request; see its log")

        return enveloped


def authorize(request: Request) -> None:
    state: HostState = request.app.state.host
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    expected = state.settings.api_token
    if scheme.lower() != "bearer" or not expected or not hmac.compare_digest(token.strip().encode("utf-8"), expected.encode("utf-8")):
        raise ApiError(401, "unauthorized", "a valid host API token is required")


router = APIRouter(prefix="/v1", route_class=EnvelopeRoute)


# ---- helpers ---------------------------------------------------------------------


def _runtime(request: Request) -> HostRuntime:
    runtime = request.app.state.host.runtime
    if not isinstance(runtime, HostRuntime):
        raise ApiError(503, "runtime_unavailable", "this manager runs without an app runtime")
    return runtime


def _app_id(app_id: str) -> str:
    if not is_valid_app_id(app_id):
        raise ApiError(400, "invalid_request", "the app id may hold letters, digits, - and _ (at most 64 characters)")
    return app_id


def _limit_body(request: Request, limit: int, message: str) -> None:
    """Refuse a body over ``limit`` bytes before it is stored anywhere.

    The form parser writes an uploaded file to the local disk as it
    arrives. A body that announces its length is refused at once; one that
    does not (chunked) is counted as it is received and cut off there.
    """

    announced = request.headers.get("content-length") or ""
    if announced.isdigit() and int(announced) > limit:
        raise ApiError(413, "bundle_too_large", message)
    receive = request._receive  # noqa: SLF001 - the only place the parser's input can be counted
    received = 0

    async def counted() -> Any:
        nonlocal received
        message_in = await receive()
        if message_in.get("type") == "http.request":
            received += len(message_in.get("body") or b"")
            if received > limit:
                raise ApiError(413, "bundle_too_large", message)
        return message_in

    request._receive = counted  # noqa: SLF001


async def _json_object(request: Request, *, required: bool = True) -> dict[str, Any]:
    raw = await request.body()
    if not raw.strip():
        if required:
            raise ApiError(400, "invalid_request", "a JSON body is required")
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        raise ApiError(400, "invalid_request", "the body is not valid JSON") from None
    if not isinstance(data, dict):
        raise ApiError(400, "invalid_request", "the body must be a JSON object")
    return data


def _env(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > MAX_ENV_VARIABLES:
        raise ApiError(400, "invalid_request", f"env must be an object of at most {MAX_ENV_VARIABLES} variables")
    env: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not _ENV_NAME_RE.fullmatch(key):
            raise ApiError(400, "invalid_request", "an env name may hold letters, digits and _ and may not start with a digit")
        if isinstance(item, bool) or not isinstance(item, (str, int, float)):
            raise ApiError(400, "invalid_request", f"env {key}: the value must be a string")
        text = str(item)
        if "\x00" in text or len(text) > MAX_ENV_VALUE_CHARS:
            raise ApiError(400, "invalid_request", f"env {key}: the value is too long or holds a NUL character")
        env[key] = text
    return env


def _limit(limits: Any, name: str) -> int | None:
    value = limits.get(name) if isinstance(limits, dict) else None
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ApiError(400, "invalid_request", f"limits.{name} must be a positive whole number or null")
    return value


def _busy(exc: AppBusy) -> ApiError:
    del exc
    return ApiError(409, "deploy_in_progress", "the app is being deployed; try again when the deployment has ended")


# ---- 4.1 record ------------------------------------------------------------------


@router.put("/apps/{app_id}")
async def put_app(app_id: str, request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    body = await _json_object(request)
    slug = body.get("slug")
    if not isinstance(slug, str) or not is_valid_slug(slug):
        raise ApiError(400, "invalid_request", "slug is missing, malformed or reserved")
    visibility = body.get("visibility", "private")
    if visibility not in VISIBILITIES:
        raise ApiError(400, "invalid_request", "visibility must be one of " + ", ".join(VISIBILITIES))
    enabled = body.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ApiError(400, "invalid_request", "enabled must be true or false")
    reason = body.get("disabled_reason")
    if reason is not None and not isinstance(reason, str):
        raise ApiError(400, "invalid_request", "disabled_reason must be a string or null")
    visitor_key = body.get("visitor_key") or ""
    if not isinstance(visitor_key, str):
        raise ApiError(400, "invalid_request", "visitor_key must be a string")
    limits = body.get("limits") or {}
    try:
        status = await runtime.register(
            _app_id(app_id),
            slug=slug,
            env=_env(body.get("env")),
            name=str(body.get("name") or ""),
            owner_username=str(body.get("owner_username") or ""),
            visibility=visibility,
            enabled=enabled,
            disabled_reason=reason,
            visitor_key=visitor_key,
            memory_mb=_limit(limits, "memory_mb"),
            data_mb=_limit(limits, "data_mb"),
        )
    except SlugTaken:
        raise ApiError(409, "slug_taken", f"another app already uses the slug {slug!r}") from None
    # The Portal sends this when the audience, the owner or the enabled flag
    # changed: what the gateway remembered about who may open the app is stale.
    access = getattr(getattr(request.app.state.host, "gateway", None), "access", None)
    if access is not None:
        access.forget_app(app_id)
    return status.to_dict()


# ---- 4.2 deploy ------------------------------------------------------------------


async def _save_bundle(upload: UploadFile, target: Path) -> int:
    def write(handle: Any, chunk: bytes) -> None:
        handle.write(chunk)

    size = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = await asyncio.to_thread(open, target, "wb")
    try:
        while True:
            chunk = await upload.read(_UPLOAD_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BUNDLE_BYTES:
                raise ApiError(413, "bundle_too_large", f"a bundle is at most {MAX_BUNDLE_BYTES // (1024 * 1024)} MiB")
            await asyncio.to_thread(write, handle, chunk)
    finally:
        await asyncio.to_thread(handle.close)
    return size


@router.post("/apps/{app_id}/releases")
async def post_release(app_id: str, request: Request) -> JSONResponse:
    runtime = _runtime(request)
    runtime.registry.get(_app_id(app_id))
    if runtime.deployer.busy(app_id):
        raise ApiError(409, "deploy_in_progress", "another deployment of this app is queued or running")
    if "multipart/form-data" not in (request.headers.get("content-type") or ""):
        raise ApiError(400, "invalid_request", "the body must be multipart/form-data with the parts spec and bundle")
    _limit_body(request, MAX_RELEASE_BODY_BYTES, f"a bundle is at most {MAX_BUNDLE_BYTES // (1024 * 1024)} MiB")
    form = await request.form(max_files=2, max_fields=4)
    try:
        spec_part = form.get("spec")
        if isinstance(spec_part, UploadFile):
            spec_text = (await spec_part.read(MAX_SPEC_BYTES + 1)).decode("utf-8", "replace")
        else:
            spec_text = spec_part or ""
        if not spec_text or len(spec_text) > MAX_SPEC_BYTES:
            raise ApiError(400, "invalid_request", "the part spec (JSON) is missing or too long")
        try:
            spec = json.loads(spec_text)
        except ValueError:
            raise ApiError(400, "invalid_request", "the part spec is not valid JSON") from None
        if not isinstance(spec, dict):
            raise ApiError(400, "invalid_request", "the part spec must be a JSON object")
        sha = spec.get("sha")
        if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
            raise ApiError(400, "invalid_request", "spec.sha must be 40 lowercase hexadecimal characters")
        entry_dir = spec.get("entry_dir") or ""
        if not isinstance(entry_dir, str) or (entry_dir and not is_safe_relative_path(entry_dir)):
            raise ApiError(400, "invalid_request", "spec.entry_dir must be a relative path without '..'")
        bundle = form.get("bundle")
        if not isinstance(bundle, UploadFile):
            raise ApiError(400, "invalid_request", "the part bundle (a .tar.gz file) is missing")
        upload = runtime.paths.jobs_dir / f".upload-{uuid.uuid4().hex}"
        try:
            size = await _save_bundle(bundle, upload)
            if size == 0:
                raise ApiError(400, "invalid_request", "the bundle is empty")
            try:
                job = runtime.deploy(
                    app_id,
                    sha=sha,
                    bundle=upload,
                    ref=str(spec.get("ref") or "")[:200],
                    entry_dir=entry_dir,
                    requested_by=str(spec.get("requested_by") or "")[:200],
                )
            except DeployInProgress:
                raise ApiError(409, "deploy_in_progress", "another deployment of this app is queued or running") from None
        finally:
            upload.unlink(missing_ok=True)
    finally:
        await form.close()
    return JSONResponse({"job_id": job.id}, status_code=202)


# ---- 4.3 jobs --------------------------------------------------------------------


def _job(runtime: HostRuntime, job_id: str):
    job = runtime.jobs.get(job_id) if is_job_id(job_id) else None
    if job is None:
        raise ApiError(404, "job_not_found", "no deployment job has this id")
    return job


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    # In a thread: reading the log's tail waits for what is still queued for it.
    return await asyncio.to_thread(runtime.jobs.public, _job(runtime, job_id))


@router.get("/jobs/{job_id}/log")
async def get_job_log(job_id: str, request: Request, offset: int = 0) -> Response:
    runtime = _runtime(request)
    job = _job(runtime, job_id)
    data, size = await asyncio.to_thread(runtime.jobs.read_log, job.id, max(0, offset))
    return PlainTextResponse(data.decode("utf-8", "replace"), headers={"X-Log-Size": str(size)})


# ---- 4.4 life cycle --------------------------------------------------------------


@router.post("/apps/{app_id}/activate")
async def post_activate(app_id: str, request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    body = await _json_object(request)
    sha = body.get("sha")
    if not isinstance(sha, str) or not sha:
        raise ApiError(400, "invalid_request", "sha is required")
    try:
        status = await runtime.activate(_app_id(app_id), sha)
    except ReleaseNotFound:
        raise ApiError(404, "release_not_found", "the host keeps no release with this sha for the app") from None
    except AppBusy as exc:
        raise _busy(exc) from None
    return status.to_dict()


@router.post("/apps/{app_id}/start")
async def post_start(app_id: str, request: Request) -> dict[str, Any]:
    return (await _runtime(request).start(_app_id(app_id))).to_dict()


@router.post("/apps/{app_id}/stop")
async def post_stop(app_id: str, request: Request) -> dict[str, Any]:
    return (await _runtime(request).stop(_app_id(app_id), "manual")).to_dict()


@router.post("/apps/{app_id}/restart")
async def post_restart(app_id: str, request: Request) -> dict[str, Any]:
    return (await _runtime(request).restart(_app_id(app_id))).to_dict()


@router.put("/apps/{app_id}/env")
async def put_env(app_id: str, request: Request) -> dict[str, Any]:
    runtime = _runtime(request)
    body = await _json_object(request)
    if "env" not in body:
        raise ApiError(400, "invalid_request", "env is required")
    return (await runtime.set_env(_app_id(app_id), _env(body.get("env")))).to_dict()


@router.delete("/apps/{app_id}/data")
async def delete_data(app_id: str, request: Request) -> dict[str, Any]:
    return (await _runtime(request).clear_data(_app_id(app_id))).to_dict()


@router.delete("/apps/{app_id}")
async def delete_app(app_id: str, request: Request, purge_data: bool = False) -> dict[str, Any]:
    try:
        await _runtime(request).delete(_app_id(app_id), purge_data=purge_data)
    except AppBusy as exc:
        raise _busy(exc) from None
    return {"ok": True}


@router.post("/sessions/revoke")
async def post_revoke(request: Request) -> dict[str, Any]:
    state: HostState = request.app.state.host
    body = await _json_object(request)
    user_id = body.get("user_id")
    if isinstance(user_id, bool) or not isinstance(user_id, int):
        raise ApiError(400, "invalid_request", "user_id must be a whole number")
    state.registry.revoke_user(user_id)
    return {"ok": True}


# ---- 4.5 logs --------------------------------------------------------------------


@router.get("/apps/{app_id}/logs")
async def get_logs(app_id: str, request: Request, tail: int = 200) -> dict[str, Any]:
    runtime = _runtime(request)
    count = max(1, min(int(tail), MAX_TAIL_LINES))
    lines, truncated = await asyncio.to_thread(runtime.log_tail, _app_id(app_id), count)
    return {"lines": lines, "truncated": truncated}


def sse_event(line: str | None) -> str:
    """One log line as a server-sent event; ``None`` is the keepalive comment."""

    if line is None:
        return ": keepalive\n\n"
    return "data: " + json.dumps({"line": line}, ensure_ascii=True) + "\n\n"


@router.get("/apps/{app_id}/logs/stream")
async def get_logs_stream(app_id: str, request: Request, tail: int = 0) -> Response:
    runtime = _runtime(request)
    runtime.registry.get(_app_id(app_id))
    state: HostState = request.app.state.host
    limit = state.extras.get("log_stream_max_seconds")
    loop = asyncio.get_running_loop()
    until = loop.time() + float(limit) if limit else None

    async def events() -> AsyncIterator[str]:
        yield ": stream open\n\n"
        stop = (lambda: loop.time() >= until) if until is not None else None
        async for line in runtime.log_follow(app_id, tail=max(0, min(int(tail), MAX_TAIL_LINES)), stop=stop):
            yield sse_event(line)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---- 4.6 status ------------------------------------------------------------------


@router.get("/apps")
async def get_apps(request: Request) -> dict[str, Any]:
    return {"apps": [status.to_dict() for status in _runtime(request).statuses()]}


@router.get("/apps/{app_id}")
async def get_app(app_id: str, request: Request) -> dict[str, Any]:
    return _runtime(request).status(_app_id(app_id)).to_dict()


@router.get("/host")
async def get_host(request: Request) -> dict[str, Any]:
    runtime = request.app.state.host.runtime
    return await asyncio.to_thread(runtime.host_info)


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"], include_in_schema=False)
async def unknown(path: str) -> Response:
    raise ApiError(404, "not_found", f"the manager API has no /v1/{path}")
