# Converting an existing project into an EFP app

You have something that already works (a web app, a dashboard, a script, a
notebook, a front-end build) and want it published as an EFP app. This guide
is the procedure: decide whether it fits, choose the app type, change what
has to change, verify, hand over.

It is written for a developer and for an AI agent doing the work. Every rule
is stated so that it can be checked. Read it together with:

- [DEVELOPING_APPS.md](DEVELOPING_APPS.md): how an app runs and the rules it
  follows;
- [SDK_REFERENCE.md](SDK_REFERENCE.md): every name of the SDK, exactly;
- [`examples/`](../examples/README.md): complete apps to copy from, with an
  index of which example shows what.

Contents: [The procedure](#the-procedure) - [Does it fit?](#1-does-it-fit) -
[Choose the type](#2-choose-the-type) - [Lay out the directory](#3-lay-out-the-directory) -
[Listen where the host expects](#4-listen-where-the-host-expects) -
[The path prefix](#5-the-path-prefix) - [Identity](#6-identity-remove-your-login) -
[Secrets and settings](#7-secrets-and-settings) - [State](#8-state-files-and-databases) -
[Model calls](#9-model-calls) - [Calls to other systems](#10-calls-to-other-systems) -
[Background work](#11-background-work) - [Dependencies](#12-dependencies) -
[Recipes](#recipes) - [Verify](#verify) - [Definition of done](#definition-of-done) -
[Hand over](#hand-over-what-only-the-owner-can-do)

## The procedure

1. Check the fit (section 1). Stop here when the project needs something an
   app cannot have, and say which thing.
2. Choose the app type (section 2).
3. Make one directory the app: `efp-app.json`, the code, `requirements.txt`
   (section 3).
4. Make it start the way the host starts it (section 4).
5. Make every URL the browser reads relative, or built from the prefix
   (section 5). This is where most conversions go wrong.
6. Remove the project's own sign-in and read the visitor from the gateway
   (section 6).
7. Move secrets and settings to environment variables (section 7).
8. Move everything that must survive a restart into `efp_app.storage`
   (section 8).
9. Replace model calls and calls to Jira, Confluence, GitHub, Jenkins, Nexus
   or Splunk with the SDK (sections 9 and 10).
10. Remove background jobs, schedulers and polling (section 11).
11. Reduce the dependencies to wheels (section 12).
12. Verify (the [checklist](#verify)), then write down what the owner has to
    do in the Portal ([hand over](#hand-over-what-only-the-owner-can-do)).

Keep the change small. Convert what is there; do not redesign it. When a
feature cannot survive the conversion (a nightly job, a login with roles),
leave it out and say so, rather than faking it.

## 1. Does it fit?

An app is one Python process (or a directory of static files) that answers
HTTP on a port, under a path prefix, for visitors the gateway has already
signed in. Check the project against this table before changing anything.

| The project ... | Verdict |
|---|---|
| is a Python web app: FastAPI, Starlette, Flask, Dash, Streamlit, Gradio | fits; these have types of their own (Dash runs as `flask`) |
| is another Python web app: Django, aiohttp, NiceGUI, Tornado, Bottle, a hand-written server | fits as type `command`; it must serve under the prefix itself, or have the gateway remove it (`"strip_prefix": true`) |
| is a static site or a built single-page app (React, Vue, Svelte, plain HTML) with no backend of its own | fits as type `static`; build it before you commit, the host has no Node |
| is a single-page app with a Python backend | fits: one Python app that also serves the built files |
| is a script, a command-line tool or a notebook | fits once it has a page: wrap it in Streamlit or Gradio (recipe below) |
| needs a server in another language at run time (Node, Java, Go, PHP, Ruby) | does not fit; only its static build output can be an app |
| needs system packages (`apt-get`), a compiler at install time, a GPU or a browser binary | does not fit; dependencies are Python wheels only |
| needs more than one process: workers, a queue consumer, a sidecar, its own database server | does not fit as it is; see sections 8 and 11 for what can replace them |
| must do work when nobody is visiting: cron jobs, schedulers, watchers, bots that listen | does not fit; an idle app is stopped (section 11) |
| must be called by other programs with their own credentials (a public API, a webhook receiver) | does not fit, unless the audience "Anyone" is acceptable and the app checks the caller itself; the gateway lets in browsers with a session |
| needs its own domain, TLS certificate, port, or raw TCP/UDP | does not fit |
| holds more than about 1 GiB of data, single files over 64 MiB, or more than about 1 GiB of memory | does not fit by default; these are the platform's limits and only its operators can raise them |
| needs a relational database | fits with changes: `efp_app.storage` objects, or SQLite in the app's data directory on a host in sandbox mode (no WAL), or an existing database server reachable from the cluster with its address and password in environment variables |

The limits in one place (defaults; the platform can change them): Python
3.11; wheels from the platform's package index; one process; about 1 GiB of
memory; about 1 GiB of data, 64 MiB per object; a request may last up to an
hour and carry up to 100 MB; the app is stopped after about 30 minutes
without a request and started again by the next one; it has 60 seconds
(`startup_timeout`, at most 300) to answer its health check after a start.

## 2. Choose the type

| The project is | `type` | `entry` / `command` | The prefix reaches the app |
|---|---|---|---|
| FastAPI or Starlette (an ASGI object) | `fastapi` | `module:attribute`, for example `main:app` | removed |
| Flask (a WSGI object) | `flask` | `module:attribute`, for example `app:app` | kept, with `SCRIPT_NAME` set |
| a Streamlit script | `streamlit` | the script, for example `app.py` | kept; Streamlit handles it |
| a Gradio script | `gradio` | the script, for example `app.py` | removed, with `GRADIO_ROOT_PATH` set |
| files only | `static` | the directory to serve, default `.` | removed |
| any other Python server | `command` | `"command": "python server.py"` | kept (or removed with `"strip_prefix": true`) |

Rules of thumb:

- The types `fastapi` and `flask` are command lines, not checks of the
  framework: the host starts `python -m uvicorn {entry} --root-path /<slug>`
  for the one and gunicorn with `SCRIPT_NAME=/<slug>` for the other. A
  framework built on them fits the same type (Dash is Flask underneath: the
  recipe below). For an unrelated ASGI or WSGI framework write the command
  line yourself with the type `command`; `efp-app check` expects a `fastapi`
  app to list `fastapi` and a `flask` app to list `flask`.
- Prefer the type whose prefix handling the framework already supports.
  "Removed" is the easy case for an app that only uses relative URLs.
- Do not choose by popularity. Choose the type that leaves the most of the
  project's code untouched.

## 3. Lay out the directory

One directory of the repository is the app (the "entry directory"). It does
not have to be the top of the repository.

```
my-app/
  efp-app.json        the manifest (required)
  requirements.txt    Python dependencies (not for static)
  .env.example        the two settings a developer's machine needs, empty
  .gitignore          .env, .efp-data/, __pycache__/, .venv/
  main.py ...         the code
```

```json
{
  "name": "Mind map",
  "description": "Draw and share mind maps.",
  "icon": "icon.png",
  "type": "fastapi",
  "entry": "main:app",
  "requirements": "requirements.txt",
  "health_path": "/"
}
```

- `name` and `type` are required; so is `entry` (or `command`) for every type
  but `static`. The rest has defaults. The full table is in
  [DEVELOPING_APPS.md](DEVELOPING_APPS.md#the-manifest).
- `health_path` must answer with a status below 500 without a visitor, without
  the model and without a connector. `/` is fine when the front page is
  cheap; add a trivial `/healthz` when it is not.
- Everything the app needs at run time must be inside the entry directory.
  The host receives that directory only, read-only. Imports from a sibling
  directory of the repository will not be there: move the shared code in.
- Paths in the manifest are relative to the entry directory, with forward
  slashes and without `..`.
- `icon` is a PNG, JPEG, GIF or WebP file of at most 256 KB; it is what the
  app's card shows in the Portal and in the app center.
- Leave `"owners"` out unless only certain members should be able to publish
  this directory.
- Copy `.env.example` and `.gitignore` from any starter (`efp-app init`), and
  never commit `.env`.

## 4. Listen where the host expects

The host starts the app itself. For the five named types it uses a fixed
command line (see [DEVELOPING_APPS.md](DEVELOPING_APPS.md#app-types-and-the-path-prefix)),
so what the project's own start-up code does is not used:

| Found in the project | Do |
|---|---|
| `uvicorn.run(app, host="0.0.0.0", port=8000)` under `if __name__ == "__main__":` | leave it or delete it; the host runs `python -m uvicorn main:app ...` and never executes that block |
| `app.run(port=5000, debug=True)` (Flask) | the same; the host runs gunicorn. Code that only runs inside `app.run()` must move |
| work done at import time that takes long (loading a model, reading a large file) | make it lazy: do it on first use. The app has `startup_timeout` seconds to answer |
| a Streamlit `config.toml` with `server.port`, `baseUrlPath`, CORS or XSRF options | remove those options; the host passes them |
| `demo.launch(server_name=..., server_port=..., root_path=..., share=True)` (Gradio) | `demo.launch()` with no such arguments, inside `if __name__ == "__main__":` |
| a start script that runs several things (`web & worker`) | one process only; see section 11 |
| a `Dockerfile`, `Procfile`, `docker-compose.yml` | not used; translate what they do into the manifest and `requirements.txt` |

For `command`, the command line is yours and so are three duties: listen on
`127.0.0.1` and the port in `$PORT` (nothing else is reachable); serve under
the prefix in `$EFP_APP_BASE_PATH`, or set `"strip_prefix": true` and serve
at the root; answer `health_path` (the host asks for it under the prefix, or
at the root with `"strip_prefix": true`). On a developer's machine
`EFP_APP_BASE_PATH` is not set at all unless `efp-app dev` runs with
`--prefix`: read it with `os.environ.get("EFP_APP_BASE_PATH", "")` or call
`efp_app.app.base_path()`.
[`examples/env-status`](../examples/env-status/) is a complete `command` app
on the standard library that does all three.

## 5. The path prefix

A deployed app lives at `https://<apps host>/<slug>/...`. On a developer's
machine it lives at `http://127.0.0.1:8000/...`. Everything that worked only
because the app was at the top of its host breaks. The rule: **nothing the
browser reads may contain a URL that starts with `/`**, unless the server
built it from the prefix.

Find the candidates first:

```
efp-app check --strict            reports src="/...", href="/...", action="/...", fetch("/..."), url(/...)
grep -rnE "[\"'(=\`]/[^/*]" --include="*.html" --include="*.js" --include="*.jsx" --include="*.ts" --include="*.tsx" --include="*.vue" --include="*.css" --include="*.py" .
```

`efp-app check` reports the five forms above (and `formaction`, `poster`,
`EventSource(...)` and `axios`) in pages, scripts, style sheets and
templates. It does not report a WebSocket address, `location = "/..."`,
`XMLHttpRequest`, `srcset`, a router's paths, the registration of a service
worker, a build tool's setting, or anything in a Python file. The `grep` (a
quote, a bracket, `=` or a backtick followed by a single `/`) finds those as
well, together with false alarms such as route declarations.
`efp-app dev --prefix` (below) finds what only shows when the app runs.

Then fix each kind:

| Where | Wrong | Right |
|---|---|---|
| a page's assets | `<script src="/static/app.js">` | `<script src="static/app.js">` (or `url_for` in Flask, `{% static %}` in Django) |
| a script's calls | `fetch("/api/items")` | `fetch("api/items")` |
| a style sheet | `url(/img/bg.png)` | `url(../img/bg.png)`: relative to the style sheet's own address |
| a form | `<form action="/save">` | `<form action="save">` |
| a link | `<a href="/reports">` | `<a href="reports">` |
| a redirect in Python | `RedirectResponse("/done")`, `redirect("/done")`, `HttpResponseRedirect("/done/")`; in FastAPI also `app.url_path_for("done")`, which has no prefix | `RedirectResponse(request.url_for("done"))` in FastAPI, `redirect(url_for("done"))` in Flask, `redirect("done")` (a URL name) in Django |
| a URL built in Python for a page | `"/items/%d" % id` | `efp_app.app.base_path() + "/items/%d" % id`, or the framework's URL builder |
| a URL built in Python for an e-mail or a message | the same, or a host name written into the code | `efp_app.app.public_url() + "items/%d" % id`: the full address of the app, ending in `/<slug>/` |
| a WebSocket | `new WebSocket("ws://" + location.host + "/ws")` | build it from the page: `new URL("ws", document.baseURI)` and swap the protocol (`https:` to `wss:`). On a page below the top level this needs the `<base>` of the first trap below |
| a client-side router | `<BrowserRouter>` with paths from `/` | a hash router. A router on the History API works only when the server answers every address of a page with `index.html` and that page carries a `<base>` (the single-page recipe shows how); its base name is then `new URL(document.baseURI).pathname` |
| a build tool | Vite `base: "/"`, webpack `publicPath: "/"`, CRA `homepage` unset | `base: "./"`, `publicPath: "auto"` or `"./"`, `"homepage": "."` |
| a cookie | `Domain=...`; a name starting with `__Host-` | no `Domain=`: the gateway drops such a cookie whole. No `__Host-` name: the gateway replaces every `Path` (also `Path=/`) with `/<slug>/`, and a browser discards a `__Host-` cookie whose path is not `/` |
| a service worker | `register("/sw.js")`, or a `scope` of `/` | `register("sw.js")`: the scope is then the app's own path. The gateway removes `Service-Worker-Allowed`. Or drop the service worker |

Two traps remain after that:

- **Pages below the top level.** A relative URL is relative to the page's
  own address: on `/<slug>/reports/2026`, `fetch("api/items")` asks for
  `/<slug>/reports/api/items`. Give such pages a base
  (`<base href="{{ base }}/">`, with `base` from `request.scope["root_path"]`
  in FastAPI or `request.script_root` in Flask), or keep pages one level deep.
  A base moves every relative URL of the page, a link to a fragment
  included: `href="#totals"` then leads to `/<slug>/#totals`, so write
  `href="reports/2026#totals"`. A page whose URLs all come from the
  framework's URL builder (`url_for` in Flask, `{% url %}` and `{% static %}`
  in Django) needs no base.
- **The trailing slash.** The app's address is `/<slug>/`, with the slash.
  The gateway redirects `/<slug>` to it. Relative URLs are right only from
  the address with the slash, so never link to the app without it.

How to see it for real before deploying: `efp-app dev --prefix my-app` serves
the app at `http://127.0.0.1:8000/my-app/` behind a small gateway that treats
the prefix as the real one does. It prints one line for every request that
lands outside the app and for every redirect of the app that points outside
it. Click through every page and read two things: the gateway's "outside the
app" lines, and the failed requests in the app's own log. A wrong relative
URL on a page below the top level (the first trap) stays inside the app, so
the gateway prints nothing for it; the app answers it with 404. No line of
either kind is the proof that the URLs are right. `efp-app check` is the
static half of the same check.

## 6. Identity: remove your login

Visitors arrive already signed in by the gateway, which adds a signed header
that the SDK verifies. The app must not ask for a password, run its own OAuth
flow, or keep its own user table for authentication.

| Found in the project | Replace with |
|---|---|
| a login page, a session cookie, `flask_login`, `fastapi-users`, OAuth or OIDC client code | delete it; read the visitor: `require_visitor` / `get_visitor` from `efp_app.integrations.<framework>`, or `efp_app.visitor.current(headers)` |
| `current_user.id`, `request.user.username` | `visitor.id` (an integer, stable for the member), `visitor.username`, `visitor.name`, `visitor.group`, `visitor.group_id` |
| "only signed-in users" | nothing to do for the audiences "Only the owner", "The owner's group" and "Every member": everyone who gets through is a member. With "Anyone", `get_visitor` returns `None` for a guest and `require_visitor` answers 401 |
| "only admins" | compare with the app's owner on the server: `visitor.id == efp_app.app.info(cached=True).owner.id` ([`examples/setup-check`](../examples/setup-check/)), or with a list of usernames kept in an environment variable or in storage |
| per-user data keyed by e-mail or by a user table | key it by `visitor.id` |
| an API key header checked for programmatic callers | no equivalent; see the fit table |
| a user id or name sent by the page (in JSON, a form field, a query) | never trust it; the server takes identity from the request's headers only |

Who may open the app at all is not the app's business: the owner chooses the
audience on the app's page in the Portal.

Locally there is no gateway. Without the header the SDK returns the app's
owner (asked from the Portal once the token is in `.env`), or the user named
by `EFP_DEV_VISITOR`; `EFP_DEV_VISITOR=anonymous` shows the app to a guest.

## 7. Secrets and settings

- Anything secret (a password, an API key of a service that is not a
  connector, a signing key) becomes an environment variable that the owner
  enters under **Environment variables** on the app's page. The app reads it
  with `os.environ`. Names may not start with `EFP_` and may not be `PORT`,
  `HOME`, `TMPDIR` or `PATH`.
- Nothing secret stays in the repository: not in code, not in a config file,
  not in a `static` app (every visitor can read every file of a static app).
- Settings that are not secret and that the owner should change without a
  deployment are environment variables too; settings that change with the
  code stay in a file next to it (`jobs.json`, `poll.json`).
- Never read `EFP_APP_TOKEN` yourself and never send it to the browser. The
  SDK uses it.
- List every variable the app needs in the README, with what happens without
  it. An app that needs a variable should say "not set up yet" on its page,
  not crash at import.

## 8. State: files and databases

The code directory is read-only. `HOME` and `TMPDIR` are private, writable
and emptied on every restart. Memory is gone whenever the app is stopped,
which happens after every idle period and every deployment. Whatever must
survive goes through `efp_app.storage`.

| Found in the project | Replace with |
|---|---|
| `open("data/x.json", "w")`, pickles, a `data/` directory | `storage.save_json("x.json", value)` / `storage.load_json("x.json", default=...)`; bytes with `storage.save` / `storage.load` |
| uploaded files kept on disk | `storage.save("uploads/<id>", data)`, after checking size and type; serve them back through a route that reads the object |
| a module-level dict or list that collects things | storage, with the dict at most as a cache that can be rebuilt |
| `functools.lru_cache` over something expensive | fine as a speed-up; it is empty after every restart. Keep results you paid for (model answers, slow reports) in storage instead ([`examples/release-notes`](../examples/release-notes/)) |
| a counter, a "last updated" time, a small settings record | one JSON object each |
| a table of records | one object per record, listed by prefix (`storage.list("items/")`); read them all for small sets, keep an index object for larger ones |
| SQLite | `Path(storage.data_dir()) / "app.db"` on a host in sandbox mode only: default journal mode (never WAL), a busy timeout, one writer. An app that must run on every host uses objects instead |
| PostgreSQL, MySQL, Redis, MongoDB started next to the app | not available. Objects in storage; or an existing server the cluster can reach, with its address and password in environment variables and a driver that installs as a wheel |

Storage has no transactions and no locks across requests. Two saves of the
same object at the same moment: one wins, the other is lost. Design so that
it cannot matter:

- give every writer an object of its own (`votes/<poll>/<visitor id>.json`)
  and combine on reading ([`examples/team-poll`](../examples/team-poll/));
- or give every item an object of its own and never rewrite a shared list;
- or carry a version number in the object and refuse a save that is based on
  an old one;
- an object that only one person can write (the owner's settings) needs none
  of this.

Keys are 1 to 512 characters of `A-Z a-z 0-9 . _ - /`. Anything that came
from a visitor and becomes part of a key is validated first.

## 9. Model calls

Calls to a model provider's own SDK become calls to `efp_app.llm`, which runs
on the owner's model provider through the Portal. The app needs no API key.

| Found in the project | Replace with |
|---|---|
| `client.chat.completions.create(model=..., messages=[...])` and its like | `llm.complete(messages, system=..., max_output_tokens=..., visitor=who)`; the text is `answer.text` |
| a system message inside `messages` | the `system=` argument; `messages` holds `user` and `assistant` turns with text content only |
| `stream=True` and a loop over chunks | `for piece in llm.stream(messages, ..., visitor=who): ...` (`astream` in async code) |
| a fixed model name | leave `model` out (the app's default), or choose from `app.info().llm.models` |
| an API key in the environment | remove it |
| retry loops around the call | remove them, or keep one retry for a failed connection; the SDK never repeats a model call by itself |

What `efp_app.llm` does not offer in this release: tool or function calls,
images or files as input, embeddings, audio, a JSON mode enforced by the
provider. A project that depends on one of these needs a redesign for that
part (ask for JSON in the prompt and validate it:
[`examples/incident-timeline`](../examples/incident-timeline/)), or its own
provider key in an environment variable and a network path to that provider.
Say so instead of pretending.

Every call is paid for by the app's owner, whoever the visitor is, and counts
against the app's daily budget. A converted app therefore must: cap
`max_output_tokens`; bound the input it sends; skip the call when there is
nothing to do; pass `visitor=`; catch `EfpError` and show `exc.user_message`
(a used-up budget is a sentence for the visitor, not a stack trace). Text
that comes from a visitor, a log or another system is data: say so in the
system prompt, and never execute what a model returns.

## 10. Calls to other systems

For Jira, Confluence, GitHub, Jenkins, Nexus and Splunk the app does not keep
credentials. It calls the owner's connector:

| Found in the project | Replace with |
|---|---|
| `requests.get(JIRA_URL + "/rest/api/2/issue/ABC-1", auth=(user, token))` | `connectors.get("jira", "/rest/api/2/issue/ABC-1", visitor=who)` |
| query parameters | keyword arguments of `get`, or `query={...}` of `request` |
| `response.raise_for_status()` | check `answer.status` (or `answer.ok`): the other system's 404 or 401 is an answer, not an exception |
| `response.json()` | `answer.json` (parsed when the body is JSON), `answer.text`, `answer.content` |
| POST, PUT, DELETE | `connectors.request(type, "POST", path, json=...)`; refused unless the owner allowed writing for that connector |
| a client library of that system (`jira`, `PyGithub`, `python-jenkins`) | plain REST paths through `connectors`; the libraries want credentials the app does not have |
| base URLs for links shown to visitors | `connectors.available()` gives each connector's `base_url` |

Rules that conversions break most often:

- **The connector is one shared account: the owner's service account, for
  every visitor.** Anything that means "me" to the other system means that
  account: Jira's `currentUser()`, GitHub's `/user` and `@me`, "my builds".
  Name the person explicitly instead, for example with `visitor.username`.
- Every visitor of the app reads those systems with the owner's access. An
  app that shows what a connector returns shows it to its whole audience.
- A visitor never names a path or a host. Validate what they type (an issue
  key, a repository name) before it becomes part of a path or a query
  ([`examples/jira-digest`](../examples/jira-digest/),
  [`examples/release-notes`](../examples/release-notes/)).
- A response body over 5 MiB is cut (`answer.truncated`). Ask for less:
  fields, a page size, a `tree` query.
- Calls cost the other system's capacity. Keep results in storage with the
  time they were fetched and reuse them while they are fresh.

Any other HTTP service is called as before (`httpx`, `urllib`), with its
address and secret in environment variables. Outbound requests follow the
proxy settings the platform gives every app (`HTTPS_PROXY`, `NO_PROXY`);
whether a given host is reachable from the cluster is a question for the
platform's operators.

## 11. Background work

An app runs only while somebody uses it. There is no place for work that
must happen on its own.

| Found in the project | Replace with |
|---|---|
| cron, APScheduler, `schedule`, a timer thread | do the work when a visitor asks, and keep the result with its time: "refresh when older than N minutes" on the next request |
| Celery, RQ, a queue and workers | do the work inside the request (it may take up to an hour) and stream progress to the page; or make the work smaller |
| a thread started at import time | remove it. A thread pool that begins and ends inside one request is fine |
| a page that polls the server every few seconds | load on demand, with a button or when the tab becomes visible. A polling tab keeps the app from ever being idle |
| live updates between visitors | a WebSocket, which keeps the app awake while somebody is connected; say so ([`examples/planning-poker`](../examples/planning-poker/)) |
| a bot that listens to a chat system or a webhook | does not fit |
| warm-up code that fills a cache at start | fill it on first use; keep the start short |

## 12. Dependencies

- `requirements.txt` lists what the app imports, nothing else. Development
  tools (pytest, linters) do not belong in it.
- Every package must install as a wheel for Python 3.11 on Linux from the
  platform's package index. No Git URLs, no local paths, no `-e`, no index
  options in the file. `efp-app check` flags such lines.
- Do not list `efp-app-sdk`; the host adds it. It also adds `uvicorn` for a
  `fastapi` app and `gunicorn` for a `flask` app when they are missing.
- Pin what the project already pins. Unpinned is acceptable for the
  framework itself.
- Remove what the conversion made unnecessary: auth libraries, database
  drivers, task queues, the model provider's SDK, clients of systems that are
  now connectors.
- A package that needs a system library works only if its wheel bundles it.

## Recipes

Each recipe lists the least that has to change. Complete apps of every type
are in [`examples/`](../examples/README.md).

Each recipe ends with a line that says how it was verified. "Run" means: a
small project was built as the recipe says and exercised with
`efp-app dev --prefix demo` and again without a prefix (the front page, a
static file, an API call, redirects, a form post, links between pages, a
page below the top level), on Windows with Python 3.13, in November 2026.
Not part of any run: the host itself (Linux, Python 3.11, nginx, gunicorn).

### FastAPI or Starlette

```json
{"name": "My app", "type": "fastapi", "entry": "main:app", "requirements": "requirements.txt"}
```

- Routes stay as they are: the gateway removes the prefix and uvicorn is told
  about it (`--root-path`), so `request.url_for(...)`, `url_for` in a Jinja
  template, the redirect to a path with a final slash and the generated
  `/docs` page (it loads `/<slug>/openapi.json`) are correct.
- Pages and scripts: relative URLs. Mount static files as usual
  (`app.mount("/static", StaticFiles(directory=...))`) and refer to them as
  `static/app.js`.
- Redirects: `RedirectResponse(request.url_for("name"))`. Not
  `app.url_path_for("name")`: that one returns the path without the prefix.
- A page below the top level:
  `<base href="{{ request.scope['root_path'] }}/">` in its template.
- A WebSocket route needs `uvicorn[standard]` (or `websockets`) in
  `requirements.txt`. Plain uvicorn answers the handshake with 404.
- Visitor: `who: Visitor = Depends(require_visitor)`.
- CORS middleware is not needed: the page and the API are one origin.

Verified: run with FastAPI 0.165, Starlette 1.10 and uvicorn 0.54, with a
raw WebSocket handshake through the gateway.

### Flask

```json
{"name": "My app", "type": "flask", "entry": "app:app", "requirements": "requirements.txt"}
```

- Routes stay as they are. The host sets `SCRIPT_NAME`, so `url_for()`
  returns addresses with the prefix, for a blueprint's routes as well. Use it
  everywhere: templates (`{{ url_for('static', filename='app.js') }}`),
  redirects, and URLs handed to scripts (`data-api="{{ url_for('api') }}"`).
- An application factory needs one more line. `"entry": "app:create_app()"`
  is refused by `efp-app check` and `efp-app dev` (the entry is
  `module:attribute`; the host's manifest check has the same rule), so call
  the factory at module level: `app = create_app()`.
- `app.secret_key` from an environment variable if sessions are still used
  for something other than sign-in. The session cookie needs no setting: the
  gateway sets its path to `/<slug>/`.
- `url_for(..., _external=True)` takes its scheme from the server: Flask's
  own server answered `http://` for a request the gateway marked as `https`.
  For an address that leaves the page (an e-mail), use
  `efp_app.app.public_url()`.
- Visitor: `get_visitor()` / `require_visitor()` from
  `efp_app.integrations.flask`.

Verified: run with Flask 3.1 (a factory, a blueprint, a session, a form
post) on Flask's own server with `SCRIPT_NAME` applied by the SDK, which is
what `efp-app dev --prefix` starts on Windows. gunicorn, which the host uses,
does not run on Windows and was not run.

### Streamlit

```json
{"name": "My app", "type": "streamlit", "entry": "app.py", "requirements": "requirements.txt"}
```

- The script runs unchanged; Streamlit serves itself under the prefix.
- Remove `server.*` and CORS / XSRF options from `.streamlit/config.toml`.
- Links: no `[Home](/)`; build from `efp_app.app.base_path()`.
- `st.session_state` and `st.cache_data` live in memory: gone when the app is
  stopped. What must stay goes to storage.
- Visitor: `efp_app.integrations.streamlit.get_visitor()` (Streamlit 1.37 or
  newer).
- Secrets: environment variables instead of `.streamlit/secrets.toml`.

Verified: the Streamlit examples ([`csv-explorer`](../examples/csv-explorer/),
[`sprint-burndown`](../examples/sprint-burndown/)) were run on Streamlit 1.67
under `efp-app dev --prefix` and used in a browser: the page, its WebSocket
and a widget round trip stay under the prefix. Not run on the real host.

### Gradio

```json
{"name": "My app", "type": "gradio", "entry": "app.py", "requirements": "requirements.txt"}
```

- `demo.launch()` without `server_name`, `server_port`, `root_path`,
  `share` or `auth`, inside `if __name__ == "__main__":`.
- Visitor: add a `request: gr.Request` parameter to the event function and
  call `efp_app.integrations.gradio.get_visitor(request)`.

- Behind a company proxy Gradio fails to start when its request to its own
  port goes to the proxy ("Couldn't start the app because ... failed (code
  502)"). The host and `efp-app dev` keep the loopback addresses out of the
  proxy for every app; when you start the script some other way, set
  `NO_PROXY=127.0.0.1,localhost` yourself.

Verified: [`examples/test-case-writer`](../examples/test-case-writer/) was
run on Gradio 6.32 under `efp-app dev --prefix` and used in a browser: the
page, its files and an event round trip stay under the prefix. Not run on
the real host.

### A static site or a built single-page app

```json
{"name": "My site", "type": "static", "entry": "dist"}
```

- Build it yourself (`npm run build`) with a relative base (Vite
  `base: "./"`) and commit the output directory; `entry` names it. The host
  runs no build.
- A router that uses the History API needs the server to answer unknown
  paths with `index.html`; the gateway does not: an address that is not a
  file answers 404. Use a hash router.
- Pages in nested directories work with relative URLs
  (`../../css/site.css`), and the address of a directory without its final
  slash is redirected inside the app.
- On the host a file or a directory whose name starts with a dot is not
  served, and a directory without an `index.html` is not listed. The local
  preview (Python's file server) serves and lists both.
- No backend means no visitor, no storage, no model, no connector, and no
  secret anywhere in the files. If the page needs any of those, it needs a
  small Python backend: see the next recipe.

Verified: run with a hand-written site with nested directories, on Python's
file server behind the local gateway. No build tool was run (there was no
Node): the pages name their files the way a build with a relative base
names them (`src="./js/app.js"`), and a page that names one the way the
default base does (`src="/assets/index-9c1e.js"`) was reported by
`efp-app check` and by the gateway. The two statements about the host are
read from its nginx configuration, which was not run.

### A single-page app with a Python backend

One `fastapi` app serves the API and the built front end:

```python
app = FastAPI()
# ... API routes under /api ...
app.mount("/", StaticFiles(directory=HERE / "dist", html=True), name="site")   # last
```

The front end is built with a relative base and calls `fetch("api/...")`.
An address that is neither an API route nor a file answers 404 (405 for a
`POST`), so a page of a History-API router cannot be reloaded or bookmarked:
use a hash router (`#/reports/2026`).

If the router must use the History API, answer its pages with `index.html`
and a `<base>`, so that the relative URLs of the page still start at the
app. Replace the mount above with:

```python
from fastapi.responses import HTMLResponse
from starlette.exceptions import HTTPException    # StaticFiles raises this one, not fastapi.HTTPException

INDEX = (HERE / "dist" / "index.html").read_text(encoding="utf-8")


class Site(StaticFiles):
    """The built files; any other address without a dot in its last part is a page of the router."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            name = path.replace("\\", "/")
            if exc.status_code != 404 or name.startswith("api/") or "." in name.rsplit("/", 1)[-1]:
                raise
            return HTMLResponse(INDEX.replace("<head>", '<head><base href="%s/">' % scope["root_path"], 1))


app.mount("/", Site(directory=HERE / "dist", html=True), name="site")   # last
```

During development the front end's dev server may proxy `/api` to
`efp-app dev`; that proxy is not part of the app.

Verified: run with FastAPI 0.165 and Starlette 1.10, both variants, with
"built" files written by hand (hashed names, a module script, a hash
route). No Node build and no dev-server proxy was run.

### Django

```json
{"name": "My site", "type": "command",
 "command": "SCRIPT_NAME=$EFP_APP_BASE_PATH python -m gunicorn mysite.wsgi --bind 127.0.0.1:$PORT --workers 1 --threads 8",
 "requirements": "requirements.txt"}
```

- gunicorn reads `SCRIPT_NAME`, takes the prefix off every request path and
  hands it to Django, which then builds every `reverse()`, `{% url %}`,
  `redirect()` and its own redirect to a path with a final slash with the
  prefix. List `gunicorn` in `requirements.txt` (the host adds it only for
  the type `flask`).
- gunicorn does not run on Windows (`No module named 'fcntl'`), so
  `efp-app dev` cannot start this command there. waitress does the same job
  on both systems: list `waitress` instead of `gunicorn`, set
  `"command": "python serve.py"` and add `serve.py`:

  ```python
  import os

  from waitress import serve

  from mysite.wsgi import application

  serve(
      application,
      host="127.0.0.1",
      port=int(os.environ["PORT"]),
      threads=8,
      url_prefix=os.environ.get("EFP_APP_BASE_PATH", ""),
      trusted_proxy="127.0.0.1",
      trusted_proxy_headers={"x-forwarded-proto"},
  )
  ```

  Without the two `trusted_proxy` lines waitress removes `X-Forwarded-Proto`
  and Django takes every request for plain `http`.
- Settings:

  ```python
  import os
  from urllib.parse import urlsplit

  BASE = os.environ.get("EFP_APP_BASE_PATH", "")  # "/<slug>" on the host, "" on a developer's machine
  PUBLIC = urlsplit(os.environ.get("EFP_APP_PUBLIC_URL") or "http://127.0.0.1/")

  ALLOWED_HOSTS = [PUBLIC.hostname, "127.0.0.1", "localhost"]  # 127.0.0.1: the host's health check
  SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
  CSRF_TRUSTED_ORIGINS = ["%s://%s" % (PUBLIC.scheme, PUBLIC.netloc)]

  STATIC_URL = BASE + "/static/"
  WHITENOISE_STATIC_PREFIX = "/static/"
  ```

  The host gives every app its public address in `EFP_APP_PUBLIC_URL`, so
  the owner enters nothing. The gateway sends the public host as `Host`:
  `USE_X_FORWARDED_HOST` is not needed.
- Do not write `STATIC_URL = "static/"` (relative) with WhiteNoise.
  WhiteNoise reads the setting when the process starts, before Django knows
  the prefix, and every `{% static %}` URL then starts with `/static/`,
  outside the app. With the two lines above the pages name
  `/<slug>/static/...` and WhiteNoise finds the files under `/static/`, where
  they are once the prefix is taken off.
- Static files: `whitenoise.middleware.WhiteNoiseMiddleware` right after
  `SecurityMiddleware`, `STATIC_ROOT` inside the entry directory, and the
  output of `python manage.py collectstatic` committed. The code directory
  is read-only on the host, so `collectstatic` cannot run there at start.
- A form post with `{% csrf_token %}` passes with these settings. Keep both
  `SECURE_PROXY_SSL_HEADER` and `CSRF_TRUSTED_ORIGINS`: with neither, behind
  a server that passes the gateway's headers on untouched, the post was
  refused (403, "Origin checking failed").
- `SECRET_KEY` and every other secret from environment variables.
- Sign-in: a small middleware replaces `django.contrib.auth` for
  authentication. Remove the login views and the admin's login.

  ```python
  from efp_app import visitor


  class VisitorMiddleware:
      def __init__(self, get_response):
          self.get_response = get_response

      def __call__(self, request):
          request.visitor = visitor.current(request.headers)  # a Visitor, or None for a guest
          return self.get_response(request)
  ```
- The database is the hard part: SQLite in `storage.data_dir()` (sandbox
  hosts only, no WAL, migrations run at start), or no ORM at all. Many Django
  projects are a poor fit for this reason; check section 1 honestly.

Verified: run with Django 6.1 and WhiteNoise 6.12, without a database
(`DATABASES = {}`; no `sessions`, `auth` or `admin` app), on waitress: once
with `serve.py` as above, once behind a wrapper that applies `SCRIPT_NAME`
the way gunicorn does. Checked: `reverse()`, `{% url %}`, redirects,
`{% static %}` and the files themselves, a form post with CSRF under
`X-Forwarded-Proto: https`, and the middleware with a signed visitor header.
Not run: the gunicorn command line itself (Windows), `collectstatic` against
a read-only directory, and anything with a database.

### Dash

```json
{"name": "My dashboard", "type": "flask", "entry": "app:server", "requirements": "requirements.txt"}
```

```python
import os
from dash import Dash

BASE = os.environ.get("EFP_APP_BASE_PATH", "")
app = Dash(__name__, requests_pathname_prefix=BASE + "/", routes_pathname_prefix="/")
server = app.server
```

gunicorn removes the prefix from the path because of `SCRIPT_NAME`, so Dash
routes at `/`, while the browser is told to ask under the prefix. List both
`dash` and `flask` in `requirements.txt`: `efp-app check` warns when a
`flask` app does not list `flask`.

Dash sends an `href` or a `src` to the browser as it is written, so build
them: `dash.get_relative_path("/analytics")`, `page["relative_path"]` for a
page of `dash.page_registry`, `dash.get_asset_url("logo.png")`. The files in
`assets/` and the pages of `use_pages=True` need nothing.

Verified: run with Dash 4.5 (two pages with `use_pages`, an asset, a
callback): the page, every file it loads, `_dash-layout`,
`_dash-dependencies` and a callback through `_dash-update-component`, on
Flask's own server with `SCRIPT_NAME` applied by the SDK. gunicorn was not
run.

### aiohttp

```json
{"name": "My tool", "type": "command", "command": "python server.py", "requirements": "requirements.txt"}
```

```python
app = web.Application()
# ... the routes, as before ...

if __name__ == "__main__":
    base = os.environ.get("EFP_APP_BASE_PATH", "")
    if base:  # the gateway keeps the prefix: mount the whole app under it
        root = web.Application()
        root.add_subapp(base, app)
        app = root
    web.run_app(app, host="127.0.0.1", port=int(os.environ["PORT"]))
```

Mounted like this, `request.app.router["name"].url_for()` and the redirects
of `normalize_path_middleware` carry the prefix. With `"strip_prefix": true`
and no mount, pages with relative URLs work as well, but those two leave the
app. The visitor is `efp_app.visitor.current(request.headers)`.

Verified: run with aiohttp 3.14 (static files, a form post, redirects, a
WebSocket), both ways.

### NiceGUI

```json
{"name": "My tool", "type": "command", "command": "python main.py", "strip_prefix": true,
 "requirements": "requirements.txt"}
```

- `ui.run(host="127.0.0.1", port=int(os.environ["PORT"]), reload=False, show=False)`.
- NiceGUI takes the prefix from the `X-Forwarded-Prefix` header that the
  gateway sends. Its own files, its socket and the address of
  `ui.link("Report", "/reports/2026")` then carry the prefix. There is
  nothing to configure.
- Every open page holds a WebSocket: the app stays awake while a tab is open
  (section 11).
- Visitor, inside a page function:
  `efp_app.visitor.current(ui.context.client.request.headers)`.

Verified: run with NiceGUI 4.0 and opened in a browser: two pages, a link, a
button whose handler runs on the server; every request stayed under the
prefix.

### Another server, as `command`

```json
{"name": "My tool", "type": "command", "command": "python server.py"}
```

The server reads `PORT` and `EFP_APP_BASE_PATH`, removes the prefix from each
request path before routing (or mounts its routes under it), and uses
relative URLs in its pages. Or set `"strip_prefix": true`, serve at the root
and rely on relative URLs alone; a redirect is then a relative `Location`
(`Location: done`) or has `EFP_APP_BASE_PATH` in front of its path, because
the server's own URL builder does not know the prefix. See
[`examples/env-status`](../examples/env-status/).

Read the prefix from the environment in the server rather than passing it as
an argument (`python server.py $EFP_APP_BASE_PATH`): the environment is what
section 8 of the contract promises. An argument works too. On Windows the
`sh` of Git would rewrite one that starts with `/` (`/demo` into
`C:/Program Files/Git/demo`); `efp-app dev` tells it not to.

Verified: run with a server on `http.server`, once with the prefix kept and
once with `"strip_prefix": true`, including the cookie rules of section 5
and the headers the gateway adds.

### A script, a command-line tool or a notebook

Give it one page:

1. Move the logic into functions that take arguments and return values (no
   `input()`, no `print()` as the result, no `sys.argv`, no global state).
2. Write a Streamlit script (or a Gradio `Blocks`) whose widgets collect the
   arguments, call the function and show the result: tables with
   `st.dataframe`, charts with `st.line_chart`, files with
   `st.download_button`.
3. Inputs that were files on disk become uploads (`st.file_uploader`) with a
   size limit; outputs that were files become downloads or storage objects.
4. Everything in sections 6 to 12 applies to the functions as well.

[`examples/csv-explorer`](../examples/csv-explorer/) is this pattern.

Verified: no script was converted for this guide; the example was run (see
Streamlit above).

## Verify

Do all of these, in this order. Each has a pass condition.

1. `efp-app check --strict` exits with 0.
2. `efp-app dev` starts the app; the front page loads; without
   `EFP_APP_TOKEN` in `.env` the page still loads and features that need the
   Portal say "not set up yet" instead of failing.
3. `efp-app dev --prefix my-app`: open `http://127.0.0.1:8000/my-app/`, use
   every page and every button once. The console shows no "outside the app"
   line and no 404 of the app for a file of a page, and the browser's
   network panel shows no request outside `/my-app/` and no failed one.
4. With `EFP_DEV_VISITOR=alice` and then `EFP_DEV_VISITOR=bob`: each sees
   their own data and cannot change the other's. With
   `EFP_DEV_VISITOR=anonymous` (only if the audience may be "Anyone"):
   nothing that needs a member works, and nothing crashes.
5. Stop and start the app: everything that should survive is still there
   (`.efp-data/` holds it locally).
6. `grep` finds no secret, token, password or private address in the entry
   directory, and no `currentUser()`, `@me` or `/user` in a connector path.
7. The project's own tests still pass. Tests of the converted parts use a
   stand-in for the Portal: see `sdk/tests/conftest.py` (`FakePortal`) and
   `sdk/tests/example_support.py` for how the examples do it.
8. After the first deployment, on the app's page in the Portal: every step of
   the deployment succeeded; the app answers at its address; **Logs** shows
   no error; a second member (and, if intended, a guest) sees what they
   should.

## Definition of done

- [ ] `efp-app.json` is valid, with a `description` and, if there is one, an `icon`.
- [ ] One entry directory holds everything the app needs; nothing is imported from outside it.
- [ ] No URL the browser reads starts with `/`; redirects and links built on the server use the prefix.
- [ ] No sign-in code is left; identity comes from the gateway on the server only.
- [ ] No secret is in the repository; every setting the owner must enter is listed in the README.
- [ ] Nothing that must survive lives in memory, in the code directory, `HOME` or `TMPDIR`.
- [ ] No two requests can lose each other's writes.
- [ ] Every model call is bounded (input and `max_output_tokens`), passes `visitor=`, and its errors end as sentences.
- [ ] Every connector call names people explicitly, validates what a visitor typed, and handles the other system's status.
- [ ] No background thread, scheduler, queue or polling timer is left.
- [ ] `requirements.txt` holds only what is imported, as wheels.
- [ ] The verification list above passed, and the README says how to run the app locally and what to turn on in the Portal.
- [ ] Everything that was left out of the conversion is written down, with the reason.

## Hand over: what only the owner can do

An agent or a developer without the owner's Portal account cannot finish
these; list them in the README or the pull request:

1. Push the directory to a GitHub repository that the platform's service
   account can read (the Portal names the account when it cannot).
2. In the Portal: **Apps > New app**: the repository, a branch or tag, the
   entry directory, a name. **Create and deploy**.
3. On the app's page, **Settings**: turn on the model and each connector the
   app uses (and writing, where the app writes); enter the environment
   variables; choose the audience.
4. For local development with the real model and connectors: copy the token
   from **Settings > App token** into `.env` as `EFP_APP_TOKEN`, with
   `EFP_PORTAL_URL`.
5. Deploy again after every push (**Deploy latest**).
