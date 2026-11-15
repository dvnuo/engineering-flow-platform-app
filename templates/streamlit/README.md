# Streamlit starter

One script (`app.py`): a greeting for the visitor, a chat with the owner's
language model and a note kept with `efp_app.storage`.

## Run it locally

```
pip install efp-app-sdk -r requirements.txt
copy .env.example .env        (cp on Linux and macOS), then fill it in
efp-app dev
```

Open http://127.0.0.1:8000/. `efp-app dev` starts Streamlit with the same
options as the host and reads `.env`. Plain `streamlit run app.py` works too
when `EFP_APP_TOKEN` and `EFP_PORTAL_URL` are set in the environment.

## Deploy it

1. Put your Portal username in `"owners"` of `efp-app.json`.
2. `efp-app check`
3. Push the code to GitHub, register the repository as an app in the Portal
   and press Deploy.

## The path prefix

Deployed, the app lives at `https://<apps host>/<slug>/`. The host passes the
prefix to Streamlit (`--server.baseUrlPath`), and Streamlit serves its page,
its files and its WebSocket under it. Nothing to do in the script.

The one mistake to avoid is a hand-written absolute link:
`st.markdown("[Home](/)")` or `<a href="/reports">` in HTML leaves the app.
Write links relative to the page, or build them from
`efp_app.app.base_path()`.

## Good to know

- The visitor is read once per browser session and then remembered in
  `st.session_state` (see `efp_app.integrations.streamlit`).
- The app stops when nobody has used it for a while and starts again on the
  next visit: keep nothing important in memory or in `st.session_state`, use
  `efp_app.storage`.
