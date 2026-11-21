# Static starter

A plain page: `public/index.html` with its style sheet, script and a data
file. The gateway serves the `public` directory as it is (`"entry"` in
`efp-app.json`); this README and the manifest are not served.

A static app has no backend. No Python runs, so there is no SDK: no language
model, no connectors, no saved data, and no token. The gateway still checks
who may open the app. When you need a backend, start from the FastAPI or
Flask starter.

## Look at it locally

```
efp-app dev
```

Open http://127.0.0.1:8000/. (This is a local preview with Python's own file
server; on the host there is no process at all.)

## Deploy it

1. `efp-app check`
2. Push the code to GitHub, register the repository as an app in the Portal
   and press Deploy.

## The path prefix

Deployed, the app lives at `https://<apps host>/<slug>/`. Every URL in a
page, style sheet or script must be relative: `href="style.css"`,
`fetch("data.json")`, `url(img/logo.png)`. The one mistake to avoid is a
leading slash (`src="/app.js"`): it works locally and breaks once deployed.
A site generator or bundler needs its "base" or "public path" option set to a
relative value (`./`) for the same reason. `efp-app check` finds URLs that
start with a slash.
