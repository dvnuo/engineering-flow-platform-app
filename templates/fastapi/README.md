# FastAPI starter

A FastAPI backend (`main.py`) behind a small static page (`static/`). It shows
who is visiting, sends a question to the owner's language model and streams
the answer, and keeps a note with `efp_app.storage`.

## Run it locally

```
pip install efp-app-sdk -r requirements.txt
copy .env.example .env        (cp on Linux and macOS), then fill it in
efp-app dev
```

Open http://127.0.0.1:8000/. Without the token the page still loads; the
model call answers with an error message.

## Deploy it

1. Put your Portal username in `"owners"` of `efp-app.json`.
2. `efp-app check`
3. Push the code to GitHub, register the repository as an app in the Portal
   and press Deploy.

## The path prefix

Deployed, the app lives at `https://<apps host>/<slug>/`. The gateway removes
`/<slug>` before a request reaches FastAPI, so routes stay as they are
(`@app.get("/api/me")`). The browser does not know about that, so pages must
use relative URLs: `fetch("api/me")`, `src="static/app.js"`. The one mistake
to avoid is a leading slash (`fetch("/api/me")`): it works locally and breaks
once deployed. `efp-app check` finds those.

If the backend itself has to build a link, use `request.url_for(...)` or
`efp_app.app.base_path()`.
