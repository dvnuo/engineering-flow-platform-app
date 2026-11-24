# Instructions for AI agents

This file is for an AI agent that was given a task about EFP apps. There are
two kinds of task. Find yours, then follow that part only.

- **A. Write an app, or convert an existing project into one.** The work
  usually happens in another repository; this one is your documentation and
  your source of examples.
- **B. Change this repository** (the app host, the SDK, the starters, the
  examples, the documents).

The documents are the authority. When this file and a document disagree, the
document wins; when a document and the code disagree, say so and follow the
code.

## A. Writing or converting an app

### Read, in this order

1. [docs/DEVELOPING_APPS.md](docs/DEVELOPING_APPS.md): what an app is, how it
   is started, the path prefix, the limits. Read all of it once.
2. [docs/CONVERTING_A_PROJECT.md](docs/CONVERTING_A_PROJECT.md), when there
   is an existing project: the procedure, the fit check, a recipe per
   framework, the verification list, the definition of done.
3. [docs/SDK_REFERENCE.md](docs/SDK_REFERENCE.md): look up every SDK name
   before you write it. Never guess a function, a parameter or an error
   class.
4. [docs/UI_KIT.md](docs/UI_KIT.md): how a page looks: the libraries every
   gateway serves at `/_efp/lib/`, the EFP kit, and the rules for a good
   page. Read it before you write the first page.
5. [examples/README.md](examples/README.md): find the example closest to
   your task with its "which example shows what" table, and read that
   example in full: code, README, and its test. The test of an example is
   `sdk/tests/test_example_<name>.py`; seven examples (`confluence-ask`,
   `jira-digest`, `log-explainer`, `release-notes`, `setup-check`,
   `team-links`, `team-poll`) are sections of `sdk/tests/test_examples.py`
   instead. Copy the example's structure rather than inventing one.

### What an app is, in ten lines

- One directory with `efp-app.json`, served at `https://<apps host>/<slug>/`.
- Types: `fastapi`, `flask`, `streamlit`, `gradio`, `command` (any Python
  server), `static` (files only). The host starts it; you do not.
- It listens on `127.0.0.1:$PORT`, as one Python 3.11 process, with
  dependencies installed as wheels from `requirements.txt`.
- Visitors are signed in by a gateway in front of it. The SDK tells the app
  who is visiting. The app has no login of its own.
- The SDK (`import efp_app`) gives it the owner's language model, for text
  (`efp_app.llm`), a vision model for questions about images
  (`efp_app.images`), the owner's connectors to Jira, Confluence, GitHub,
  Jenkins, Nexus and Splunk (`efp_app.connectors`), and durable storage
  (`efp_app.storage`).
- It is stopped when nobody uses it and started by the next visit: nothing
  survives in memory or on local disk, and nothing runs in the background.

### Rules you must not break

Each one is checked by `efp-app check`, by the verification list, or by a
reviewer. The reason for each is in the guide.

1. **No URL that the browser reads starts with `/`**, except
   `/_efp/lib/...`, the shared libraries every gateway serves. Relative URLs
   in pages, scripts and style sheets; the framework's URL builder
   (`url_for`, `request.url_for`) or `efp_app.app.base_path()` on the server.
2. **Identity comes from the gateway, on the server.** Use `require_visitor`
   / `get_visitor` (`efp_app.integrations.<framework>`) or
   `efp_app.visitor.current(headers)`. Never from a request body, a query
   parameter or the app's own cookie. Never add a login page. "Only the
   owner" is `visitor.username == app.info(cached=True).owner.username`, on
   the server; when `app.info` raises, nobody is the owner.
3. **A connector is the owner's shared service account, not the visitor.**
   Never `currentUser()`, `@me`, `/user` or "my ..." through a connector;
   name the person with `visitor.username`. Check `answer.status`: the other
   system's 404 or 401 is an answer, not an exception.
4. **Every model call is bounded and attributed**: a capped input, a
   `max_output_tokens`, `visitor=who`. Never execute or `eval` what a model
   returns; text from elsewhere is data and the prompt says so. An image
   call (`efp_app.images`) is a model call: bound the number and the size of
   the images too, and treat what an image shows as data.
5. **Every SDK error ends as a sentence**: catch `efp_app.errors.EfpError`,
   log it, show `exc.user_message`. A used-up budget or a switched-off
   connector must not produce a stack trace or a blank page.
6. **What must survive is in `efp_app.storage`.** Not in a module-level
   variable, not in the code directory, not in `HOME` or `TMPDIR`.
7. **No two requests may lose each other's writes.** Storage has no
   transactions and no "save only if unchanged": one object per writer or
   per item; where an object is shared, a `threading.Lock` around load,
   change, save (the app is one process), with a version number inside the
   lock when two pages can edit the same object.
8. **No background work**: no scheduler, no thread that outlives a request,
   no queue, no page that polls the server on a timer.
9. **No secret in the repository**, and none in a `static` app at all.
   Secrets are environment variables the owner enters in the Portal.
10. **Validate everything a visitor sends** before it becomes a storage key,
    a connector path, a query, a file or a prompt: type, length, format,
    count, upload size.
11. **A page is finished, not bare.** Build it on the UI kit
    ([docs/UI_KIT.md](docs/UI_KIT.md)) and keep its rules: every action
    shows that it is working, nothing jumps, an empty state says what to do,
    a failure is a sentence, light and dark, phone and desktop, no HTML built
    from data. A converted project keeps its own look when it has one.
12. **Do not invent.** If the platform cannot do something the project needs
    (see "Does it fit?" in the conversion guide), stop and report it. Do not
    work around a limit, fake a feature, or silently drop one.

### Commands

```
pip install efp-app-sdk                       the SDK and the efp-app command
efp-app init fastapi my-app                   a starter (fastapi, flask, streamlit, gradio, static)
efp-app check --strict [directory]            the manifest, the files, root-absolute URLs; must exit 0
efp-app dev [directory] [--port N]            run it as the host does, without a prefix (serves /_efp/lib/ too)
efp-app dev [directory] --prefix my-app       run it under /my-app/ behind a local gateway; prints every request that leaves the app
```

`pip install efp-app-sdk` takes the SDK from the company's package index.
Where that index is not at hand and a checkout of this repository is,
install from it: `pip install <checkout>/sdk`. That build leaves
`sdk/build/` and `sdk/efp_app_sdk.egg-info/` in the checkout; git ignores
both, so they are not a change of yours to that repository.

Local settings go in `.env` in the app's directory (never committed):
`EFP_APP_TOKEN` and `EFP_PORTAL_URL` (needed only for the model, image
analysis and the connectors), `EFP_DEV_VISITOR=<username>` or `anonymous` to
choose who is visiting. You will usually have no token: set
`EFP_DEV_VISITOR`, or there is no visitor at all and `require_visitor`
answers 401. Without a token every model, image and connector call raises
`ConfigurationError`; the app must turn that into its "not set up yet"
sentence, and what happens when the call works is shown by tests (below),
not by clicking.

### What to deliver

- The app's directory: `efp-app.json`, the code, `requirements.txt`,
  `.env.example`, `.gitignore`.
- A `README.md` in it: what the app does, how to run it locally, what the
  owner must turn on in the Portal (model, image analysis, connectors,
  writing), every environment variable it needs, and what was left out of a
  conversion and why.
- Tests, in the app's own repository, for every route that calls the model,
  image analysis or a connector and for every check of identity.
  `efp_app.testing` is part of the SDK and needs nothing from this
  repository: `testing.deployed(...)` gives a test the environment of a
  deployed app and a stand-in Portal
  (`portal.on("POST", "/llm/complete", testing.completion("..."))`,
  `portal.on("POST", "/images/inspect", testing.inspection("..."))`),
  `testing.visitor_headers("alice")` the signed header of a visitor
  ([docs/SDK_REFERENCE.md](docs/SDK_REFERENCE.md), section 14; an example is
  under "Verify" in the conversion guide).
- A report that states which items of the
  [verification list](docs/CONVERTING_A_PROJECT.md#verify) you ran and what
  each showed, which you could not run and why, and the steps that only the
  owner can do in the Portal (registering, turning things on, the token).

You cannot register, deploy or configure an app in the Portal, and you
should not ask for the owner's token. Say what the owner has to do.

## B. Changing this repository

### Where things are

| Path | What |
|---|---|
| `docs/CONTRACTS.md` | every wire format and shared name between the Portal, the host, an app and the SDK. **Change it first, then the code.** |
| `host/` | the app host (`efp_app_host`): manager, gateway, pages, nginx template; tests in `host/tests` |
| `sdk/` | `efp-app-sdk` (`efp_app`) and the `efp-app` command; tests in `sdk/tests` |
| `templates/` | the starters; `sdk/efp_app/_templates/` is a mirror kept by `python sdk/sync_templates.py` |
| `examples/` | complete example apps; each has `sdk/tests/test_example_<name>.py` (the first seven are in `test_examples.py`) |
| `lib/` | the shared browser libraries served at `/_efp/lib/` (docs/UI_KIT.md): third-party files written by `scripts/vendor_libs.py` only, never edited by hand; the EFP kit in `lib/efp-kit@<version>/`, a new directory for each changed version |
| `integration/` | the container smoke test CI runs (needs Docker and Linux) |
| `k8s/`, `terraform/`, `scripts/` | deployment files and their checker |

The Portal's half of every contract is in another repository
(`engineering-flow-platform-portal`); a change to sections 4, 5 or 9 of the
contracts needs a change there too.

### Checks before you say it is done

```
cd host && python -m pytest          the host
cd sdk  && python -m pytest          the SDK, the starters and every example
python scripts/check_manifests.py    the Kubernetes files (needs PyYAML)
python sdk/sync_templates.py --check the starters' mirror
python scripts/vendor_libs.py --check lib/ against its manifest
```

The container smoke test (`integration/ci.sh`) runs in CI on every push; say
so when your change can only be verified there.

### Conventions

- English, ASCII only, in code, comments, tests and documents. Text in
  another script is written as escapes in code and lives only in the locale
  catalogues (`host/pages/zh-*.json`).
- No new runtime dependency for the SDK (it depends on `httpx` only) and
  none for an example beyond its framework. The libraries under `lib/` are not
  dependencies: every gateway serves them (docs/UI_KIT.md).
- An example follows the rules in
  [examples/README.md](examples/README.md#writing-a-new-example) and comes
  with tests.
- Comments and commit messages say why, not what. Sentences, not labels.
- Do not weaken a test to make it pass. Do not skip the contract.
