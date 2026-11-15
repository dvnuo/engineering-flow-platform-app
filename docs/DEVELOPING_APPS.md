# Developing EFP apps

An EFP app is a small web app (or a static site) that you write, keep in a
GitHub repository and publish through the EFP Portal. It runs inside the EFP
app host at `https://<apps host>/<slug>/`, behind a gateway that signs
visitors in. With one token, its Python code can use what you, the owner,
have in the Portal: your language model and your connectors (Jira,
Confluence, GitHub ...).

This guide covers the Python SDK (`efp-app-sdk`, imported as `efp_app`), the
`efp-app` command and the rules an app has to follow. The wire contracts
behind it are in [CONTRACTS.md](CONTRACTS.md).

Contents: [Quick start](#quick-start) - [The manifest](#the-manifest) -
[App types and the path prefix](#app-types-and-the-path-prefix) -
[What reaches your app](#what-reaches-your-app) -
[Visitor identity](#visitor-identity) - [The language model](#the-language-model) -
[Connectors](#connectors) - [Storage](#storage) - [About the app](#about-the-app) -
[Errors](#errors) - [Local development](#local-development) -
[efp-app check](#efp-app-check) - [Limits](#limits) -
[Troubleshooting](#troubleshooting)

## Quick start

1. Install the SDK in a virtual environment (it comes from your company's
   package index):

   ```
   python -m venv .venv
   .venv/bin/pip install efp-app-sdk          (.venv\Scripts\pip on Windows)
   ```

2. Create an app from a starter. There are five: `fastapi`, `flask`,
   `streamlit`, `gradio`, `static`.

   ```
   efp-app init fastapi my-app --owner <your Portal username>
   cd my-app
   pip install -r requirements.txt
   ```

3. Run it. `efp-app dev` starts the app the way the host does.

   ```
   efp-app dev
   ```

   Open http://127.0.0.1:8000/. The page works; calls to the language model
   answer with "the app is not set up yet" until step 5.

4. Push the directory to a GitHub repository that EFP can read, then register
   it in the Portal (Apps, New app): the repository address, a branch or tag,
   the directory that holds `efp-app.json`, and a name. Your Portal username
   must be listed in `"owners"` of the manifest; that is how the Portal knows
   you may publish this code.

5. Copy the app token from the app's page in the Portal into `.env` (see
   `.env.example`), together with the Portal's address, and run `efp-app dev`
   again. Now the model and the connectors work locally, as you. Turn on the
   language model and the connectors the app needs on the app's page.

   ```
   EFP_APP_TOKEN=efpa_...
   EFP_PORTAL_URL=https://efp.example.com
   ```

6. Check and deploy:

   ```
   efp-app check
   ```

   Commit, push, and press Deploy on the app's page. The host installs
   `requirements.txt`, starts the app, checks its health and switches to the
   new release; the previous one is kept for rollback. The app is then at
   `https://<apps host>/<slug>/`.

Keep `.env` out of the repository (the starters ship a `.gitignore` for it).
The token works from anywhere the Portal can be reached; if it leaks, reset
it on the app's page.

## The manifest

`efp-app.json` lives in the app's entry directory.

```json
{
  "name": "Mind map",
  "description": "Draw and share mind maps.",
  "icon": "icon.png",
  "type": "fastapi",
  "entry": "main:app",
  "requirements": "requirements.txt",
  "health_path": "/",
  "startup_timeout": 60,
  "owners": ["alice"]
}
```

| Field | Rule |
|---|---|
| `name` | required, 1-80 characters |
| `description` | optional, up to 500 characters |
| `icon` | optional, an image file in the entry directory |
| `type` | required: `fastapi`, `flask`, `streamlit`, `gradio`, `command`, `static` |
| `entry` | `fastapi` / `flask`: `module:attribute` (`main:app`). `streamlit` / `gradio`: the script (`app.py`). `static`: the directory to serve, default `.`. Not used by `command` |
| `command` | only for `command`: one shell command line; it may use `$PORT` and `$EFP_APP_BASE_PATH` |
| `requirements` | optional, default `requirements.txt`; without the file the app has no dependencies |
| `health_path` | optional, default `/`; the app is healthy when a `GET` of it answers with a status below 500 |
| `startup_timeout` | optional, whole seconds, default 60, at most 300 |
| `strip_prefix` | optional; overrides whether the gateway removes the path prefix (next section) |
| `owners` | the Portal usernames that may register this directory as an app |

Paths are relative to the entry directory, use forward slashes and may not
contain `..`. Unknown keys are ignored.

## App types and the path prefix

Every app is served under a path prefix, `/<slug>`. On your own machine there
is none. This difference is behind most "works locally, broken when deployed"
reports, and it comes down to one rule: **never write a URL that starts with
`/` in something the browser reads.** `/static/app.js` means "the top of the
apps host", which is not your app.

The host starts each type with a fixed command line (`efp-app dev` uses the
same one, without the prefix):

| Type | Started as | The prefix |
|---|---|---|
| `fastapi` | `python -m uvicorn {entry} --host 127.0.0.1 --port $PORT --root-path /<slug> --no-server-header` | removed before the request arrives |
| `flask` | `python -m gunicorn {entry} --bind 127.0.0.1:$PORT --workers 1 --threads 8 --timeout 120`, with `SCRIPT_NAME=/<slug>` | kept |
| `streamlit` | `python -m streamlit run {entry} --server.port $PORT --server.address 127.0.0.1 --server.baseUrlPath /<slug> --server.headless true ...` | kept |
| `gradio` | `python {entry}`, with `GRADIO_SERVER_NAME`, `GRADIO_SERVER_PORT`, `GRADIO_ROOT_PATH=/<slug>` | removed |
| `command` | `sh -c "{command}"` | kept |
| `static` | no process: the gateway serves the directory | removed |

The host adds `uvicorn` (FastAPI) or `gunicorn` (Flask) when your
requirements do not name it, and the SDK in the same way; that is why the
starters' `requirements.txt` does not list `efp-app-sdk`.

### FastAPI

Routes are written without the prefix (`@app.get("/api/items")`). Pages use
relative URLs: `fetch("api/items")`, `<script src="static/app.js">`.

The one mistake: a leading slash in the front end, `fetch("/api/items")`.

When the backend builds a link (a redirect, an e-mail), use
`request.url_for("route_name")`, or `efp_app.app.base_path() + "/items"`.
A `RedirectResponse("/done")` leaves the app; `RedirectResponse(request.url_for("done"))` does not.

### Flask

Flask receives the full path, and `SCRIPT_NAME` tells it which part is the
prefix. Routes stay as they are (`@app.get("/")`), and `url_for()` returns
addresses that include the prefix.

The one mistake: a hand-written URL. Use
`{{ url_for('static', filename='app.js') }}` in templates,
`redirect(url_for('index'))` in Python, and hand scripts their URLs from the
template (`data-ask-url="{{ url_for('ask') }}"`).

### Streamlit

Streamlit serves its page, its files and its WebSocket under the prefix by
itself. Nothing to do in the script.

The one mistake: an absolute link, `st.markdown("[Home](/)")` or
`<a href="/reports">` in HTML. Write links relative to the page or build them
from `efp_app.app.base_path()`. Also leave `server.baseUrlPath`, CORS and
XSRF settings out of `.streamlit/config.toml`: the host passes them.

### Gradio

`demo.launch()` reads where to listen and the prefix from the `GRADIO_*`
variables the host sets.

The one mistake: passing them yourself. `demo.launch(server_port=7860)`,
`demo.launch(root_path="/")` or `share=True` replace what the host set. Keep
`demo.launch()` without arguments, inside `if __name__ == "__main__":`.

### Static

Every URL in a page, style sheet or script is relative: `href="style.css"`,
`fetch("data.json")`, `url(img/logo.png)`.

The one mistake: a build tool's default of `/` as the public path. Set the
"base" (Vite), "publicPath" (webpack) or "homepage" (Create React App) option
to `./`. A static app has no backend, so there is no SDK, no model, no
connector and nothing saved on the server.

### Command

For anything else that can serve HTTP. The command must listen on
`127.0.0.1:$PORT` and serve under `$EFP_APP_BASE_PATH` (the prefix is kept),
or set `"strip_prefix": true` and serve at the root.

### Pages below the top level, and WebSockets

A relative URL is relative to the page's own address. On a page at
`/<slug>/reports/2026`, `fetch("api/items")` asks for
`/<slug>/reports/api/items`. Either keep pages one level deep, or give the
page a base: `<base href="{{ base }}/">` with `base` set to
`request.scope["root_path"]` (FastAPI) or `request.script_root` (Flask).

A WebSocket needs a full address; build it from the page:

```js
const url = new URL("ws", document.baseURI);
url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
const socket = new WebSocket(url);
```

## What reaches your app

- The path, with or without the prefix (see above), and
  `X-Forwarded-For`, `X-Forwarded-Proto`, `X-Forwarded-Host`,
  `X-Forwarded-Prefix: /<slug>`.
- The visitor's cookies, without the gateway's own session cookie.
- `X-EFP-Visitor`, when the visitor is signed in. The gateway removes every
  `X-EFP-*` header a browser sends, so this one cannot be forged from outside.
- WebSockets and streamed answers pass through unbuffered; a request may take
  up to an hour.

On the way back the gateway confines your cookies to `/<slug>/`, drops
cookies that set `Domain=` and removes `Service-Worker-Allowed`. Camera,
microphone, location and screen capture are switched off for all apps.

A request that is not a page load (a `fetch`, for example) never gets a
sign-in redirect. It gets JSON, `{"error": {"code": "...", "message": "..."}}`:
`401 sign_in_required` when the visitor's session has ended (reload the
page), `403 no_access`, `503 app_starting` (try again after `Retry-After`).
The starters' scripts show how to handle that.

All apps share one origin. `localStorage`, `sessionStorage` and IndexedDB of
your pages can be read by the pages of every other app, so keep nothing
confidential there.

## Visitor identity

```python
from efp_app import visitor

who = visitor.current(request.headers)      # a Visitor, or None
if who:
    who.id, who.username, who.name, who.group, who.group_id
```

`current()` takes any mapping of the request's headers. It verifies the
signature of `X-EFP-Visitor` with the app's own key (`EFP_VISITOR_KEY`) and
its expiry, and returns `None` for anything it cannot trust. `None` therefore
means "anonymous": possible only when the app's visibility is "anyone".

Helpers for each framework (`efp_app.integrations`):

```python
# FastAPI / Starlette
from efp_app.integrations.fastapi import get_visitor, require_visitor

@app.get("/api/me")
def me(who: Optional[Visitor] = Depends(get_visitor)): ...

@app.post("/api/items")
def add(who: Visitor = Depends(require_visitor)): ...      # 401 when anonymous

# Flask
from efp_app.integrations.flask import get_visitor, require_visitor
who = get_visitor()

# Streamlit (1.37 or newer)
from efp_app.integrations.streamlit import get_visitor
who = get_visitor()

# Gradio: add a gr.Request parameter to the event function
from efp_app.integrations.gradio import get_visitor
def greet(request: gr.Request):
    who = get_visitor(request)
```

Good to know:

- The value is valid for 60 seconds. Read the visitor while handling the
  request, not later in a thread or a queue.
- Streamlit only sees the headers of the request that opened the browser
  session, so its helper verifies the visitor once and remembers it in
  `st.session_state`.
- Do not take identity from anything else (a query parameter, a form field,
  your own cookie without a check).
- On your own machine there is no gateway: without the header, `current()`
  returns the owner of the app (asked from the Portal once), or the user named
  by `EFP_DEV_VISITOR`. `EFP_DEV_VISITOR=anonymous` shows the app as a
  visitor who is not signed in.

## The language model

```python
from efp_app import llm

answer = llm.complete("Summarise: ...", system="You are concise.", visitor=who)
answer.text, answer.model, answer.finish_reason        # "stop" or "length"
answer.usage.input_tokens, answer.usage.output_tokens, answer.usage.credits

stream = llm.stream([{"role": "user", "content": "Tell me a story"}], visitor=who)
for piece in stream:            # pieces of text as they arrive
    ...
stream.text, stream.usage       # complete once the stream has ended
```

| Argument | Meaning |
|---|---|
| first argument | a prompt, or a list of `{"role": "user" or "assistant", "content": "..."}` |
| `system` | the system prompt |
| `model` | default: the app's default model; `app.info().llm.models` lists the allowed ones |
| `max_output_tokens`, `temperature` | optional; a provider may ignore `temperature` |
| `reasoning_effort` | `"low"`, `"medium"` or `"high"`; left out, an app call runs at `low`, and because reasoning tokens count as output tokens, a high effort with a small `max_output_tokens` can come back with an empty text |
| `visitor` | the `Visitor` (or the header value): the Portal records who caused the call |
| `timeout` | seconds; default 600 |

`llm.acomplete(...)` and `llm.astream(...)` are the same for asynchronous
code:

```python
stream = await llm.astream(question, visitor=who)     # sends the request; errors are raised here
async for piece in stream:
    ...
```

`llm.stream()` sends the request when it is called, so a refusal (budget,
model switched off) is raised before any text arrives and a web handler can
still answer with a proper error. There are no tool calls in this release.

Every call runs on your own model provider and counts against the app's daily
budget, whoever the visitor is. When the budget is used up the call raises
`BudgetExceededError`; `exc.retry_after` is the wait in seconds and
`exc.user_message` a sentence you can show.

## Connectors

```python
from efp_app import connectors

issue = connectors.get("jira", "/rest/api/2/issue/ABC-1", fields="summary")
if issue.ok:
    issue.json["fields"]["summary"]

answer = connectors.request("github", "POST", "/repos/acme/app/issues",
                            json={"title": "Bug"}, visitor=who)
answer.status, answer.headers, answer.json, answer.content, answer.text, answer.truncated

connectors.available()      # [Connector(type="jira", base_url="https://...", write=False)]
```

- Types: `jira`, `confluence`, `github`, `jenkins`, `nexus`, `splunk`. A
  connector must be turned on for the app in the Portal, and it is read-only
  (`GET`, `HEAD`) unless you allowed writes for it.
- The path is relative to the connector's address: it starts with `/` and has
  no host and no `..`.
- `get(type, path, **query)` is a `GET` with the keyword arguments as the
  query string. `request()` also takes `query`, `headers` (only `Accept`,
  `Content-Type` and `X-*` are forwarded), `json` or `body` (bytes or text).
- The answer carries the other system's own status: a 404 from Jira is a
  `ConnectorResponse` with `status == 404`, not an exception. A JSON body is
  in `.json`, any body in `.content`; a body over 5 MiB is cut and
  `.truncated` is true.
- `arequest`, `aget` and `aavailable` are the asynchronous variants.

The Portal adds your connector's credentials; the app never sees them. Every
visitor of the app reads those systems as your connector does, so think about
who may open the app before you turn a connector on.

## Storage

```python
from efp_app import storage

storage.save("notes/alice.txt", "remember the milk")     # bytes, or text saved as UTF-8
storage.load("notes/alice.txt")                           # b"remember the milk"
storage.load("notes/nobody.txt", default=b"")             # no error for a missing key
storage.exists("notes/alice.txt")
storage.delete("notes/alice.txt")                         # True when there was something
storage.save_json("settings.json", {"theme": "dark"})
storage.load_json("settings.json", default={})
for item in storage.list("notes/"):                       # ordered by key
    item.key, item.size, item.modified_at
```

A key is 1-512 characters of `A-Z a-z 0-9 . _ - /`, does not start with `/`
and has no empty, `.` or `..` segment; one segment (the text between two `/`)
is at most 255 characters. One object is at most 64 MiB. A name
cannot be an object and a folder at once (`a` and `a/b`). Each save replaces
the whole object and is complete or absent, never half written; there are no
transactions across objects.

Use storage for everything that must survive. The app's code directory is
read-only, and `HOME` and `TMPDIR` are emptied on every restart.

### The two isolation modes

A platform runs its app host in one of two modes, and the SDK hides the
difference for the calls above:

| | `sandbox` | `hidden` |
|---|---|---|
| The app's environment | `EFP_APP_DATA_DIR` | `EFP_APP_STORAGE_SOCKET` |
| `storage.save()` and friends | files under `$EFP_APP_DATA_DIR/objects/` | the host's storage service |
| `storage.data_dir()` | the app's own directory | raises `DataDirUnavailableError` |
| `storage.mode()` | `"sandbox"` | `"hidden"` |

On your own machine `storage.mode()` is `"local"`: objects go to
`./.efp-data/objects/` and `data_dir()` is `./.efp-data/`.

`storage.data_dir()` is for what does not fit keys and values: an SQLite
file, uploads you stream to disk. It exists only in sandbox mode, so an app
that uses it does not run on a host in hidden mode. If you want an app, or a
template others will reuse, to run everywhere, stay with `save` / `load`.
Files you put in the data directory yourself should not go into its `objects`
folder.

The app's data has a quota (1 GiB unless the platform changed it). In hidden
mode a save beyond it raises `QuotaExceededError`; in sandbox mode the owner
is warned in the Portal. In hidden mode a listing returns at most 10,000
keys, so narrow it with a prefix.

## About the app

```python
from efp_app import app

app.base_path()       # "/mindmap" when deployed, "" locally
app.public_url()      # "https://app.example.com/mindmap/"
app.is_deployed()     # False on your own machine

info = app.info()     # asks the Portal
info.name, info.slug, info.url
info.owner.username, info.owner.display_name, info.owner.group_name
info.llm.enabled, info.llm.default_model, info.llm.models
info.connectors       # (ConnectorGrant(type="jira", write=False), ...)
info.budget.daily_credits, info.budget.used_credits, info.budget.daily_tokens, info.budget.used_tokens
```

## Errors

Every error the SDK raises derives from `efp_app.errors.EfpError` and has
`code`, `message` (the detail, for you and the log), `status` and
`user_message` (a sentence that is safe to show to a visitor).

```python
from efp_app.errors import BudgetExceededError, EfpError

try:
    answer = llm.complete(question, visitor=who)
except BudgetExceededError as exc:
    return show(exc.user_message)          # "This app has used up its AI budget for today. ..."
except EfpError as exc:
    log.warning("%s", exc)                 # "llm_disabled: ..."
    return show(exc.user_message)
```

| Code | Class | Usual cause |
|---|---|---|
| `invalid_request` | `InvalidRequestError` | a malformed call |
| `model_not_allowed` | `ModelNotAllowedError` | a `model=` the app may not use |
| `invalid_path` | `InvalidPathError` | a connector path with a host or `..` |
| `invalid_token` | `InvalidTokenError` | the token was reset or mistyped |
| `app_disabled` | `AppDisabledError` | the app was disabled in the Portal |
| `llm_disabled` | `LlmDisabledError` | the model is not turned on for the app |
| `connector_disabled` | `ConnectorDisabledError` | the connector is not turned on for the app |
| `method_not_allowed` | `MethodNotAllowedError` | a write to a read-only connector |
| `provider_auth_failed` | `ProviderAuthFailedError` | the owner's model provider needs a new sign-in |
| `connector_not_configured` | `ConnectorNotConfiguredError` | the owner has no such connector set up |
| `rate_limited` | `RateLimitedError` | too many calls; `retry_after` |
| `budget_exceeded` | `BudgetExceededError` | the daily budget is used up; `retry_after` |
| `provider_error` | `ProviderError` | the model provider failed |
| `upstream_error` | `UpstreamError` | the connector's system could not be reached |
| `upstream_timeout` | `UpstreamTimeoutError` | the connector's system was too slow |
| `not_configured` | `ConfigurationError` | `EFP_APP_TOKEN` or `EFP_PORTAL_URL` is missing |
| `portal_unreachable`, `portal_timeout` | `PortalUnreachableError`, `PortalTimeoutError` | the Portal cannot be reached |
| `unexpected_response`, `stream_interrupted` | `UnexpectedResponseError`, `StreamInterruptedError` | a broken answer, usually a wrong `EFP_PORTAL_URL` or a restart |
| `not_found`, `invalid_key`, `too_large`, `quota_exceeded` | `StorageNotFoundError`, `InvalidKeyError`, `TooLargeError`, `QuotaExceededError` | storage; all derive from `StorageError` |
| `data_dir_unavailable`, `storage_unavailable` | `DataDirUnavailableError`, `StorageUnavailableError` | storage that this host does not offer |

The SDK repeats a read (`GET`) when the connection fails and never repeats a
model or connector call: repeat those yourself only when doing so is safe.

## Local development

`efp-app dev [directory] [--port N]` reads `efp-app.json` and starts the app
with the host's command line, an empty path prefix, `PORT` (default 8000) and
the variables of `.env` in the app's directory. Variables already set in your
shell win over `.env`. `--dry-run` shows the command without starting it.

| Variable | Locally |
|---|---|
| `EFP_APP_TOKEN` | the app token, from the app's page in the Portal |
| `EFP_PORTAL_URL` | the Portal's address as you open it in the browser |
| `EFP_DEV_VISITOR` | optional: a username to act as, or `anonymous` |
| `EFP_VISITOR_KEY` | not needed (there is no gateway to sign anything) |
| `HTTPS_PROXY`, `NO_PROXY`, `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` | honoured, for a company proxy or CA |

What a deployed app gets, for reference: `EFP_APP_ID`, `EFP_APP_SLUG`,
`EFP_APP_TOKEN`, `EFP_APP_BASE_PATH`, `EFP_APP_PUBLIC_URL`, `EFP_PORTAL_URL`,
`EFP_VISITOR_KEY`, `EFP_APP_DATA_DIR` or `EFP_APP_STORAGE_SOCKET`, `PORT`,
`HOME`, `TMPDIR`, plus the variables you define on the app's page (their
names may not start with `EFP_` or be `PORT`, `HOME`, `TMPDIR`, `PATH`).
Read your own settings from the environment; do not commit secrets.

Differences from the host to keep in mind:

- No gateway: nobody signs in, no `X-EFP-Visitor`, no path prefix. Run
  `efp-app check` before every deployment; it finds the prefix mistakes that
  cannot show up locally.
- Flask on Windows runs on Flask's own server (`python -m flask --app {entry}
  run`), because gunicorn needs a POSIX system. The host uses gunicorn.
- A static app is previewed with Python's file server; on the host there is
  no process.
- Local data is in `./.efp-data/`. Windows cannot keep keys whose segments
  end in a dot or are device names (`NUL`, `COM1`); the SDK says so instead of
  saving them under another name.
- Calls made locally are real: they use your model provider, count against
  the app's budget and reach the real Jira.

## efp-app check

`efp-app check [directory] [--strict]` reports, with file and line:

- errors (exit code 1): a manifest that breaks a rule of the table above; a
  missing entry module, script or directory; a missing requirements file or
  icon that the manifest names; an empty `"owners"` or one that still holds
  the starter's placeholder;
- warnings (exit code 0, or 1 with `--strict`): root-absolute URLs in pages,
  scripts, style sheets and templates (`src="/..."`, `href="/..."`,
  `action="/..."`, `fetch("/...")`, `url(/...)`), each with the relative form
  to use; requirement lines the host cannot install (repositories, URLs, local
  paths, index options); a requirements file that does not list the app's
  framework; unknown manifest keys.

The URL scan is a text search: a flagged line that never reaches a browser is
harmless, and a URL assembled at run time is not found. Python sources are
not scanned.

## Limits

- **One Python version.** The platform chooses it (3.11 in the first
  release); an app cannot pick another. The SDK itself supports 3.9 and newer.
- **Wheels only.** Dependencies come from the platform's package index as
  wheels. A source-only package, a Git URL, an editable install or a local
  path fails the deployment.
- **No system packages.** There is no `apt-get`; a library works when its
  wheel carries everything it needs.
- **One process.** The host starts one process per app (gunicorn with one
  worker and eight threads, uvicorn without workers). Do not start worker
  processes of your own. Memory is capped (1 GiB by default); an app that
  goes over it is stopped and the reason shown in the Portal.
- **It stops when idle.** After a while without visitors (30 minutes by
  default) the app is stopped; the next visit starts it again and sees a
  "starting" page for a moment. So: no background jobs, no schedulers, no
  timers that must fire, nothing important kept in memory. Do the work in
  request handlers and keep state in storage. Start quickly: the app has
  `startup_timeout` seconds to answer its health check.
- **SQLite: default journal mode only, no WAL.** Possible in sandbox mode
  only, with the file in `storage.data_dir()`. The data lives on a network
  file system, where write-ahead logging does not work: leave
  `journal_mode` at its default (`DELETE`), never `PRAGMA journal_mode=WAL`,
  and set a busy timeout. One process means one writer at a time is natural.
- **Listen on `127.0.0.1:$PORT` only.** Nothing else is reachable.
- **Storage:** 64 MiB per object, a quota for the app's data.
- **Cookies** are confined to the app's path; names starting with `__Host-`
  do not survive that. `Domain=` is not allowed.
- **Shared origin:** see [What reaches your app](#what-reaches-your-app).
- **No service worker beyond the app's path, no camera, microphone, location
  or screen capture.**

## Troubleshooting

**Works locally; deployed, the page has no styles or scripts, or `fetch`
answers 404.** A URL starts with `/`. Run `efp-app check` and make the URLs
relative (FastAPI, static), or build them with `url_for` (Flask).

**A redirect or a form lands on a "not found" page.** The same
cause in Python code: `redirect("/done")`. Use `url_for` /
`request.url_for`, or prepend `efp_app.app.base_path()`.

**`not_configured: EFP_APP_TOKEN is not set`.** Locally: put the token and
`EFP_PORTAL_URL` in `.env` and start with `efp-app dev` (a plain `uvicorn`
or `streamlit run` does not read `.env`). Deployed: redeploy, or reset the
token.

**`invalid_token`.** The token was reset in the Portal. Copy the new one into
`.env`; the deployed app received it automatically.

**`unexpected_response` or `portal_unreachable` locally.** `EFP_PORTAL_URL`
is not the Portal's own address (a redirect or a login page answered), or a
proxy is in the way: set `HTTPS_PROXY` / `NO_PROXY`, and `SSL_CERT_FILE` for
a company CA.

**`llm_disabled`, `connector_disabled`, `method_not_allowed`.** Turn on the
model, the connector or its write access on the app's page.

**`provider_auth_failed`.** Your sign-in to the model provider expired;
renew it in the Portal's model provider settings.

**The visitor is `None` although I am signed in.** Check that the code reads
the headers of the current request (`request.headers`, a `gr.Request`
parameter), that Streamlit is 1.37 or newer, and that the visitor is not read
more than a minute after the request arrived. Locally, set `EFP_DEV_VISITOR`.

**The deployment fails at "dependencies".** A package has no wheel for the
host's Python version, or is not in the platform's index. Pin a version that
has one. The log on the app's page shows pip's own message.

**The deployment fails at "health".** The app did not answer `health_path`
in time, or answered with a 5xx. It must listen on `127.0.0.1` and the port
in `PORT`; a Gradio app must not pass its own port, a `command` app must use
`$PORT`. Raise `startup_timeout` (up to 300) only when the start is slow for
a good reason.

**Gradio loads but nothing happens when I click.** `launch()` was given
`root_path`, `server_name` or `server_port`. Remove them.

**Streamlit shows its loading screen forever.** Usually the page cannot
reach its WebSocket: a `.streamlit/config.toml` sets `baseUrlPath`, CORS or
XSRF options that differ from what the host passes. Remove them.

**The WebSocket connects locally, not when deployed.** Its address is
hand-written. Build it from `document.baseURI` (see above).

**A `fetch` answers 401 with `sign_in_required` after the page has been open
for long.** The visitor's session ended. Reload the page.

**My data is gone after a restart.** It was written to the working
directory, `HOME` or `TMPDIR`. Use `efp_app.storage`.

**`data_dir_unavailable`.** The host hides the disk from apps; use
`storage.save()` and `load()`.

**The app is slow on the first visit.** It had been stopped while idle and
is starting. Keep the start light: import heavy libraries lazily, load data
on first use.

**`efp-app dev` says a module is not installed.** Install the app's
requirements into the same virtual environment as the SDK: `efp-app dev`
starts the app with the Python it runs on.
