# Flask starter

A Flask app (`app.py`) with a Jinja template (`templates/index.html`). It shows
who is visiting, sends a question to the owner's language model and streams
the answer, and keeps a note with `efp_app.storage`.

## Run it locally

```
pip install efp-app-sdk -r requirements.txt
copy .env.example .env        (cp on Linux and macOS), then fill it in
efp-app dev
```

Open http://127.0.0.1:8000/. The host runs the app with gunicorn; on Windows
`efp-app dev` uses Flask's own server instead, because gunicorn needs a POSIX
system.

## Deploy it

1. `efp-app check`
2. Push the code to GitHub, register the repository as an app in the Portal
   and press Deploy.

## The path prefix

Deployed, the app lives at `https://<apps host>/<slug>/`, and Flask receives
the full path. The host sets `SCRIPT_NAME` to `/<slug>`, so routes stay as
they are (`@app.get("/")`) and `url_for()` returns addresses with the prefix.

The one mistake to avoid is a hand-written URL: `href="/static/style.css"`,
`fetch("/ask")`, `redirect("/done")`. Use `url_for('static', filename=...)`,
`url_for('ask')` and `redirect(url_for('done'))`. Scripts get their URLs from
the template (see the `data-` attributes in `templates/index.html`).
`efp-app check` finds hand-written ones in pages, scripts and style sheets.
