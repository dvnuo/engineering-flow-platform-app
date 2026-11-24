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
9. Replace model calls (those that send images too) and calls to Jira,
   Confluence, GitHub, Jenkins, Nexus or Splunk with the SDK (sections 9 and
   10).
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
| must do work when nobody is visiting: cron jobs, schedulers, watchers, bots that listen | does not fit; an idle app is stopped (section 11). A job whose only purpose is to keep a copy fresh (a cache warmer, a periodic fetch) is not such a need: section 11 replaces it with "fetch when somebody asks and the copy is old" |
| must be called by other programs with their own credentials (a public API, a webhook receiver) | does not fit, unless the audience "Anyone" is acceptable and the app checks the caller itself; the gateway lets in browsers with a session |
| needs its own domain, TLS certificate, port, or raw TCP/UDP | does not fit |
| holds more than about 1 GiB of data, single files over 64 MiB, or more than about 1 GiB of memory | does not fit by default; these are the platform's limits and only its operators can raise them |
| needs a relational database | fits with changes: `efp_app.storage` objects, or SQLite in the app's data directory on a host in sandbox mode (no WAL), or an existing database server reachable from the cluster with its address and password in environment variables |
| sends images to a vision model (screenshots, photos, charts) | fits: `efp_app.images`, one to four images in a call, no stream (section 9); the owner needs the Image analysis connector |

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
  .env.example        what a developer puts in .env: every name, no value
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
  never commit `.env`. The starter's `.env.example` has the SDK's settings
  (`EFP_APP_TOKEN`, `EFP_PORTAL_URL`, `EFP_DEV_VISITOR`); add one line for
  every variable of the app's own (section 7), as a name with an empty value
  and a comment that says what it is. `efp-app dev` reads `.env`, so this
  file is how the next developer learns what to put there.

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
| a style sheet | `url(/img/bg.png)` in `static/style.css` | `url(../img/bg.png)`: relative to the style sheet's own address, not the page's. A file beside the sheet is its bare name: `url(/static/bg.png)` becomes `url(bg.png)`. `efp-app check` prints this form for a sheet that is served at the path it has in the directory |
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
| "only signed-in users" | nothing to do for the audiences "Only the owner", "The owner's group" and "Every member": everyone who gets through is a member. With "Anyone", `get_visitor` returns `None` for a guest and `require_visitor` answers 401 (the body is the framework's: `{"detail": "..."}` in FastAPI, an HTML error page in Flask; a page's script goes by the status) |
| "only admins" | compare with the app's owner on the server, by username: `visitor.username == efp_app.app.info(cached=True).owner.username` ([`examples/setup-check`](../examples/setup-check/)), or with a list of usernames kept in an environment variable or in storage |
| per-user data keyed by e-mail or by a user table | key it by `visitor.id` |
| an API key header checked for programmatic callers | no equivalent; see the fit table |
| a user id or name sent by the page (in JSON, a form field, a query) | never trust it; the server takes identity from the request's headers only |

Who may open the app at all is not the app's business: the owner chooses the
audience on the app's page in the Portal.

Three facts about the comparison with the owner:

- A username is what to compare. It is unique in the Portal (at most 64
  characters) and does not change once the member exists; compare it exactly
  as the SDK gives it, and write the names of a list the same way the Portal
  shows them. `visitor.id` is as stable, but on a developer's machine the
  visitor named by `EFP_DEV_VISITOR` has a stand-in id, not the member's real
  one, so a comparison of ids is never true there.
- `app.info` asks the Portal. It raises `ConfigurationError` on a developer's
  machine without a token, and another `EfpError` when the Portal cannot be
  reached. In both cases nobody is the owner: catch the error, refuse, and
  show `exc.user_message`. Never fall back to "everybody may".
- `cached=True` asks once per process. The owner of an app does not change
  while it runs.

Locally there is no gateway. Without the header the SDK returns the user
named by `EFP_DEV_VISITOR`, or else the app's owner (asked from the Portal,
which needs the token in `.env`); `EFP_DEV_VISITOR=anonymous` shows the app
to a guest. With neither a token nor `EFP_DEV_VISITOR` there is no visitor at
all: every request is a guest's and `require_visitor` answers 401. For a
first run put `EFP_DEV_VISITOR=<a username>` in `.env`.

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
  it, and in `.env.example` as a name without a value (section 3). An app
  that needs a variable should say "not set up yet" on its page, not crash at
  import.
- A secret that was ever committed stays in the repository's history, and
  removing the line does not take it back. Say in the hand-over which secrets
  were in the code, so that the owner revokes them.

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
| SQLite | `Path(storage.data_dir()) / "app.db"` on a host in sandbox mode only: default journal mode (never WAL), a busy timeout, one writer. An app that must run on every host uses objects instead. Which mode a host runs is its operators' choice and cannot be seen from a developer's machine: ask them, or let the app find out when it runs (`storage.mode()` is `"sandbox"` there; on a host in the other mode, `"hidden"`, `storage.data_dir()` raises `DataDirUnavailableError`) and say so on its page |
| PostgreSQL, MySQL, Redis, MongoDB started next to the app | not available. Objects in storage; or an existing server the cluster can reach, with its address and password in environment variables and a driver that installs as a wheel |

Storage has no transactions and no locks across requests. Two saves of the
same object at the same moment: one wins, the other is lost. Design so that
it cannot matter:

- give every writer an object of its own (`votes/<poll>/<visitor id>.json`)
  and combine on reading ([`examples/team-poll`](../examples/team-poll/));
- or give every item an object of its own and never rewrite a shared list;
- or, where one shared object cannot be avoided, guard the three steps
  (load, change, save) with a `threading.Lock` of the module. The host runs
  one process per app, so a lock inside the process is enough
  ([SDK_REFERENCE.md](SDK_REFERENCE.md), 6.6);
- an object that only one person can write (the owner's settings) needs none
  of this.

The lock covers two requests that meet. It does not cover two pages that
each loaded an object, changed it for a minute and now save: the second save
would undo the first. Where one object can be edited from two pages (two
tabs, two people), also keep a version number in it: a save names the
version its page loaded and is refused with 409 when the stored one is
newer, and the check and the save happen inside the lock
([`examples/mind-map`](../examples/mind-map/)). A version number without the
lock is not enough: storage has no "save only if unchanged", so the check and
the save would still be two steps.

Keys are 1 to 512 characters of `A-Z a-z 0-9 . _ - /`. Anything that came
from a visitor and becomes part of a key is validated first.

**The data the project already has.** Nothing can be copied into a deployed
app's storage from outside: there is no shell, no upload and no shared disk.
Two ways bring existing data along, and both are code of the app; the third
is to leave it behind:

- Reference data that may be read by everyone who can read the repository
  and that changes with the code (a catalogue, a glossary): commit it as a
  file in the entry directory and read it from there. It needs no storage.
- Everything else (notes, votes, records people entered): give the app an
  import that only the owner may use. The owner exports the old data to a
  file (JSON or CSV), uploads it once on that page, and the app validates
  each record and saves it as objects, reporting how many it took and which
  it refused. Make the import safe to run twice (the same record gives the
  same key). Never commit the export.
- Leave it behind, when it cannot be matched to the people who will use the
  app. Records keyed by the names of the project's own login belong to
  accounts that no longer exist: an import needs a table from each old name
  to a Portal username, which only the owner can supply (as a column of the
  export, or a field of the import page). Without one the app starts empty.

A data file that is already committed stays in the repository's history
whatever you do. Take it out of the app's directory when the app no longer
reads it, and say so when it holds anything personal.

Say in the hand-over which of the three applies, and that old data is not in
the app until the owner has imported it.

## 9. Model calls

Calls to a model provider's own SDK become calls to `efp_app.llm`, which runs
on the owner's model provider through the Portal. The app needs no API key.
Calls that send an image become calls to `efp_app.images` (below the table).

| Found in the project | Replace with |
|---|---|
| `client.chat.completions.create(model=..., messages=[...])` and its like | `llm.complete(messages, system=..., max_output_tokens=..., visitor=who)`; the text is `answer.text` |
| a system message inside `messages` | the `system=` argument; `messages` holds `user` and `assistant` turns with text content only |
| `stream=True` and a loop over chunks | `for piece in llm.stream(messages, ..., visitor=who): ...` (`astream` in async code) |
| a fixed model name | leave `model` out (the app's default), or choose from `app.info().llm.models` |
| an API key in the environment | remove it |
| retry loops around the call | remove them, or keep one retry for a failed connection; the SDK never repeats a model call by itself |
| a message with an image in it (`{"type": "image_url", ...}`, a `data:` URL, an `images=[...]` argument), or a command-line tool that reads an image | `images.inspect(data, prompt, system=..., max_output_tokens=..., visitor=who)` from `efp_app.images`; `data` is the content of the image file as bytes, and the text is `answer.text` |

Images go to a model of their own. A project that sends images to a vision
model uses `efp_app.images`, which asks the model of the owner's Image
analysis connector, not the app's text model:

- One call is one question about one to four images (JPEG, PNG, WebP or
  GIF; 5 MiB each), and its answer comes whole: no stream, no tool calls,
  no choice of model, no earlier turns. What the model must know goes into
  the prompt and `system` of that call.
- An image is given as its bytes. Where the project passed a path, a URL or
  base64 text, read the file, fetch the address or decode the text first.
- More than four images for one question: ask in several calls, or join
  them into fewer images before the call (with a library that comes as a
  wheel). Say in the hand-over which of the two you did.
- The provider's own framing is gone. A tool that wrapped the prompt in
  instructions of its own, or returned an envelope around the answer, did
  that outside the model: the Portal sends the app's prompt as it is and
  returns the model's text as it is. Ask for the form you need in the
  prompt and validate what comes back.
- `app.info().image_analysis.enabled` says whether the owner has turned it
  on; a page that offers an upload asks it first, and the call's errors end
  as sentences all the same.

What the SDK does not offer in this release: tool or function calls, files
other than images as input (a PDF, a spreadsheet), images as output,
embeddings, audio, a JSON mode enforced by the provider. A project that
depends on one of these needs a redesign for that part (ask for JSON in the
prompt and validate it:
[`examples/incident-timeline`](../examples/incident-timeline/)), or its own
provider key in an environment variable and a network path to that provider.
Say so instead of pretending.

Every call is paid for by the app's owner, whoever the visitor is, and counts
against the app's daily budget; an image call counts against the same one. A
converted app therefore must: cap `max_output_tokens`; bound the input it
sends (for images: how many a visitor can send, and how large); skip the
call when there is nothing to do; pass `visitor=`; catch `EfpError` and show
`exc.user_message` (a used-up budget is a sentence for the visitor, not a
stack trace). Text that comes from a visitor, a log, an image or another
system is data: say so in the system prompt, and never execute what a model
returns.

Where the sentence goes depends on who asked. A route that a script calls
answers JSON (`{"error": {"code": exc.code, "message": exc.user_message}}`,
the shape the starters use) and the script shows the message. A route that
the browser opens as a page (a link to an export, a download, a report)
answers a small page with the sentence and a link back, with the status of
the error: raw JSON in a browser tab is not an answer for a visitor.

A model call may take minutes; the SDK waits up to 600 seconds for one. The
request that makes it stays open that long, which the gateway allows (up to
an hour). In a `flask` app the host runs gunicorn with one process and eight
threads, so eight such requests at once occupy the whole app; its
`--timeout 120` does not end a long request, because with threads gunicorn
applies it to the worker as a whole and not to one request (gunicorn's
documented behaviour; not run for this guide). Stream the answer
(`llm.stream`) when a visitor would otherwise watch a blank page. An image
call cannot be streamed: the page shows that it is working until the answer
is there.

## 10. Calls to other systems

For Jira, Confluence, GitHub, Jenkins, Nexus and Splunk the app does not keep
credentials. It calls the owner's connector:

| Found in the project | Replace with |
|---|---|
| `requests.get(JIRA_URL + "/rest/api/2/issue/ABC-1", auth=(user, token))` | `connectors.get("jira", "/rest/api/2/issue/ABC-1", visitor=who)` |
| query parameters | keyword arguments of `get`, or `query={...}` of `request` |
| `timeout=30` | `connectors.request(type, "GET", path, query={...}, timeout=30, visitor=who)`. Not `connectors.get(..., timeout=30)`: every keyword of `get` except `visitor` is a query parameter, so that would send `?timeout=30` to the other system and wait the default 120 seconds |
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

"Refresh on the next request" has three cases, and a converted app handles
all of them: the copy is fresh (use it); the copy is old and the fetch works
(save the new one with its time, use it); the copy is old and the fetch
fails. In the last case show the old copy, say when it was fetched and show
the error as a sentence; when there is no copy yet, the sentence alone. Do
not save a failed fetch over a good copy, and do not try again on every
request of the next minute: remember the time of the failure too. Keep the
copy in storage, not only in memory, or every start of the app begins with a
fetch. No example of this repository shows all three cases
([`examples/build-health`](../examples/build-health/) keeps each answer in
storage with its time, and keeps a refusal in place of the old answer), so
here they are in one function:

```python
MAX_AGE = 600        # seconds a copy counts as fresh
RETRY_AFTER = 60     # seconds to leave the other system alone after a failed fetch


def jobs(who=None):
    """The kept copy: ``items`` and ``fetched_at``, and ``problem`` when the last fetch failed."""
    kept = storage.load_json("jobs.json", default=None) or {}
    now = time.time()
    if now - kept.get("fetched_at", 0) < MAX_AGE or now - kept.get("failed_at", 0) < RETRY_AFTER:
        return kept                                              # fresh, or a fetch has just failed
    problem = None
    try:
        answer = connectors.get("jenkins", "/api/json", visitor=who, tree="jobs[name,color]")
        if answer.ok and isinstance(answer.json, dict):
            kept = {"items": answer.json.get("jobs", []), "fetched_at": now}    # a new copy; the old problem is gone
        else:
            problem = "Jenkins answered with status %d." % answer.status
    except EfpError as exc:
        log.warning("%s", exc)
        problem = exc.user_message
    if problem:
        kept = dict(kept, failed_at=now, problem=problem)        # the old copy stays, with the sentence
    storage.save_json("jobs.json", kept)
    return kept
```

The page shows `items` with "as of `fetched_at`", and `problem` when there is
one. Two requests that fetch at the same moment save the same thing, so this
object needs no lock.

## 12. Dependencies

- `requirements.txt` lists what the app imports, nothing else. Development
  tools (pytest, linters) do not belong in it.
- Every package must install as a wheel for Python 3.11 on Linux from the
  platform's package index. No Git URLs, no local paths, no `-e`, no index
  options in the file. `efp-app check` flags such lines.
- Do not list `efp-app-sdk`; the host adds it. It also adds `uvicorn` for a
  `fastapi` app and `gunicorn` for a `flask` app when they are missing, so
  both ways work. List them, as the starters and the examples do: the file
  then says everything the app runs on, and you choose the version. A
  WebSocket route needs `uvicorn[standard]` listed in any case.
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
- `gr.Blocks(..., analytics_enabled=False)`. The host and `efp-app dev` set
  `GRADIO_ANALYTICS_ENABLED=False` already; the argument keeps the script
  from reporting to Gradio when it is started some other way, and keeps
  `launch(theme=...)` from failing on a theme whose fonts are strings
  ("'str' object has no attribute 'name'", Gradio 6.32).

- Behind a company proxy Gradio fails to start when its request to its own
  port goes to the proxy ("Couldn't start the app because ... failed (code
  502)"). The host and `efp-app dev` keep the loopback addresses out of the
  proxy for every app; when you start the script some other way, set
  `NO_PROXY=127.0.0.1,localhost` yourself.

Verified: a Gradio 6.32 app (the earlier, Gradio version of
[`examples/test-case-writer`](../examples/test-case-writer/)) was run under
`efp-app dev --prefix` and used in a browser: the page, its files and an
event round trip stay under the prefix. Not run on the real host. The
example is now a FastAPI page on the UI kit: a converted Gradio tool that
should look like the other apps can take the same route, keeping its
Python functions and replacing `gr.Blocks` with a page and a few routes.

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
2. `efp-app dev` with `EFP_DEV_VISITOR=<a username>` in `.env` and no
   `EFP_APP_TOKEN`: the app starts, the front page loads, and features that
   need the Portal (the model, image analysis, a connector, the owner check)
   say "not set up yet" instead of failing. Without `EFP_DEV_VISITOR` and
   without a token there is no visitor at all, and a 401 from
   `require_visitor` is then the right answer, not a failure.
3. `efp-app dev --prefix my-app`: open `http://127.0.0.1:8000/my-app/`, use
   every page and every button once. The console shows no "outside the app"
   line and no 404 of the app for a file of a page, and the browser's
   network panel shows no request outside `/my-app/` and no failed one. One
   line does not count: `outside the app: GET /favicon.ico`. A browser asks
   for that address by itself when a page names no icon; give the page
   `<link rel="icon" href="static/icon.png">` (or `href="data:,"`) to be rid
   of it. A button that calls the model, image analysis or a connector
   cannot succeed without the app's token, which only the owner has:
   without one, such a button must end in the "not set up" sentence here,
   and what it does when the call works is shown by the tests of item 7.
   Say in the report which buttons were used live and which only in tests.
4. Identity, as far as the app has it. If it keeps data per visitor: with
   `EFP_DEV_VISITOR=alice` and then `EFP_DEV_VISITOR=bob`, each sees their
   own data and cannot change the other's. If visitors share data: what bob
   adds carries bob's name, taken on the server. If a part is for the owner
   only: bob is refused there, on the server, not just by a hidden button.
   With `EFP_DEV_VISITOR=anonymous`: nothing that needs a member works, and
   nothing crashes (the audience is the owner's choice, so check it even
   when the app is meant for members). Who the owner is comes from the
   Portal, so without the app's token nobody is the owner and the part for
   the owner refuses everybody: that it lets the owner in is shown by the
   tests of item 7, not here.
5. Stop and start the app: everything that should survive is still there
   (`.efp-data/` holds it locally).
6. No secret and no "me" in the entry directory. Run both, and read every
   hit: the name of a variable is fine, a value is not.

   ```
   grep -rnEi "passw|secret|token|[_-]key|apikey|bearer|authorization|private key|://[^/ \"']*:[^/ \"']*@" --exclude-dir=.git --exclude-dir=.venv --exclude-dir=.efp-data --exclude-dir=node_modules --exclude-dir=__pycache__ --exclude=.env .
   grep -rnE "currentUser\(\)|@me\b|/user([\"'?/]|$)|/myself\b" --include="*.py" .
   ```

   The first finds passwords, keys and addresses with a password in them;
   also look for host names and IP addresses of internal systems that are
   now connectors. The second finds what means "the account of the
   connector" to Jira (`currentUser()`, `/myself`) and GitHub (`@me`,
   `/user`). `.env` is not committed and may hold the token.
7. The project's own tests still pass, and the converted parts have tests
   that run without a Portal: `efp_app.testing`, which is part of the SDK
   ([SDK_REFERENCE.md](SDK_REFERENCE.md), section 14), gives a test the
   environment of a deployed app, a stand-in Portal that answers the model,
   image and connector calls with what the test says, and the signed header
   of a visitor. At least: one test per route that calls the model, image
   analysis or a connector (the call works; the call is refused; for an
   image call also: a file that is no image, one that is too large), one
   for every check of identity (a member, another member, the owner, a
   guest if there can be one).
8. After the first deployment, on the app's page in the Portal: every step of
   the deployment succeeded; the app answers at its address; **Logs** shows
   no error; a second member (and, if intended, a guest) sees what they
   should.

A test of item 7, for a Flask app. For FastAPI the client is
`TestClient(app, root_path="/example")` and every request carries the
prefix (`client.post("/example/api/summary", ...)`), as uvicorn hands it to
the app on the host; without the prefix a mounted `StaticFiles` answers 404:

```python
from efp_app import testing


def test_the_summary(tmp_path):
    with testing.deployed(tmp_path) as portal:
        portal.on("POST", "/llm/complete", testing.completion("Three bugs are open."))
        from app import app                      # inside the block: the app reads its settings

        client = app.test_client()
        answer = client.post("/api/summary", headers=testing.visitor_headers("alice"))
        assert answer.get_json() == {"text": "Three bugs are open."}
        sent = portal.sent_to("/llm/complete")[0]                # what the app asked the model
        assert sent["max_output_tokens"] <= 600 and sent["system"]

        portal.on("POST", "/llm/complete", testing.error(429, "budget_exceeded"))
        refused = client.post("/api/summary", headers=testing.visitor_headers("alice"))
        assert refused.get_json()["error"]["code"] == "budget_exceeded"

        assert client.post("/api/summary").status_code == 401    # a guest: no header
```

## Definition of done

- [ ] `efp-app.json` is valid, with a `description` and, if there is one, an `icon`.
- [ ] One entry directory holds everything the app needs; nothing is imported from outside it.
- [ ] No URL the browser reads starts with `/`; redirects and links built on the server use the prefix.
- [ ] No sign-in code is left; identity comes from the gateway on the server only.
- [ ] No secret is in the repository; every setting the owner must enter is listed in the README.
- [ ] Nothing that must survive lives in memory, in the code directory, `HOME` or `TMPDIR`.
- [ ] No two requests can lose each other's writes.
- [ ] Every model call is bounded (input and `max_output_tokens`), passes `visitor=`, and its errors end as sentences. The same for every image call, with the number and the size of the images bounded.
- [ ] Every connector call names people explicitly, validates what a visitor typed, and handles the other system's status.
- [ ] No background thread, scheduler, queue or polling timer is left.
- [ ] `requirements.txt` holds only what is imported, as wheels.
- [ ] The parts that call the model, image analysis, a connector or check identity have tests that run without a Portal (`efp_app.testing`).
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
   variables; choose the audience. When the app reads images
   (`efp_app.images`): first set up the **Image analysis** connector among
   your own connectors in the Portal (an AI Platform account and a model),
   then turn image analysis on for the app here, the way a connector is
   turned on. Until both are done the app's image features answer "not
   turned on".
4. For local development with the real model and connectors: copy the token
   from **Settings > App token** into `.env` as `EFP_APP_TOKEN`, with
   `EFP_PORTAL_URL`.
5. Deploy again after every push (**Deploy latest**).
6. Revoke every password, key and token that was in the old code or its
   history (section 7); the converted app does not use them.
7. Bring the old data along, if there is any: export it and import it once on
   the app's import page (section 8). Until then the app starts empty.
