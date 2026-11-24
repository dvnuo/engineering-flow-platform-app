# efp-app-sdk

The Python SDK and command-line tool of EFP Apps: small web apps that members
write, that run inside the EFP app host and that use, with one token, what
their owner has in the EFP Portal.

```python
from efp_app import llm, images, connectors, storage, visitor, app

who = visitor.current(request.headers)            # the signed-in visitor, or None
answer = llm.complete("Hello", visitor=who)       # the owner's language model
seen = images.inspect(upload, "What is shown?")   # a vision model, for images
issue = connectors.get("jira", "/rest/api/2/issue/ABC-1")
storage.save_json("state.json", {"count": 1})     # survives restarts and deployments
app.base_path()                                   # the path prefix the app is served under
```

```
efp-app init fastapi my-app      copy a starter (fastapi, flask, streamlit, gradio, static)
efp-app dev                      run it locally the way the host runs it
efp-app check                    check the manifest, the files and the URLs before deploying
```

The developer guide is `docs/DEVELOPING_APPS.md` in the repository; the wire
contracts are in `docs/CONTRACTS.md`.

## Layout

| Path | What |
|---|---|
| `efp_app/config.py` | settings from the environment |
| `efp_app/errors.py` | one exception per error code, all derived from `EfpError` |
| `efp_app/client.py` | the HTTP clients (sync and async) of the Portal's SDK API |
| `efp_app/visitor.py` | the `X-EFP-Visitor` header |
| `efp_app/llm.py`, `images.py`, `connectors.py`, `app.py` | the calls |
| `efp_app/storage.py` | files, the host's storage service, or a local folder |
| `efp_app/integrations/` | FastAPI / Starlette, Flask, Streamlit, Gradio |
| `efp_app/manifest.py`, `_check.py`, `cli.py` | `efp-app.json` and the command-line tool |
| `efp_app/testing.py` | for an app's own tests: the environment of a deployed app, a stand-in Portal, the signed header of a visitor. A running app never imports it |
| `efp_app/_templates/` | the starters `efp-app init` copies |

Runtime dependency: `httpx` only. Python 3.9 or newer; Linux, macOS, Windows.

## The starter templates

`templates/` at the repository root is the single source of the starters.
`efp_app/_templates/` is a copy inside the package, so an installed wheel can
run `efp-app init`. After editing a starter:

```
python sync_templates.py            refresh the copy
python sync_templates.py --check    exit 1 when it is out of date
```

`tests/test_templates.py` fails when the two differ, and a wheel built from a
checkout always takes the top-level directory (`setup.py`).

## The example apps

`examples/` at the repository root holds twenty-six complete apps, from a
static page of links to dashboards, drawing boards, team games and tools
that use the model ([what each one shows](../examples/README.md)). They are
not part of the package: a member copies one into a repository of their own.
`tests/test_examples.py` and `tests/test_example_<name>.py` run every one of
them against the fake Portal, so an example cannot fall behind the SDK
without a test failing.

The reference of everything in this package, with examples that the tests
run, is [docs/SDK_REFERENCE.md](../docs/SDK_REFERENCE.md).

## Tests

```
cd sdk
python -m pytest
```

They need `pytest` and `httpx`. With `fastapi` installed the FastAPI helper
and starter run for real, with `flask` the Flask starter does; Streamlit and
Gradio are exercised through small stand-ins and need not be installed. The
tests that need a unix socket are skipped on Windows.

## Build

```
python -m pip wheel --no-deps -w dist ./sdk
```

gives `efp_app_sdk-<version>-py3-none-any.whl`. The app host bundles that
wheel and installs it into every app whose requirements do not name
`efp-app-sdk`.
