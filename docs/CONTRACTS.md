# Interface contracts

This file is the single source of truth for every wire format and shared name
between the four parties of the Apps feature:

- **Portal** (`engineering-flow-platform-portal`): registry of apps, access
  rules, deployment orchestration, SDK endpoints (LLM, connectors).
- **App host** (`host/` in this repo): one container that runs every app.
  Inside it: the **gateway** (nginx), the **manager** (Python service), a
  process supervisor and the app processes.
- **App**: a member's Python web app (or static site) running in the host.
- **SDK** (`sdk/` in this repo): the Python package apps import.

Change this file first, then the code. Every JSON body is UTF-8. Every
timestamp is an ISO-8601 UTC string (`2026-11-15T08:30:00Z`).

## 1. Names

| Name | Value |
|---|---|
| App id | UUID string, assigned by the Portal |
| Slug | `^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$`, unique, immutable; the URL path segment |
| Reserved slugs | `_efp`, `api`, `app`, `apps`, `admin`, `assets`, `auth`, `efp`, `health`, `login`, `logout`, `portal`, `static`, `www` |
| App URL | `{APPS_PUBLIC_URL}/{slug}/` |
| App center | `{APPS_PUBLIC_URL}/`: the apps a signed-in member may open (section 6.5) |
| Base path | `/{slug}` (no trailing slash) |
| App token | `efpa_` + 43 url-safe characters |
| Visibility | `private` (owner only), `public` (the owner's group), `platform` (every member), `anyone` (no sign-in) |
| Host API token | one shared secret, used as `Authorization: Bearer <token>` in both directions between Portal and manager |
| Gateway session cookie | `__Host-efp_apps` (HttpOnly, Secure, SameSite=Lax, Path=/) |
| Sign-in state cookie | `__Host-efp_apps_state` (same attributes, one hour; section 6.3) |
| Visitor header | `X-EFP-Visitor` |

## 2. The app manifest: `efp-app.json`

Lives in the app's entry directory.

```json
{
  "name": "Mind map",
  "description": "Draw and share mind maps.",
  "icon": "icon.png",
  "type": "fastapi",
  "entry": "main:app",
  "command": null,
  "requirements": "requirements.txt",
  "health_path": "/",
  "startup_timeout": 60,
  "strip_prefix": null,
  "owners": ["alice"]
}
```

| Field | Rule |
|---|---|
| `name` | required, 1-80 characters |
| `description` | optional, up to 500 characters |
| `icon` | optional, relative path of an image inside the entry directory |
| `type` | required: `fastapi`, `flask`, `streamlit`, `gradio`, `command`, `static` |
| `entry` | `fastapi` / `flask`: `module:attribute`. `streamlit` / `gradio`: script path. `static`: directory, default `.`. Ignored for `command` |
| `command` | required for `command`: a shell command line; may use `$PORT` and `$EFP_APP_BASE_PATH` |
| `requirements` | optional, default `requirements.txt`; a missing file means no dependencies |
| `health_path` | optional, default `/`; relative to the app's own root |
| `startup_timeout` | optional seconds, default 60, maximum 300 |
| `strip_prefix` | optional override of the per-type default (section 6.2) |
| `owners` | optional list of Portal usernames. Absent or empty: any member may register and deploy this directory. Otherwise only the listed members may (the Portal checks the app owner's username when registering and at every deployment; an app created from a published template is exempt) |

Unknown keys are ignored. Paths may not be absolute or contain `..`.

## 3. Host configuration (environment of the host container)

| Variable | Default | Meaning |
|---|---|---|
| `EFP_HOST_API_TOKEN` | required | shared secret (section 1) |
| `EFP_PORTAL_INTERNAL_URL` | required | Portal as reached from the cluster |
| `EFP_PORTAL_PUBLIC_URL` | required | Portal as reached by browsers |
| `EFP_APPS_PUBLIC_URL` | required | this host as reached by browsers |
| `EFP_HOST_ISOLATION_MODE` | `sandbox` | `sandbox` or `hidden` (section 7) |
| `EFP_HOST_SANDBOX_BACKEND` | `auto` | `auto`, `bwrap`, `bwrap-userns`, `landlock` |
| `EFP_HOST_STORE_DIR` | `/var/lib/efp-host/store` | the persistent volume |
| `EFP_HOST_RUNTIME_DIR` | `/srv/efp-apps` | container-local run directory (hidden mode) |
| `EFP_HOST_RUN_DIR` | `/run/efp-host` | sockets and generated config |
| `EFP_HOST_RESOURCE_DIR` | `/opt/efp` in the image | holds `nginx/` (the gateway template) and `pages/` |
| `EFP_HOST_ADMIN_PORT` | `9000` | manager API |
| `EFP_HOST_GATEWAY_PORT` | `8080` | nginx |
| `EFP_HOST_PORT_RANGE_START` | `20000` | first loopback port given to an app |
| `EFP_HOST_UID_RANGE_START` | `20000` | first OS uid given to an app |
| `EFP_HOST_IDLE_STOP_MINUTES` | `30` | `0` disables idle stop |
| `EFP_HOST_APP_MEMORY_MB` | `1024` | resident memory ceiling per app |
| `EFP_HOST_APP_DATA_MB` | `1024` | soft quota of an app's data |
| `EFP_HOST_APP_NPROC` | `256` | process ceiling per app |
| `EFP_HOST_SESSION_TTL_HOURS` | `168` | sliding lifetime of a gateway session |
| `EFP_HOST_ACCESS_CACHE_SECONDS` | `30` | lifetime of a cached access decision |
| `EFP_HOST_ANON_RATE_PER_MINUTE` | `120` | per source address, `anyone` apps |
| `EFP_HOST_METRICS_PUSH_SECONDS` | `300` | interval of section 5.4 |
| `EFP_HOST_INSTALL_CONCURRENCY` | `1` | deployments installing at once |
| `EFP_HOST_DEPLOY_TIMEOUT_SECONDS` | `900` | one deployment |
| `EFP_HOST_PIP_ALLOW_SDIST` | `false` | allow source distributions |
| `PIP_INDEX_URL`, `PIP_TRUSTED_HOST`, `PIP_CERT`, ... | | read by pip itself; point them at the private index |

## 4. Manager API (Portal -> host), port `EFP_HOST_ADMIN_PORT`

Every request carries `Authorization: Bearer <host API token>`; anything else
is `401 {"error": {"code": "unauthorized", "message": "..."}}`. Errors always
use that envelope. The API is never routed from the public ingress.

### 4.1 `PUT /v1/apps/{app_id}` - create or update an app's record

```json
{
  "slug": "mindmap",
  "name": "Mind map",
  "owner_username": "alice",
  "visibility": "public",
  "enabled": true,
  "disabled_reason": null,
  "visitor_key": "<base64url, 32 random bytes>",
  "env": {"EFP_APP_TOKEN": "efpa_...", "MY_SETTING": "x"},
  "limits": {"memory_mb": null, "data_mb": null}
}
```

`200` with the status object (4.6). `409 {"error":{"code":"slug_taken"}}` when
another app owns the slug. `env` replaces the stored environment; when it
changed and the app is running, the app restarts. Deploys nothing.

### 4.2 `POST /v1/apps/{app_id}/releases` - deploy

`multipart/form-data` with two parts: `spec` (JSON) and `bundle` (a `.tar.gz`,
either the entry directory itself or a GitHub tarball whose single top-level
directory is skipped).

```json
{"sha": "<40 hex>", "ref": "main", "entry_dir": "apps/mindmap", "requested_by": "alice"}
```

`202 {"job_id": "<uuid>"}`. `404 app_not_found` when 4.1 was never called.
`409 deploy_in_progress` while another deployment of the same app runs.

Steps, in order: `unpack`, `manifest`, `environment`, `dependencies`, `start`,
`health`, `activate`. On success the new release is current and the previous
one is kept for rollback (older ones are removed). On failure the running
release stays untouched.

### 4.3 `GET /v1/jobs/{job_id}`

```json
{
  "id": "...", "app_id": "...", "kind": "deploy",
  "status": "queued|running|succeeded|failed",
  "sha": "...",
  "steps": [{"name": "unpack", "status": "pending|running|succeeded|failed|skipped",
             "started_at": "...", "finished_at": "..."}],
  "error": {"step": "dependencies", "message": "..."},
  "log_tail": "last 4000 characters",
  "log_size": 1234,
  "created_at": "...", "finished_at": null
}
```

`GET /v1/jobs/{job_id}/log?offset=N` returns `text/plain` from byte `N` on,
with the header `X-Log-Size`.

### 4.4 Lifecycle

| Request | Effect |
|---|---|
| `POST /v1/apps/{id}/activate` `{"sha": "..."}` | switch to a retained release (rollback); `404 release_not_found` |
| `POST /v1/apps/{id}/start` | start now |
| `POST /v1/apps/{id}/stop` | stop; the next visit starts it again unless `enabled` is false |
| `POST /v1/apps/{id}/restart` | |
| `PUT /v1/apps/{id}/env` `{"env": {...}}` | replace the environment, restart if running |
| `DELETE /v1/apps/{id}/data` | empty the app's data |
| `DELETE /v1/apps/{id}?purge_data=true|false` | stop and remove; data survives unless purged |
| `POST /v1/sessions/revoke` `{"user_id": 12}` | end every gateway session of that user |

All return `200` with the status object, except the last two (`200 {"ok": true}`).

### 4.5 Logs

- `GET /v1/apps/{id}/logs?tail=200` -> `{"lines": ["..."], "truncated": true}`
- `GET /v1/apps/{id}/logs/stream` -> `text/event-stream`; each event is
  `data: {"line": "..."}`; a comment line `: keepalive` every 15 seconds.

### 4.6 Status

`GET /v1/apps/{id}` and each item of `GET /v1/apps` (`{"apps": [...]}`):

```json
{
  "id": "...", "slug": "mindmap", "type": "fastapi",
  "state": "running|starting|stopped|crashed|killed|failed|undeployed|disabled",
  "reason": "idle|manual|exit:1|memory|deploy|not_deployed|<disabled reason>|null",
  "release": {"sha": "...", "deployed_at": "..."},
  "previous_release": {"sha": "...", "deployed_at": "..."},
  "uptime_seconds": 120, "restarts": 0,
  "rss_bytes": 123456, "cpu_percent": 1.5, "data_bytes": 1024,
  "last_activity_at": "...", "port": 20003,
  "last_log_lines": ["only when crashed, killed or failed"]
}
```

`GET /v1/host`:

```json
{
  "version": "0.1.0", "isolation_mode": "sandbox", "sandbox_backend": "bwrap",
  "apps_total": 12, "apps_running": 5,
  "memory": {"total_bytes": 0, "used_bytes": 0},
  "cpu_percent": 3.0,
  "disk": {"store": {"total_bytes": 0, "used_bytes": 0}, "local": {"total_bytes": 0, "used_bytes": 0}},
  "started_at": "..."
}
```

## 5. Portal API for the host (host -> Portal), prefix `/api/app-host/v1`

Same bearer token. Never routed from the public ingress.

### 5.1 `POST /handoff/redeem` `{"code": "..."}`

```json
{"app_id": "...", "slug": "mindmap",
 "user": {"id": 12, "username": "bob", "display_name": "Bob", "group_id": 3, "group_name": "QA"}}
```

A code works once and for 60 seconds. `400 {"error":{"code":"invalid_code"}}`.
A code issued for the app center (section 6.5) names no app: `app_id` and
`slug` are `null`.

### 5.2 `POST /access/check` `{"app_id": "...", "user_id": 12}`

```json
{"allowed": true, "reason": null,
 "user": {"id": 12, "username": "bob", "display_name": "Bob", "group_id": 3, "group_name": "QA"}}
```

`allowed` is false with `reason` one of `no_access`, `user_inactive`,
`app_disabled`, `app_not_found`. `user` is omitted when the user is unknown.

### 5.3 `GET /apps`

The desired state, used by the host when it boots:

```json
{"apps": [{"id": "...", "slug": "...", "name": "...", "owner_username": "...",
           "visibility": "...", "enabled": true, "disabled_reason": null,
           "current_sha": "...", "visitor_key": "...", "env": {"EFP_APP_TOKEN": "..."},
           "limits": {"memory_mb": null, "data_mb": null}}]}
```

### 5.4 `POST /metrics/ingest`

Sent every `EFP_HOST_METRICS_PUSH_SECONDS`. Counters are running totals for
their UTC day, so a repeated or late post is harmless.

```json
{
  "sent_at": "...",
  "traffic": [{"day": "2026-11-15", "app_id": "...", "requests": 120,
               "errors_4xx": 3, "errors_5xx": 1, "p95_ms": 240, "visitors": 7,
               "users": [{"user_id": 12, "requests": 40, "last_at": "..."}]}],
  "apps": [<status object 4.6>],
  "host": <host object 4.6>
}
```

`visitors` is the number of distinct signed-in members that day; anonymous
visitors of an `anyone` app are counted in `requests` only. Within one day the
Portal keeps the larger value of each counter, so a host whose counters
started afresh cannot lower them. `200 {"ok": true}`.

### 5.5 `POST /events`

```json
{"app_id": "...", "kind": "crashed|killed|idle_stopped|started|quota_exceeded",
 "detail": {"exit_code": 1, "log": ["..."]}, "at": "..."}
```

### 5.6 `POST /catalog` `{"user_id": 12}`

What the app center shows a member (section 6.5):

```json
{"allowed": true, "reason": null,
 "user": {"id": 12, "username": "bob", "display_name": "Bob", "group_id": 3, "group_name": "QA"},
 "apps": [{"id": "...", "slug": "mindmap", "name": "Mind map",
           "description": "Draw and share mind maps.", "type": "fastapi",
           "visibility": "public", "mine": false,
           "owner": {"username": "alice", "display_name": "Alice"},
           "icon": "5f2c9a81d3e0", "last_visited_at": "2026-11-18T09:12:00Z"}]}
```

- `apps` holds every app the member may open that is enabled and has been
  deployed, their own included (`mine`). A system administrator gets what a
  member in their place would: the list is not the administration's.
- Order: the apps the member used, the most recent first, then the others by
  name.
- `visibility` is the audience in force (`anyone` only when it is approved).
- `icon` is a version string when the app has an icon (5.7), else `null`.
- `allowed` is false with `reason` `user_inactive` when the member is unknown
  or no longer active; `apps` is then empty and `user` is omitted.

### 5.7 `GET /apps/{app_id}/icon`

The app's icon as the last deployment read it: the bytes, with
`Content-Type` `image/png`, `image/jpeg`, `image/gif` or `image/webp`.
`404 {"error":{"code":"icon_not_found"}}` when the app has none or does not
exist. The host decides who may see it.

## 6. Gateway

### 6.1 Request handling

1. `/_efp/health` -> `200 ok`. `/_efp/callback`, `/_efp/logout` and
   `/_efp/icon/{slug}` are handled by the manager. `/_efp/lib/...` are the
   shared browser libraries (6.6). Every other path under `/_efp/` is `404`.
   `/` is the app center (6.5).
2. `/{slug}` redirects to `/{slug}/`.
3. For `/{slug}/...` the gateway decides, in this order:
   - unknown slug -> `404` page;
   - app disabled -> `403` page that states the reason;
   - visibility is not `anyone` and there is no valid session -> sign-in
     (6.3);
   - a session whose user is not allowed -> `403` page naming the owner;
   - app never deployed -> `503` page;
   - app stopped -> start it and answer `503` with the "starting" page
     (`Retry-After: 2`, the page refreshes itself);
   - otherwise proxy to the app.
4. A request that is not a page navigation (`Sec-Fetch-Mode` present and not
   `navigate`, or `Accept` without `text/html`) gets JSON instead of a page or
   a redirect: `{"error": {"code": "<code>", "message": "..."}}` with status
   `401` (`sign_in_required`), `403` (`no_access`, `app_disabled`), `404`
   (`app_not_found`), `503` (`app_starting`, `app_unavailable`).
5. Requests to an `anyone` app from a visitor without a session are limited
   per source address (`429 rate_limited`).

### 6.2 What reaches the app

- The path keeps or loses the `/{slug}` prefix by type: stripped for
  `fastapi`, `gradio`, `static`; kept for `flask`, `streamlit`, `command`.
  `strip_prefix` in the manifest overrides it.
- `Cookie` without the gateway session cookie.
- A request in which the client itself sent an `X-EFP-*` header is refused
  with `400`; `X-EFP-Visitor` (6.4) is added by the gateway when the visitor
  is signed in, so an app can trust it.
- `X-Forwarded-For`, `X-Forwarded-Proto`, `X-Forwarded-Host`,
  `X-Forwarded-Prefix: /{slug}`.
- WebSocket upgrades and streaming responses pass through unbuffered; read
  and send timeouts are 3600 seconds.

From the app's response the gateway removes `Service-Worker-Allowed`, `NEL`,
`Report-To`, `Reporting-Endpoints` and `Clear-Site-Data` (they act on the
whole hostname), ignores every `X-Accel-*` header (an app cannot redirect a
request inside the gateway), drops `Set-Cookie` headers that carry `Domain=`
or a name starting with `__Host-efp` or `__Secure-efp`, and rewrites the
remaining cookies to `Path=/{slug}/`. It
adds `X-Content-Type-Options: nosniff`, `X-Robots-Tag: noindex`,
`Referrer-Policy: same-origin` and
`Permissions-Policy: camera=(), microphone=(), geolocation=(), display-capture=()`.

### 6.3 Sign-in hand-off

1. Gateway -> browser:
   `302 {PORTAL_PUBLIC_URL}/apps/launch/{slug}?next=<url-encoded path and query>&state=<state>`,
   and a cookie `__Host-efp_apps_state=<state>` (HttpOnly, one hour). The
   state is 22 to 64 characters of `[A-Za-z0-9_-]`, random, and binds the
   sign-in to the browser that started it; a browser that already holds one
   gets the same value again, so tabs that sign in together share it.
2. Portal (after its own sign-in when needed) -> browser:
   `302 {APPS_PUBLIC_URL}/_efp/callback?code=<code>&next=<same value>&state=<same value>`,
   or its own "no access" page. The Portal issues a code only to a request
   that carries a state. Without one (a launch address somebody typed or
   kept) it answers `302 {APPS_PUBLIC_URL}<next>`, and the gateway starts
   the hand-off from step 1.
3. Manager: redeems the code (5.1) only when `state` equals the browser's
   state cookie, then sets the session cookie, clears the state cookie and
   answers `302 <next>`. `next` must start with `/{slug}/` of the redeemed
   app, otherwise the redirect goes to `/{slug}/`. A bad code answers `400`
   with a page that links back to the app. A callback whose state does not
   match is never redeemed: a browser that has a session is sent on to
   `<next>` (another tab finished the same sign-in), any other gets the same
   `400` page. Without this, a member could sign in, keep the address with
   the code, and have somebody else's browser load it: that person would
   then use every app as the member.

`GET /_efp/logout` clears the cookie and redirects to `{PORTAL_PUBLIC_URL}`.

### 6.4 Visitor header

```
X-EFP-Visitor: v1.<payload>.<signature>
payload   = base64url(no padding) of the JSON below, keys sorted, no spaces
signature = base64url(no padding) of HMAC-SHA256(key, "v1." + payload)
key       = base64url-decoded visitor_key of the app
```

```json
{"app": "<app id>", "exp": 1763200000, "group": "QA", "group_id": 3,
 "name": "Bob", "sub": 12, "username": "bob"}
```

`exp` is 60 seconds ahead. A missing header means an anonymous visitor.

### 6.5 The app center

`GET /` on the apps address lists the apps the visitor may open; a card
leads to `/{slug}/`. It is for members: it needs a session.

1. A page navigation without a valid session:
   `302 {PORTAL_PUBLIC_URL}/apps/launch?state=<state>` (no slug), with the
   state cookie of 6.3. The Portal, after its own sign-in when needed,
   answers
   `302 {APPS_PUBLIC_URL}/_efp/callback?code=<code>&next=%2F&state=<same value>`
   (without a state: `302 {APPS_PUBLIC_URL}/`). The code redeems (5.1) to a
   member and no app; the manager checks the state as in 6.3, sets the
   session cookie and answers `302 /?signed_in=1`.
2. `/?signed_in=1` still without a session means the browser did not keep the
   cookie: the "sign-in did not finish" page (`400`), never another redirect.
3. With a session the manager asks 5.6 and answers the page. Every app in the
   answer counts as an access decision (5.2) for the time such decisions are
   remembered, so a click on a card costs no further call. `allowed: false`
   ends the session (the cookie is cleared) and starts at 1.
4. The Portal cannot be asked: `503` with a page that offers to try again.
5. A request that is not a page navigation gets JSON: `401 sign_in_required`
   without a session, `404 app_not_found` with one. The list is a page, not
   an API.

`GET /_efp/icon/{slug}` answers the icon of an app (5.7) to a visitor with a
session who may open it, with `Cache-Control: private, max-age=86400`; the
page names it as `/_efp/icon/{slug}?v=<icon version>`. Anything else is
`404`, which does not tell whether the app exists.

Apps run on this same origin, so the script of an app a member has opened can
load the app center as that member. The page therefore holds nothing but
what the member may open anyway: names, descriptions, owners and addresses.

### 6.6 The shared browser libraries

`GET /_efp/lib/<name>@<version>/<file>` answers a file of the repository's
`lib/` directory, which the image holds at `{EFP_HOST_RESOURCE_DIR}/lib`:
daisyUI, Tailwind CSS (the browser build), the EFP kit, Motion,
AutoAnimate, SortableJS, canvas-confetti, ECharts, lucide and two
typefaces. `lib/MANIFEST.json` lists every third-party file with its
version, licence and SHA-256; `scripts/vendor_libs.py` writes them, and
`docs/UI_KIT.md` says what each one is for.

- The same files for every app and every visitor: no session is asked for
  and nothing reaches the manager. `GET` and `HEAD` only.
- A path never changes its content: a new version is a new directory, and an
  old one stays while a published app may link it. The answer carries
  `Cache-Control: public, max-age=31536000, immutable`, so a browser keeps a
  file for a year and every app on the host shares that one copy.
- `Content-Type` by extension (`css`, `js`, `json`, `svg`, `png`, `woff2`;
  anything else `text/plain`), `X-Content-Type-Options: nosniff`, gzip for
  text.
- A file that is not there, a directory and a path that would leave `lib/`
  are `404`.

This is the one root-absolute address a page of an app may use
(`<link href="/_efp/lib/daisyui@5.8.2/daisyui.css">`): every gateway
answers it, whatever the app's slug. `efp-app check` accepts it when the
file exists and warns about a version that does not. `efp-app dev` answers
it the same way, from the SDK's copy of `lib/` (its wheel) or from the
checkout it runs in.

## 7. Isolation and storage

Both modes run each app as its own OS user with no extra privileges.

| | `sandbox` | `hidden` |
|---|---|---|
| Code and virtual environment | on the volume, read-only for the app | container-local disk, rebuilt on demand after a restart |
| What the app can see of the volume | only its own directory | nothing |
| `EFP_APP_DATA_DIR` | set: a directory the app reads and writes, kept across restarts and deployments | not set |
| `EFP_APP_STORAGE_SOCKET` | not set | set: the storage service below |

Volume layout (`EFP_HOST_STORE_DIR`):

```
registry.json
keys/session.key
metrics/state.json
jobs/<job id>.json, jobs/<job id>.log
apps/<app id>/env.json
apps/<app id>/releases/<sha>/code/            the entry directory
apps/<app id>/releases/<sha>/manifest.json
apps/<app id>/releases/<sha>/requirements.lock
apps/<app id>/releases/<sha>/venv/            sandbox mode
apps/<app id>/releases/<sha>/wheels/          hidden mode
apps/<app id>/data/                           sandbox: the app's files; hidden: objects/
apps/<app id>/logs/app.log
```

### 7.1 Storage service (hidden mode)

A unix stream socket at `EFP_APP_STORAGE_SOCKET`. The manager identifies the
calling app by the peer's OS uid. One request per connection:

```
request : 4-byte big-endian header length, JSON header, body bytes
response: 4-byte big-endian header length, JSON header, body bytes
```

| Request header | Body | Response header | Body |
|---|---|---|---|
| `{"op":"put","key":"a/b.json","size":N}` | N bytes | `{"ok":true}` | |
| `{"op":"get","key":"a/b.json"}` | | `{"ok":true,"size":N}` | N bytes |
| `{"op":"delete","key":"a/b.json"}` | | `{"ok":true,"deleted":true}` | |
| `{"op":"list","prefix":"a/"}` | | `{"ok":true,"keys":[{"key":"a/b.json","size":N,"modified_at":"..."}]}` | |

Failure: `{"ok":false,"error":{"code":"not_found|invalid_key|too_large|quota_exceeded|bad_request","message":"..."}}`.
A key is 1-512 characters of `A-Z a-z 0-9 . _ - /`, no leading `/`, no empty,
`.` or `..` segment, and no segment longer than 255 characters. One object is
at most 64 MiB.

The SDK's `storage` module offers the same four operations in all three
places: files under `EFP_APP_DATA_DIR/objects/` (sandbox), this socket
(hidden), `./.efp-data/objects/` (a developer's machine).

## 8. Environment of an app process

| Variable | Value |
|---|---|
| `EFP_APP_ID`, `EFP_APP_SLUG` | |
| `EFP_APP_TOKEN` | the app token |
| `EFP_APP_BASE_PATH` | `/{slug}` |
| `EFP_APP_PUBLIC_URL` | `{APPS_PUBLIC_URL}/{slug}/` |
| `EFP_PORTAL_URL` | `EFP_PORTAL_INTERNAL_URL` of the host |
| `EFP_VISITOR_KEY` | the app's `visitor_key` |
| `EFP_APP_DATA_DIR` or `EFP_APP_STORAGE_SOCKET` | section 7 |
| `PORT` | the loopback port to listen on (`127.0.0.1`) |
| `HOME`, `TMPDIR` | a private writable directory, emptied on restart |
| `PYTHONUNBUFFERED` | `1` |
| owner-defined variables | may not start with `EFP_` and may not be `PORT`, `HOME`, `TMPDIR`, `PATH`. A name that starts with `LD_`, and `GCONV_PATH` and `GLIBC_TUNABLES` (in either case), never reaches a process: the Portal refuses such a name, and the host leaves out one it is sent all the same |
| `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` (either case), `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` | the host's own, when it has them. With a proxy set, `127.0.0.1`, `localhost` and `::1` are added to `NO_PROXY` and `no_proxy`: what an app asks of its own port never goes to the proxy |

On a developer's machine the SDK needs only `EFP_APP_TOKEN` and
`EFP_PORTAL_URL` (the Portal's public address).

### 8.1 How each type is started

`python` is the interpreter of the app's virtual environment, the working
directory is the entry directory, `{BASE}` is `EFP_APP_BASE_PATH` and `{PORT}`
is `PORT`. The host and `efp-app dev` use the same command lines.

| Type | Command | Extra environment | Prefix |
|---|---|---|---|
| `fastapi` | `python -m uvicorn {entry} --host 127.0.0.1 --port {PORT} --root-path {BASE} --no-server-header` | | stripped |
| `flask` | `python -m gunicorn {entry} --bind 127.0.0.1:{PORT} --workers 1 --threads 8 --timeout 120` | `SCRIPT_NAME={BASE}` | kept |
| `streamlit` | `python -m streamlit run {entry} --server.port {PORT} --server.address 127.0.0.1 --server.baseUrlPath {BASE} --server.headless true --browser.gatherUsageStats false --server.enableCORS false --server.enableXsrfProtection false` | | kept |
| `gradio` | `python {entry}` | `GRADIO_SERVER_NAME=127.0.0.1`, `GRADIO_SERVER_PORT={PORT}`, `GRADIO_ROOT_PATH={BASE}`, `GRADIO_ANALYTICS_ENABLED=False` | stripped |
| `command` | `sh -c "{command}"` | | kept |
| `static` | no process; the gateway serves the directory | | stripped |

- When `{BASE}` is empty (a developer's machine) the options that carry it
  (`--root-path`, `--server.baseUrlPath`, `SCRIPT_NAME`, `GRADIO_ROOT_PATH`)
  are left out.
- The host installs the runner when the requirements do not name it:
  `uvicorn` for `fastapi`, `gunicorn` for `flask`.
- Neither framework reports to its makers: `--browser.gatherUsageStats false`
  for Streamlit, `GRADIO_ANALYTICS_ENABLED=False` for Gradio. With its
  analytics on, Gradio also asks its servers for the latest version at every
  start, and its `launch()` compares the page's theme with the built-in ones,
  which fails ("'str' object has no attribute 'name'", Gradio 6.32) when a
  theme names a font as a string.
- On Windows `efp-app dev` starts `flask` apps with
  `python -m flask --app {entry} run --port {PORT}` (gunicorn needs a POSIX
  system). With `--prefix` it wraps the app so that Flask's own server
  applies `SCRIPT_NAME` the way gunicorn does.
- `efp-app dev` puts a stand-in for the gateway in front of the app, which
  listens on a free port behind it. The stand-in answers `/_efp/lib/` (6.6)
  and passes every other request on with its path and cookies unchanged.
  With `--prefix NAME` it also does what the gateway does for `/NAME/`: the
  prefix is removed or kept as in the table, the forwarded headers of 6.2
  are added, the app's cookies are confined to `/NAME/`, and a request
  outside `/NAME/` is answered 404. It signs nobody in and sets no
  `EFP_APP_ID`, so identity and storage stay those of a developer's machine.
- An app is healthy when `GET {health_path}` on its port (with the prefix when
  the type keeps it) answers with a status below 500.

## 9. SDK API (app -> Portal), prefix `/api/app-sdk/v1`

`Authorization: Bearer <app token>`. No cookies, no CORS. Reachable from the
cluster and through the Portal's public address. Errors:
`{"error": {"code": "...", "message": "..."}}`.

| Status | Codes |
|---|---|
| 400 | `invalid_request`, `model_not_allowed`, `invalid_path` |
| 401 | `invalid_token` |
| 403 | `app_disabled`, `llm_disabled`, `connector_disabled`, `method_not_allowed` |
| 424 | `provider_auth_failed`, `connector_not_configured` |
| 429 | `rate_limited`, `budget_exceeded` (with `Retry-After`) |
| 502 | `provider_error`, `upstream_error` |
| 504 | `upstream_timeout` |

An app may pass the `X-EFP-Visitor` value it received as the request header
`X-EFP-Visitor`; the Portal records who caused the call.

### 9.1 `GET /app`

```json
{
  "id": "...", "slug": "mindmap", "name": "Mind map",
  "url": "https://app.example.com/mindmap/",
  "owner": {"id": 5, "username": "alice", "display_name": "Alice", "group_id": 3, "group_name": "QA"},
  "llm": {"enabled": true, "default_model": "gpt-5.4", "models": ["gpt-5.4"]},
  "connectors": [{"type": "jira", "write": false}],
  "image_analysis": {"enabled": true, "model": "gpt-5.4", "max_images": 4,
                     "max_image_bytes": 5242880, "max_total_bytes": 20971520},
  "budget": {"daily_credits": 50.0, "used_credits": 1.2, "daily_tokens": 2000000, "used_tokens": 3400}
}
```

`image_analysis.enabled` is true when the owner turned image analysis on for
the app and the owner's Image analysis connector is configured (9.4);
otherwise it is false and `model` is `null`. The three limits are sent in
both cases. Image analysis is not one of the `connectors`: it has no address
an app could call through 9.3.

### 9.2 `POST /llm/complete`

```json
{
  "messages": [{"role": "user", "content": "Hello"}],
  "system": "You are concise.",
  "model": null,
  "max_output_tokens": 1024,
  "temperature": null,
  "reasoning_effort": null,
  "stream": false
}
```

`role` is `user` or `assistant`; `content` is a string. `reasoning_effort` is
`low`, `medium` or `high`; left out it is `low` (an app call does not inherit
the level the owner chose for their assistants), and the Portal lowers it to
what the model supports. `temperature` is accepted and may be ignored by the
provider. Not streaming:

```json
{"text": "...", "model": "gpt-5.4", "finish_reason": "stop|length",
 "usage": {"input_tokens": 10, "output_tokens": 20, "credits": 0.004}}
```

Streaming (`"stream": true`) answers `text/event-stream`:

```
data: {"type": "delta", "text": "Hel"}
data: {"type": "delta", "text": "lo"}
data: {"type": "done", "model": "gpt-5.4", "finish_reason": "stop", "usage": {...}}
```

A failure after the stream began is `data: {"type": "error", "error": {"code": "...", "message": "..."}}`.
`usage.credits` is `null` when the provider reports none.

Limits: `messages` has at most 1,000 items, `messages` and `system` together
at most 4,000,000 characters, `max_output_tokens` is at most 128,000; a
request over one of them is `400 invalid_request`. The daily budget counts
the calls an app has under way as well as the finished ones: a call under
way counts as a quarter of its characters plus its `max_output_tokens`
(4,096 when left out). When spent and under way together reach the daily
tokens, a new call is `429 rate_limited` with `Retry-After: 5` (nothing is
spent yet, so it is worth another try); `429 budget_exceeded` means the
finished calls alone have used the day's budget.

### 9.3 Connectors

`GET /connectors` -> `{"connectors": [{"type": "jira", "base_url": "https://jira.example.com", "write": false}]}`

`POST /connectors/{type}/request`

```json
{"method": "GET", "path": "/rest/api/2/issue/ABC-1", "query": {"fields": "summary"},
 "headers": {"Accept": "application/json"}, "json": null, "body_base64": null}
```

```json
{"status": 200, "headers": {"content-type": "application/json"},
 "json": {"key": "ABC-1"}, "body_base64": null, "truncated": false}
```

- `type`: `jira`, `confluence`, `github`, `jenkins`, `nexus`, `splunk`.
- `path` starts with `/`, has no scheme, host, `..` segment or backslash.
- Only `GET` and `HEAD` unless the owner allowed writes for that connector.
- Only `Accept`, `Content-Type` and `X-*` request headers are forwarded; a
  value that is not ASCII is `400 invalid_request`.
- A connector whose address means the Portal's own machine, a link-local or
  a metadata address (as written; names are not resolved) is never called:
  it is not listed, and a request answers `424 connector_not_configured`.
  The same holds for a row the owner switched off.
- A JSON response comes back in `json`, anything else in `body_base64`. A body
  over 5 MiB is cut and `truncated` is true.
- The upstream's own status is returned in `status`; the HTTP status of this
  endpoint is `200` whenever the upstream answered.

### 9.4 `POST /images/inspect`

One question about one to four images, answered by the model of the owner's
Image analysis connector (in the Portal: the connector `image_analysis`, an
AI Platform account and a model). The owner turns it on for the app the way
a connector is turned on.

```json
{
  "images": [{"media_type": "image/png", "data_base64": "iVBORw0KGgo..."}],
  "prompt": "Which field is highlighted, and what does the message under it say?",
  "system": null,
  "max_output_tokens": 4096,
  "reasoning_effort": "medium"
}
```

```json
{"text": "...", "model": "gpt-5.4", "finish_reason": "stop|length",
 "usage": {"input_tokens": 1830, "output_tokens": 210, "credits": null}}
```

- `images` has 1 to 4 items. `media_type` is `image/jpeg`, `image/png`,
  `image/webp` or `image/gif`. The Portal goes by the first bytes of the
  decoded data, as the SDK does: `FF D8 FF` is JPEG, the eight bytes
  `89 50 4E 47 0D 0A 1A 0A` are PNG, `RIFF`, four bytes of any value and
  `WEBP` are WebP, `GIF87a` and `GIF89a` are GIF. Data that is none of the
  four, or a `media_type` that disagrees with it, is `400 invalid_request`.
  So is an image without bytes and a `data_base64` that is not standard
  base64. One image holds at most 5 MiB decoded (5,242,880 bytes), all of
  them together at most 20 MiB.
- `prompt` is a string that is not blank. `system` is a string or `null`.
  Together they hold at most 200,000 characters.
- `max_output_tokens` is a whole number from 1 to 16,000; left out or `null`
  it is 4,096. `reasoning_effort` is as in 9.2, and may be left out or
  `null`. A field the contract does not name is ignored.
- There is no `model` field and no stream: the model is the connector's, and
  the answer comes whole.
- Image analysis is a switch of its own. It does not need the model of 9.2
  to be turned on for the app, and `llm_disabled` is never its answer.
- A request with four full images is about 28 MB of JSON. The Portal reads a
  body of that size on this route; whatever stands in front of the Portal
  (an ingress) has to let it through.
- The Portal adds no instruction of its own. The model receives `system`
  (when given) and one user message: the prompt, then the images in order.
- `403 connector_disabled`: image analysis is not turned on for the app.
  `424 connector_not_configured`: the owner has no Image analysis connector,
  it is switched off, or the deployment has no AI Platform address.
  `424 provider_auth_failed`: the connector's account was refused.
  `502 provider_error`: AI Platform failed. `429 rate_limited` and
  `429 budget_exceeded` as in 9.2.
- It is counted as a model call of the app, against the daily budget of 9.2.
  A call under way counts as a quarter of the characters of `prompt` and
  `system`, plus 1,500 tokens for each image, plus its `max_output_tokens`.
  `usage` is what AI Platform reported; `credits` is `null` when it reports
  none.
