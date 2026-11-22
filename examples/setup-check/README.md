# Setup check

A page for the owner of an app: is the language model on, what is left of
today's budget, which connectors may the app use, and what does Jira (or
GitHub, Confluence, Jenkins, Nexus, Splunk) answer to a path. Deploy it first
when you start with EFP apps, and keep it around: it answers "is it my code
or my setup?" in a minute.

**It shows**

- `app.info()`: the app's owner, model and budget as the Portal reports them;
- `connectors.available()` and a plain `connectors.get()`;
- a page that only the owner may use;
- one FastAPI exception handler for every SDK error, instead of a
  `try` / `except` in each route;
- a page built on the UI kit ([docs/UI_KIT.md](../../docs/UI_KIT.md)): three
  checks at the top that say at a glance what is ready, budget bars that
  glide to their new length after a test call (the call is paid for, and
  the page shows it), and a connector's answer with its status in colour.

**Turn on in the Portal:** whatever you want to check. The page shows what is
off.

## Try it

```
pip install efp-app-sdk -r examples/setup-check/requirements.txt
efp-app dev examples/setup-check
```

Open http://127.0.0.1:8000/. The app needs the token in `.env`: everything
it shows comes from the Portal. Locally you are the owner; start it with
`EFP_DEV_VISITOR=somebody-else` to see what every other visitor gets.

## Make it yours

1. Copy this directory into your own repository.
2. `efp-app check`, push, register the directory as an app in the Portal and
   press Deploy. The audience can stay "only me".
3. For another look, set `data-theme="corporate"` (or any daisyUI theme) on
   `<html>` in `static/index.html` and link
   `/_efp/lib/daisyui@5.8.2/themes.css`
   ([docs/UI_KIT.md](../../docs/UI_KIT.md#make-it-yours)).

## Things to notice

- **Owner only, checked on the server.** `owner_only` compares the visitor
  the gateway signed in with the owner the Portal names, and every API route
  depends on it. Hiding a button in the page would not be a check. Use the
  same few lines for the "admin" part of any app.
- **Why it is owner only.** Trying a path reads your systems with your
  connector. A page that lets every visitor send any `GET` would hand them
  everything that account can read.
- **Connector paths can carry their query.** `/rest/api/2/search?jql=...`
  works as typed; in code, `connectors.get(type, path, jql=...)` builds the
  query for you.
- **A test call is a real call.** "Send a test call" asks the model for one
  word and shows the tokens and credits it cost; the budget line moves.
- **Errors in one place.** `sdk_error` answers every `EfpError` with
  `exc.user_message` and logs the detail, which you read on the app's page in
  the Portal.
