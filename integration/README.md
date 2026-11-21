# Container smoke test

Starts the real image next to a stub Portal and checks it from outside, once
per isolation variant: it deploys the starter templates, an example app
(`examples/team-poll`) and a probe app, uses them through the gateway, checks
that one app cannot reach another, and that redeploy, rollback and a new
container on the same volume work.

CI runs it (`.github/workflows/ci.yml`, the jobs `image-tests` and `smoke`).
It is the only place where the Linux parts of the host run for real; the unit
tests are in `host/tests` and `sdk/tests`.

## Run it

On a Linux machine with Docker, from the repository root:

```bash
docker build -t efp-app-host:ci .
python3 -m pip install pyyaml
sh integration/ci.sh all hidden
sh integration/ci.sh clean
```

`hidden` is one of the variants: `hidden`, `sandbox`, `hidden-k8s`,
`sandbox-k8s`, `sandbox-landlock`, `sandbox-userns`. The two ending in `-k8s`
start the container with exactly the capabilities of the Deployment files in
`k8s/`. A sandbox variant needs a kernel and a security policy that allow its
backend; `sh integration/ci.sh available <variant>` says whether this machine
can run it. `sh integration/ci.sh` without arguments lists every command.

## Files

| File | What it is |
|---|---|
| `ci.sh` | starts the stub Portal and the host with the privileges of a variant, runs the checks, prints what is worth reading after a failure |
| `smoke.py` | the checks; standard library only. Every check prints one line |
| `stub_portal.py` | a stand-in for the Portal that answers the host's calls with canned data and remembers what the host sent |
| `seed_apps.json` | the app the stub Portal already knows when the host starts |
| `probe_app/` | an app that reads whatever path it is told to; deployed twice, one copy is asked to reach the other and must fail |
| `check_bwrap_root.sh` | run inside a container: can bubblewrap, started by root, drop to another user on this machine? |

The helpers (the stub Portal, the probe app, the hand-written HTTP, multipart
and WebSocket code of `smoke.py`) are unit-tested without Docker in
`host/tests/test_integration_tools.py`.
