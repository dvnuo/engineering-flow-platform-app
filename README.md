# engineering-flow-platform-app

EFP Apps: members of the Engineering Flow Platform publish small web apps of
their own. This repository holds everything outside the Portal.

| Directory | What it is |
|---|---|
| [`host/`](host/) | the app host: one container that runs every app behind a gateway that signs visitors in |
| [`sdk/`](sdk/) | `efp-app-sdk`, the Python SDK an app uses (visitor, language model, connectors, storage), and the `efp-app` command |
| [`templates/`](templates/) | the starters `efp-app init` copies: FastAPI, Flask, Streamlit, Gradio, static |
| [`examples/`](examples/) | seven complete small apps to copy and learn from |
| [`deploy/`](deploy/) | Kubernetes manifests, Terraform, the storage probe, the AppArmor profile |
| [`docs/`](docs/) | [developing apps](docs/DEVELOPING_APPS.md), [operating the host](docs/OPERATIONS.md), [the wire contracts](docs/CONTRACTS.md) |

Writing an app? Start with [docs/DEVELOPING_APPS.md](docs/DEVELOPING_APPS.md)
and the [examples](examples/README.md).
