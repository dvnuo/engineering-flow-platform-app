# Environment status

"Is QA up?" One page that answers it: every test environment with its state,
how long it took to answer, how often it was up in the last 50 checks and a
small curve of its latency. A member presses "Check now" and the server asks
every environment at once.

It is also the reference for the `command` type: `server.py` is a plain
Python web server on the standard library (`http.server`), with no
framework, and it does by hand everything an app has to do. Read it when you
want to turn a server you already have into an app.

**It shows**

- `"type": "command"`: the host runs one command line and expects a web
  server on `127.0.0.1:$PORT`;
- taking the path prefix off: for a `command` app the gateway leaves
  `/{slug}` in every request, and `EFP_APP_BASE_PATH` says what it is;
- the health path, the visitor without a framework's helper
  (`efp_app.visitor.current(headers)`), the log on standard output;
- requests a server makes on behalf of its visitors, kept on a leash: a
  fixed list, a timeout, no redirect to another host, a small read limit,
  nothing passed on to the page;
- a thread pool that ends with the request, and results in storage, one
  object per environment.

**Turn on in the Portal:** nothing. The app uses no model and no connector,
so it costs nothing to run. The environments must be reachable from the app
host; ask your platform team when a check that works on your machine fails
there.

## Try it

```
pip install efp-app-sdk
efp-app dev examples/env-status
```

Open http://127.0.0.1:8000/. There is no `requirements.txt`: the app needs
only the SDK. `efp-app dev` runs the manifest's command with `sh`, so start
it from the environment in which `python` is the Python that has the SDK
(on Windows, from Git Bash or WSL). The list that ships with the example
names `example.com` hosts, which do not exist, so everything is "down" until
you put your own addresses into `targets.json`. Checking is for members:
copy `.env.example` to `.env` in this directory and add the line
`EFP_DEV_VISITOR=alice`. To see the app under a prefix, as it is deployed:
`efp-app dev examples/env-status --prefix status`.

## Make it yours

1. Copy this directory into your own repository.
2. Put your environments into `targets.json`: an `id` (lower-case letters,
   digits, `-`; it names the stored results), a `name`, a `group`, the `url`
   of a health page and the status that means "up" (`expect`, 200 unless you
   say otherwise). With `"contains"`, the first 2 KiB of the answer must
   also hold that text, for a health page that answers 200 with
   `"status":"DOWN"`.
3. `efp-app check`, push, register the directory as an app in the Portal and
   press Deploy.

To run a server of your own this way, keep the five duties at the top of
`server.py` and replace the rest; or set `"strip_prefix": true` in the
manifest and let the gateway take the prefix off for you.

## Things to notice

- **A `command` app gets the prefix in every path.** FastAPI apps never see
  `/{slug}`; this server does (`/{slug}/api/status`), reads
  `EFP_APP_BASE_PATH` and takes it off in one place (`app_path`). The health
  check arrives under the prefix as well. On your own machine the variable
  is empty, which is why a server that ignores it works locally and answers
  404 to everything once deployed.
- **The page can never name a URL.** A server that requests whatever address
  a visitor gives it is a way into the network it runs in: the metadata
  service, an admin port, a neighbour's database. Here the addresses come
  from `targets.json` in the repository and from nowhere else; `POST
  api/check` has no parameters at all. Changing the list is a commit.
- **And the list is still not trusted to behave.** A redirect is followed
  only on the same host (`SameHostRedirects`), so a health page cannot send
  the server elsewhere. The opener speaks http and https and nothing else.
  Every check has a timeout. A body is read only when a target asks for a
  text in it, and then 2 KiB of it. The page gets a state, a status and a
  time, never the answer itself.
- **Threads that end with the request.** The checks run side by side in a
  `ThreadPoolExecutor` inside a `with` block, which waits for all of them.
  Nothing runs between requests: an idle app is stopped, and a background
  checker would both keep it awake and die with it.
- **A result younger than 30 seconds is reused.** Together with one lock
  (an app is one process) this means that ten people pressing "Check now"
  cause one round of requests, and that two rounds never overwrite each
  other's results in storage.
- **No timers in the page.** The page checks when asked. An open tab that
  polled would knock on every environment all day.
- **Guests see results, members see addresses.** With the audience "anyone",
  a visitor who is not signed in gets the names and states, not the URLs of
  your test network, and cannot start a check.
