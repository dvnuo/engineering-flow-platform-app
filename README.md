# engineering-flow-platform-app

EFP Apps: members of the Engineering Flow Platform publish small web apps of
their own. This repository holds everything outside the Portal.

| Directory | What it is |
|---|---|
| [`host/`](host/) | the app host: one container that runs every app behind a gateway that signs visitors in, and its unit tests |
| [`Dockerfile`](Dockerfile) | the image of the app host; build it from this directory: `docker build -t efp-app-host .` |
| [`sdk/`](sdk/) | `efp-app-sdk`, the Python SDK an app uses (visitor, language model, image analysis, connectors, storage), the `efp-app` command, and its tests |
| [`templates/`](templates/) | the starters `efp-app init` copies: FastAPI, Flask, Streamlit, Gradio, static |
| [`examples/`](examples/) | twenty-six complete apps to copy and learn from: tools, dashboards, drawing boards, games, model-backed helpers |
| [`k8s/`](k8s/) | Kubernetes manifests for the app host, the probe that says which isolation a cluster allows, the AppArmor profile |
| [`terraform/`](terraform/) | the EFS access point of the app host's volume |
| [`integration/`](integration/) | the container smoke test CI runs: the real image next to a stub Portal |
| [`scripts/`](scripts/) | `check_manifests.py`, which checks the files in `k8s/` |
| [`docs/`](docs/) | [developing apps](docs/DEVELOPING_APPS.md), [converting an existing project](docs/CONVERTING_A_PROJECT.md), [the SDK reference](docs/SDK_REFERENCE.md), [operating the host](docs/OPERATIONS.md), [the wire contracts](docs/CONTRACTS.md); [docs/README.md](docs/README.md) says which to read when |
| [`AGENTS.md`](AGENTS.md) | the brief for an AI agent that writes an app, converts a project into one, or changes this repository |

The layout follows the Portal repository's (`Dockerfile`, `k8s/`,
`terraform/`, `integration/`, `scripts/`, `docs/`). This repository holds two
Python packages instead of one, so each keeps its tests next to it
(`host/tests`, `sdk/tests`).

Writing an app? Start with [docs/DEVELOPING_APPS.md](docs/DEVELOPING_APPS.md)
and the [examples](examples/README.md). Turning something you already have
into an app? [docs/CONVERTING_A_PROJECT.md](docs/CONVERTING_A_PROJECT.md).
