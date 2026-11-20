# EFP Apps SDK reference

The SDK of EFP Apps is the Python package `efp_app` (distribution
`efp-app-sdk`, version 0.1.0). It runs on Python 3.9 or newer and needs one
other package, `httpx` (0.24 or newer, below 1.0). It imports without any web
framework installed.

This file names every public part of the SDK: signature, parameters,
defaults, limits, return value, errors. Its last section, 14, is about
testing an app in its own repository with `efp_app.testing`. The guide is
[DEVELOPING_APPS.md](DEVELOPING_APPS.md); the wire formats are in
[CONTRACTS.md](CONTRACTS.md). The code in `sdk/efp_app/` decides.
`sdk/tests/test_sdk_reference.py` runs the examples of this file, compares
its signatures and dataclass fields with the source, and fails when a public
name is missing here.

## 1. Overview

| Module | Use it for |
|---|---|
| `efp_app.visitor` | who is visiting: verifies the `X-EFP-Visitor` header (section 3) |
| `efp_app.llm` | the owner's language model: `complete`, `stream`, `acomplete`, `astream` (section 4) |
| `efp_app.connectors` | the owner's connectors: Jira, Confluence, GitHub, Jenkins, Nexus, Splunk (section 5) |
| `efp_app.storage` | data that survives restarts and deployments (section 6) |
| `efp_app.app` | facts about the app: owner, model, budget, base path, public address (section 7) |
| `efp_app.client` | the HTTP clients behind `llm`, `connectors` and `app` (section 8) |
| `efp_app.integrations` | the visitor of a request in a framework: `efp_app.integrations.fastapi`, `efp_app.integrations.flask`, `efp_app.integrations.streamlit`, `efp_app.integrations.gradio` (section 9) |
| `efp_app.errors` | the exceptions; all derive from `EfpError` (section 10) |
| `efp_app.config` | the settings read from the environment (section 2) |
| `efp_app.manifest` | the rules of `efp-app.json` (section 11) |
| `efp_app.cli` | the `efp-app` command: `init`, `dev`, `check` (section 12) |
| `efp_app.testing` | an app's own tests: the host's environment, a fake Portal and the gateway's visitor header, with no Portal and no host (section 14). A running app never uses it |
| `efp_app._check`, `efp_app._sse`, `efp_app._devgateway` | internal: what `efp-app check` looks at (12.4), the parser of streamed answers (4.6), the local gateway of `efp-app dev --prefix` (12.3) |

The import lines of an app (the last one is for FastAPI; section 9 has the
other frameworks):

<!-- runnable -->
```python
from efp_app import app, connectors, llm, storage, visitor
from efp_app import EfpError, Visitor
from efp_app.errors import BudgetExceededError, StorageNotFoundError
from efp_app.integrations.fastapi import get_visitor, require_visitor
```

The five calls most apps make:

<!-- runnable -->
```python
from efp_app import app, connectors, llm, storage, visitor
from efp_app.errors import EfpError

who = visitor.current(request_headers)                 # a Visitor, or None for an anonymous visitor
try:
    answer = llm.complete("Say hello.", max_output_tokens=100, visitor=who)
    issue = connectors.get("jira", "/rest/api/2/issue/ABC-1", visitor=who)
    storage.save_json("last.json", {"answer": answer.text, "issue": issue.status})
except EfpError as exc:
    print(exc)                                         # "code: detail": for the log
    print(exc.user_message)                            # a sentence for the visitor
print(app.base_path())                                 # "" on a developer's machine, "/{slug}" when deployed
```

### 1.1 Names of the package

`import efp_app` gives these names (`efp_app.__all__`):

| Name | What |
|---|---|
| `efp_app.app`, `efp_app.client`, `efp_app.config`, `efp_app.connectors`, `efp_app.errors`, `efp_app.llm`, `efp_app.storage`, `efp_app.visitor` | the modules |
| `efp_app.EfpError` | the same class as `errors.EfpError` |
| `efp_app.Visitor` | the same class as `visitor.Visitor` |
| `efp_app.__version__` | the version, a string: `"0.1.0"` |

`efp_app.manifest`, `efp_app.cli`, `efp_app.testing` and the modules of
`efp_app.integrations` are not imported by `import efp_app`: import them by
their full name.

### 1.2 Rules that hold everywhere

- A function that calls the Portal has an asynchronous twin with the prefix
  `a`: `llm.acomplete`, `llm.astream`, `connectors.arequest`,
  `connectors.aget`, `connectors.aavailable`, `app.ainfo`. The twin takes the
  same arguments. `storage` and `visitor` have synchronous functions only.
- A function that calls the Portal takes `client=` (section 8); without it
  the shared client of the process is used. `connectors.get` and
  `connectors.aget` are the exception: their keyword arguments are query
  parameters.
- `visitor=` takes a `Visitor` or the `X-EFP-Visitor` value. The Portal then
  records who caused the call (3.7).
- Every error the SDK raises on purpose derives from `EfpError`, with two
  exceptions that are mistakes in the calling code: `ValueError` for an
  unknown `reasoning_effort`, and `TypeError` for data that `storage.save`
  cannot store. `storage.save_json` lets the error of `json.dumps` through.
- The SDK reads the environment at each call. It does not read `.env`:
  `efp-app dev` does that and hands the values to the app's process (12.3).

### 1.3 How to read this file

Signatures are copied from the source. A block of fields lists the fields of
a dataclass as `name: type = default`; a field without a default is required.

Every Python example runs in `sdk/tests/test_sdk_reference.py`, except the
ones that need FastAPI, Flask, Streamlit or Gradio. In the source of this
file a runnable example has the HTML comment `<!-- runnable -->` on the line
before its fence. A comment `<!-- environment: NAME -->` above that names
the situation it runs in, when that is not the first one of this table:

| Environment | The situation |
|---|---|
| `local` (the default) | A developer's machine. `EFP_APP_TOKEN` and `EFP_PORTAL_URL` are set, nothing else. |
| `deployed` | The host. `EFP_APP_ID`, `EFP_APP_SLUG=example`, `EFP_APP_BASE_PATH=/example`, `EFP_APP_PUBLIC_URL=https://apps.example.com/example/`, `EFP_VISITOR_KEY`, `EFP_APP_DATA_DIR`, `EFP_APP_TOKEN`, `EFP_PORTAL_URL` and `PORT=20003` are set. |
| `no-token` | A developer's machine with no `EFP_` variable at all. |
| `budget-used-up` | As `local`; the Portal refuses every model call with `budget_exceeded` and `Retry-After: 3600`. |
| `stream-fails` | As `local`; a streamed answer breaks off with an error event after its first piece. |

An environment line may also set variables (`EFP_DEV_VISITOR=carol`).

The examples talk to a fake Portal with these answers:

- The app is `example`, named `Example`, at
  `https://apps.example.com/example/`. Its owner is `alice` (id 5, display
  name `Alice`, group `QA` with id 3).
- The model is `gpt-5.4`. Every model call answers `Hello.`, streamed as the
  pieces `Hel` and `lo.`, with 12 input tokens, 3 output tokens and 0.002
  credits.
- Three connectors are turned on: `jira` and `jenkins` (read only) and
  `github` (writes allowed). The other types are off. Jira knows the issue
  `ABC-1` and a search that finds it; another Jira path answers 404.

`request_headers` is the one name an example uses without defining it: the
headers of the request being handled. In `deployed` it holds the
`X-EFP-Visitor` header that the gateway adds for the signed-in member `bob`
(id 12, name `Bob`, group `QA` with id 3). In every other environment it is
empty. In an app, pass `request.headers` there.

An `assert` in an example states what the SDK guarantees, or what the fake
Portal above answers.

## 2. Configuration

### 2.1 Environment variables

The first table has one row per variable of `efp_app.config`. For a deployed
app the host sets them (CONTRACTS.md, section 8); the third column says with
what. On a developer's machine only `EFP_APP_TOKEN` and `EFP_PORTAL_URL` are
needed, and only for the model, the connectors and `app.info`.

| Variable | Constant | Value on the host | In `.env` on a developer's machine | Without it |
|---|---|---|---|---|
| `EFP_APP_ID` | `config.APP_ID` | the app's id | never set it | the process counts as a developer's machine (2.2) |
| `EFP_APP_SLUG` | `config.APP_SLUG` | the slug | not needed (`efp-app dev --prefix NAME` sets it) | `app.slug()` returns `None` |
| `EFP_APP_TOKEN` | `config.APP_TOKEN` | the app token | the app token, from the app's page in the Portal | every call to the Portal raises `ConfigurationError`: the functions of `llm` and `connectors`, `app.info`, `app.ainfo`. Storage, `visitor.parse` and the other functions of `app` still work |
| `EFP_PORTAL_URL` | `config.PORTAL_URL` | the Portal's address inside the cluster | the Portal's address as a browser opens it, for example `https://efp.example.com` | as for the token. A value that does not start with `http://` or `https://` raises `ConfigurationError` as well |
| `EFP_APP_BASE_PATH` | `config.APP_BASE_PATH` | `/{slug}` | not needed: `efp-app dev` removes it, `efp-app dev --prefix NAME` sets it to `/NAME` | `app.base_path()` returns `""` |
| `EFP_APP_PUBLIC_URL` | `config.APP_PUBLIC_URL` | `{apps address}/{slug}/` | not needed (`efp-app dev --prefix NAME` sets it) | `app.public_url()` returns `http://127.0.0.1:{PORT}/` on a developer's machine when `PORT` is set, else `None` |
| `EFP_VISITOR_KEY` | `config.VISITOR_KEY` | the app's visitor key, base64url text | not needed: no gateway signs a header there | no `X-EFP-Visitor` value can be verified: `visitor.parse` returns `None` |
| `EFP_APP_DATA_DIR` | `config.APP_DATA_DIR` | on a host in `sandbox` mode: the app's own directory | not needed | storage chooses another place (6.1) |
| `EFP_APP_STORAGE_SOCKET` | `config.APP_STORAGE_SOCKET` | on a host in `hidden` mode: the unix socket of the storage service | not needed | storage chooses another place (6.1) |
| `EFP_DEV_VISITOR` | `config.DEV_VISITOR` | not set; ignored when deployed | optional: the username to act as, or `anonymous` | the local visitor is the owner of the app (3.2) |
| `PORT` | `config.PORT` | the loopback port the app listens on | optional: `efp-app dev` listens there unless `--port` is given; it sets `PORT` for the app | `app.public_url()` cannot name a local address; nothing else in the SDK reads it |

The SDK also reads these, when it builds an HTTP client (section 8):

| Variable | Effect |
|---|---|
| `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE` | The first one that names an existing file is the CA bundle for calls to the Portal, deployed or not. A name that is not a file is skipped. A file that cannot be loaded raises `ConfigurationError`. |
| `SSL_CERT_DIR` | A directory of CA certificates, used with or without the file above. A name that is not a directory is skipped. |
| `HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY`, `NO_PROXY` | Used on a developer's machine only. A deployed app never calls the Portal through a proxy. |

How values are read:

- Leading and trailing blanks are dropped. An empty value counts as not set.
- `EFP_PORTAL_URL` loses its trailing slashes. It may hold a path:
  `https://corp.example.com/efp/` gives requests to
  `https://corp.example.com/efp/api/app-sdk/v1/...`.
- `EFP_APP_BASE_PATH` gets a leading slash and loses a trailing one;
  `/` becomes `""`.
- `PORT` must be a whole number from 1 to 65535; anything else counts as not
  set.

A `.env` for local work (next to `efp-app.json`, never committed):

```text
EFP_APP_TOKEN=efpa_...
EFP_PORTAL_URL=https://efp.example.com
EFP_DEV_VISITOR=carol
```

Without a token the app still starts. Only the calls that need the Portal
fail:

<!-- environment: no-token -->
<!-- runnable -->
```python
from efp_app import llm, storage
from efp_app.errors import ConfigurationError

storage.save("works.txt", "storage needs no token")
caught = None
try:
    llm.complete("Hello")
except ConfigurationError as exc:
    caught = exc
assert caught.code == "not_configured" and caught.status is None
assert "EFP_APP_TOKEN" in caught.message
assert caught.user_message == "This app is not set up yet. Its owner needs to finish the configuration."
```

### 2.2 Deployed, or a developer's machine

The process is **deployed** when `EFP_APP_ID` is set and not blank. Nothing
else is looked at. `app.is_deployed()` and `config.is_deployed()` return that
answer.

| | Deployed | A developer's machine |
|---|---|---|
| A request without `X-EFP-Visitor` | `visitor.current` returns `None`: an anonymous visitor | `visitor.current` returns the user named by `EFP_DEV_VISITOR`, else the owner of the app, else `None` |
| A header whose payload names another app | refused: the app id in it must be `EFP_APP_ID` | not checked |
| `Visitor.raw` | the header value; `visitor=who` forwards it | `None` for the local stand-in; nothing is forwarded |
| Storage | `EFP_APP_DATA_DIR` or `EFP_APP_STORAGE_SOCKET`; with neither, `StorageUnavailableError` | `./.efp-data/` in the current working directory, unless one of the two variables is set |
| `app.base_path()` | `/{slug}` | `""`, or `/NAME` under `efp-app dev --prefix NAME` |
| `app.public_url()` | `EFP_APP_PUBLIC_URL` | `EFP_APP_PUBLIC_URL` when set, else `http://127.0.0.1:{PORT}/`, else `None` |
| `app.slug()` | the slug | `None`, or `NAME` under `efp-app dev --prefix NAME` |
| Proxy variables | ignored | used |
| The message of a missing token | says to redeploy or to reset the token | says to export the token or to put it in `.env` |

### 2.3 `efp_app.config`

The constants `config.APP_ID`, `config.APP_SLUG`, `config.APP_TOKEN`,
`config.APP_BASE_PATH`, `config.APP_PUBLIC_URL`, `config.PORTAL_URL`,
`config.VISITOR_KEY`, `config.APP_DATA_DIR`, `config.APP_STORAGE_SOCKET`,
`config.DEV_VISITOR` and `config.PORT` hold the names of the variables of
2.1.

#### `config.load`

<!-- signature: efp_app.config.load -->
```python
def load(env: Optional[Mapping[str, str]] = None) -> Settings:
```

| Parameter | Meaning |
|---|---|
| `env` | The mapping to read. `None`: `os.environ`, read now. |

Returns a `config.Settings`. Raises nothing: a missing or unusable value
becomes `None` (or `""` for the base path).

#### `config.is_deployed`

<!-- signature: efp_app.config.is_deployed -->
```python
def is_deployed(env: Optional[Mapping[str, str]] = None) -> bool:
```

Returns `load(env).deployed`. Raises nothing.

#### `config.Settings`

A frozen dataclass. `repr()` leaves out `token` and `visitor_key`.

<!-- fields: efp_app.config.Settings -->
```python
app_id: Optional[str] = None
slug: Optional[str] = None
token: Optional[str] = None
portal_url: Optional[str] = None
base_path: str = ''
public_url: Optional[str] = None
visitor_key: Optional[str] = None
data_dir: Optional[str] = None
storage_socket: Optional[str] = None
port: Optional[int] = None
dev_visitor: Optional[str] = None
```

| Member | Meaning |
|---|---|
| `app_id`, `slug`, `token`, `portal_url`, `base_path`, `public_url`, `visitor_key`, `data_dir`, `storage_socket`, `port`, `dev_visitor` | The variables of 2.1, in the order of that table, read as described there. |
| `deployed` | A property: `True` when `app_id` is set. |
| `require_token()` | Returns the token. Without one it raises `ConfigurationError`; the message names `EFP_APP_TOKEN` and says what to do, deployed or locally. |
| `require_portal_url()` | Returns the Portal's address. It raises `ConfigurationError` when the address is missing or does not start with `http://` or `https://`; the message names `EFP_PORTAL_URL`. |

<!-- signature: efp_app.config.Settings.require_token efp_app.config.Settings.require_portal_url -->
```python
def require_token(self) -> str:
def require_portal_url(self) -> str:
```

<!-- runnable -->
```python
from efp_app import config

settings = config.load()                               # the environment of the process
assert settings.deployed is False                      # EFP_APP_ID is not set
assert settings.base_path == "" and settings.port is None
assert settings.require_token() == settings.token
assert settings.token not in repr(settings)

hosted = config.load({"EFP_APP_ID": "0b0f", "EFP_APP_BASE_PATH": "mindmap/", "PORT": "20003"})
assert hosted.deployed is True and hosted.base_path == "/mindmap" and hosted.port == 20003
assert config.is_deployed({"EFP_APP_ID": "0b0f"}) is True
assert config.is_deployed({"EFP_APP_ID": "  "}) is False     # a blank value is not set
```

## 3. `efp_app.visitor`

The gateway of the host adds the header `X-EFP-Visitor` to every request of
a signed-in visitor. The value is `v1.<payload>.<signature>`: the payload is
JSON in base64url, the signature is an HMAC-SHA256 made with the app's
visitor key (`EFP_VISITOR_KEY`), and the value is valid for 60 seconds
(CONTRACTS.md, section 6.4). This module verifies the value inside the app's
process. It calls nobody, except once to learn the owner of the app on a
developer's machine (3.2).

Its names: `visitor.Visitor`, `visitor.current`, `visitor.parse`,
`visitor.header_value`, `visitor.dev_visitor_id`, `visitor.reset_dev_visitor`, `visitor.HEADER` (the
name of the header, `"X-EFP-Visitor"`) and `visitor.log` (the logger
`efp_app`, which gets the one warning of 3.2).

### 3.1 `visitor.Visitor`

A frozen dataclass: a signed-in visitor.

<!-- fields: efp_app.visitor.Visitor -->
```python
id: int
username: str
name: str
group: Optional[str] = None
group_id: Optional[int] = None
raw: Optional[str] = None
```

| Field | Meaning |
|---|---|
| `id` | The Portal's user id. For a local stand-in named by `EFP_DEV_VISITOR`: a number made from the name (3.6), not a member's id. |
| `username` | The Portal username. Never empty. |
| `name` | The display name; the username when the header carries none. |
| `group` | The name of the visitor's group, or `None`. |
| `group_id` | The id of that group, or `None`. |
| `raw` | The header value the visitor was read from; `None` for a local stand-in. `repr()` does not show it and `==` does not compare it. |

A `Visitor` is always true in a condition: `if who:` tells a signed-in
visitor from an anonymous one.

### 3.2 `visitor.current`

<!-- signature: efp_app.visitor.current -->
```python
def current(headers: Optional[Mapping[str, Any]] = None) -> Optional[Visitor]:
```

| Parameter | Meaning |
|---|---|
| `headers` | The headers of the request being handled, as any mapping: `request.headers` in FastAPI, Starlette and Flask, `st.context.headers` in Streamlit, the `headers` of a `gr.Request` in Gradio, a WSGI environment. `None`: a request without the header. |

Returns the `Visitor`, or `None` for an anonymous visitor. Raises nothing.

| The header `X-EFP-Visitor` is | Deployed | A developer's machine |
|---|---|---|
| present and valid | the `Visitor` | the `Visitor` (this needs `EFP_VISITOR_KEY`) |
| present and not valid: wrong signature, expired, made for another app, no key | `None` | `None` |
| absent | `None` | the local stand-in below |

The local stand-in, in this order:

1. `EFP_DEV_VISITOR` is `anonymous`, `anon`, `none` or `nobody` (in any
   letter case): `None`.
2. `EFP_DEV_VISITOR` is another name: a `Visitor` with that name as
   `username` and as `name`, the id `visitor.dev_visitor_id(name)` (3.6) and
   no group. The Portal is not asked.
3. Otherwise the owner of the app, from `app.info(cached=True)`: the owner's
   id, username, display name, group name and group id. The Portal is asked
   once and the answer is kept for the life of the process.
4. The owner cannot be learnt (no token, the Portal refuses or cannot be
   reached): `None`, and one warning on the logger `efp_app`. The Portal is
   asked again after 30 seconds at the earliest.

A local stand-in has `raw=None`.

Deployed:

<!-- environment: deployed -->
<!-- runnable -->
```python
from efp_app import visitor

who = visitor.current(request_headers)                 # request.headers in a web framework
assert (who.id, who.username, who.name, who.group, who.group_id) == (12, "bob", "Bob", "QA", 3)
assert who.raw == request_headers["X-EFP-Visitor"]

assert visitor.current({}) is None                     # no header: an anonymous visitor
assert visitor.current() is None
assert visitor.current({"X-EFP-Visitor": "v1.forged.forged"}) is None
```

On a developer's machine, with a token:

<!-- runnable -->
```python
from efp_app import visitor

who = visitor.current(request_headers)                 # no gateway, no header: the owner of the app
assert (who.id, who.username, who.name, who.group, who.group_id) == (5, "alice", "Alice", "QA", 3)
assert who.raw is None
```

With `EFP_DEV_VISITOR=carol`:

<!-- environment: local EFP_DEV_VISITOR=carol -->
<!-- runnable -->
```python
from efp_app import visitor

who = visitor.current({})
assert (who.username, who.name, who.group, who.group_id, who.raw) == ("carol", "carol", None, None, None)
assert who.id == visitor.dev_visitor_id("carol")       # made from the name; another name, another id
```

With `EFP_DEV_VISITOR=anonymous`:

<!-- environment: local EFP_DEV_VISITOR=anonymous -->
<!-- runnable -->
```python
from efp_app import visitor

assert visitor.current({}) is None                     # the app as a visitor who is not signed in sees it
```

### 3.3 `visitor.parse`

<!-- signature: efp_app.visitor.parse -->
```python
def parse(
    header_value: Optional[str],
    key: Union[str, bytes, None] = None,
    *,
    now: Optional[float] = None,
) -> Optional[Visitor]:
```

| Parameter | Meaning |
|---|---|
| `header_value` | The value of `X-EFP-Visitor`. |
| `key` | The visitor key: its base64url text (padding optional) or the raw bytes. `None`: `EFP_VISITOR_KEY`. |
| `now` | The time to check the expiry against, in seconds since the epoch. `None`: `time.time()`. |

Returns the `Visitor`. Returns `None`, and never raises, when

- the value is missing, empty or not a `str`;
- there is no key, or the key is not base64url text;
- the value is not `v1.<payload>.<signature>` in base64url characters;
- the signature is wrong (compared in constant time);
- the payload is not a JSON object;
- `exp` is not a number, or `now` is later than `exp` (at `now == exp` the
  value still counts);
- `EFP_APP_ID` is set and the payload names another app;
- `sub` is not a whole number, or `username` is not a non-empty string.

An app treats every one of these as an anonymous visitor.

<!-- environment: deployed -->
<!-- runnable -->
```python
import time

from efp_app import visitor

value = request_headers["X-EFP-Visitor"]
who = visitor.parse(value)                             # the key is EFP_VISITOR_KEY
assert who.username == "bob" and who.raw == value
assert visitor.parse(value, key=b"another key") is None
assert visitor.parse(value, now=time.time() + 61) is None   # a value is valid for 60 seconds
assert visitor.parse(None) is None and visitor.parse("garbage") is None
```

### 3.4 `visitor.header_value`

<!-- signature: efp_app.visitor.header_value -->
```python
def header_value(headers: Any) -> Optional[str]:
```

Returns the text of the `X-EFP-Visitor` header in `headers`, without blanks
around it, or `None`. It does not verify anything. The name is found in any
letter case and as the WSGI key `HTTP_X_EFP_VISITOR`; a name or a value may
be `bytes`; a value may be a list, of which the first item counts. Raises
nothing: an object that is not a mapping gives `None`.

<!-- runnable -->
```python
from efp_app import visitor

assert visitor.header_value({"x-efp-visitor": "v1.a.b"}) == "v1.a.b"
assert visitor.header_value({"HTTP_X_EFP_VISITOR": "v1.a.b"}) == "v1.a.b"        # a WSGI environment
assert visitor.header_value({b"X-EFP-Visitor": b" v1.a.b "}) == "v1.a.b"
assert visitor.header_value({"accept": "text/html"}) is None
assert visitor.header_value(None) is None
```

### 3.5 `visitor.reset_dev_visitor`

<!-- signature: efp_app.visitor.reset_dev_visitor -->
```python
def reset_dev_visitor() -> None:
```

Forgets the owner that `visitor.current` remembered (3.2, step 3) and lets
the warning of step 4 be logged again. For tests. Returns `None`, raises
nothing.

<!-- runnable -->
```python
from efp_app import visitor

first = visitor.current({})                            # asks the Portal for the owner
assert visitor.current({}) == first                    # remembered: no second request
visitor.reset_dev_visitor()
assert visitor.current({}) == first                    # asked again
```

### 3.6 `visitor.dev_visitor_id`

<!-- signature: efp_app.visitor.dev_visitor_id -->
```python
def dev_visitor_id(username: str) -> int:
```

The id that `visitor.current` gives a local stand-in named by
`EFP_DEV_VISITOR`. It is made from the name (blanks around it and letter
case do not count), is the same on every run and on every machine, and lies
between 900,000,000 and 999,999,999: far above the ids the Portal gives its
members, and nobody's real id. Two different names give two different ids
(two names with the same number are possible in principle and unlikely in
practice), so an app that keeps data per `visitor.id` can be tried as two
members on one machine: run it as `EFP_DEV_VISITOR=alice`, then as
`EFP_DEV_VISITOR=bob`. Returns an `int`, raises nothing.

<!-- runnable -->
```python
from efp_app import visitor

alice = visitor.dev_visitor_id("alice")
assert alice == visitor.dev_visitor_id(" Alice ")       # the same person however the name is typed
assert alice != visitor.dev_visitor_id("bob")
assert 900_000_000 <= alice < 1_000_000_000
```

### 3.7 Passing the visitor on: `visitor=`

The functions of `llm` and `connectors` take `visitor=`. The SDK then sends
the header `X-EFP-Visitor` with the call and the Portal records who caused
it (CONTRACTS.md, section 9).

- A `Visitor` stands for its `raw` value; a `str` stands for itself.
- The value is sent only when it has the form `v1.<base64url>.<base64url>`.
  Anything else is left out without an error: `None`, a local stand-in
  (`raw` is `None`), a malformed string.
- The SDK does not check the signature or the expiry of a value it forwards.

`client.visitor_header_value` (section 8.6) returns what would be sent.

<!-- environment: deployed -->
<!-- runnable -->
```python
from efp_app import client, llm, visitor

who = visitor.current(request_headers)
assert client.visitor_header_value(who) == who.raw     # what visitor=who sends
assert client.visitor_header_value("not a header value") is None
answer = llm.complete("Hello", visitor=who)
```

## 4. `efp_app.llm`

The owner's language model, through the Portal: every function sends one
`POST {EFP_PORTAL_URL}/api/app-sdk/v1/llm/complete` (CONTRACTS.md, section
9.2). Every call uses the model provider of the app's owner and counts
against the app's daily budget, whoever the visitor is. The request has no
field for tools: there are no tool calls.

Its names: `llm.complete`, `llm.stream`, `llm.acomplete`, `llm.astream`,
`llm.Completion`, `llm.Usage`, `llm.CompletionStream`,
`llm.AsyncCompletionStream`, `llm.LLM_TIMEOUT` (`600.0`),
`llm.REASONING_EFFORTS` (`("low", "medium", "high")`) and `llm.Messages` (the
type of the first argument).

### 4.1 The four calls

| Function | Kind | Returns |
|---|---|---|
| `llm.complete` | synchronous, the whole answer | `llm.Completion` |
| `llm.stream` | synchronous, piece by piece | `llm.CompletionStream` |
| `llm.acomplete` | `async def`, the whole answer | `llm.Completion` |
| `llm.astream` | a plain function that returns an awaitable, asynchronous stream | `llm.AsyncCompletionStream` |

<!-- signature: efp_app.llm.complete efp_app.llm.stream efp_app.llm.acomplete efp_app.llm.astream -->
```python
def complete(
    messages: Messages,
    *,
    system: Optional[str] = None,
    model: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    reasoning_effort: Optional[str] = None,
    visitor: Any = None,
    timeout: _client.TimeoutLike = None,
    client: Optional[_client.Client] = None,
) -> Completion:

def stream(
    messages: Messages,
    *,
    system: Optional[str] = None,
    model: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    reasoning_effort: Optional[str] = None,
    visitor: Any = None,
    timeout: _client.TimeoutLike = None,
    client: Optional[_client.Client] = None,
) -> CompletionStream:

async def acomplete(
    messages: Messages,
    *,
    system: Optional[str] = None,
    model: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    reasoning_effort: Optional[str] = None,
    visitor: Any = None,
    timeout: _client.TimeoutLike = None,
    client: Optional[_client.AsyncClient] = None,
) -> Completion:

def astream(
    messages: Messages,
    *,
    system: Optional[str] = None,
    model: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    reasoning_effort: Optional[str] = None,
    visitor: Any = None,
    timeout: _client.TimeoutLike = None,
    client: Optional[_client.AsyncClient] = None,
) -> AsyncCompletionStream:
```

`_client` is `efp_app.client`. All arguments after `messages` are keyword
arguments.

### 4.2 Parameters

| Parameter | Type | Default | Meaning and limits |
|---|---|---|---|
| `messages` | `str`, one message, or a sequence of messages | required | A `str` is one user message. A message is a mapping `{"role": ..., "content": ...}`, a pair `(role, content)`, or an object with the attributes `role` and `content`. `role` is `"user"`, `"assistant"` or `"system"`. `content` must be a `str`. System messages are taken out of the list and joined with a blank line into the system prompt. At least one user or assistant message must remain. |
| `system` | `Optional[str]` | `None` | The system prompt. Together with a system message in `messages` it raises `InvalidRequestError`. |
| `model` | `Optional[str]` | `None` | `None`: the app's default model. The allowed names are `app.info().llm.models`; another name raises `ModelNotAllowedError`. |
| `max_output_tokens` | `Optional[int]` | `None` | The most tokens the answer may use; reasoning tokens count as output tokens. Sent as given. The SDK has no default and checks no range. |
| `temperature` | `Optional[float]` | `None` | Sent as given. The provider may ignore it. The SDK checks no range. |
| `reasoning_effort` | `Optional[str]` | `None` | `"low"`, `"medium"` or `"high"`, written exactly so. `None`: the field is left out of the request and the Portal uses `low`. Any other value raises `ValueError` before a request is made. The Portal lowers the level to what the model supports. |
| `visitor` | `Visitor`, `str` or `None` | `None` | Who caused the call (3.7). |
| `timeout` | seconds (`int`, `float`) or an `httpx.Timeout` | `None` | `None`: `llm.LLM_TIMEOUT`, 600 seconds. See 4.7. |
| `client` | `client.Client`; `client.AsyncClient` for `acomplete` and `astream` | `None` | `None`: the shared client of the process (section 8). |

The body of the request, for `llm.complete("Hello")`:

```json
{"messages": [{"role": "user", "content": "Hello"}], "system": null, "model": null,
 "max_output_tokens": null, "temperature": null, "stream": false}
```

`"stream"` is `true` for `stream` and `astream`. `"reasoning_effort"` is
added only when the argument is given.

A high `reasoning_effort` with a small `max_output_tokens` can spend the
whole allowance on reasoning. The answer then has an empty `text` and the
`finish_reason` `"length"`.

### 4.3 `llm.Completion` and `llm.Usage`

Frozen dataclasses. `str(completion)` is `completion.text`.

<!-- fields: efp_app.llm.Completion -->
```python
text: str
model: Optional[str] = None
finish_reason: Optional[str] = None
usage: Usage = Usage()
```

| Field | Meaning |
|---|---|
| `text` | The answer. It can be empty. |
| `model` | The model that answered. |
| `finish_reason` | `"stop"`, or `"length"` when the answer reached `max_output_tokens`. The SDK passes on what the Portal sends. |
| `usage` | A `llm.Usage`. |

<!-- fields: efp_app.llm.Usage -->
```python
input_tokens: int = 0
output_tokens: int = 0
credits: Optional[float] = None
```

| Member | Meaning |
|---|---|
| `input_tokens`, `output_tokens` | Token counts of the call; `0` when the Portal sent no number. |
| `credits` | What the call cost, or `None` when the provider reports no cost. |
| `total_tokens` | A property: `input_tokens + output_tokens`. |

### 4.4 `llm.CompletionStream`

What `llm.stream` returns. The app does not construct it.

| Member | Meaning |
|---|---|
| iteration: `for piece in stream`, `next(stream)` | The pieces of the text as they arrive. Each piece is a non-empty `str`. The iteration ends after the final event. |
| `text` | Everything received so far, as one `str`. |
| `done` | `True` once the final event has arrived. |
| `model`, `finish_reason` | As in `llm.Completion`; `None` until `done`. |
| `usage` | A `llm.Usage`; `None` until `done`. |
| `completion()` | Reads the rest of the stream and returns the whole answer as a `llm.Completion`. |
| `close()` | Closes the connection. Calling it again does nothing. |
| `with stream:` | Closes the stream when the block is left. |

<!-- signature: efp_app.llm.CompletionStream.completion efp_app.llm.CompletionStream.close -->
```python
def completion(self) -> Completion:
def close(self) -> None:
```

### 4.5 `llm.AsyncCompletionStream`

What `llm.astream` returns. It has the same `text`, `done`, `model`,
`finish_reason` and `usage`.

| Member | Meaning |
|---|---|
| `await stream` | Sends the request now and returns the stream itself. The errors of the request are raised here. |
| `async for piece in stream` | The pieces of the text. The first step sends the request when nothing has sent it yet. |
| `async with stream:` | Sends the request on entering and closes the stream on leaving. |
| `await stream.completion()` | Reads the rest and returns a `llm.Completion`. |
| `await stream.aclose()` | Closes the connection. |

<!-- signature: efp_app.llm.AsyncCompletionStream.completion efp_app.llm.AsyncCompletionStream.aclose -->
```python
async def completion(self) -> Completion:
async def aclose(self) -> None:
```

### 4.6 How a stream behaves

1. `llm.stream(...)` sends the request before it returns. A refusal of the
   Portal (`BudgetExceededError`, `LlmDisabledError`, `InvalidTokenError`
   ...) is raised by the call itself, before any text exists. A web handler
   can still answer with an error status.
2. `llm.astream(...)` checks its arguments and returns at once; it sends
   nothing. The request goes out at `await stream`, at `async with`, or at
   the first step of `async for`, and its errors are raised there. Write
   `stream = await llm.astream(...)` to get the errors before the first
   piece.
3. The Portal answers with server-sent events (4.6.1). A `delta` event gives
   one piece. The `done` event ends the stream and fills `model`,
   `finish_reason` and `usage`; the connection is then closed.
4. An `error` event raises the error of its code at that point of the
   iteration (`ProviderError` when the event has no code). The stream is
   closed. `text` keeps what had arrived; `done` stays `False` and `usage`
   stays `None`. The error has the status of its class and, for a
   `BudgetExceededError`, `retry_after` is `None`: an event has no
   `Retry-After` header.
5. A stream that ends without a `done` event raises `StreamInterruptedError`.
6. A connection that breaks raises `PortalUnreachableError`; a silence
   longer than the timeout raises `PortalTimeoutError`.
7. After the end, after an error and after `close()` the iteration yields
   nothing more. `close()` before the end is not an error: `completion()`
   then returns the text received so far with `finish_reason=None` and an
   empty `Usage()`.
8. When the app stops reading early it must close the stream (`close()`,
   `aclose()`, `with`, `async with`). A `CompletionStream` that is dropped
   without that closes its connection only when the object is collected; an
   `AsyncCompletionStream` has no such safety net.
9. When the Portal answers a stream request with JSON instead of events, the
   whole text arrives as one piece.

#### 4.6.1 The events (`efp_app._sse`)

```text
data: {"type": "delta", "text": "Hel"}
data: {"type": "delta", "text": "lo"}
data: {"type": "done", "model": "gpt-5.4", "finish_reason": "stop", "usage": {"input_tokens": 3, "output_tokens": 2, "credits": null}}
data: {"type": "error", "error": {"code": "provider_error", "message": "..."}}
```

The internal class `_sse.SSEParser` reads them. It skips comment lines
(`: keepalive`) and fields other than `data`, joins several `data` lines of
one event, and takes an event as complete at a blank line or as soon as its
data is a whole JSON document. Data that is not a JSON object is dropped. A
`delta` with an empty text and an event of an unknown type yield nothing.

<!-- signature: efp_app._sse.SSEParser.feed efp_app._sse.SSEParser.flush -->
```python
def feed(self, line: str) -> Optional[Dict[str, Any]]:
def flush(self) -> Optional[Dict[str, Any]]:
```

### 4.7 Timeouts and retries

- The timeout is not a limit for the whole call. A number `n` becomes
  `httpx.Timeout(n, connect=min(n, 10.0))`: at most 10 seconds to connect
  (`client.CONNECT_TIMEOUT`), and `n` seconds for each wait for data in
  either direction. For a stream, `n` is the longest silence between two
  pieces.
- The default of the four calls is `llm.LLM_TIMEOUT`, 600 seconds, whatever
  the timeout of the client is.
- An `httpx.Timeout` is used as it is.
- No call of this module is repeated. A `POST` is never sent twice (8.3).
- A timeout raises `PortalTimeoutError`, a failed connection
  `PortalUnreachableError`.

### 4.8 Errors

| Raised | When |
|---|---|
| `ValueError` | `reasoning_effort` is not `None`, `"low"`, `"medium"` or `"high"`. Before any request. Not an `EfpError`. |
| `InvalidRequestError` | `messages` cannot be sent: not a string, message or sequence; a role other than `user`, `assistant`, `system`; a `content` that is not a `str`; `system=` together with a system message; no user or assistant message. Before any request. Also the Portal's `invalid_request`. |
| `ConfigurationError` | `EFP_APP_TOKEN` or `EFP_PORTAL_URL` is missing or unusable (when no `client=` is given). |
| `ModelNotAllowedError` | `model` is not one of the app's models. |
| `InvalidTokenError` | The token was reset or is wrong. |
| `AppDisabledError` | The app is disabled in the Portal. |
| `LlmDisabledError` | The owner has not turned on the model for the app. |
| `ProviderAuthFailedError` | The owner's sign-in to the model provider has expired. |
| `RateLimitedError` | Too many calls. `retry_after`. |
| `BudgetExceededError` | The app's daily budget is used up. `retry_after`. |
| `ProviderError` | The model provider failed; also an error event without a code. |
| `PortalUnreachableError`, `PortalTimeoutError` | The Portal could not be reached, or was silent for longer than the timeout. |
| `UnexpectedResponseError` | The answer is not what the contract says: no `text`, not JSON, a redirect, an error page. |
| `StreamInterruptedError` | A stream ended before its final event. |

Section 10 has the code, the status and the sentence of each class.

### 4.9 Examples

The whole answer:

<!-- runnable -->
```python
from efp_app import llm

answer = llm.complete(
    "Summarise in one sentence: the build failed because the disk was full.",
    system="You are concise.",
    max_output_tokens=200,
)
assert isinstance(answer, llm.Completion)
assert answer.text == str(answer) == "Hello."          # the fake Portal's answer
assert (answer.model, answer.finish_reason) == ("gpt-5.4", "stop")
assert (answer.usage.input_tokens, answer.usage.output_tokens, answer.usage.total_tokens) == (12, 3, 15)
assert answer.usage.credits == 0.002
```

A conversation, with every option:

<!-- runnable -->
```python
from efp_app import llm

history = [
    {"role": "user", "content": "My name is Bob."},
    {"role": "assistant", "content": "Hello, Bob."},
    ("user", "What is my name?"),                      # a pair works as well
]
answer = llm.complete(
    history,
    system="You are concise.",
    model="gpt-5.4",
    max_output_tokens=300,
    temperature=0.2,
    reasoning_effort="medium",
    timeout=120,
)
assert answer.finish_reason in ("stop", "length")
```

A stream:

<!-- runnable -->
```python
from efp_app import llm

stream = llm.stream("Tell me a story.", max_output_tokens=500)    # the request is sent here
assert stream.done is False and stream.usage is None
pieces = [piece for piece in stream]
assert pieces == ["Hel", "lo."] and stream.text == "Hello."
assert stream.done is True and stream.finish_reason == "stop"
assert stream.usage.output_tokens == 3
assert list(stream) == []                              # nothing more
```

The rest of a stream as one answer, and stopping early:

<!-- runnable -->
```python
from efp_app import llm

stream = llm.stream("Hello")
first = next(stream)
answer = stream.completion()                           # reads what is left
assert (first, answer.text, answer.usage.total_tokens) == ("Hel", "Hello.", 15)

with llm.stream("Count to one hundred.") as stream:
    first = next(stream)
assert stream.done is False and stream.usage is None   # closed before the end
assert stream.completion().text == "Hel"               # what had arrived; no error
```

Asynchronous code:

<!-- runnable -->
```python
import asyncio

from efp_app import llm


async def main():
    answer = await llm.acomplete("Hello", max_output_tokens=100)
    stream = await llm.astream("Hello")                # sends the request; its errors are raised here
    pieces = [piece async for piece in stream]
    async with llm.astream("Hello") as other:          # the same, closed at the end of the block
        rest = await other.completion()
    return answer, pieces, stream, rest


answer, pieces, stream, rest = asyncio.run(main())
assert answer.text == "".join(pieces) == stream.text == rest.text == "Hello."
assert stream.done is True and stream.usage.total_tokens == 15
```

The budget is used up: the call itself raises, before any text.

<!-- environment: budget-used-up -->
<!-- runnable -->
```python
from efp_app import llm
from efp_app.errors import BudgetExceededError

caught = None
try:
    llm.stream("Tell me a story.")
except BudgetExceededError as exc:
    caught = exc
assert (caught.code, caught.status, caught.retry_after) == ("budget_exceeded", 429, 3600.0)
assert caught.user_message == "This app has used up its AI budget for today. Try again in about 60 minutes."
```

A stream that fails after it began: show what arrived, then the sentence.

<!-- environment: stream-fails -->
<!-- runnable -->
```python
from efp_app import llm
from efp_app.errors import EfpError, ProviderError

stream = llm.stream("Tell me a story.")
shown = ""
try:
    for piece in stream:
        shown += piece
except EfpError as exc:                                # here a ProviderError, from an error event
    assert isinstance(exc, ProviderError) and exc.status == 502
    shown += "\n\n[%s]" % exc.user_message
finally:
    stream.close()
assert shown == "Hel\n\n[The AI provider returned an error. Please try again.]"
assert stream.text == "Hel" and stream.done is False and stream.usage is None
```

Arguments that are refused before any request:

<!-- runnable -->
```python
from efp_app import llm
from efp_app.errors import EfpError, InvalidRequestError

refused = []
for call in (
    lambda: llm.complete("Hello", reasoning_effort="max"),
    lambda: llm.complete([{"role": "tool", "content": "x"}]),
    lambda: llm.complete([{"role": "user", "content": ["not", "text"]}]),
    lambda: llm.complete([{"role": "system", "content": "Be brief."}]),
    lambda: llm.astream("Hello", reasoning_effort="LOW"),            # astream checks at once, too
):
    try:
        call()
    except (ValueError, EfpError) as exc:
        refused.append(type(exc))
assert refused == [ValueError, InvalidRequestError, InvalidRequestError, InvalidRequestError, ValueError]
```

## 5. `efp_app.connectors`

The owner's connectors, through the Portal (CONTRACTS.md, section 9.3). The
app names a connector type, a method and a path. The Portal adds the owner's
credentials, sends the request to the connector's address and returns what
that system answered. The app never sees the credentials. Every visitor of
the app reads those systems as the owner's connector does, not as
themselves.

Its names: `connectors.request`, `connectors.get`, `connectors.available`,
`connectors.arequest`, `connectors.aget`, `connectors.aavailable`,
`connectors.ConnectorResponse`, `connectors.Connector` and
`connectors.TYPES`.

`connectors.TYPES` is a tuple of the connector types of this release:
`"jira"`, `"confluence"`, `"github"`, `"jenkins"`, `"nexus"`, `"splunk"`.
The SDK does not compare `type` with it. It accepts any name made of
lower-case letters, digits, `_` and `-` that starts with a letter or a
digit, and the Portal decides.

### 5.1 `connectors.request` and `connectors.arequest`

<!-- signature: efp_app.connectors.request efp_app.connectors.arequest -->
```python
def request(
    type: str,
    method: str,
    path: str,
    *,
    query: Optional[Mapping[str, Any]] = None,
    json: Any = None,
    body: Union[bytes, bytearray, str, None] = None,
    headers: Optional[Mapping[str, str]] = None,
    visitor: Any = None,
    timeout: _client.TimeoutLike = None,
    client: Optional[_client.Client] = None,
) -> ConnectorResponse:

async def arequest(
    type: str,
    method: str,
    path: str,
    *,
    query: Optional[Mapping[str, Any]] = None,
    json: Any = None,
    body: Union[bytes, bytearray, str, None] = None,
    headers: Optional[Mapping[str, str]] = None,
    visitor: Any = None,
    timeout: _client.TimeoutLike = None,
    client: Optional[_client.AsyncClient] = None,
) -> ConnectorResponse:
```

| Parameter | Type | Default | Meaning and limits |
|---|---|---|---|
| `type` | `str` | required | The connector type, for example `"jira"`. |
| `method` | `str` | required | An HTTP method. Blanks around it are dropped and it is sent in upper case. The Portal allows `GET` and `HEAD`; any other method needs the owner's permission to write for that connector. |
| `path` | `str` | required | The path below the connector's address (`Connector.base_url`). It may carry a query string. Rules: 5.5. |
| `query` | mapping | `None` | Query parameters. An entry whose value is `None` is left out. |
| `json` | any JSON value | `None` | Sent as a JSON body. |
| `body` | `bytes`, `bytearray` or `str` | `None` | Sent as it is; a `str` as UTF-8. Not together with `json`. |
| `headers` | mapping | `None` | Request headers. The Portal forwards only `Accept`, `Content-Type` and names that start with `X-`. |
| `visitor` | `Visitor`, `str` or `None` | `None` | Who caused the call (3.7). |
| `timeout` | seconds or an `httpx.Timeout` | `None` | `None`: the timeout of the client, 120 seconds for the shared one. The number means what 4.7 says. |
| `client` | `client.Client`; `client.AsyncClient` for `arequest` | `None` | `None`: the shared client (section 8). |

Returns a `connectors.ConnectorResponse` (5.4), whatever status the other
system answered with. Sends one
`POST {EFP_PORTAL_URL}/api/app-sdk/v1/connectors/{type}/request` and never
repeats it, also when `method` is `GET`. Errors: 5.6.

The body of that request, for the first example of 5.7:

```json
{"method": "GET", "path": "/rest/api/2/issue/ABC-1", "query": {"fields": "summary"},
 "headers": null, "json": null, "body_base64": null}
```

### 5.2 `connectors.get` and `connectors.aget`

<!-- signature: efp_app.connectors.get efp_app.connectors.aget -->
```python
def get(type: str, path: str, *, visitor: Any = None, **query: Any) -> ConnectorResponse:
async def aget(type: str, path: str, *, visitor: Any = None, **query: Any) -> ConnectorResponse:
```

A `GET` with the keyword arguments as the query string:
`connectors.get(type, path, visitor=who, **query)` is
`connectors.request(type, "GET", path, query=query or None, visitor=who)`.

- Every keyword other than `visitor` is a query parameter. `timeout=5`,
  `headers={...}` and `client=...` are sent to the other system as query
  parameters; they are not options of `get`. Use `request` for those.
- A query parameter that is itself named `type`, `path` or `visitor` needs
  `request(..., query={...})`.
- A parameter whose value is `None` is left out.

Returns and raises what `connectors.request` does.

### 5.3 `connectors.available` and `connectors.aavailable`

<!-- signature: efp_app.connectors.available efp_app.connectors.aavailable -->
```python
def available(*, client: Optional[_client.Client] = None) -> List[Connector]:
async def aavailable(*, client: Optional[_client.AsyncClient] = None) -> List[Connector]:
```

Returns the connectors the owner turned on for this app, as a list of
`connectors.Connector`; an empty list when there are none. Sends one
`GET {EFP_PORTAL_URL}/api/app-sdk/v1/connectors`, repeated up to three times
when the connection fails (8.3). Raises `ConfigurationError`,
`InvalidTokenError`, `AppDisabledError`, `RateLimitedError`,
`PortalUnreachableError`, `PortalTimeoutError` and `UnexpectedResponseError`.

### 5.4 `connectors.ConnectorResponse` and `connectors.Connector`

Frozen dataclasses.

<!-- fields: efp_app.connectors.ConnectorResponse -->
```python
status: int
headers: Dict[str, str] = dict()
json: Any = None
content: bytes = b''
truncated: bool = False
```

| Member | Meaning |
|---|---|
| `status` | The HTTP status of the other system. |
| `headers` | Its response headers. The names are in lower case. |
| `json` | The decoded body when the other system answered with JSON, else `None`. |
| `content` | The body as `bytes`; empty when there was none. For a JSON answer these are not the bytes the other system sent: the SDK encodes `json` again, as UTF-8 without escaping non-ASCII characters. |
| `truncated` | `True` when the body was longer than 5 MiB and was cut. |
| `ok` | A property: `True` when `200 <= status < 300`. |
| `text` | A property: `content` decoded as UTF-8; a byte that cannot be decoded becomes the replacement character. |

<!-- fields: efp_app.connectors.Connector -->
```python
type: str
base_url: Optional[str] = None
write: bool = False
```

| Field | Meaning |
|---|---|
| `type` | The connector type. |
| `base_url` | The address of the owner's connector, for links to the other system. `None` when the Portal sent none. |
| `write` | `True` when the owner allowed methods other than `GET` and `HEAD`. |

### 5.5 Rules

**The other system's status is an answer, not an error.** A 404 from Jira is
a `ConnectorResponse` with `status == 404`. The same holds for 401, 403 and
500 of the other system. Check `ok` or `status` before using `json`. An
exception means that the Portal refused the call or could not get an answer.

| What happened | What the app gets |
|---|---|
| The other system answered, with any status | a `ConnectorResponse` with that `status` |
| The connector type is not turned on for the app | `ConnectorDisabledError` |
| A method other than `GET` or `HEAD`, without permission to write | `MethodNotAllowedError` |
| The owner has no connector of this type set up | `ConnectorNotConfiguredError` |
| The Portal got no answer from the other system | `UpstreamError`, or `UpstreamTimeoutError` when it was too slow |
| The path breaks a rule | `InvalidPathError` |

**The path.** The SDK checks it before the call and raises
`InvalidPathError` without sending anything:

- it is a `str` that starts with `/`;
- it does not start with `//` and holds no backslash, so it cannot name a
  host;
- the part before `?` has no `..` segment (dots inside a query string are
  fine).

The Portal checks the path again.

**Methods.** `GET` and `HEAD` always. Anything else only when
`Connector.write` is true for that type.

**Headers.** All of `headers` goes to the Portal; the Portal forwards
`Accept`, `Content-Type` and `X-*` and drops the rest. The credentials are
the Portal's to add.

**The body of the answer.** JSON comes back decoded in `json`; anything else
as bytes in `content`. A body over 5 MiB is cut by the Portal and `truncated`
is true; a cut body is incomplete, so test `truncated` before trusting the
content.

**Who is acting.** The owner's connector account, for every visitor. A query
such as Jira's `currentUser()` names that account, not the visitor.

### 5.6 Errors

| Raised | When |
|---|---|
| `InvalidRequestError` | Before any request: `type` is not a connector name, `method` is empty or not a `str`, `json` and `body` are both given. Also the Portal's `invalid_request`. |
| `InvalidPathError` | Before any request: the path breaks a rule of 5.5. Also the Portal's `invalid_path`. |
| `ConfigurationError` | `EFP_APP_TOKEN` or `EFP_PORTAL_URL` is missing or unusable. |
| `InvalidTokenError`, `AppDisabledError` | The token is wrong; the app is disabled. |
| `ConnectorDisabledError` | The connector is not turned on for the app. |
| `MethodNotAllowedError` | A write to a connector that is read only for the app. |
| `ConnectorNotConfiguredError` | The owner has not set up this connector in the Portal. |
| `RateLimitedError` | Too many calls. `retry_after`. |
| `UpstreamError`, `UpstreamTimeoutError` | The Portal got no answer from the other system, or not in time. |
| `PortalUnreachableError`, `PortalTimeoutError` | The Portal itself could not be reached, or was too slow. |
| `UnexpectedResponseError` | The Portal's answer is not what the contract says: no whole-number `status`, a damaged body, not JSON. |

### 5.7 Examples

Reading, and the other system's own status:

<!-- runnable -->
```python
from efp_app import connectors

issue = connectors.get("jira", "/rest/api/2/issue/ABC-1", fields="summary")
assert issue.ok and issue.status == 200
assert issue.json["fields"]["summary"] == "Login fails"
assert issue.headers["content-type"] == "application/json"      # names are in lower case

missing = connectors.get("jira", "/rest/api/2/issue/NOPE-1")
assert missing.status == 404 and missing.ok is False            # an answer, not an exception
assert missing.json == {"errorMessages": ["Issue Does Not Exist"]}
```

`request` with a query, headers, a timeout and a visitor; a write:

<!-- runnable -->
```python
from efp_app import connectors, visitor

who = visitor.current(request_headers)
found = connectors.request(
    "jira",
    "GET",
    "/rest/api/2/search",
    query={"jql": 'project = "ABC" ORDER BY updated DESC', "maxResults": 20, "startAt": None},
    headers={"Accept": "application/json"},
    visitor=who,
    timeout=30,
)
assert found.json["total"] == 1 and found.json["issues"][0]["key"] == "ABC-1"

created = connectors.request("github", "POST", "/repos/acme/app/issues", json={"title": "Bug"})
assert created.status == 201 and created.json["number"] == 7    # the owner allowed writes for github
```

An answer that is not JSON:

<!-- runnable -->
```python
from efp_app import connectors

log = connectors.get("jenkins", "/job/build/1/consoleText")
assert log.json is None                                         # not JSON
assert isinstance(log.content, bytes) and log.truncated is False
assert log.text.endswith("Finished: SUCCESS\n")
```

What the Portal refuses, and what the SDK refuses before it asks:

<!-- runnable -->
```python
from efp_app import connectors
from efp_app.errors import EfpError

refused = []
for call in (
    lambda: connectors.request("jira", "POST", "/rest/api/2/issue", json={"fields": {}}),   # jira is read only
    lambda: connectors.get("splunk", "/services/search/jobs"),                              # not turned on
    lambda: connectors.get("jira", "https://elsewhere.example.com/"),                       # no request is made
    lambda: connectors.get("jira", "/rest/../admin"),
    lambda: connectors.request("jira", "POST", "/x", json={}, body=b"x"),
):
    try:
        call()
    except EfpError as exc:
        refused.append((type(exc).__name__, exc.code, exc.status))
assert refused == [
    ("MethodNotAllowedError", "method_not_allowed", 403),
    ("ConnectorDisabledError", "connector_disabled", 403),
    ("InvalidPathError", "invalid_path", 400),
    ("InvalidPathError", "invalid_path", 400),
    ("InvalidRequestError", "invalid_request", 400),
]
```

Which connectors the app may use:

<!-- runnable -->
```python
from efp_app import connectors

on = {item.type: item for item in connectors.available()}
assert sorted(on) == ["github", "jenkins", "jira"]
assert on["jira"] == connectors.Connector(type="jira", base_url="https://jira.example.com", write=False)
assert on["github"].write is True
link = on["jira"].base_url.rstrip("/") + "/browse/ABC-1"        # a link for the page
```

Asynchronous code:

<!-- runnable -->
```python
import asyncio

from efp_app import connectors


async def main():
    return await asyncio.gather(
        connectors.aget("jira", "/rest/api/2/issue/ABC-1"),
        connectors.arequest("github", "GET", "/repos/acme/app", headers={"Accept": "application/vnd.github+json"}),
        connectors.aavailable(),
    )


issue, repository, on = asyncio.run(main())
assert issue.json["key"] == "ABC-1" and repository.json["default_branch"] == "main"
assert [item.type for item in on] == ["github", "jenkins", "jira"]
```

## 6. `efp_app.storage`

Data that outlives restarts and deployments: named objects of bytes, and on
some hosts a directory. The app's code directory is read-only on the host,
and `HOME` and `TMPDIR` are emptied at every restart, so everything that
must survive goes here.

Its names: `storage.save`, `storage.load`, `storage.exists`,
`storage.delete`, `storage.save_json`, `storage.load_json`, `storage.list`,
`storage.mode`, `storage.data_dir`, `storage.check_key`,
`storage.check_prefix`, `storage.windows_problem`, `storage.StoredObject`,
and the constants `storage.MAX_OBJECT_BYTES` (`67108864`, 64 MiB),
`storage.MAX_KEY_LENGTH` (`512`), `storage.MAX_SEGMENT_LENGTH` (`255`),
`storage.LOCAL_DIR` (`".efp-data"`), `storage.OBJECTS_DIR` (`"objects"`) and
`storage.SOCKET_TIMEOUT` (`60.0` seconds).

All functions are synchronous: they read and write files, or talk to a unix
socket. None of them calls the Portal, so none needs the token.

### 6.1 The three places data lives

The SDK picks the place at every call, in this order:

| `storage.mode()` | Chosen when | Objects are kept in | `storage.data_dir()` |
|---|---|---|---|
| `"sandbox"` | `EFP_APP_DATA_DIR` is set | files under `$EFP_APP_DATA_DIR/objects/` | `$EFP_APP_DATA_DIR` |
| `"hidden"` | otherwise, `EFP_APP_STORAGE_SOCKET` is set | the storage service of the host, behind that unix socket | raises `DataDirUnavailableError` |
| `"local"` | otherwise, on a developer's machine | files under `./.efp-data/objects/` | `./.efp-data` |
| none | otherwise (deployed) | every function raises `StorageUnavailableError` | raises `StorageUnavailableError` |

- A host runs in one of two isolation modes, `sandbox` or `hidden`, and sets
  one of the two variables. The same code works in both, except
  `storage.data_dir`.
- `./` is the current working directory at the moment of the call.
  `efp-app dev` starts the app in the app's directory.
- The mode says where the data goes, not whether the app is deployed:
  `EFP_APP_DATA_DIR` set on a developer's machine gives `"sandbox"`.
- In `hidden` mode every call opens one connection to the socket, and each
  wait on it is limited to `storage.SOCKET_TIMEOUT`.

### 6.2 Keys

A key is checked the same way in every place, before anything is read or
written. A bad key raises `InvalidKeyError`.

- a `str` of 1 to 512 characters (`storage.MAX_KEY_LENGTH`);
- only `A-Z a-z 0-9 . _ - /`;
- no `/` at the start;
- no empty segment, no `.` segment, no `..` segment (a segment is the text
  between two `/`); so no `//` and no `/` at the end;
- no segment longer than 255 characters (`storage.MAX_SEGMENT_LENGTH`).

`.hidden`, `a.b..c` and `reports/2026/q4.json` are valid keys.

Two more rules:

- A name is an object or a folder, never both. With an object `a`, saving
  `a/b` raises `InvalidKeyError`; with an object `a/b`, saving `a` does.
- On a developer's machine with Windows, a key with a segment that ends in a
  dot or is a device name (`CON`, `PRN`, `AUX`, `NUL`, `COM1` to `COM9`,
  `LPT1` to `LPT9`, with or without an extension) raises `InvalidKeyError`.
  The key is valid on the host. `storage.windows_problem` tells in advance.

A prefix (for `storage.list`) follows the same rules, except that it may be
empty and may end with `/`.

### 6.3 Sizes and the quota

- One object is at most 64 MiB (`storage.MAX_OBJECT_BYTES`). The SDK checks
  the size before it writes, in every place, and raises `TooLargeError`.
- The SDK has no quota of its own. The host gives the data of an app a
  quota: 1024 MiB unless the platform changed it (OPERATIONS.md,
  `EFP_HOST_APP_DATA_MB`). In `hidden` mode a save beyond the quota raises
  `QuotaExceededError`. In `sandbox` mode the quota is not enforced: the
  owner is told in the Portal. Where objects are files, `QuotaExceededError`
  is raised only when the file system has no space left.
- In `hidden` mode one listing returns at most 10,000 keys. Narrow it with a
  prefix.

### 6.4 The functions

Every function that takes a key raises `InvalidKeyError` for a bad key,
`StorageUnavailableError` when no place can be used (6.1, or the storage
service cannot be reached) and `StorageError` when reading or writing fails
for another reason. The lists below name what each function raises in
addition.

#### `storage.save`

<!-- signature: efp_app.storage.save -->
```python
def save(key: str, data: Union[bytes, bytearray, memoryview, str]) -> None:
```

| Parameter | Meaning |
|---|---|
| `key` | The name of the object (6.2). |
| `data` | The content: `bytes`, `bytearray`, `memoryview`, or a `str`, which is saved as UTF-8. It may be empty. |

Stores `data` under `key` and replaces what was there. The object is written
completely or not at all: a failed save leaves the old content. Returns
`None`. Also raises `TooLargeError` (more than 64 MiB), `QuotaExceededError`
(6.3) and `TypeError` for any other type of `data`.

#### `storage.load`

<!-- signature: efp_app.storage.load -->
```python
def load(key: str, default: Any = _MISSING) -> Any:
```

| Parameter | Meaning |
|---|---|
| `key` | The name of the object. |
| `default` | What to return for a missing key. Any value counts, `None` too. Leave the argument out to get an error instead. |

Returns the content as `bytes`, also when a `str` was saved. For a missing
key it returns `default` when one is given and raises `StorageNotFoundError`
otherwise.

#### `storage.exists`

<!-- signature: efp_app.storage.exists -->
```python
def exists(key: str) -> bool:
```

Returns `True` when an object is stored under `key`. A folder is not an
object: with `a/b` stored, `exists("a")` is `False`. In `hidden` mode this
is a listing with the key as the prefix.

#### `storage.delete`

<!-- signature: efp_app.storage.delete -->
```python
def delete(key: str) -> bool:
```

Removes the object. Returns `True` when there was one and `False` when there
was none; a missing key is not an error. Folders left empty are removed too,
so their names are free for objects again.

#### `storage.save_json`

<!-- signature: efp_app.storage.save_json -->
```python
def save_json(key: str, value: Any) -> None:
```

Stores `json.dumps(value, ensure_ascii=False)` as UTF-8 under `key`. Returns
`None`. Raises what `storage.save` raises. A value that JSON cannot express
raises the error of `json.dumps`: `TypeError`, or `ValueError` for a value
that contains itself.

#### `storage.load_json`

<!-- signature: efp_app.storage.load_json -->
```python
def load_json(key: str, default: Any = _MISSING) -> Any:
```

Returns the decoded value. For a missing key it returns `default` when one
is given and raises `StorageNotFoundError` otherwise. An object that is not
JSON raises `StorageError` with the code `invalid_json`, also when a
default is given.

#### `storage.list`

<!-- signature: efp_app.storage.list -->
```python
def list(prefix: str = "") -> List[StoredObject]:
```

| Parameter | Meaning |
|---|---|
| `prefix` | The start of the keys to return. `""`: every object. |

Returns the objects whose key starts with `prefix`, as a list of
`storage.StoredObject` ordered by key, at every depth. The prefix is
compared as text, not as a folder: `"notes"` matches `notes/a.txt` and
`notes2.txt`; `"notes/"` matches only what lies below `notes/`. An empty
list when nothing matches. A bad prefix raises `InvalidKeyError`.

Files that the app put into the data directory itself are not listed, and
neither is a file in `objects` whose name is not a valid key.

#### `storage.mode`

<!-- signature: efp_app.storage.mode -->
```python
def mode() -> str:
```

Returns `"sandbox"`, `"hidden"` or `"local"` (6.1). Raises
`StorageUnavailableError` when deployed without either variable.

#### `storage.data_dir`

<!-- signature: efp_app.storage.data_dir -->
```python
def data_dir() -> Path:
```

Returns a directory the app may read and write directly, as a
`pathlib.Path`, and creates it when it is missing. It survives restarts and
deployments. Use it for what does not fit named objects: an SQLite file,
uploads streamed to disk. The objects of `storage.save` live in its folder
`objects`, so use other names for your own files.

Raises `DataDirUnavailableError` in `hidden` mode: an app that needs this
function does not run on such a host. Raises `StorageUnavailableError` when
deployed without either variable, and `StorageError` when the directory
cannot be created.

#### `storage.check_key` and `storage.check_prefix`

<!-- signature: efp_app.storage.check_key efp_app.storage.check_prefix -->
```python
def check_key(key: Any) -> str:
def check_prefix(prefix: Any) -> str:
```

Return the key, or the prefix, when it follows the rules of 6.2
(`check_prefix` returns `""` for `None`). Raise `InvalidKeyError` otherwise.
Use `check_key` on a name that comes from a visitor before building a key
from it.

#### `storage.windows_problem`

<!-- signature: efp_app.storage.windows_problem -->
```python
def windows_problem(key: str) -> Optional[str]:
```

Returns a sentence that says why Windows cannot keep a file for this valid
key, or `None`. Raises nothing. Only a developer's machine is affected.

### 6.5 `storage.StoredObject`

A frozen dataclass: one entry of `storage.list`.

<!-- fields: efp_app.storage.StoredObject -->
```python
key: str
size: int
modified_at: Optional[datetime] = None
```

| Field | Meaning |
|---|---|
| `key` | The key of the object. |
| `size` | Its size in bytes. |
| `modified_at` | When it was last written: a `datetime` with the UTC time zone. `None` when the storage service sent no usable time. |

### 6.6 Concurrency

There are no transactions and no locks.

- One `save` is atomic: a reader sees the old object or the new one, never a
  half-written one.
- Two saves of the same key: the later one wins. Nothing is merged and
  nothing is reported.
- On a developer's Windows machine the file system refuses to replace,
  read or remove a file that is open elsewhere at that very moment. The
  SDK therefore lets the requests of one process take turns on an object
  there, and waits and tries again (for up to about two seconds) when
  another process holds the file, so two requests that meet on one object
  behave as they do on the host instead of failing with `StorageError`.
- `load`, change, `save` is three steps. Two requests that do this at the
  same time lose one of the two changes. There is no version check.

Two ways to stay correct:

- Keep one object per writer or per item (`votes/<user id>.json`,
  `items/<id>.json`) and build totals with `storage.list`. Nobody then
  overwrites somebody else's object.
- Or guard the three steps with a lock inside the process. The host runs
  one process per app (DEVELOPING_APPS.md, "Limits"), so a `threading.Lock`
  is enough there.

### 6.7 Examples

Objects:

<!-- runnable -->
```python
from efp_app import storage
from efp_app.errors import StorageNotFoundError

storage.save("notes/alice.txt", "remember the milk")             # a str is saved as UTF-8
assert storage.load("notes/alice.txt") == b"remember the milk"   # always bytes
assert storage.load("notes/alice.txt").decode("utf-8") == "remember the milk"
assert storage.exists("notes/alice.txt") is True
assert storage.exists("notes") is False                          # a folder is not an object

assert storage.load("notes/nobody.txt", default=None) is None    # a default: no error
assert storage.load("notes/nobody.txt", b"") == b""
caught = None
try:
    storage.load("notes/nobody.txt")                             # no default: an error
except StorageNotFoundError as exc:
    caught = exc
assert caught.code == "not_found" and caught.user_message == "That item does not exist."

assert storage.delete("notes/alice.txt") is True
assert storage.delete("notes/alice.txt") is False                # nothing left to remove
```

JSON:

<!-- runnable -->
```python
from efp_app import storage
from efp_app.errors import StorageError

storage.save_json("settings.json", {"theme": "dark", "tags": ["a", "b"]})
assert storage.load_json("settings.json") == {"theme": "dark", "tags": ["a", "b"]}
assert storage.load_json("missing.json", default={}) == {}

storage.save("broken.json", "{not json")
caught = None
try:
    storage.load_json("broken.json", default={})                 # the default is for a missing key only
except StorageError as exc:
    caught = exc
assert caught.code == "invalid_json"
```

Listing:

<!-- runnable -->
```python
from efp_app import storage

for key in ("notes/a.txt", "notes/deep/b.txt", "notes2.txt"):
    storage.save(key, b"x")
assert [item.key for item in storage.list("notes/")] == ["notes/a.txt", "notes/deep/b.txt"]
assert [item.key for item in storage.list("notes")] == ["notes/a.txt", "notes/deep/b.txt", "notes2.txt"]
assert storage.list("nothing/") == []
first = storage.list()[0]
assert (first.key, first.size) == ("notes/a.txt", 1) and first.modified_at.tzinfo is not None
```

Keys:

<!-- runnable -->
```python
from efp_app import storage
from efp_app.errors import InvalidKeyError

assert storage.check_key("reports/2026/q4.json") == "reports/2026/q4.json"
refused = []
for bad in ("", "/absolute", "a//b", "a/../b", "trailing/", "with space", "x" * 513, 7):
    try:
        storage.check_key(bad)
    except InvalidKeyError:
        refused.append(bad)
assert len(refused) == 8

assert storage.check_prefix("reports/") == "reports/" and storage.check_prefix(None) == ""
assert storage.windows_problem("logs/com1.txt") == "'com1.txt' is a device name on Windows"
assert storage.windows_problem("a.b") is None

storage.save("a", b"an object")
caught = None
try:
    storage.save("a/b", b"below an object")                      # a name is an object or a folder
except InvalidKeyError as exc:
    caught = exc
assert caught.code == "invalid_key"
```

The place, and the data directory, on a developer's machine:

<!-- runnable -->
```python
from efp_app import storage

assert storage.mode() == "local"
folder = storage.data_dir()                                      # ./.efp-data, created when missing
assert folder.name == ".efp-data" and folder.is_dir()
(folder / "app.sqlite3").write_bytes(b"")                        # a file of your own
storage.save("k", b"v")
assert (folder / "objects" / "k").read_bytes() == b"v"
assert [item.key for item in storage.list()] == ["k"]            # your own files are not listed
```

On a host in `sandbox` mode:

<!-- environment: deployed -->
<!-- runnable -->
```python
import os

from efp_app import storage

assert storage.mode() == "sandbox"
assert str(storage.data_dir()) == os.environ["EFP_APP_DATA_DIR"]
storage.save("k", b"v")
assert (storage.data_dir() / "objects" / "k").is_file()
```

Sizes and types:

<!-- runnable -->
```python
from efp_app import storage
from efp_app.errors import TooLargeError

refused = []
for data in (b"x" * (storage.MAX_OBJECT_BYTES + 1), {"a": 1}):
    try:
        storage.save("big", data)
    except (TooLargeError, TypeError) as exc:
        refused.append(type(exc))
assert refused == [TooLargeError, TypeError]                      # use save_json for a dict
assert storage.exists("big") is False
```

A counter that two requests may change at the same time:

<!-- runnable -->
```python
import threading

from efp_app import storage

_lock = threading.Lock()


def add_vote(option):
    with _lock:                                                  # load, change, save as one step
        votes = storage.load_json("votes.json", default={})
        votes[option] = votes.get(option, 0) + 1
        storage.save_json("votes.json", votes)
        return votes


add_vote("hike")
assert add_vote("hike") == {"hike": 2}
```

## 7. `efp_app.app`

Facts about the app itself. `app.info` and `app.ainfo` ask the Portal
(CONTRACTS.md, section 9.1); the other four functions read the environment
and call nobody.

Its names: `app.info`, `app.ainfo`, `app.base_path`, `app.public_url`,
`app.slug`, `app.is_deployed`, `app.AppInfo`, `app.Owner`, `app.LlmInfo`,
`app.ConnectorGrant` and `app.Budget`.

### 7.1 `app.info` and `app.ainfo`

<!-- signature: efp_app.app.info efp_app.app.ainfo -->
```python
def info(*, cached: bool = False, client: Optional[_client.Client] = None) -> AppInfo:
async def ainfo(*, cached: bool = False, client: Optional[_client.AsyncClient] = None) -> AppInfo:
```

| Parameter | Default | Meaning |
|---|---|---|
| `cached` | `False` | `False`: ask the Portal. `True`: return the answer of the last call made through the same client, without a request; ask only when there is none yet. The budget in a cached answer may be out of date. |
| `client` | `None` | `None`: the shared client (section 8). The last answer is kept on the client. |

Returns an `app.AppInfo`. Sends one
`GET {EFP_PORTAL_URL}/api/app-sdk/v1/app`, repeated up to three times when
the connection fails (8.3). Raises `ConfigurationError`,
`InvalidTokenError`, `AppDisabledError`, `RateLimitedError`,
`PortalUnreachableError`, `PortalTimeoutError` and `UnexpectedResponseError`.

### 7.2 `app.AppInfo` and its parts

Frozen dataclasses. A value the Portal did not send is `None`, `False` or
empty: a sparse answer is not an error.

<!-- fields: efp_app.app.AppInfo -->
```python
id: Optional[str] = None
slug: Optional[str] = None
name: Optional[str] = None
url: Optional[str] = None
owner: Owner = Owner()
llm: LlmInfo = LlmInfo()
connectors: Tuple[ConnectorGrant, ...] = ()
budget: Budget = Budget()
raw: Dict[str, Any] = dict()
```

| Field | Meaning |
|---|---|
| `id` | The app's id. |
| `slug` | The slug: the path segment of the app's address. |
| `name` | The app's name. |
| `url` | The address visitors use. |
| `owner` | An `app.Owner`: who owns the app. |
| `llm` | An `app.LlmInfo`: what the app may use of the owner's model. |
| `connectors` | A tuple of `app.ConnectorGrant`: the connectors the owner turned on. |
| `budget` | An `app.Budget`: the daily budget and what is used of it. |
| `raw` | The JSON as the Portal sent it. `repr()` does not show it and `==` does not compare it. |

<!-- fields: efp_app.app.Owner -->
```python
id: Optional[int] = None
username: Optional[str] = None
display_name: Optional[str] = None
group_id: Optional[int] = None
group_name: Optional[str] = None
```

The owner's Portal user id, username and display name, and the id and name
of the owner's group.

<!-- fields: efp_app.app.LlmInfo -->
```python
enabled: bool = False
default_model: Optional[str] = None
models: Tuple[str, ...] = ()
```

| Field | Meaning |
|---|---|
| `enabled` | Whether the owner turned on the model for the app. When `False`, the calls of `llm` raise `LlmDisabledError`. |
| `default_model` | The model a call without `model=` uses. |
| `models` | The names a call may pass as `model=`. |

<!-- fields: efp_app.app.ConnectorGrant -->
```python
type: str
write: bool = False
```

The type of a connector that is turned on, and whether the app may send
methods other than `GET` and `HEAD` to it. `connectors.available` gives the
same list with the addresses.

<!-- fields: efp_app.app.Budget -->
```python
daily_credits: Optional[float] = None
used_credits: Optional[float] = None
daily_tokens: Optional[int] = None
used_tokens: Optional[int] = None
```

The app's budget for a day and what is used of it today, in credits and in
tokens. The SDK passes the numbers on as the Portal sent them; a value the
Portal did not send is `None`.

### 7.3 `app.base_path`

<!-- signature: efp_app.app.base_path -->
```python
def base_path() -> str:
```

Returns the path prefix the app is served under: `EFP_APP_BASE_PATH` with a
leading slash and without a trailing one, for example `"/mindmap"`. Returns
`""` when the variable is not set, as on a developer's machine. Raises
nothing. Use it when the backend builds a link to itself:
`app.base_path() + "/reports"`.

### 7.4 `app.public_url`

<!-- signature: efp_app.app.public_url -->
```python
def public_url() -> Optional[str]:
```

Returns the address visitors use, always ending in `/`: `EFP_APP_PUBLIC_URL`
when it is set. Otherwise, on a developer's machine with `PORT` set,
`http://127.0.0.1:{PORT}/`. Otherwise `None`. Raises nothing.

### 7.5 `app.slug`

<!-- signature: efp_app.app.slug -->
```python
def slug() -> Optional[str]:
```

Returns `EFP_APP_SLUG`, or `None` when it is not set. Raises nothing.

### 7.6 `app.is_deployed`

<!-- signature: efp_app.app.is_deployed -->
```python
def is_deployed() -> bool:
```

Returns `True` inside the host (`EFP_APP_ID` is set), `False` on a
developer's machine. Raises nothing.

### 7.7 Examples

On a developer's machine:

<!-- runnable -->
```python
from efp_app import app

info = app.info()
assert (info.slug, info.name, info.url) == ("example", "Example", "https://apps.example.com/example/")
assert (info.owner.id, info.owner.username, info.owner.display_name, info.owner.group_name) == (5, "alice", "Alice", "QA")
assert info.llm.enabled is True and info.llm.default_model == "gpt-5.4" and info.llm.models == ("gpt-5.4",)
assert [(grant.type, grant.write) for grant in info.connectors] == [("github", True), ("jenkins", False), ("jira", False)]
assert (info.budget.daily_credits, info.budget.used_credits) == (50.0, 1.2)
assert (info.budget.daily_tokens, info.budget.used_tokens) == (2000000, 3400)
assert info.raw["owner"]["username"] == "alice"

assert app.info(cached=True) is info                   # no request
assert app.info() is not info                          # asked again

assert app.is_deployed() is False
assert app.base_path() == "" and app.slug() is None and app.public_url() is None
```

Deployed:

<!-- environment: deployed -->
<!-- runnable -->
```python
from efp_app import app

assert app.is_deployed() is True
assert app.base_path() == "/example" and app.slug() == "example"
assert app.public_url() == "https://apps.example.com/example/"
link = app.base_path() + "/reports/2026"               # a link the backend builds
assert link == "/example/reports/2026"
```

Asynchronous code:

<!-- runnable -->
```python
import asyncio

from efp_app import app

info = asyncio.run(app.ainfo())
assert info.owner.username == "alice" and info.llm.enabled is True
```

## 8. `efp_app.client`

The HTTP clients of the Portal's SDK API (CONTRACTS.md, section 9), built on
`httpx`. `llm`, `connectors` and `app` use them.

Its names: `client.Client`, `client.AsyncClient`, `client.get_client`,
`client.get_async_client`, `client.configure`, `client.reset`,
`client.as_timeout`, `client.ca_bundle`, `client.visitor_header_value`,
`client.TimeoutLike` (the type of a timeout: `None`, a number of seconds or
an `httpx.Timeout`) and these constants:

| Constant | Value | Meaning |
|---|---|---|
| `client.API_PREFIX` | `"/api/app-sdk/v1"` | The path of the SDK API below `EFP_PORTAL_URL`. |
| `client.VISITOR_HEADER` | `"X-EFP-Visitor"` | The header `visitor=` is sent in. |
| `client.USER_AGENT` | `"efp-app-sdk/0.1.0"` | The `User-Agent` of every request. |
| `client.DEFAULT_TIMEOUT` | `120.0` | Seconds; the timeout of a client built without `timeout=`. |
| `client.CONNECT_TIMEOUT` | `10.0` | Seconds; the longest time to open a connection. |
| `client.GET_ATTEMPTS` | `3` | How often a `GET` is tried when the connection fails. |
| `client.RETRY_DELAY` | `0.2` | Seconds; the pause before the second try. The pause before the third is twice as long. |

### 8.1 When an app touches this module

Most apps never do. The cases:

| You want | Do |
|---|---|
| another timeout for one call | pass `timeout=` to that call |
| another timeout for every call | `client.configure(client=client.Client(timeout=30))` at start-up |
| a stand-in for the Portal in a test | use `efp_app.testing` (section 14): `testing.deployed` and `testing.FakePortal` do the rest of this row for you. By hand: build a `client.Client` with `transport=httpx.MockTransport(...)` and pass it as `client=`, or `client.configure` it |
| a call with another token or Portal address | build a `client.Client(token=..., portal_url=...)` and pass it as `client=` |
| the shared client for a path this SDK has no function for | `client.get_client()` |

### 8.2 `client.Client` and `client.AsyncClient`

<!-- signature: efp_app.client.Client.__init__ -->
```python
def __init__(
    self,
    *,
    token: Optional[str] = None,
    portal_url: Optional[str] = None,
    timeout: TimeoutLike = None,
    transport: Optional[httpx.BaseTransport] = None,
    settings: Optional[config.Settings] = None,
) -> None:
```

| Parameter | Default | Meaning |
|---|---|---|
| `token` | `None` | The app token. `None`: `EFP_APP_TOKEN`. |
| `portal_url` | `None` | The Portal's address. `None`: `EFP_PORTAL_URL`. |
| `timeout` | `None` | The timeout of every request of this client that names none: seconds or an `httpx.Timeout`. `None`: 120 seconds. A number means what 4.7 says. |
| `transport` | `None` | An `httpx` transport, for tests. A client with a transport uses no proxy and no CA variable. `client.AsyncClient` takes an `httpx.AsyncBaseTransport`. |
| `settings` | `None` | A `config.Settings` to use instead of reading the environment. |

The constructor raises `ConfigurationError` when the token or the address is
missing, when the address does not start with `http://` or `https://`, or
when a CA bundle named by the environment cannot be loaded. It sends
nothing.

<!-- signature: efp_app.client.Client.get efp_app.client.Client.post efp_app.client.Client.stream efp_app.client.Client.close -->
```python
def get(
    self,
    path: str,
    *,
    params: Optional[Mapping[str, Any]] = None,
    visitor: Any = None,
    timeout: TimeoutLike = None,
) -> Any:
def post(self, path: str, body: Any, *, visitor: Any = None, timeout: TimeoutLike = None) -> Any:
def stream(self, path: str, body: Any, *, visitor: Any = None, timeout: TimeoutLike = None) -> httpx.Response:
def close(self) -> None:
```

| Member | Meaning |
|---|---|
| `get(path, params=, visitor=, timeout=)` | `GET {portal}/api/app-sdk/v1{path}`. Returns the decoded JSON. |
| `post(path, body, visitor=, timeout=)` | `POST` with `body` as JSON. Returns the decoded JSON. Never repeated. |
| `stream(path, body, visitor=, timeout=)` | `POST`, and returns the open `httpx.Response` once its status is a success. The caller reads the body and closes the response. |
| `transport_error(exc)` | Returns the `EfpError` that stands for an `httpx` error. |
| `close()` | Closes the connections. `with client.Client() as mine:` does it at the end of the block. |
| `cached_app_info` | The last answer of `app.info` made through this client, or `None`. |

`client.AsyncClient` has the same constructor and the same members as
coroutines; it is closed with `await mine.aclose()` or `async with`.

<!-- signature: efp_app.client.AsyncClient.get efp_app.client.AsyncClient.post efp_app.client.AsyncClient.stream efp_app.client.AsyncClient.aclose -->
```python
async def get(
    self,
    path: str,
    *,
    params: Optional[Mapping[str, Any]] = None,
    visitor: Any = None,
    timeout: TimeoutLike = None,
) -> Any:
async def post(self, path: str, body: Any, *, visitor: Any = None, timeout: TimeoutLike = None) -> Any:
async def stream(
    self, path: str, body: Any, *, visitor: Any = None, timeout: TimeoutLike = None
) -> httpx.Response:
async def aclose(self) -> None:
```

### 8.3 What a client does on the wire

- Every request carries `Authorization: Bearer <app token>`,
  `Accept: application/json` and `User-Agent: efp-app-sdk/<version>`, and
  `X-EFP-Visitor` when `visitor=` holds a well-formed value. No cookies.
- The token is never logged, never part of an error message and not shown
  by `repr()`.
- A `GET` is tried up to three times when the connection cannot be made or
  breaks before an answer, with pauses of 0.2 and 0.4 seconds. It is not
  repeated after an answer with an error status or after a read timeout.
- A `POST` is never repeated. Every model call and every connector call is
  a `POST`.
- A redirect is not followed: it raises `UnexpectedResponseError`, whose
  message says that `EFP_PORTAL_URL` must be the Portal's own address.
- An answer with a status of 400 or more and the body
  `{"error": {"code": ..., "message": ...}}` raises the class of that code
  (section 10) with the status of the answer. Without that body it raises
  `UnexpectedResponseError`.
- A success whose body is not JSON raises `UnexpectedResponseError`.
- A timeout raises `PortalTimeoutError`; any other failure of the connection
  raises `PortalUnreachableError`. The message names the Portal's address.
- TLS trust and proxies follow the variables of 2.1.

### 8.4 The shared clients

<!-- signature: efp_app.client.get_client efp_app.client.get_async_client efp_app.client.configure efp_app.client.reset -->
```python
def get_client() -> Client:
def get_async_client() -> AsyncClient:
def configure(*, client: Optional[Client] = None, async_client: Optional[AsyncClient] = None) -> None:
def reset() -> None:
```

| Function | What it does |
|---|---|
| `client.get_client()` | Returns the shared synchronous client of the process. It is built at the first call, from the environment, and built again when `EFP_APP_TOKEN`, `EFP_PORTAL_URL` or the presence of `EFP_APP_ID` has changed. Raises `ConfigurationError` when it cannot be built. |
| `client.get_async_client()` | Returns the shared asynchronous client of the running event loop. A call from another loop gets a new client. Raises `ConfigurationError` when it cannot be built. |
| `client.configure(client=, async_client=)` | Makes the given clients the shared ones. They stay until `client.reset()`, whatever the environment says. An argument left out changes nothing. Returns `None`. |
| `client.reset()` | Closes the shared synchronous client and forgets both. The next call builds new ones from the environment. Returns `None`. |

A test rarely calls these two itself. `testing.FakePortal.install` calls
`client.configure` with clients that talk to a fake Portal, and
`testing.deployed` calls `client.reset` when its block begins and when it
ends (section 14).

### 8.5 `client.as_timeout` and `client.ca_bundle`

<!-- signature: efp_app.client.as_timeout efp_app.client.ca_bundle -->
```python
def as_timeout(value: TimeoutLike) -> httpx.Timeout:
def ca_bundle(env: Optional[Mapping[str, str]] = None) -> Union[bool, ssl.SSLContext]:
```

`client.as_timeout(value)` returns the `httpx.Timeout` of one request:
`value` itself when it is an `httpx.Timeout`; for a number `n`,
`httpx.Timeout(n, connect=min(n, 10.0))`; for `None`, the same with `n` =
120. Raises nothing for these types.

`client.ca_bundle(env)` returns the TLS trust for the Portal: an
`ssl.SSLContext` (or `True`, the default of `httpx`) built from the CA
variables of 2.1 in `env`, or in `os.environ` when `env` is `None`. Each
context is built once per process and shared. Raises `ConfigurationError`
when a named bundle cannot be loaded.

### 8.6 `client.visitor_header_value`

<!-- signature: efp_app.client.visitor_header_value -->
```python
def visitor_header_value(visitor: Any) -> Optional[str]:
```

Returns the `X-EFP-Visitor` value that `visitor=visitor` would send, or
`None` when nothing would be sent (3.7). Raises nothing.

### 8.7 Examples

A shared client with another timeout:

<!-- runnable -->
```python
from efp_app import client

mine = client.Client(timeout=30)                       # 30 seconds for every call made through it
client.configure(client=mine)                          # llm, connectors and app use it from now on
assert client.get_client() is mine
client.reset()                                         # closes it; the next call builds one from the environment
assert client.get_client() is not mine
```

A stand-in for the Portal in a test of your own, built by hand
(`efp_app.testing` has a ready one, with canned answers: section 14):

<!-- runnable -->
```python
import httpx

from efp_app import client, llm


def portal(request):
    assert request.url.path == "/api/app-sdk/v1/llm/complete"
    assert request.headers["authorization"] == "Bearer efpa_test"
    return httpx.Response(200, json={"text": "pong", "model": "m", "finish_reason": "stop", "usage": {}})


with client.Client(token="efpa_test", portal_url="https://portal.test", transport=httpx.MockTransport(portal)) as mine:
    assert llm.complete("ping", client=mine).text == "pong"
```

The shared clients, used directly:

<!-- runnable -->
```python
import asyncio

from efp_app import client

shared = client.get_client()
assert shared.get("/app")["slug"] == "example"         # the decoded JSON of GET /api/app-sdk/v1/app
answer = shared.post("/llm/complete", {"messages": [{"role": "user", "content": "Hello"}], "stream": False})
assert answer["text"] == "Hello."


async def main():
    return await client.get_async_client().get("/connectors")


assert [item["type"] for item in asyncio.run(main())["connectors"]] == ["github", "jenkins", "jira"]
```

Timeouts and trust:

<!-- runnable -->
```python
import ssl

import httpx

from efp_app import client

assert client.as_timeout(None) == httpx.Timeout(120.0, connect=10.0)
assert client.as_timeout(600) == httpx.Timeout(600.0, connect=10.0)
assert client.as_timeout(3) == httpx.Timeout(3.0)      # connecting never gets longer than the rest
custom = httpx.Timeout(5.0, read=300.0)
assert client.as_timeout(custom) is custom

trust = client.ca_bundle({})                           # no CA variable: the default trust
assert trust is True or isinstance(trust, ssl.SSLContext)
assert client.ca_bundle({"SSL_CERT_FILE": "/no/such/file.pem"}) is trust     # not a file: skipped
```

## 9. `efp_app.integrations`

One small module per web framework. Each finds the request's headers and
ends in `visitor.current` (3.2), so everything said there holds: the result
is a `Visitor` or `None`, and on a developer's machine it is the local
stand-in. Each module imports its framework only when a function is called;
`import efp_app.integrations.flask` works without Flask installed.

| Module | Functions |
|---|---|
| `efp_app.integrations.fastapi` | `get_visitor(connection)`, `require_visitor(connection)` |
| `efp_app.integrations.flask` | `get_visitor()`, `require_visitor()` |
| `efp_app.integrations.streamlit` | `get_visitor()`; the constant `SESSION_KEY` |
| `efp_app.integrations.gradio` | `get_visitor(request=None)` |

There is no `require_visitor` for Streamlit and Gradio.

What the browser receives when `require_visitor` refuses an anonymous
request differs by framework:

| `require_visitor` of | Status | Body |
|---|---|---|
| `efp_app.integrations.fastapi` | `401` | JSON: `{"detail": "Sign in to use this app."}` |
| `efp_app.integrations.flask` | `401` | HTML: Flask's standard error page, with the sentence `Sign in to use this app.` in it, unless the app registers `@app.errorhandler(401)` (9.2) |

Neither body has the shape of the gateway's own refusals,
`{"error": {"code": ..., "message": ...}}` (CONTRACTS.md, section 6.1). A
script in a page should therefore go by the status `401`, not by the body.

### 9.1 FastAPI and Starlette: `efp_app.integrations.fastapi`

<!-- signature: efp_app.integrations.fastapi.get_visitor efp_app.integrations.fastapi.require_visitor -->
```python
def get_visitor(connection: HTTPConnection) -> Optional[Visitor]:
def require_visitor(connection: HTTPConnection) -> Visitor:
```

| Function | Returns | Raises |
|---|---|---|
| `efp_app.integrations.fastapi.get_visitor` | the `Visitor` of the request, or `None` for an anonymous one | nothing |
| `efp_app.integrations.fastapi.require_visitor` | the `Visitor` | `starlette.exceptions.HTTPException(status_code=401, detail="Sign in to use this app.")` for an anonymous request; FastAPI answers it with `401` and `{"detail": "Sign in to use this app."}` |

`connection` is a `starlette.requests.HTTPConnection`: a `Request` or a
`WebSocket`. Both functions work as FastAPI dependencies, for HTTP and for
WebSocket routes. In a plain Starlette app call `get_visitor(request)`.
Without Starlette installed the module still imports;
`efp_app.integrations.fastapi.HTTPConnection` is then an empty stand-in
class.

The `401` of `require_visitor` is FastAPI's usual answer to an
`HTTPException`: the JSON body `{"detail": "Sign in to use this app."}`. It
has no `error` object and no code.

```python
from typing import Optional

from fastapi import Depends, FastAPI

from efp_app import Visitor
from efp_app.integrations.fastapi import get_visitor, require_visitor

app = FastAPI()


@app.get("/api/me")
def me(who: Optional[Visitor] = Depends(get_visitor)):
    return {"name": who.name if who else None}


@app.post("/api/notes")
def add_note(who: Visitor = Depends(require_visitor)):          # 401 for an anonymous visitor
    return {"saved_for": who.username}
```

### 9.2 Flask: `efp_app.integrations.flask`

<!-- signature: efp_app.integrations.flask.get_visitor efp_app.integrations.flask.require_visitor -->
```python
def get_visitor() -> Optional[Visitor]:
def require_visitor() -> Visitor:
```

| Function | Returns | Raises |
|---|---|---|
| `efp_app.integrations.flask.get_visitor` | the `Visitor` of the request being handled, or `None` | Flask's own error when called outside a request |
| `efp_app.integrations.flask.require_visitor` | the `Visitor` | for an anonymous request, what `flask.abort(401, description="Sign in to use this app.")` raises: `werkzeug.exceptions.Unauthorized`. Flask answers it with `401` and its standard HTML error page, unless the app registers `@app.errorhandler(401)` |

Both read `flask.request.headers`.

The standard page is `text/html`: the title `401 Unauthorized` and the
sentence `Sign in to use this app.` in a paragraph. It is not JSON, so
`response.json()` in a page's script fails on it; test `response.status`.

```python
from flask import Flask, jsonify

from efp_app.integrations.flask import get_visitor, require_visitor

app = Flask(__name__)


@app.get("/me")
def me():
    who = get_visitor()                                         # a Visitor, or None
    return jsonify(name=who.name if who else None)


@app.post("/notes")
def add_note():
    who = require_visitor()                                     # aborts with 401 for an anonymous visitor
    return jsonify(saved_for=who.username)
```

An app that wants one shape for all its errors, the gateway's, registers a
handler. It answers every `401` the app aborts with, not only the one of
`require_visitor`:

```python
from flask import Flask, jsonify

app = Flask(__name__)


@app.errorhandler(401)
def sign_in_required(error):                                    # error.description: "Sign in to use this app."
    return jsonify(error={"code": "sign_in_required", "message": error.description}), 401
```

The anonymous request then gets `401` with
`{"error": {"code": "sign_in_required", "message": "Sign in to use this app."}}`.

### 9.3 Streamlit: `efp_app.integrations.streamlit`

<!-- signature: efp_app.integrations.streamlit.get_visitor -->
```python
def get_visitor() -> Optional[Visitor]:
```

`efp_app.integrations.streamlit.get_visitor()` returns the `Visitor` of the
browser session, or `None`. It raises nothing of its own.

- Streamlit gives a script only the headers of the request that opened the
  session, and the visitor value in them is valid for 60 seconds. So the
  visitor is verified at the first run and then remembered in
  `st.session_state` under the key `streamlit.SESSION_KEY`
  (`"_efp_visitor"`). Later runs return the remembered `Visitor`.
- An anonymous result is not remembered: it is looked up again at each run.
- The headers come from `st.context.headers` (Streamlit 1.37 or newer). On
  an older Streamlit the function falls back to Streamlit's internal
  WebSocket headers, and to no headers when that fails.

```python
import streamlit as st

from efp_app.integrations.streamlit import get_visitor

who = get_visitor()                                             # a Visitor, or None
st.write("Hello, " + (who.name if who else "guest"))
```

### 9.4 Gradio: `efp_app.integrations.gradio`

<!-- signature: efp_app.integrations.gradio.get_visitor -->
```python
def get_visitor(request: Any = None) -> Optional[Visitor]:
```

| Parameter | Meaning |
|---|---|
| `request` | The `gr.Request` of the event: add a parameter annotated `gr.Request` to the event function and Gradio fills it in. Any object with a `headers` mapping works. `None`, or an object without `headers`: a request without the header. |

`efp_app.integrations.gradio.get_visitor(request)` returns the `Visitor`
behind the event, or `None`. It raises nothing. The module does not import
Gradio.

```python
import gradio as gr

from efp_app.integrations.gradio import get_visitor


def greet(request: gr.Request):                                 # Gradio fills in the request
    who = get_visitor(request)
    return "Hello, " + (who.name if who else "guest")


with gr.Blocks() as demo:
    greeting = gr.Markdown()
    demo.load(greet, None, greeting)

if __name__ == "__main__":
    demo.launch()                                               # no server_name, server_port or root_path
```

## 10. `efp_app.errors`

One exception class per error code of the Portal's SDK API (CONTRACTS.md,
section 9) and of the storage service (section 7.1), plus the errors the SDK
finds itself. All derive from `errors.EfpError`.

### 10.1 `errors.EfpError`

<!-- signature: efp_app.errors.EfpError.__init__ -->
```python
def __init__(self, message: Optional[str] = None, *, code: Optional[str] = None, status: Optional[int] = None):
```

| Member | Meaning |
|---|---|
| `code` | The machine-readable code, for example `budget_exceeded`. The default is the code of the class. |
| `message` | The detail, written for the developer and the log. It may name internal things. Without a detail it is the sentence of `user_message`. |
| `status` | The HTTP status that goes with the error: the one the Portal answered with; or, when the SDK found the error before a call or inside a stream, the status of the class. `None` for errors without one: configuration, connection, storage. |
| `user_message` | A property: a short sentence that can be shown to a visitor as it is. It is fixed per class and never holds the detail. |
| `str(exc)` | `"code: message"`. |
| `repr(exc)` | `ClassName(code=..., status=..., message=...)`. |

`RateLimitedError` and `BudgetExceededError` take one more keyword,
`retry_after`, and have the attribute of that name (10.3). No other class
has it.

### 10.2 The hierarchy

| Class | Derives from | `code` | Status | `user_message` | Raised when |
|---|---|---|---|---|---|
| `errors.EfpError` | `Exception` | `error` | none | Something went wrong. Please try again. | Itself only for an error code of the Portal that this SDK has no class for; `code` and `status` are then the Portal's. |
| `errors.ConfigurationError` | `EfpError` | `not_configured` | none | This app is not set up yet. Its owner needs to finish the configuration. | `EFP_APP_TOKEN` or `EFP_PORTAL_URL` is missing; the address is not an `http(s)` URL; a CA bundle cannot be loaded. Raised when a client is built: at the first call that needs the Portal, not at import. |
| `errors.PortalUnreachableError` | `EfpError` | `portal_unreachable` | none | The app cannot reach the EFP Portal right now. Please try again in a moment. | The connection to the Portal could not be made or broke: DNS, refused, TLS, reset. Also in the middle of a stream. |
| `errors.PortalTimeoutError` | `PortalUnreachableError` | `portal_timeout` | none | The EFP Portal took too long to answer. Please try again. | The Portal did not answer within the timeout. |
| `errors.UnexpectedResponseError` | `EfpError` | `unexpected_response` | none | The EFP Portal gave an unexpected answer. Please try again. | The answer is not part of the contract: a redirect, an error status without the error body, a body that is not JSON, a model answer without text, a connector answer without a status. `status` is the HTTP status of that answer when there was one. Usually `EFP_PORTAL_URL` is wrong. |
| `errors.StreamInterruptedError` | `EfpError` | `stream_interrupted` | none | The answer was cut off. Please try again. | A streamed answer ended before its final event. |
| `errors.InvalidRequestError` | `EfpError` | `invalid_request` | 400 | The app sent a request the EFP Portal could not accept. | A malformed call. The SDK raises it before the call for bad `messages`, a bad connector `type` or `method`, or `json` together with `body`. |
| `errors.ModelNotAllowedError` | `EfpError` | `model_not_allowed` | 400 | The requested model is not available to this app. | `model=` names a model the app may not use. |
| `errors.InvalidPathError` | `EfpError` | `invalid_path` | 400 | The app asked for an address that is not allowed. | A connector path that breaks a rule of 5.5. The SDK raises it before the call. |
| `errors.InvalidTokenError` | `EfpError` | `invalid_token` | 401 | This app is not connected to the EFP Portal: its token is missing or no longer valid. | The token was reset or is mistyped. |
| `errors.AppDisabledError` | `EfpError` | `app_disabled` | 403 | This app has been disabled. | The app was disabled in the Portal. |
| `errors.LlmDisabledError` | `EfpError` | `llm_disabled` | 403 | The owner has not turned on the AI model for this app. | A model call while the model is off for the app. |
| `errors.ConnectorDisabledError` | `EfpError` | `connector_disabled` | 403 | The owner has not turned on this connector for the app. | A connector call for a type that is off for the app. |
| `errors.MethodNotAllowedError` | `EfpError` | `method_not_allowed` | 403 | This app is only allowed to read from that system. | A method other than `GET` or `HEAD` to a connector without permission to write. |
| `errors.ProviderAuthFailedError` | `EfpError` | `provider_auth_failed` | 424 | The owner's sign-in to the AI provider has expired. The owner needs to reconnect it in the Portal. | The owner's model provider needs a new sign-in. |
| `errors.ConnectorNotConfiguredError` | `EfpError` | `connector_not_configured` | 424 | The owner has not set up this connector in the Portal. | The owner has no connector of that type. |
| `errors.RateLimitedError` | `EfpError` | `rate_limited` | 429 | Too many requests right now. Please wait a moment and try again. | Too many calls in a short time. Has `retry_after`. |
| `errors.BudgetExceededError` | `EfpError` | `budget_exceeded` | 429 | This app has used up its AI budget for today. | The app's daily budget is used up. Has `retry_after`; the sentence gets a hint about the wait (10.3). |
| `errors.ProviderError` | `EfpError` | `provider_error` | 502 | The AI provider returned an error. Please try again. | The model provider failed. Also an error event without a code inside a stream. |
| `errors.UpstreamError` | `EfpError` | `upstream_error` | 502 | The other system returned an error. Please try again. | The Portal got no usable answer from the connector's system. |
| `errors.UpstreamTimeoutError` | `EfpError` | `upstream_timeout` | 504 | The other system took too long to answer. Please try again. | The connector's system was too slow. |
| `errors.StorageError` | `EfpError` | `storage_error` | none | The app could not read or save its data. Please try again. | The base of the storage errors. Itself for a failed read or write, a storage service that answers wrongly, an object that is not JSON (`code` is `invalid_json`), and a code of the storage service without a class, such as `bad_request` (`code` is that code). |
| `errors.StorageNotFoundError` | `StorageError` | `not_found` | none | That item does not exist. | `storage.load` or `storage.load_json` of a missing key without a default. |
| `errors.InvalidKeyError` | `StorageError` | `invalid_key` | none | The app could not read or save its data. Please try again. | A key or prefix that breaks a rule of 6.2. |
| `errors.TooLargeError` | `StorageError` | `too_large` | none | That is too large to save. | An object of more than 64 MiB. |
| `errors.QuotaExceededError` | `StorageError` | `quota_exceeded` | none | This app has run out of storage space. | A save beyond the app's quota (6.3), or a full disk. |
| `errors.StorageUnavailableError` | `StorageError` | `storage_unavailable` | none | The app could not read or save its data. Please try again. | No place for the data: deployed without `EFP_APP_DATA_DIR` and `EFP_APP_STORAGE_SOCKET`, or the storage service cannot be reached. |
| `errors.DataDirUnavailableError` | `StorageError` | `data_dir_unavailable` | none | The app could not read or save its data. Please try again. | `storage.data_dir()` on a host in `hidden` mode. |

Reading the table:

- "Status" is the status of the class (`default_status`). When the Portal
  answered with an error, `exc.status` is the status of that answer; the
  contract gives each code the status shown here.
- `except PortalUnreachableError` also catches `PortalTimeoutError`;
  `except StorageError` catches the six classes below it; `except EfpError`
  catches everything in the table.
- `errors.PORTAL_ERRORS` maps the fifteen codes of the Portal
  (`invalid_request` to `upstream_timeout`) to their classes.
  `errors.STORAGE_ERRORS` maps `not_found`, `invalid_key`, `too_large` and
  `quota_exceeded` to theirs.

### 10.3 `retry_after`

`RateLimitedError` and `BudgetExceededError` have `retry_after`: the seconds
to wait, a `float`, from the `Retry-After` header of the Portal's answer.
The header may be a number of seconds or an HTTP date; the result is never
negative. `None` when the Portal sent no header, and always `None` for an
error that arrived as an event inside a stream.

`BudgetExceededError.user_message` adds the wait to its sentence:

| `retry_after` | Added to the sentence |
|---|---|
| `None` or `0` | nothing |
| under 90 seconds | ` Try again in about a minute.` |
| under 90 minutes | ` Try again in about N minutes.` (N is the seconds divided by 60, rounded) |
| longer | ` Try again in about N hours.` (rounded) |

`RateLimitedError.user_message` is always the sentence of the table.

### 10.4 From an error to an answer for the visitor

The pattern of the starters and the examples:

1. Catch `EfpError` around every call of the SDK (a more specific class
   first, when the app can do something about it).
2. Write `str(exc)` to the log. The owner reads the log in the Portal.
3. Show `exc.user_message`. Never show `exc.message`.
4. In an HTTP answer, use `429` when `exc.status == 429` and `502`
   otherwise: the failure is behind the app, not in the visitor's request.
5. In a stream that already began, append the sentence to the text.

<!-- runnable -->
```python
from efp_app import errors


def answer_for(exc):
    """An SDK error as (HTTP status, JSON body) for the page."""
    print("%s" % exc)                                  # the log: "budget_exceeded: daily budget of 50 credits used"
    status = 429 if exc.status == 429 else 502
    return status, {"error": {"code": exc.code, "message": exc.user_message}}


spent = errors.from_response(
    429,
    {"error": {"code": "budget_exceeded", "message": "daily budget of 50 credits used"}},
    {"Retry-After": "7200"},
)
assert isinstance(spent, errors.BudgetExceededError) and spent.retry_after == 7200.0
assert answer_for(spent) == (
    429,
    {"error": {"code": "budget_exceeded", "message": "This app has used up its AI budget for today. Try again in about 2 hours."}},
)
off = errors.LlmDisabledError("llm is off for app 0b0f")
assert answer_for(off) == (502, {"error": {"code": "llm_disabled", "message": "The owner has not turned on the AI model for this app."}})
assert answer_for(errors.StorageNotFoundError())[1]["error"]["message"] == "That item does not exist."
assert getattr(off, "retry_after", None) is None       # only two classes have the attribute
```

### 10.5 Functions

The SDK uses these to build its errors. An app rarely needs them; a test of
an app may.

<!-- signature: efp_app.errors.for_code efp_app.errors.for_storage_code efp_app.errors.from_response efp_app.errors.parse_retry_after -->
```python
def for_code(
    code: str,
    message: Optional[str] = None,
    *,
    status: Optional[int] = None,
    retry_after: Optional[float] = None,
) -> EfpError:
def for_storage_code(code: str, message: Optional[str] = None) -> StorageError:
def from_response(status: int, body: Any, headers: Optional[Mapping[str, Any]] = None) -> EfpError:
def parse_retry_after(headers: Optional[Mapping[str, Any]], *, now: Optional[datetime] = None) -> Optional[float]:
```

| Function | Returns |
|---|---|
| `errors.for_code(code, message, status=, retry_after=)` | The exception for a code of the SDK API; a plain `EfpError` with that code for an unknown one. `retry_after` is used by the two classes that have it. |
| `errors.for_storage_code(code, message)` | The exception for a code of the storage service; a plain `StorageError` with that code for an unknown one. |
| `errors.from_response(status, body, headers)` | The exception for a failed answer of the Portal. `body` is `bytes`, a `str` or decoded JSON. Without the error body of the contract: an `UnexpectedResponseError` with that status. |
| `errors.parse_retry_after(headers, now=)` | The seconds of a `Retry-After` header (a number or an HTTP date), or `None`. `now` is the time to measure a date against; `None`: the current time. |

All four return the exception; none raises it.

<!-- runnable -->
```python
from datetime import datetime, timezone

from efp_app import errors

exc = errors.for_code("connector_disabled", "jira is off for app 0b0f")
assert type(exc) is errors.ConnectorDisabledError and exc.status == 403
assert str(exc) == "connector_disabled: jira is off for app 0b0f"
assert exc.user_message == "The owner has not turned on this connector for the app."

unknown = errors.for_code("teapot", "short and stout", status=418)
assert type(unknown) is errors.EfpError and (unknown.code, unknown.status) == ("teapot", 418)
assert type(errors.for_storage_code("quota_exceeded")) is errors.QuotaExceededError
assert errors.for_storage_code("bad_request", "x").code == "bad_request"

assert errors.parse_retry_after({"Retry-After": "120"}) == 120.0
now = datetime(2026, 11, 15, 8, 0, 0, tzinfo=timezone.utc)
assert errors.parse_retry_after({"Retry-After": "Sun, 15 Nov 2026 08:30:00 GMT"}, now=now) == 1800.0
assert errors.parse_retry_after({}) is None

page = errors.from_response(503, b"<html>upstream down</html>")
assert type(page) is errors.UnexpectedResponseError and page.status == 503
assert errors.BudgetExceededError("x", retry_after=600).user_message.endswith("Try again in about 10 minutes.")
```

## 11. `efp_app.manifest`

The rules of the app manifest `efp-app.json` (CONTRACTS.md, section 2), as
`efp-app check` and `efp-app dev` apply them. The file lives in the app's
entry directory and is UTF-8 JSON (a byte order mark is accepted).

Its names: `manifest.validate`, `manifest.parse`, `manifest.read`,
`manifest.split_entry`, `manifest.path_problem`, `manifest.Problem`,
`manifest.Manifest` and the constants of 11.4.

### 11.1 The keys and their rules

```json
{
  "name": "Mind map",
  "description": "Draw and share mind maps.",
  "icon": "icon.png",
  "type": "fastapi",
  "entry": "main:app",
  "requirements": "requirements.txt",
  "health_path": "/",
  "startup_timeout": 60
}
```

| Key | Needed | Rule that `manifest.validate` checks | Default that `manifest.parse` fills in |
|---|---|---|---|
| `name` | always | A string of 1 to 80 characters, not counting blanks at its ends. | none |
| `type` | always | One of `fastapi`, `flask`, `streamlit`, `gradio`, `command`, `static`. | none |
| `entry` | for `fastapi`, `flask`, `streamlit`, `gradio` | `fastapi`, `flask`: `module:attribute`, both sides dotted Python names (`main:app`, `pkg.web:server.app`). `streamlit`, `gradio`: the relative path of the script (`app.py`). `static`: the relative path of the directory to serve. `command`: not checked. | `"."` for `static` |
| `command` | for `command` | One line that is not blank, of at most 2000 characters. For another type it is not used, but must be a string when present. | none |
| `description` | no | A string of at most 500 characters. | `""` |
| `icon` | no | A relative path that is not empty. | none |
| `requirements` | no | A relative path. | `"requirements.txt"` |
| `health_path` | no | A string of at most 200 characters that starts with `/` and holds only URL characters: letters, digits and ``._~!$&'()*+,;=:@%/?-``. | `"/"` |
| `startup_timeout` | no | A whole number from 1 to 300, in seconds. `true`, `60.0` and `"60"` are refused. | `60` |
| `strip_prefix` | no | `true`, `false` or `null`. | by type: `true` for `fastapi`, `gradio`, `static`; `false` for `flask`, `streamlit`, `command` |
| `owners` | no | A list of strings that are not blank (Portal usernames). An empty list is allowed. | `()` |

- A relative path uses forward slashes, is not absolute (no leading `/`, no
  drive letter such as `C:`), has no `..` segment and no NUL character.
- `null` for an optional key is the same as leaving the key out. So is `""`
  for `requirements` and `health_path`.
- A key that is not in the table gives the warning
  `unknown key "x" is ignored`, unless its name starts with `_` or `$`.
- `validate` checks the JSON only. Whether the files exist is checked by
  `efp-app check` (12.4).

### 11.2 Functions

<!-- signature: efp_app.manifest.validate efp_app.manifest.parse efp_app.manifest.read efp_app.manifest.split_entry efp_app.manifest.path_problem -->
```python
def validate(data: Any, *, for_launch: bool = False) -> List[Problem]:
def parse(data: Dict[str, Any]) -> Manifest:
def read(directory: Path) -> Tuple[Optional[Dict[str, Any]], List[Problem]]:
def split_entry(entry: str) -> Optional[Tuple[str, str]]:
def path_problem(value: str) -> Optional[str]:
```

| Function | Parameters | Returns |
|---|---|---|
| `manifest.validate(data, for_launch=False)` | `data`: the decoded JSON. `for_launch=True` skips the rule for `owners`, which does not matter for running the app locally. | A list of `manifest.Problem`: errors for broken rules, warnings for unknown keys. An empty list for a valid manifest. For data that is not a JSON object: one error. |
| `manifest.parse(data)` | `data`: a decoded manifest that `validate` found no error in. | A `manifest.Manifest` with the defaults filled in. It does not check: a value of the wrong type becomes the default, and an unknown `type` becomes `"command"`. |
| `manifest.read(directory)` | `directory`: the app's directory. | A pair. The decoded JSON and the problems of `validate`; or `None` and one error when the file is missing, is not UTF-8, is not JSON (the problem carries the line) or cannot be read. |
| `manifest.split_entry(entry)` | `entry`: text. | `("main", "app")` for `"main:app"`; `None` when the text is not `module:attribute`. |
| `manifest.path_problem(value)` | `value`: a path from a manifest. | `None` for an acceptable relative path, else the reason: `must use forward slashes`, `may not be an absolute path`, `may not contain ".."` or `may not contain a NUL character`. |

None of the five raises for bad input; what is wrong comes back as a value.

### 11.3 `manifest.Problem` and `manifest.Manifest`

Frozen dataclasses.

<!-- fields: efp_app.manifest.Problem -->
```python
level: str
message: str
file: Optional[str] = None
line: Optional[int] = None
```

| Member | Meaning |
|---|---|
| `level` | `"error"` (`manifest.ERROR`) or `"warning"` (`manifest.WARNING`). |
| `message` | What is wrong. |
| `file` | The file it is about, relative to the app's directory, or `None`. |
| `line` | The line in that file, or `None`. |
| `render()` | The line `efp-app check` prints: the level padded to seven characters, then `file:line: message`. |

<!-- signature: efp_app.manifest.Problem.render -->
```python
def render(self) -> str:
```

<!-- fields: efp_app.manifest.Manifest -->
```python
name: str
type: str
description: str = ''
icon: Optional[str] = None
entry: Optional[str] = None
command: Optional[str] = None
requirements: str = 'requirements.txt'
requirements_named: bool = False
health_path: str = '/'
startup_timeout: int = 60
strip_prefix: bool = False
owners: Tuple[str, ...] = ()
```

The fields are the keys of 11.1 with the defaults filled in.
`requirements_named` is `True` when the manifest itself names a requirements
file, and `False` when the default applies.

### 11.4 Constants

| Constant | Value |
|---|---|
| `manifest.FILE_NAME` | `"efp-app.json"` |
| `manifest.TYPES` | `("fastapi", "flask", "streamlit", "gradio", "command", "static")` |
| `manifest.MODULE_TYPES` | `("fastapi", "flask")`: `entry` is `module:attribute` |
| `manifest.SCRIPT_TYPES` | `("streamlit", "gradio")`: `entry` is a script |
| `manifest.STRIP_PREFIX_BY_TYPE` | `{"fastapi": True, "gradio": True, "static": True, "flask": False, "streamlit": False, "command": False}`: whether the gateway removes `/{slug}` before a request reaches the app |
| `manifest.KNOWN_KEYS` | the eleven keys of 11.1 |
| `manifest.DEFAULT_REQUIREMENTS`, `manifest.DEFAULT_HEALTH_PATH`, `manifest.DEFAULT_STARTUP_TIMEOUT` | `"requirements.txt"`, `"/"`, `60` |
| `manifest.MAX_NAME`, `manifest.MAX_DESCRIPTION`, `manifest.MAX_COMMAND`, `manifest.MAX_HEALTH_PATH`, `manifest.MAX_STARTUP_TIMEOUT` | `80`, `500`, `2000`, `200`, `300` |
| `manifest.ERROR`, `manifest.WARNING` | `"error"`, `"warning"` |

### 11.5 Examples

<!-- runnable -->
```python
from efp_app import manifest

good = {"name": "Mind map", "type": "fastapi", "entry": "main:app"}
assert manifest.validate(good) == []
found = manifest.parse(good)
assert (found.requirements, found.requirements_named, found.health_path) == ("requirements.txt", False, "/")
assert (found.startup_timeout, found.strip_prefix, found.owners) == (60, True, ())

problems = manifest.validate({"name": "x", "type": "flask", "entry": "app.py", "startup_timeout": 900, "colour": "red"})
assert [(problem.level, problem.message) for problem in problems] == [
    ("error", '"entry" must be module:attribute for a flask app, for example "main:app"'),
    ("error", '"startup_timeout" must be between 1 and 300 seconds'),
    ("warning", 'unknown key "colour" is ignored'),
]
assert problems[1].render() == 'error   efp-app.json: "startup_timeout" must be between 1 and 300 seconds'

assert manifest.validate({"name": "x", "type": "static", "owners": "alice"}) != []
assert manifest.validate({"name": "x", "type": "static", "owners": "alice"}, for_launch=True) == []

assert manifest.split_entry("pkg.web:app") == ("pkg.web", "app") and manifest.split_entry("app.py") is None
assert manifest.path_problem("../icon.png") == 'may not contain ".."' and manifest.path_problem("img/icon.png") is None
```

Reading the file of a directory:

<!-- runnable -->
```python
import json
from pathlib import Path

from efp_app import manifest

data, problems = manifest.read(Path("."))              # no efp-app.json here
assert data is None and problems[0].level == "error" and "not found" in problems[0].message

Path("efp-app.json").write_text(json.dumps({"name": "Team links", "type": "static"}), encoding="utf-8")
data, problems = manifest.read(Path("."))
assert problems == [] and manifest.parse(data).entry == "."
```

## 12. The `efp-app` command

The package installs the command `efp-app` (`efp_app.cli.main`).
`python -m efp_app` is the same command.

```text
efp-app init <fastapi|flask|streamlit|gradio|static> [directory] [--owner USERNAME]... [--name NAME] [--force]
efp-app dev [directory] [--port N] [--prefix NAME] [--dry-run]
efp-app check [directory] [--strict]
efp-app --version
```

### 12.1 Exit codes and output

| Exit code | When |
|---|---|
| `0` | The command did its work. For `check`: no error, and with `--strict` no warning either. |
| `1` | The command says what is wrong: `init` would overwrite files; `dev` cannot start the app; `check` found an error, or a warning with `--strict`. |
| `2` | A usage error: no command (the help is printed), an unknown option or type, a bad `--prefix`. |
| the app's | `dev` returns the exit code of the app's process when that process ends. |
| `130` | `dev` was stopped with Ctrl+C. |

`--version` prints `efp-app 0.1.0`. `-h` and `--help` print the help of the
command or of a subcommand and exit with `0`.

What a command found or did goes to standard output. Why `init` or `dev`
cannot go on goes to standard error. The findings of `check` are results:
they go to standard output.

### 12.2 `efp-app init`

Copies a starter app into a directory.

| Argument | Meaning |
|---|---|
| `type` | Which starter: `fastapi`, `flask`, `streamlit`, `gradio` or `static`. There is no starter for `command`. |
| `directory` | Where to put it. Default: the current directory. It is created when missing. |
| `--owner USERNAME` | Writes `"owners"` into the manifest: only the listed Portal members may register the app. Repeat it for more names. Default: no `"owners"`, so any member may. |
| `--name NAME` | Writes `"name"` into the manifest. |
| `--force` | Overwrite files that already exist. |

Without `--force` nothing is written when one of the starter's files already
exists in the directory: the files are listed on standard error and the exit
code is `1`. Files of yours that the starter does not have are never touched.
A `directory` that exists and is not a directory gives `1` as well.

| Starter | Files |
|---|---|
| `fastapi` | `efp-app.json`, `main.py`, `requirements.txt`, `static/index.html`, `static/app.js`, `static/style.css`, `README.md`, `.env.example`, `.gitignore` |
| `flask` | `efp-app.json`, `app.py`, `requirements.txt`, `templates/index.html`, `static/app.js`, `static/style.css`, `README.md`, `.env.example`, `.gitignore` |
| `streamlit` | `efp-app.json`, `app.py`, `requirements.txt`, `README.md`, `.env.example`, `.gitignore` |
| `gradio` | `efp-app.json`, `app.py`, `requirements.txt`, `README.md`, `.env.example`, `.gitignore` |
| `static` | `efp-app.json`, `public/index.html`, `public/app.js`, `public/style.css`, `public/data.json`, `README.md` |

It prints the files it created and the next steps:

```text
Created a fastapi app in my-app:
  .env.example
  ...
  static/style.css

Next:
  1. cd my-app
  2. pip install -r requirements.txt
  3. copy .env.example to .env and fill in the app token and the Portal address
  4. efp-app dev
  5. efp-app check, then push the code and register the app in the Portal
```

### 12.3 `efp-app dev`

Starts the app of `efp-app.json` on this machine with the command line the
host uses for its type (CONTRACTS.md, section 8.1).

| Argument | Meaning |
|---|---|
| `directory` | The app's directory. Default: the current one. The app runs with this directory as its working directory. |
| `--port N` | The port to listen on, on `127.0.0.1`. Default: `PORT` of the environment, else `PORT` of `.env`, else `8000`. |
| `--prefix NAME` | Serve the app under `/NAME/`, behind a local stand-in for the gateway, as it is addressed once deployed. Without it the app is served at `/`, with no path prefix. |
| `--dry-run` | Print what would be started and stop. |

**What it starts.** `python` is the interpreter that runs `efp-app`, so the
app's requirements must be installed in the same environment.

| Type | Command | Variables it sets |
|---|---|---|
| `fastapi` | `python -m uvicorn {entry} --host 127.0.0.1 --port {port} --no-server-header` | |
| `flask` | `python -m gunicorn {entry} --bind 127.0.0.1:{port} --workers 1 --threads 8 --timeout 120`; on Windows `python -m flask --app {entry} run --port {port}`, because gunicorn needs a POSIX system | |
| `streamlit` | `python -m streamlit run {entry} --server.port {port} --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false --server.enableCORS false --server.enableXsrfProtection false` | |
| `gradio` | `python {entry}` | `GRADIO_SERVER_NAME=127.0.0.1`, `GRADIO_SERVER_PORT={port}` |
| `command` | `sh -c "{command}"` | |
| `static` | `python -m http.server {port} --bind 127.0.0.1 --directory {entry}`: a local preview; on the host a static app has no process | |

**The environment of the app.** In this order:

1. the environment `efp-app dev` was started in;
2. each variable of `.env` in the app's directory that is not set, or is
   empty, in that environment: the environment wins over `.env`;
3. `EFP_APP_BASE_PATH`, `SCRIPT_NAME` and `GRADIO_ROOT_PATH` are removed;
4. `PORT` is the port the app listens on, and `PYTHONUNBUFFERED=1`; when no
   proxy variable is set but the operating system's own settings name a
   proxy (Windows, macOS), it is written as `HTTP_PROXY` and `HTTPS_PROXY`,
   with the system's bypass list as `NO_PROXY`; when a proxy variable is
   set, `127.0.0.1`, `localhost` and `::1` are added to
   `NO_PROXY` and `no_proxy`, so that what the app asks of its own port does
   not go to the proxy (the host does the same); on Windows the three names
   of step 3 are added to `MSYS2_ENV_CONV_EXCL`, and `MSYS_NO_PATHCONV=1`
   and `MSYS2_ARG_CONV_EXCL=*` are set unless they already are, so that Git
   Bash rewrites neither the prefix nor a command's arguments into Windows
   paths;
5. the variables of the table above;
6. with `--prefix`, the variables named below.

`EFP_APP_ID` is never set by `efp-app dev`, so the SDK behaves as on a
developer's machine (2.2). When the variable is set in the environment
anyway, a warning says so.

**The `.env` file.** One `NAME=value` per line. Blank lines and lines that
start with `#` are skipped. A leading `export ` is allowed. Quotes around a
value are removed; in a value without quotes, text after ` #` is a comment.
A line without `=` or with a name that is not a variable name is skipped. A
missing file is not an error.

**With `--prefix NAME`.** `NAME` is a slug: 3 to 40 characters, lower-case
letters, digits and `-`, with a letter or a digit at both ends. A leading
`/` is accepted. A reserved name (`_efp`, `api`, `app`, `apps`, `admin`,
`assets`, `auth`, `efp`, `health`, `login`, `logout`, `portal`, `static`,
`www`) or another bad value ends with exit code `2`.

- The stand-in gateway listens on the port; the app listens on a free port
  behind it. Open the address that `efp-app dev` prints, not the one the
  app's own server prints: that port has no gateway.
- The app is started as on the host: `--root-path /NAME` for `fastapi`,
  `SCRIPT_NAME=/NAME` for `flask`, `--server.baseUrlPath /NAME` for
  `streamlit`, `GRADIO_ROOT_PATH=/NAME` for `gradio`. `EFP_APP_BASE_PATH`
  is `/NAME`, `EFP_APP_SLUG` is `NAME` and `EFP_APP_PUBLIC_URL` is
  `http://127.0.0.1:{port}/NAME/`. On Windows, Flask's own server ignores
  `SCRIPT_NAME`, so the app is started through
  `efp_app._devgateway:flask_app`, which applies it the way gunicorn does.
- The gateway does what the real one does to a request (CONTRACTS.md,
  sections 6.1 and 6.2): `/NAME` redirects to `/NAME/`; `/NAME/...` goes to
  the app with the prefix removed or kept as its type says; the app gets the
  `X-Forwarded-` headers; a request that carries an `X-EFP-` header is
  refused with `400`; the app's cookies are confined to `/NAME/`.
- A request outside `/NAME/` gets `404` and a line on the console: it would
  not reach the app once deployed. `/` is redirected to `/NAME/`, with a
  line as well.
- Not reproduced: sign-in and the visitor header. `EFP_APP_ID` stays unset,
  so the visitor and the storage are those of a developer's machine.

**What it prints**, before the app's own output:

```text
efp-app dev: Mind map (fastapi)
  directory  /home/alice/my-app
  address    http://127.0.0.1:8000/
  command    /home/alice/my-app/.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000 --no-server-header
  Portal     https://efp.example.com
  token      set
```

More lines appear when they apply: `sets NAME=value` for each variable it
sets, `note` for a difference from the host (Flask on Windows, the static
preview), `gateway` with `--prefix`, and `warning` when `EFP_APP_TOKEN` or
`EFP_PORTAL_URL` is not set or `EFP_APP_ID` is set. The token itself is
never printed. A missing token does not stop the app from starting: the
model and connector calls then raise `ConfigurationError`. A `static` app
has no `Portal` and `token` lines.

**It stops with exit code `1`**, and a message on standard error, when

- `efp-app.json` is missing or breaks a rule (a slip in `owners` is let
  through);
- the module that runs the app is not installed in this Python: `uvicorn`,
  `gunicorn` (`flask` on Windows), `streamlit` or `gradio`;
- the entry script of a `streamlit` or `gradio` app does not exist;
- a `command` app is started on a machine without `sh`;
- the app's command cannot be started, or, with `--prefix`, the port is
  taken.

### 12.4 `efp-app check`

Checks an app before it is registered or deployed.

| Argument | Meaning |
|---|---|
| `directory` | The app's directory. Default: the current one. A path that is not a directory: a message on standard error and exit code `1`. |
| `--strict` | Exit with `1` on warnings too. |

It prints the errors, then the warnings, one per line, then a summary:

```text
error   efp-app.json: "entry" is "server:app", but server.py does not exist
warning static/app.js:3: root-absolute URL fetch("/api/items"): use a relative URL: fetch("api/items")
efp-app check: 1 error, 1 warning
```

With nothing to report it prints one line:
`efp-app check: no problems found in <directory>`.

**Errors** (exit code `1`):

| What is wrong | The message |
|---|---|
| no manifest | `efp-app.json: not found in <directory>: an app needs this manifest in its entry directory (...)` |
| the manifest cannot be read | `is not UTF-8 text`, `is not valid JSON: <reason>` (with the line), `cannot be read: <reason>`, `the manifest must be a JSON object` |
| `name` | `"name" is required: 1 to 80 characters`, `"name" is longer than 80 characters` |
| `description` | `"description" must be text`, `"description" is longer than 500 characters` |
| `type` | `"type" is required: one of fastapi, flask, streamlit, gradio, command, static` |
| `entry` | `"entry" must be module:attribute for a <type> app, for example "main:app"`; `"entry" must be the path of the script for a <type> app, for example "app.py"`; `"entry" must be a directory for a static app (default ".")`; `"entry" <path reason>` |
| `command` | `"command" is required for a command app: a shell command line that listens on $PORT`, `"command" must be a single line of up to 2000 characters`, `"command" must be text` |
| `icon`, `requirements` | `"icon" must be the relative path of an image`, `"requirements" must be the relative path of a requirements file`, `"icon" <path reason>`, `"requirements" <path reason>` |
| `health_path` | `"health_path" must start with "/" and hold only URL characters, for example "/healthz"` |
| `startup_timeout` | `"startup_timeout" must be a whole number of seconds`, `"startup_timeout" must be between 1 and 300 seconds` |
| `strip_prefix` | `"strip_prefix" must be true, false or null` |
| `owners` | `"owners" must be a list of Portal usernames; leave it out to let any member register this app` |
| the entry module of a `fastapi` or `flask` app is missing | `"entry" is "<entry>", but <module>.py does not exist` (a package, `<module>/__init__.py`, counts as well) |
| the entry script of a `streamlit` or `gradio` app is missing | `"entry" is "<entry>", which does not exist` |
| the directory of a `static` app is missing | `"entry" is "<entry>", which is not a directory` |
| a requirements file that the manifest names is missing | `"requirements" is "<file>", which does not exist` |
| the icon is missing | `"icon" is "<file>", which does not exist` |
| the requirements file cannot be read | `<file>: cannot be read: <reason>` |

`<path reason>` is one of the four reasons of `manifest.path_problem` (11.2).

**Warnings** (exit code `0`, or `1` with `--strict`):

| What it found | The message |
|---|---|
| an unknown key in the manifest | `unknown key "<key>" is ignored` |
| the entry module does not contain the attribute's name | `"entry" is "<entry>", but <file> does not mention "<name>"` |
| a `static` app without `index.html` in its directory | `there is no index.html in the served directory: the app's address answers 404` |
| no `requirements.txt`, and the manifest names none (`fastapi`, `flask`, `streamlit`, `gradio`) | `there is no requirements.txt: the host installs the app without any dependency` |
| a requirements line with an index option: `-i`, `--index-url`, `--extra-index-url`, `-f`, `--find-links`, `--trusted-host` | `the host uses its own package index; remove this option` |
| a requirements line the host cannot install: `-e`, `--editable`, a line that starts with `git+`, `hg+`, `svn+`, `bzr+`, `http://`, `https://`, `file:`, `./`, `../` or `/`, or a `name @ URL` reference | `the host installs wheels from its package index only: a repository, URL or local path cannot be installed` |
| the requirements do not list the framework of the type | `does not list "<package>", which a <type> app needs` |
| a root-absolute URL in an attribute: `src`, `href`, `action`, `formaction`, `poster` | `root-absolute URL src="/static/app.js": use a relative URL: src="static/app.js"`; in a `flask` app followed by `or {{ url_for(...) }}` |
| a root-absolute URL in a call: `fetch(...)`, `EventSource(...)`, `axios(...)`, `axios.get(...)` and the like | `root-absolute URL fetch("/api/items"): use a relative URL: fetch("api/items")` |
| a root-absolute URL in a `url(...)` of a style sheet, a `.css` file (here `static/style.css`) | `root-absolute URL url(/img/bg.png): use a path relative to the style sheet: url(../img/bg.png)` |
| a root-absolute URL in a `url(...)` of any other file: a `style` attribute or a `<style>` element of a page or a template | `root-absolute URL url(/img/bg.png): use a relative URL: url(img/bg.png)` |
| more than 20 such URLs in one file | `<N> more root-absolute URLs in this file` |

How it looks:

- The files the manifest names are checked when the manifest is a JSON
  object with a valid `type`. The URLs are searched in every case.
- Requirements: lines that include other files (`-r`, `-c`) are not
  followed, and the "does not list" warning is then left out. The framework
  is `fastapi`, `flask`, `streamlit` or `gradio`; `uvicorn` and `gunicorn`
  are not asked for, because the host adds them. The requirements of a
  `static` app are not looked at.
- URLs: a text search, line by line, in files that end in `.html`, `.htm`,
  `.js`, `.mjs`, `.cjs`, `.jsx`, `.ts`, `.tsx`, `.css`, `.vue`, `.svelte`,
  `.jinja`, `.jinja2`, `.j2`, `.tmpl` or `.tpl`. A URL counts when it starts
  with one `/`; `//host/...` and `https://...` do not. Folders whose name
  starts with a dot, and `node_modules`, `__pycache__`, `venv`, `env` and
  `site-packages`, are skipped; so is a file of more than 2 MiB.
- A `url(...)` in a `.css` file: a browser resolves it against the address
  of the style sheet, not of the page, so the hint is written as the sheet
  sees it. The sheet is taken to be served at the path it has in the app's
  directory. `url(/static/bg.png)` in `static/style.css` gives
  `url(bg.png)`; `url(/img/bg.png)` there gives `url(../img/bg.png)`, and in
  a `style.css` at the top of the directory `url(img/bg.png)`. A query
  string or a fragment is kept. In a `static` app the path starts from the
  served folder, the manifest's `entry`: with `"entry": "public"`,
  `url(/img/bg.png)` in `public/style.css` gives `url(img/bg.png)`. When the
  app serves the sheet at another address, work the path out from that
  address.
- A `url(...)` in any other file (a `style` attribute or a `<style>` element
  of a page or a template) is resolved against the page. The hint is the
  URL without its leading `/`: `use a relative URL: url(img/bg.png)`.
- Python files are not searched. A URL that the code puts together at run
  time is not found. A flagged line that never reaches a browser is
  harmless.

### 12.5 The command from Python: `efp_app.cli`

<!-- signature: efp_app.cli.main -->
```python
def main(argv: Optional[Sequence[str]] = None) -> int:
```

`cli.main(argv)` runs the command and returns its exit code. `argv` is the
list of arguments without the program name; `None` means `sys.argv[1:]`. A
usage error raises `SystemExit(2)`, as `argparse` does.

<!-- runnable -->
```python
from efp_app import cli

assert cli.main(["init", "static", "site", "--name", "Team links"]) == 0    # copies the starter into ./site
assert cli.main(["init", "static", "site"]) == 1                            # its files exist: nothing is written
assert cli.main(["check", "site"]) == 0                                     # no problems found
assert cli.main(["check", "site", "--strict"]) == 0
assert cli.main(["dev", "site", "--dry-run"]) == 0                          # prints the command, starts nothing
assert cli.main(["dev", "site", "--prefix", "team-links", "--dry-run"]) == 0
assert cli.main(["check", "no-such-directory"]) == 1
```

The other names of the module are the parts of the command. An app does not
need them.

| Name | What |
|---|---|
| `cli.TEMPLATE_TYPES` | `("fastapi", "flask", "streamlit", "gradio", "static")`: the starters of `init`. |
| `cli.DEFAULT_PORT` | `8000`. |
| `cli.ENV_FILE` | `".env"`. |
| `cli.HOST` | `"127.0.0.1"`. |
| `cli.build_parser()` | The `argparse` parser of the command. |
| `cli.cmd_init(args)`, `cli.cmd_dev(args)`, `cli.cmd_check(args)` | The three subcommands; each takes the parsed arguments and returns the exit code. |
| `cli.templates_dir()` | The directory of the starters inside the installed package. |
| `cli.template_files(app_type)` | The files of one starter, as relative paths. |
| `cli.read_env_file(path)` | The variables of a `.env` file, as a dict; empty for a missing file. |
| `cli.child_environment(base, env_file, port, *, system_proxy=None)` | The environment of the app's process, built as 12.3 says (steps 1 to 4). `system_proxy` is the result of `cli.system_proxy_settings()`; `efp-app dev` passes it. |
| `cli.system_proxy_settings()` | The proxy of the operating system's own settings (Windows, macOS), as `({"http": ..., "https": ...}, [hosts that bypass it])`; empty when there is none. When no proxy variable is set, `efp-app dev` writes it into the app's environment as `HTTP_PROXY`, `HTTPS_PROXY` and `NO_PROXY`: Python's HTTP libraries would use that proxy anyway, and httpx ignores the system's bypass list, so the app's own port would be sent to the proxy. |
| `cli.exempt_loopback_from_proxy(env)` | Changes `env` in place: when it holds a proxy variable (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, in either case), `127.0.0.1`, `localhost` and `::1` are added to `NO_PROXY` and `no_proxy`. The app listens on 127.0.0.1 and some frameworks ask their own port while they start (Gradio's `launch()`); through a company proxy that request fails. The host does the same for a deployed app. `cli.child_environment` calls it. |
| `cli.keep_paths_from_msys(env)` | Changes `env` in place: adds `EFP_APP_BASE_PATH`, `SCRIPT_NAME` and `GRADIO_ROOT_PATH` to `MSYS2_ENV_CONV_EXCL`, so that the `sh` of Git Bash does not turn the path prefix into a Windows path when it starts a `command` app, and sets `MSYS_NO_PATHCONV=1` and `MSYS2_ARG_CONV_EXCL=*` (unless they are set) so that it does not rewrite the command's arguments either. `cli.child_environment` calls it on Windows only. |
| `cli.build_launch(found, port, base_path="", python=None, platform=None)` | The command line for a `manifest.Manifest`, as a `cli.Launch`. `base_path` is the prefix, `python` the interpreter, `platform` the value of `sys.platform` to decide for. |
| `cli.Launch` | How an app is started: see below. |

<!-- fields: efp_app.cli.Launch -->
```python
argv: List[str]
env: Dict[str, str] = dict()
needs: Optional[str] = None
note: Optional[str] = None
```

`argv` is the command line, `env` the variables it adds, `needs` the module
that must be installed to run it, `note` a remark that `dev` prints.

The checks of `efp-app check` are functions of the internal module
`efp_app._check`. Each returns a list of `manifest.Problem`.

<!-- signature: efp_app._check.check_directory efp_app._check.check_files efp_app._check.check_requirements efp_app._check.scan_urls -->
```python
def check_directory(directory: Path) -> List[Problem]:
def check_files(directory: Path, data: Dict[str, Any]) -> List[Problem]:
def check_requirements(path: Path, shown: str, app_type: str) -> List[Problem]:
def scan_urls(directory: Path, app_type: Optional[str] = None, served_from: str = "") -> List[Problem]:
```

| Function | What it returns |
|---|---|
| `_check.check_directory(directory)` | Everything `efp-app check` reports for the directory. |
| `_check.check_files(directory, data)` | The problems with the files a decoded manifest names. |
| `_check.check_requirements(path, shown, app_type)` | The problems of one requirements file; `shown` is the name to report it under. |
| `_check.scan_urls(directory, app_type=None, served_from="")` | The root-absolute URLs below the directory. `served_from` is the folder whose files are served at the top of the app (the `entry` of a `static` app); the hint for a style sheet in it starts from there. |

<!-- runnable -->
```python
from pathlib import Path

from efp_app import _check, cli

assert cli.main(["init", "fastapi", "my-app"]) == 0
assert _check.check_directory(Path("my-app")) == []

Path("my-app/static/extra.js").write_text('fetch("/api/items");\n', encoding="utf-8")
[problem] = _check.check_directory(Path("my-app"))
assert (problem.level, problem.file, problem.line) == ("warning", "static/extra.js", 1)
assert problem.render() == (
    'warning static/extra.js:1: root-absolute URL fetch("/api/items"): use a relative URL: fetch("api/items")'
)
assert cli.main(["check", "my-app"]) == 0 and cli.main(["check", "my-app", "--strict"]) == 1
```

## 13. Behaviour worth knowing

What a reader of the signatures alone would get wrong.

### The visitor

1. **No header means different things.** Deployed, `visitor.current`
   returns `None` for a request without `X-EFP-Visitor`: an anonymous
   visitor, which happens only when the app is open to anyone. On a
   developer's machine the same call returns a stand-in: the user of
   `EFP_DEV_VISITOR`, else the owner of the app. Set
   `EFP_DEV_VISITOR=anonymous` to see the app as an anonymous visitor.
2. **A header that fails the check is `None` everywhere.** Also on a
   developer's machine: a present but unverifiable header does not fall back
   to the stand-in.
3. **A value is valid for 60 seconds.** Read the visitor while the request
   is handled. A `Visitor` kept for later stays usable as an object, but its
   `raw` value has expired by then.
4. **A stand-in forwards nothing.** Its `raw` is `None`, so `visitor=who`
   sends no header on a developer's machine. A stand-in named by
   `EFP_DEV_VISITOR` has an id made from the name
   (`visitor.dev_visitor_id`), so two names are two different visitors.
5. **`visitor=` never fails.** A value that is not a well-formed header is
   left out silently.
6. **Take identity from the header only**: never from a request body, a
   query parameter or a cookie of the app.
7. **Streamlit remembers the visitor** for the browser session; the other
   integrations read the header at each request.

### The model

8. **`llm.stream` raises at the call; `llm.astream` does not.** `stream()`
   sends the request before it returns. `astream()` sends it when the result
   is awaited, entered or first iterated. Both check their arguments at
   once.
9. **A used-up budget.** When the budget is used up before the call,
   `complete`, `stream` and `await astream(...)` raise `BudgetExceededError`
   with status 429 and `retry_after`; no text was produced. When an error
   event arrives inside a stream, the iteration raises at that point,
   `stream.text` holds what arrived, and `retry_after` is `None`. Whether
   the Portal ends a running stream when the budget runs out is not defined
   by the SDK.
10. **`usage` is `None` until the stream is done.** Read it after the loop.
11. **Close a stream you stop early.** After `close()` the stream is silent:
    no error, `done` stays `False`, and `completion()` returns the partial
    text.
12. **An empty answer is possible.** With a high `reasoning_effort` and a
    small `max_output_tokens`, `text` can be `""` with `finish_reason`
    `"length"`.
13. **An unknown `reasoning_effort` is a `ValueError`.** `except EfpError`
    does not catch it. `"LOW"` and `" low"` are unknown.
14. **The timeout is a limit on silence, not on the whole call**: 600
    seconds for the model, 120 for everything else, at most 10 to connect.
15. **No model or connector call is retried for you.** A `POST` that failed
    is not sent again. Repeat such a call yourself only when doing it twice
    is safe. Only a `GET` (`app.info`, `connectors.available`) is tried
    again, and only when the connection failed.
16. **Every call is the owner's**: the owner's provider, the app's budget,
    whoever the visitor is.

### Connectors

17. **A status other than 2xx is returned, not raised.** `connectors.get`
    gives a `ConnectorResponse` for a 404, a 401 or a 500 of the other
    system. Test `ok` or `status`.
18. **`json` is `None` for an answer that is not JSON**, an empty answer
    included. `answer.json["key"]` then raises `TypeError`.
19. **`content` of a JSON answer is not the original bytes.** The SDK
    encodes `json` again.
20. **Keyword arguments of `connectors.get` are query parameters.**
    `timeout=`, `headers=` and `client=` there do not do what they do in
    `connectors.request`.
21. **A connector call is a `POST` to the Portal** and is never repeated,
    also for a `GET` to the other system.
22. **Only `Accept`, `Content-Type` and `X-*` headers are forwarded.**
23. **A body over 5 MiB is cut.** `truncated` is then `True`.
24. **`connectors.TYPES` is a list, not a check.** A type that is not in it
    is still sent to the Portal, which decides.
25. **The connector is the owner's account.** "The current user" of the
    other system is that account for every visitor.

### Storage

26. **`storage.load` of a missing key raises `StorageNotFoundError`**,
    unless a default is given. `default=None` is a default: the call then
    returns `None`. The same holds for `storage.load_json`.
27. **`storage.load` returns `bytes`**, also for text. Decode it.
28. **A default does not cover a broken object.** `storage.load_json` of an
    object that is not JSON raises `StorageError` (`invalid_json`).
29. **`storage.list("notes")` also matches `notes2.txt`.** Use `"notes/"`
    for a folder. The listing goes through all levels.
30. **A name is an object or a folder.** `a` and `a/b` cannot both exist.
    `storage.exists("a")` is `False` when only `a/b` exists.
31. **No transactions.** Load, change, save can lose a concurrent change
    (6.6).
32. **`storage.data_dir()` does not exist on every host.** In `hidden` mode
    it raises `DataDirUnavailableError`; `save` and `load` work everywhere.
33. **Local data is under the current working directory**, in
    `./.efp-data/`. Start the app from the same directory each time, or use
    `efp-app dev`.
34. **`TypeError`, not an `EfpError`,** for `storage.save("k", {"a": 1})`.
    Use `storage.save_json`.
35. **The quota is the host's.** `QuotaExceededError` comes from a host in
    `hidden` mode, or from a full disk.

### Errors and configuration

36. **`message` is for the log, `user_message` for the visitor.** The
    detail can name internal things.
37. **`exc.status` can be `None`**: for configuration, connection and
    storage errors. `exc.status == 429` is a safe test.
38. **Only two classes have `retry_after`.** Use
    `getattr(exc, "retry_after", None)` on a general `EfpError`.
39. **An unknown code of the Portal is a plain `EfpError`** with that code.
40. **A missing token shows at the first call, not at import.** The app
    starts, pages work, and the calls that need the Portal raise
    `ConfigurationError`.
41. **The SDK does not read `.env`.** `efp-app dev` does. An app started
    with `uvicorn main:app` or `streamlit run app.py` sees only the real
    environment.
42. **`app.info()` asks the Portal every time.** Pass `cached=True` where
    the budget does not matter.
43. **`app.public_url()` can be `None`** and `app.base_path()` is `""` on a
    developer's machine. Do not build links that need them without a
    fallback.
44. **A redirect from the Portal is an error.** `UnexpectedResponseError`
    with status 302 means that `EFP_PORTAL_URL` is not the Portal's own
    address.
45. **A deployed app never uses a proxy for the Portal**; a developer's
    machine uses the proxy variables.

### The command

46. **`efp-app dev` has no path prefix unless `--prefix` is given.** A URL
    that starts with `/` works there and breaks once deployed. Run
    `efp-app check`, and `efp-app dev --prefix NAME`, before deploying.
47. **`efp-app dev` starts the app with its own Python.** Install the app's
    requirements into the environment that has the SDK.
48. **A warning of `efp-app check` does not change the exit code** without
    `--strict`.
49. **`efp-app check` reads text, not behaviour.** It does not import the
    app, does not search Python files and does not find a URL built at run
    time.

## 14. Testing an app: `efp_app.testing`

Deployed, an app gets three things from outside. This module has a stand-in
for each, so an app can be tested in its own repository with nothing
installed but the SDK and the app's own requirements: no Portal, no host,
no gateway.

| Deployed, the app gets | From | In a test |
|---|---|---|
| its environment: the variables of 2.1 | the host | `testing.deployed` sets them, and puts back what was there |
| the answers to its model calls, its connector calls and `app.info` | the Portal | `testing.FakePortal`, with the canned answers `testing.completion`, `testing.stream`, `testing.connector_answer`, `testing.error` and `testing.app_info` |
| the signed header that names the visitor | the gateway | `testing.visitor_headers` |

Everything else in a test is the real thing: `llm.complete` builds its real
request, `visitor.current` verifies a real signature, `storage` writes real
files. Only the far end is replaced, and no request leaves the process.

A running app never uses this module. `import efp_app` does not import it:
write `from efp_app import testing`. The module needs no test runner; the
examples here use pytest.

Its names: `testing.deployed`, `testing.environment`, `testing.FakePortal`,
`testing.app_info`, `testing.completion`, `testing.stream`,
`testing.connector_answer`, `testing.error`, `testing.visitor_headers` and
the constants `testing.APP_ID`, `testing.VISITOR_KEY`, `testing.TOKEN`,
`testing.PORTAL_URL` and `testing.APPS_URL` (14.7).

The examples of this section bring their own Portal: inside a
`testing.deployed` block the fake Portal of 1.3 is not asked.

### 14.1 A first test

A Flask app, `app.py`. It asks the model for a summary, keeps the summary
per visitor, and lets only the owner hide a bug:

```python
from flask import Flask, jsonify

from efp_app import app as efp, errors, llm, storage
from efp_app.integrations.flask import require_visitor

app = Flask(__name__)


@app.post("/api/summary")
def summary():
    who = require_visitor()                                     # 401 for an anonymous visitor
    try:
        answer = llm.complete("Summarise the bugs.", max_output_tokens=200, visitor=who)
    except errors.EfpError as exc:
        return jsonify(error={"code": exc.code, "message": exc.user_message}), 502
    storage.save_json("summaries/%d.json" % who.id, {"text": answer.text})
    return jsonify(text=answer.text, by=who.username)


@app.post("/api/hide")
def hide():
    who = require_visitor()
    if who.id != efp.info(cached=True).owner.id:
        return jsonify(error={"code": "owner_only", "message": "Only the owner may hide a bug."}), 403
    return jsonify(ok=True)
```

Its tests, `test_app.py` next to it. The fixture `portal` runs each test as
a deployed app would run; the fixture `client` imports the app inside it:

```python
import pytest
from flask.testing import FlaskClient

from efp_app import storage, testing, visitor


@pytest.fixture
def portal(tmp_path):
    with testing.deployed(tmp_path) as portal:                  # the host's environment and a fake Portal
        yield portal                                            # both are taken back after the test


@pytest.fixture
def client(portal) -> FlaskClient:
    from app import app                                         # imported while the environment is the host's

    return app.test_client()


def test_a_member_gets_a_summary(portal, client):
    portal.on("POST", "/llm/complete", testing.completion("Three bugs are open."))

    answer = client.post("/api/summary", headers=testing.visitor_headers("bob"))

    assert answer.status_code == 200
    assert answer.get_json() == {"text": "Three bugs are open.", "by": "bob"}
    [sent] = portal.sent_to("/llm/complete")                    # what the app asked the model
    assert sent["messages"][-1] == {"role": "user", "content": "Summarise the bugs."}
    assert sent["max_output_tokens"] == 200
    bob = visitor.dev_visitor_id("bob")                         # the id visitor_headers("bob") carries
    assert storage.load_json("summaries/%d.json" % bob) == {"text": "Three bugs are open."}


def test_a_guest_is_refused(portal, client):
    assert client.post("/api/summary").status_code == 401       # no header: nobody is signed in
    assert portal.count("POST", "/llm/complete") == 0


def test_only_the_owner_may_hide(client):
    assert client.post("/api/hide", headers=testing.visitor_headers("bob")).status_code == 403
    assert client.post("/api/hide", headers=testing.visitor_headers("alice")).get_json() == {"ok": True}


def test_a_used_up_budget_ends_as_a_sentence(portal, client):
    portal.on("POST", "/llm/complete", testing.error(429, "budget_exceeded", headers={"Retry-After": "3600"}))

    refused = client.post("/api/summary", headers=testing.visitor_headers("bob"))

    assert refused.status_code == 502
    assert refused.get_json()["error"]["code"] == "budget_exceeded"
    assert refused.get_json()["error"]["message"].startswith("This app has used up its AI budget for today.")
```

What the four tests use:

| In the test | What it stands for |
|---|---|
| `testing.deployed(tmp_path)` | the host: the app is deployed, its storage is `tmp_path`, its Portal is the `FakePortal` the block gives (14.2) |
| `portal.on("POST", "/llm/complete", ...)` | the Portal's answer to a model call (14.3, 14.4) |
| `testing.completion(...)`, `testing.error(...)` | an answer and a refusal of the Portal (14.5) |
| `testing.visitor_headers("bob")` | the gateway: the header of the signed-in member `bob`. No headers: a guest. `"alice"` is the owner of the app (14.6) |
| `portal.sent_to(...)`, `portal.count(...)` | what the app sent to the Portal (14.3) |

The same for a FastAPI app, `main.py`:

```python
from typing import Optional

from fastapi import Depends, FastAPI

from efp_app import Visitor, connectors
from efp_app.integrations.fastapi import get_visitor, require_visitor

app = FastAPI()


@app.get("/api/me")
def me(who: Optional[Visitor] = Depends(get_visitor)):
    return {"username": who.username if who else None}


@app.get("/api/bugs")
async def bugs(who: Visitor = Depends(require_visitor)):        # 401 for an anonymous visitor
    found = await connectors.aget("jira", "/rest/api/2/search", visitor=who, jql="project = SHOP")
    return {"keys": [issue["key"] for issue in found.json["issues"]] if found.ok else []}
```

Its tests, `test_main.py`. `TestClient(app, root_path=...)` gives the app
the path prefix the way the host does, which starts a `fastapi` app with
`--root-path /{slug}` (12.3); the requests themselves are written without
the prefix:

```python
import pytest
from fastapi.testclient import TestClient

from efp_app import app as efp, testing


@pytest.fixture
def portal(tmp_path):
    with testing.deployed(tmp_path) as portal:
        yield portal


@pytest.fixture
def client(portal):
    from main import app

    with TestClient(app, root_path=efp.base_path()) as client:  # "/example"
        yield client


def test_who_is_visiting(client):
    assert client.get("/api/me").json() == {"username": None}   # a guest
    assert client.get("/api/me", headers=testing.visitor_headers("bob")).json() == {"username": "bob"}


def test_the_bugs_come_from_jira(portal, client):
    issues = {"issues": [{"key": "SHOP-1"}, {"key": "SHOP-2"}]}
    portal.on("POST", "/connectors/jira/request", testing.connector_answer(issues))

    refused = client.get("/api/bugs")
    assert refused.status_code == 401 and refused.json() == {"detail": "Sign in to use this app."}
    answer = client.get("/api/bugs", headers=testing.visitor_headers("bob"))

    assert answer.json() == {"keys": ["SHOP-1", "SHOP-2"]}
    [sent] = portal.sent_to("/connectors/jira/request")         # the one call the app made
    assert (sent["method"], sent["path"], sent["query"]) == ("GET", "/rest/api/2/search", {"jql": "project = SHOP"})


def test_jira_answers_with_an_error(portal, client):
    portal.on("POST", "/connectors/jira/request", testing.connector_answer({"errorMessages": ["down"]}, status=503))

    assert client.get("/api/bugs", headers=testing.visitor_headers("bob")).json() == {"keys": []}
```

Run either with `python -m pytest` in the app's directory, with the app's
requirements, the SDK and pytest installed.

### 14.2 `testing.deployed` and `testing.environment`

<!-- signature: efp_app.testing.deployed efp_app.testing.environment -->
```python
def deployed(data_dir: Union[str, Path], *, slug: str = "example", info: Optional[Dict[str, Any]] = None) -> Iterator[FakePortal]:
def environment(data_dir: Union[str, Path], *, slug: str = "example") -> Dict[str, str]:
```

`testing.deployed` is a context manager: `with testing.deployed(...) as
portal:` runs the block as a deployed app would run and gives the
`testing.FakePortal` (14.3) that answers it.

| Parameter | Default | Meaning |
|---|---|---|
| `data_dir` | required | Where the app's storage goes, as a `str` or a `Path`. Give each test a fresh directory: pytest's `tmp_path`. It need not exist yet. |
| `slug` | `"example"` | The slug of the app. The base path is `"/" + slug`. |
| `info` | `None` | What `GET /app` answers, so what `app.info()` returns: a `dict` such as `testing.app_info(...)` gives (14.5). `None`: `testing.app_info()` with the `slug` and the address of this block. An `info` of your own is answered as it is: its `"slug"` and `"url"` do not follow `slug=`. |

| Function | Returns | Raises |
|---|---|---|
| `testing.deployed(data_dir, slug=, info=)` | a context manager; its `with` statement gives the `testing.FakePortal` | nothing of its own; an error of the block passes through, after everything is put back |
| `testing.environment(data_dir, slug=)` | a `dict`: the eight variables of the table below, with the values the block gets. It sets nothing | nothing |

What `testing.deployed` does when the block begins:

1. It builds a `testing.FakePortal` and gives it the answer of `GET /app`.
2. It removes ten variables from the environment of the process: the
   eight of the table below, `EFP_APP_STORAGE_SOCKET` and `EFP_DEV_VISITOR`.
3. It sets the eight to what `testing.environment(data_dir, slug=slug)`
   returns.
4. It calls `client.reset()` and `visitor.reset_dev_visitor()`.
5. It makes the fake Portal the Portal of the SDK's shared clients
   (`install()`, 14.3), and gives it to the block.

| Variable | Value in the block |
|---|---|
| `EFP_APP_ID` | `testing.APP_ID` |
| `EFP_APP_SLUG` | `slug` |
| `EFP_APP_TOKEN` | `testing.TOKEN` |
| `EFP_APP_BASE_PATH` | `"/" + slug`: `/example` |
| `EFP_APP_PUBLIC_URL` | `testing.APPS_URL`, the slug and a slash: `https://apps.test/example/` |
| `EFP_PORTAL_URL` | `testing.PORTAL_URL` |
| `EFP_VISITOR_KEY` | `testing.VISITOR_KEY` |
| `EFP_APP_DATA_DIR` | `str(data_dir)` |

When the block ends, also when it ends with an error (a failed `assert`
included): `client.reset()` and `visitor.reset_dev_visitor()` again, and
each of the ten variables gets the value it had before the block. One that
was not set is removed. Other variables, `PORT` among them, are never
touched.

What follows from that inside the block:

- **The app is deployed.** `app.is_deployed()` is `True`, `app.slug()` is
  the slug, `app.base_path()` is `"/" + slug` and `app.public_url()` is
  `https://apps.test/{slug}/`.
- **Storage goes to `data_dir`.** `storage.mode()` is `"sandbox"`: objects
  are files under `{data_dir}/objects/` and `storage.data_dir()` is
  `data_dir` (6.1). Another test with another directory sees none of it.
- **A request without the header is a guest.** `EFP_DEV_VISITOR` is
  removed, and a deployed app has no local stand-in anyway (3.2):
  `visitor.current` returns `None` unless the request carries a header of
  `testing.visitor_headers` (14.6).
- **Every call to the Portal goes to the fake one.** `GET /app` is
  answered; any other route answers what the test gives with `portal.on`
  (14.3, 14.4).
- **The shared clients are new ones.** A client that was made the shared
  one with `client.configure` before the block is dropped by the
  `client.reset()` of step 4 and is not put back at the end: after the
  block the next call builds a client from the environment (8.4).

`testing.deployed` changes the process, not one thread: its environment and
its shared clients. Run one block at a time in a process.

**Import the app inside the block** when it reads settings while it is
imported (a module-level `app.base_path()`, `storage.data_dir()` or
`os.environ[...]`): outside the block the environment is not the host's.
Python imports a module once per process, so such an app keeps what the
first test gave it; an app that reads its settings while it handles a
request has no such problem. The SDK itself reads the environment at each
call (1.2).

`testing.environment` is for a test that wants the variables without the
rest: `config.load(testing.environment(tmp_path))` is the `config.Settings`
of a deployed app, with nothing changed in the process.

### 14.3 `testing.FakePortal`

A Portal for tests: canned answers for the routes an app calls, and a
record of every request the app made. `testing.deployed` gives one that is
already installed. It has no rules of its own: it does not know which model
is allowed, which connector is turned on or what the budget is. It answers
what the test told it to answer.

<!-- signature: efp_app.testing.FakePortal.__init__ efp_app.testing.FakePortal.on efp_app.testing.FakePortal.sent_to efp_app.testing.FakePortal.count efp_app.testing.FakePortal.body -->
```python
def __init__(self) -> None:
def on(self, method: str, path: str, answer: Any) -> "FakePortal":
def sent_to(self, path: str) -> List[Any]:
def count(self, method: str, path: str) -> int:
def body(self, index: int = -1) -> Any:
```

<!-- signature: efp_app.testing.FakePortal.client efp_app.testing.FakePortal.async_client efp_app.testing.FakePortal.install efp_app.testing.FakePortal.handler -->
```python
def client(self, **options: Any) -> _client.Client:
def async_client(self, **options: Any) -> _client.AsyncClient:
def install(self) -> "FakePortal":
def handler(self, request: httpx.Request) -> httpx.Response:
```

| Member | Returns | What it does |
|---|---|---|
| `on(method, path, answer)` | the `FakePortal` itself, so calls can be chained | Answers `method path` with `answer` from now on. `method` may be in any letter case. `path` is the route below `/api/app-sdk/v1`, written out in full: there are no patterns. Another `on` for the same route replaces the answer. |
| `sent_to(path)` | a `list` | The decoded JSON bodies of the requests to `path`, in order: what the app sent to one route. A request without a body (a `GET`) is `None` in the list. |
| `count(method, path)` | an `int` | How many requests went to `method path`. `0` says that the app did not call. |
| `body(index=-1)` | the decoded JSON body of one request | The latest request, or the one at position `index` of `requests`. `None` when the request had no body. Raises `IndexError` when there is no such request. |
| `last` | an `httpx.Request` | A property: the latest request, with its `method`, `url`, `headers` and `content`. Raises `IndexError` before the first request. |
| `requests` | a `list` of `httpx.Request` | Every request the SDK made, in order, those without an answer included. |
| `routes` | a `dict` | `(METHOD, path)` to the answer: what `on` has filled. |
| `client(**options)` | a `client.Client` | A client of the SDK whose requests come to this Portal, for code that takes `client=`. `options` are those of `client.Client` (8.2), without `transport`. Outside `testing.deployed` pass `token=` and `portal_url=`, or the two variables must be set: else `ConfigurationError`, as 8.2 says. |
| `async_client(**options)` | a `client.AsyncClient` | The same for asynchronous code. |
| `install()` | the `FakePortal` itself | Makes the SDK's shared clients talk to this Portal: `client.configure(client=self.client(), async_client=self.async_client())`. `client.reset()` undoes it. `testing.deployed` calls it for you. |
| `handler(request)` | an `httpx.Response` | What `httpx.MockTransport` calls for each request: it records the request and finds the answer. A test does not call it; `httpx.MockTransport(portal.handler)` is the transport of a client you build yourself. |

`testing.FakePortal()` takes no arguments and starts with no answers and no
requests.

**The forms of `answer`.**

| `answer` is | The app's call gets |
|---|---|
| a `dict`, or another value that `json` can encode, a `list` excepted | status `200` with the value as its JSON body: `testing.completion(...)`, `testing.connector_answer(...)`, `testing.app_info(...)` |
| an `httpx.Response` | that response: `testing.stream(...)`, `testing.error(...)`, or one built by hand. One response can answer any number of calls |
| an exception (an instance) | The exception is raised where the SDK sends the request, as a broken connection would be. An `httpx.ConnectError` reaches the app as `PortalUnreachableError`, an `httpx.ReadTimeout` as `PortalTimeoutError` (8.3). On a `GET` route the SDK tries a failed connection three times before it gives up, with its usual pauses, and the three requests are recorded |
| a callable | It is called with the `httpx.Request`. What it returns is used as one of the three forms above |
| a `list` of these | One item per call, in order. The last item stays: it answers every later call. `on` copies the list, so the one you passed stays as it is; an empty list is refused with `ValueError` |

**A route without an answer** gets status `404` with the body
`{"error": {"code": "not_found", "message": "the test gave no answer for METHOD /path"}}`.
The SDK raises it as a plain `EfpError` with `code == "not_found"` and
`status == 404`: `not_found` is not a code of the Portal's SDK API, so no
class of `errors` stands for it (10.5). The message names the route, so the
test says which `portal.on` is missing. The request is recorded like any
other. An app that catches `EfpError` answers it as it answers any failed
call: an error answer that a test did not expect often means a route
without an answer.

### 14.4 The routes an app's calls arrive at

Every call of the SDK to the Portal is one of four routes. The second
column is how `on`, `count` and `sent_to` name them.

| The app calls | Route | Give it |
|---|---|---|
| `llm.complete`, `llm.acomplete` | `("POST", "/llm/complete")`; the body has `"stream": false` | `testing.completion(...)` |
| `llm.stream`, `llm.astream` | `("POST", "/llm/complete")`; the body has `"stream": true` | `testing.stream(...)` |
| `connectors.request`, `connectors.get`, `connectors.arequest`, `connectors.aget` | `("POST", "/connectors/<type>/request")`, for example `("POST", "/connectors/jira/request")` | `testing.connector_answer(...)` |
| `connectors.available`, `connectors.aavailable` | `("GET", "/connectors")` | `{"connectors": [{"type": "jira", "base_url": "https://jira.example.com", "write": False}]}` |
| `app.info`, `app.ainfo` | `("GET", "/app")` | answered by `testing.deployed`; change it with `info=` |

- The two kinds of model call share one route. An app that makes both in
  one test gets a callable that looks at `"stream"` in the body, or a list
  in the order of the calls (14.8).
- A connector call is a `POST` to the Portal whatever the method at the
  other system is. The type is in the route; the rest is in the body:
  `"method"`, `"path"`, `"query"`, `"headers"`, `"json"` and
  `"body_base64"`.
- The body of a model call has `"messages"`, `"system"`, `"model"`,
  `"max_output_tokens"`, `"temperature"` and `"stream"`, and
  `"reasoning_effort"` when the call names one. What the call did not give
  is `null`.
- A refusal can be given on every route: `testing.error(...)`.

### 14.5 The canned answers

<!-- signature: efp_app.testing.app_info efp_app.testing.completion efp_app.testing.stream efp_app.testing.connector_answer efp_app.testing.error -->
```python
def app_info(owner: str = "alice", **changes: Any) -> Dict[str, Any]:
def completion(text: str = "Hello.", **changes: Any) -> Dict[str, Any]:
def stream(*pieces: str, **done: Any) -> httpx.Response:
def connector_answer(body: Any = None, *, status: int = 200, text: Optional[str] = None, truncated: bool = False) -> Dict[str, Any]:
def error(status: int, code: str, message: str = "refused by the test", headers: Optional[Dict[str, str]] = None) -> httpx.Response:
```

Each builds one answer in the form the Portal sends it (CONTRACTS.md,
section 9) and does nothing else: it calls nobody and changes nothing in
the process.

| Function | Returns | The app sees |
|---|---|---|
| `testing.app_info(owner="alice", **changes)` | a `dict`: the body of `GET /app` | `app.info()`: the app `example`, named `Example`, at `https://apps.test/example/`, with the id `testing.APP_ID`. Its owner is `owner`: id `visitor.dev_visitor_id(owner)`, display name `owner.capitalize()`, group `QA` with id 3. The model is on, with `gpt-5.4` as the default and the only model. No connector. A budget of 50 credits and 2,000,000 tokens a day, nothing used. |
| `testing.completion(text="Hello.", **changes)` | a `dict`: the body of a completion | `llm.complete(...)`: the text, model `gpt-5.4`, `finish_reason` `"stop"`, 12 input tokens, 4 output tokens, 0.001 credits. |
| `testing.stream(*pieces, **done)` | an `httpx.Response` with a `text/event-stream` body | `llm.stream(...)`: one piece per argument, then the closing event: model `gpt-5.4`, `finish_reason` `"stop"`, 12 input tokens, as many output tokens as there are pieces, 0.001 credits. Without pieces the answer is empty. |
| `testing.connector_answer(body=None, *, status=200, text=None, truncated=False)` | a `dict`: the Portal's answer to a connector call | A `connectors.ConnectorResponse` (5.4) with the other system's `status`, `body` as its `json`, and `truncated`. With `text=` in place of `body`, the other system answered something that is not JSON (a page, a log): `json` is `None`, `text` and `content` hold the text, and the content type is `text/plain` instead of `application/json`. |
| `testing.error(status, code, message="refused by the test", headers=None)` | an `httpx.Response`: a refusal of the Portal | The exception of `code` (10.2) with this `status` and `message`. `headers={"Retry-After": "3600"}` gives `retry_after` to the two classes that have it (10.3). A code the SDK has no class for is a plain `EfpError`. |

- `changes` of `testing.app_info` and `testing.completion` replace keys of
  the answer, each as a whole: `testing.app_info(llm={"enabled": False})`
  is an app whose `llm` has no default model and no models,
  `testing.app_info(connectors=[{"type": "jira", "write": False}])` one
  with a connector, `testing.completion("...", finish_reason="length")` an
  answer that was cut. `done` of `testing.stream` replaces keys of the
  closing event in the same way.
- `testing.app_info` describes the app; it switches nothing on or off. A
  connector that is missing from it still answers when the test gave the
  route an answer. What the Portal would refuse, the test refuses:
  `testing.error(403, "connector_disabled")`,
  `testing.error(403, "llm_disabled")`,
  `testing.error(403, "method_not_allowed")`,
  `testing.error(429, "budget_exceeded", headers={"Retry-After": "3600"})`.
  The table of 10.2 has the status of each code.
- The `status` of `testing.connector_answer` is the other system's. The
  Portal's own answer is a success, so the SDK returns the
  `ConnectorResponse` and raises nothing, also for a `404` or a `500`
  (5.5).
- An error event inside a stream is not among the canned answers. Build
  the `httpx.Response` yourself from the events of 4.6.1.

### 14.6 The visitor: `testing.visitor_headers`

<!-- signature: efp_app.testing.visitor_headers -->
```python
def visitor_headers(
    username: str = "bob",
    *,
    id: Optional[int] = None,
    name: Optional[str] = None,
    group: Optional[str] = "QA",
    group_id: Optional[int] = 3,
    expires_in: int = 60,
) -> Dict[str, str]:
```

| Parameter | Default | Meaning |
|---|---|---|
| `username` | `"bob"` | The Portal username of the member. |
| `id` | `None` | The member's id. `None`: `visitor.dev_visitor_id(username)` (3.6). |
| `name` | `None` | The display name. `None`: `username.capitalize()`, `"Bob"`. |
| `group` | `"QA"` | The name of the member's group. `None`: a member without a group. |
| `group_id` | `3` | The id of that group, or `None`. |
| `expires_in` | `60` | Seconds until the value expires; 60 is what the gateway gives. A negative number makes a header that has expired already. |

Returns a `dict` with one item, `{"X-EFP-Visitor": "v1.<payload>.<signature>"}`:
the header the gateway adds for a signed-in visitor (section 3;
CONTRACTS.md, section 6.4). Pass it as the `headers` of a request of the
framework's test client.

- **It is signed for the app of the block.** The value names the app in
  `EFP_APP_ID` and is signed with the key in `EFP_VISITOR_KEY`, as they are
  when the function is called; without these variables it uses
  `testing.APP_ID` and `testing.VISITOR_KEY`, which are also what
  `testing.deployed` sets. `visitor.current` verifies it like a header of
  the real gateway.
- **The owner.** Without `id` the member has the id
  `visitor.dev_visitor_id(username)`. `testing.app_info(owner=...)` gives
  its owner the same id, and the owner of the default app is `alice`. So
  `testing.visitor_headers("alice")` is the owner of the app, and any other
  name is a member who is not.
- **A guest** of an app open to anyone has no header at all: pass no
  headers.
- **An expired header is a guest too**, like every header that fails the
  check (3.2): `testing.visitor_headers("bob", expires_in=-5)` makes
  `visitor.current` return `None`.
- **Make the header in the test.** It is valid for 60 seconds, so one made
  when the test module is imported can be too old when a later test uses
  it.
- **`visitor=` of a model or connector call does not take the dict.** It
  takes a `Visitor` (`visitor.current(headers)`, or what `require_visitor`
  and `get_visitor` return) or the header's value
  (`headers["X-EFP-Visitor"]`). The dict is left out without an error, like
  every other value that is not a well-formed header value (3.7).

### 14.7 Constants

| Constant | Value | Meaning |
|---|---|---|
| `testing.APP_ID` | `"0b0f3a4e-0000-4000-8000-000000000001"` | The id of the app: `EFP_APP_ID` in the block, the `"id"` of `testing.app_info`, the app a visitor header is made for. |
| `testing.VISITOR_KEY` | `"AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"` | The key visitor headers are signed with, as `EFP_VISITOR_KEY` holds it: the base64url text of the 32 bytes 0 to 31. |
| `testing.TOKEN` | `"efpa_"` and 43 times `t` | The app token, `EFP_APP_TOKEN`. No Portal knows it. |
| `testing.PORTAL_URL` | `"https://portal.test"` | `EFP_PORTAL_URL`. Nothing is ever sent there. |
| `testing.APPS_URL` | `"https://apps.test"` | The address of the apps: the app's public address is `https://apps.test/{slug}/`. |

### 14.8 Examples

These need no web framework. Each runs in an empty directory, so
`testing.deployed("data")` keeps the storage in `./data`; in a pytest test
write `testing.deployed(tmp_path)`.

A completion, and what the app sent:

<!-- runnable -->
```python
from efp_app import llm, testing, visitor

with testing.deployed("data") as portal:
    portal.on("POST", "/llm/complete", testing.completion("Three bugs are open.", model="gpt-5.4-mini"))
    bob = testing.visitor_headers("bob")

    answer = llm.complete("Summarise the bugs.", system="Be short.", max_output_tokens=200, visitor=visitor.current(bob))

    assert (answer.text, answer.model, answer.finish_reason) == ("Three bugs are open.", "gpt-5.4-mini", "stop")
    assert (answer.usage.input_tokens, answer.usage.output_tokens, answer.usage.credits) == (12, 4, 0.001)
    [sent] = portal.sent_to("/llm/complete")               # the JSON body of the one call
    assert sent["messages"][-1] == {"role": "user", "content": "Summarise the bugs."}
    assert (sent["system"], sent["max_output_tokens"], sent["stream"]) == ("Be short.", 200, False)
    assert portal.count("POST", "/llm/complete") == 1 and portal.body() == sent
    assert portal.last.headers["X-EFP-Visitor"] == bob["X-EFP-Visitor"]     # the Portal learns who caused the call
```

A streamed answer:

<!-- runnable -->
```python
from efp_app import llm, testing

with testing.deployed("data") as portal:
    portal.on("POST", "/llm/complete", testing.stream("Three ", "bugs."))
    with llm.stream("Summarise the bugs.") as reply:
        assert list(reply) == ["Three ", "bugs."]
        assert (reply.text, reply.finish_reason, reply.usage.output_tokens) == ("Three bugs.", "stop", 2)
    assert portal.body()["stream"] is True

    portal.on("POST", "/llm/complete", testing.stream("Cut", finish_reason="length"))     # replaces the answer of the route
    assert llm.stream("Again.").completion().finish_reason == "length"
```

Both kinds of model call in one test: a callable that reads the request:

<!-- runnable -->
```python
import json

from efp_app import llm, testing


def model(request):                                        # the httpx.Request of each call
    if json.loads(request.content)["stream"]:
        return testing.stream("Hel", "lo.")
    return testing.completion("Hello.")


with testing.deployed("data") as portal:
    portal.on("POST", "/llm/complete", model)
    assert llm.complete("Say hello.").text == "Hello."
    assert list(llm.stream("Say hello.")) == ["Hel", "lo."]
```

Connector answers: JSON, something that is not JSON, and the list of
connectors:

<!-- runnable -->
```python
from efp_app import connectors, testing

with testing.deployed("data") as portal:
    portal.on("POST", "/connectors/jira/request", testing.connector_answer({"issues": [{"key": "SHOP-1"}]}))
    found = connectors.get("jira", "/rest/api/2/search", jql="project = SHOP")
    assert found.ok and found.status == 200 and found.json == {"issues": [{"key": "SHOP-1"}]}
    [sent] = portal.sent_to("/connectors/jira/request")
    assert (sent["method"], sent["path"], sent["query"]) == ("GET", "/rest/api/2/search", {"jql": "project = SHOP"})

    # A log is not JSON: text= in place of the body. The other system's 404 is an answer, not an error.
    portal.on("POST", "/connectors/jenkins/request", testing.connector_answer(text="BUILD FAILED", status=404, truncated=True))
    log = connectors.get("jenkins", "/job/shop/1/consoleText")
    assert (log.status, log.ok, log.json, log.text, log.truncated) == (404, False, None, "BUILD FAILED", True)
    assert log.headers == {"content-type": "text/plain"}

    turned_on = {"connectors": [{"type": "jira", "base_url": "https://jira.example.com", "write": False}]}
    portal.on("GET", "/connectors", turned_on)
    assert [(item.type, item.write) for item in connectors.available()] == [("jira", False)]
```

A refusal of the Portal is raised as the error of its code:

<!-- runnable -->
```python
from efp_app import connectors, errors, llm, testing

with testing.deployed("data") as portal:
    portal.on("POST", "/llm/complete", testing.error(429, "budget_exceeded", headers={"Retry-After": "3600"}))
    caught = None
    try:
        llm.complete("Hello")
    except errors.BudgetExceededError as exc:
        caught = exc
    assert (caught.status, caught.retry_after, caught.message) == (429, 3600, "refused by the test")
    assert caught.user_message == "This app has used up its AI budget for today. Try again in about 60 minutes."

    portal.on("POST", "/connectors/jira/request", testing.error(403, "connector_disabled", "jira is not turned on"))
    try:
        connectors.get("jira", "/rest/api/2/issue/SHOP-1")
    except errors.ConnectorDisabledError as exc:
        caught = exc
    assert str(caught) == "connector_disabled: jira is not turned on"
```

Answers in turn, and a broken connection:

<!-- runnable -->
```python
import httpx

from efp_app import errors, llm, testing

with testing.deployed("data") as portal:
    portal.on("POST", "/llm/complete", [testing.error(502, "provider_error"), testing.completion("Second try.")])
    caught = None
    try:
        llm.complete("Hello")                              # the first answer of the list
    except errors.ProviderError as exc:
        caught = exc
    assert caught is not None
    assert llm.complete("Hello").text == "Second try."     # the second
    assert llm.complete("Hello").text == "Second try."     # the last answer stays
    assert portal.count("POST", "/llm/complete") == 3

    for broken, raised in (
        (httpx.ConnectError("no route to the Portal"), errors.PortalUnreachableError),
        (httpx.ReadTimeout("the Portal is silent"), errors.PortalTimeoutError),
    ):
        portal.on("POST", "/llm/complete", broken)         # an exception: raised where the SDK sends the request
        try:
            llm.complete("Hello")
        except errors.EfpError as exc:
            caught = exc
        assert type(caught) is raised
```

A route the test gave no answer for:

<!-- runnable -->
```python
from efp_app import connectors, errors, testing

with testing.deployed("data") as portal:
    caught = None
    try:
        connectors.get("github", "/repos/acme/app")
    except errors.EfpError as exc:
        caught = exc
    assert type(caught) is errors.EfpError and (caught.code, caught.status) == ("not_found", 404)
    assert caught.message == "the test gave no answer for POST /connectors/github/request"
    assert portal.count("POST", "/connectors/github/request") == 1     # recorded all the same
```

The visitor: the owner, a member, a guest:

<!-- runnable -->
```python
from efp_app import app, testing, visitor

with testing.deployed("data"):
    owner = app.info().owner
    assert (owner.username, owner.display_name, owner.group_name) == ("alice", "Alice", "QA")
    assert visitor.current(testing.visitor_headers("alice")).id == app.info().owner.id     # the owner
    bob = visitor.current(testing.visitor_headers("bob"))
    assert bob.id != owner.id and bob.id == visitor.dev_visitor_id("bob")                   # a member who is not
    assert (bob.username, bob.name, bob.group, bob.group_id) == ("bob", "Bob", "QA", 3)

    dana = visitor.current(testing.visitor_headers("dana", id=77, name="Dana K", group=None, group_id=None))
    assert (dana.id, dana.name, dana.group, dana.group_id) == (77, "Dana K", None, None)

    assert visitor.current({}) is None                                                      # no header: a guest
    assert visitor.current(testing.visitor_headers("bob", expires_in=-5)) is None           # expired: a guest too

# An app of another owner, with a connector:
with testing.deployed("data", info=testing.app_info(owner="dana", connectors=[{"type": "jira", "write": False}])):
    info = app.info()
    assert info.owner.username == "dana" and [grant.type for grant in info.connectors] == ["jira"]
    assert visitor.current(testing.visitor_headers("dana")).id == info.owner.id
```

What `visitor=` takes:

<!-- runnable -->
```python
from efp_app import llm, testing, visitor

with testing.deployed("data") as portal:
    portal.on("POST", "/llm/complete", testing.completion())
    headers = testing.visitor_headers("bob")
    assert list(headers) == ["X-EFP-Visitor"]

    llm.complete("Hello", visitor=visitor.current(headers))            # a Visitor
    assert portal.last.headers["X-EFP-Visitor"] == headers["X-EFP-Visitor"]
    llm.complete("Hello", visitor=headers["X-EFP-Visitor"])            # the header's value
    assert portal.last.headers["X-EFP-Visitor"] == headers["X-EFP-Visitor"]
    llm.complete("Hello", visitor=headers)                             # the dict: left out, without an error
    assert "X-EFP-Visitor" not in portal.last.headers
```

The environment of the block, and what is left of it afterwards:

<!-- runnable -->
```python
import os

from efp_app import app, config, storage, testing

assert testing.environment("data", slug="triage") == {
    "EFP_APP_ID": testing.APP_ID,
    "EFP_APP_SLUG": "triage",
    "EFP_APP_TOKEN": testing.TOKEN,
    "EFP_APP_BASE_PATH": "/triage",
    "EFP_APP_PUBLIC_URL": testing.APPS_URL + "/triage/",
    "EFP_PORTAL_URL": testing.PORTAL_URL,
    "EFP_VISITOR_KEY": testing.VISITOR_KEY,
    "EFP_APP_DATA_DIR": "data",
}
assert not config.is_deployed()                            # environment() sets nothing

os.environ["EFP_DEV_VISITOR"] = "carol"
with testing.deployed("data", slug="triage"):
    assert app.is_deployed() and (app.slug(), app.base_path()) == ("triage", "/triage")
    assert app.public_url() == "https://apps.test/triage/"
    assert (app.info().slug, app.info().url) == ("triage", "https://apps.test/triage/")
    assert "EFP_DEV_VISITOR" not in os.environ             # a request without the header is a guest
    assert storage.mode() == "sandbox"
    storage.save_json("notes/1.json", {"text": "hello"})
    assert str(storage.data_dir()) == "data" and os.path.isfile("data/objects/notes/1.json")
assert os.environ["EFP_DEV_VISITOR"] == "carol"            # what was there before is there again
assert "EFP_APP_ID" not in os.environ and not config.is_deployed()
```

The asynchronous calls arrive at the same routes:

<!-- runnable -->
```python
import asyncio

from efp_app import connectors, llm, testing


async def main():
    answer = await llm.acomplete("Summarise the bugs.")
    found = await connectors.aget("jira", "/rest/api/2/search", jql="project = SHOP")
    pieces = [piece async for piece in llm.astream("Again.")]
    return answer.text, found.json, pieces


with testing.deployed("data") as portal:
    portal.on("POST", "/llm/complete", [testing.completion("Three bugs are open."), testing.stream("Three ", "bugs.")])
    portal.on("POST", "/connectors/jira/request", testing.connector_answer({"issues": []}))
    assert asyncio.run(main()) == ("Three bugs are open.", {"issues": []}, ["Three ", "bugs."])
    assert portal.count("POST", "/llm/complete") == 2
```

A `testing.FakePortal` without `testing.deployed`, for code that takes
`client=`. No variable is set here, so the client is given the token and
the address:

<!-- environment: no-token -->
<!-- runnable -->
```python
from efp_app import app, testing

portal = testing.FakePortal().on("GET", "/app", testing.app_info(name="Mine"))
with portal.client(token=testing.TOKEN, portal_url=testing.PORTAL_URL) as mine:      # a client.Client
    assert app.info(client=mine).name == "Mine"
assert portal.last.url == testing.PORTAL_URL + "/api/app-sdk/v1/app"
assert portal.last.headers["Authorization"] == "Bearer " + testing.TOKEN
assert len(portal.requests) == 1 and list(portal.routes) == [("GET", "/app")]
assert not app.is_deployed()                               # the environment is untouched
```

`install()` by hand, on a developer's machine with a token and an address
(the environment `local` of 1.3):

<!-- runnable -->
```python
from efp_app import app, client, testing

portal = testing.FakePortal().on("GET", "/app", testing.app_info(name="Mine")).install()
assert app.info().name == "Mine"                           # the shared client asks the fake Portal
assert portal.count("GET", "/app") == 1
client.reset()                                             # undoes install()
```

### 14.9 What this does not check

A test with `efp_app.testing` shows that the app's own code does the right
thing with the answers it is given. It does not show:

- **The path prefix and the gateway.** A test client calls the app
  directly. Nothing adds or removes `/{slug}`, nothing redirects, no cookie
  is confined to the app's path, and no page is loaded by a browser. A URL
  that starts with `/` passes every test here and breaks once deployed. Use
  `efp-app check` (12.4) and `efp-app dev --prefix NAME` (12.3) for that.
- **Sign-in.** `testing.visitor_headers` is the result of a sign-in, not
  the sign-in: who may open the app is decided by the gateway and the
  Portal.
- **What the real model answers** to the app's prompt, and how long it
  takes. A canned answer is the text the test wrote.
- **How the other system behaves**: its paths, its fields, its permissions,
  its errors. `testing.connector_answer` returns what the test thinks Jira
  answers.
- **The Portal's rules.** The fake Portal refuses nothing by itself: not a
  model the app may not use, not a connector that is off, not a write
  through a read-only connector, not a used-up budget. The checks the SDK
  makes before a call still run, because the SDK is the real one.
- **The host's install of the requirements.** The tests run in your
  environment, with your packages. Whether the host can install
  `requirements.txt` from its package index is seen when the app is
  deployed; `efp-app check` warns about lines it cannot install (12.4).
