# Example apps

Seven small apps, each one useful as it is and each one built around a single
idea. The starters (`efp-app init`) show the shape of an app; these show what
to build with it and the habits that keep an app correct, cheap and safe.

Read them in this order when you are new: every example adds one thing to the
ones before it.

| Example | What it is | Type | Uses | Turn on in the Portal |
|---|---|---|---|---|
| [team-links](team-links/) | a searchable page of the team's links | static | nothing: no backend at all | nothing |
| [team-poll](team-poll/) | a poll, one vote per member | FastAPI | visitor, storage | nothing |
| [log-explainer](log-explainer/) | paste a failing log, get it explained | FastAPI | model (streamed) | model |
| [jira-digest](jira-digest/) | recent issues of a project and a digest | Flask | Jira connector, model | Jira, model |
| [release-notes](release-notes/) | release notes drafted from commits | FastAPI | GitHub connector, model, storage | GitHub, model |
| [confluence-ask](confluence-ask/) | answers from wiki pages, with sources | Streamlit | Confluence connector, model (streamed) | Confluence, model |
| [setup-check](setup-check/) | what the app may use; try a connector path | FastAPI | app info, connectors, model | what you want to check |

Not sure whether your setup works? Deploy [setup-check](setup-check/) first.

## What each one teaches

- **team-links**: relative URLs, and that a page for your group needs no
  backend.
- **team-poll**: identity comes from the gateway (`require_visitor`); with no
  transactions in storage, give every visitor an object of their own.
- **log-explainer**: you pay for every visitor's model call, so cut the input
  and cap the output; mask secrets before text leaves the app; a refusal
  arrives before the stream starts.
- **jira-digest**: a connector is the owner's account, not the visitor's
  (never `currentUser()`); the other system's "no" is a status, not an
  exception; check what a visitor types before it becomes a query.
- **release-notes**: keep what you paid for (drafts are saved in storage and
  reused); typed text that becomes a path.
- **confluence-ask**: an answer written only from sources that are shown;
  skip the model call when nothing was found.
- **setup-check**: a page for the owner alone, checked on the server; one
  handler for every SDK error.

## Run one

From a checkout of this repository, in a virtual environment:

```
pip install -e sdk                                  (or: pip install efp-app-sdk)
pip install -r examples/team-poll/requirements.txt
efp-app dev examples/team-poll
```

Open http://127.0.0.1:8000/. `team-links` and `team-poll` need no Portal (to
vote in the poll, name yourself with `EFP_DEV_VISITOR`; its README says how).
The others call the model or a connector and need the app's token: register
the example as an app in the Portal first (next section), then copy
`.env.example` to `.env` in the example's directory and fill in the token and
the Portal's address. Calls made locally are real: they use your model
provider, count against the app's budget and reach your real Jira.

## Publish one

1. Copy the example's directory into a GitHub repository of your own that
   EFP can read.
2. `efp-app check <directory>`
3. In the Portal: Apps, New app; give the repository, the branch, the
   directory and a name. On the app's page turn on what the table above
   lists, then press Deploy.

## What they have in common

- **Relative URLs in every page** (`fetch("api/poll")`,
  `src="static/app.js"`), and `url_for()` in Flask: the app is served under
  `/<slug>/` once deployed. `efp-app check` finds the slips.
- **Errors a visitor can read.** Every SDK error becomes
  `{"error": {"code", "message"}}` with `exc.user_message`; the detail goes
  to the log on the app's page. A used-up budget is a sentence, not a crash.
- **`visitor=` on every model and connector call**, so the Portal's usage
  page shows who caused it.
- **Text from elsewhere is data.** Logs, issue summaries, commit subjects and
  wiki pages reach the page through `textContent` and the model with a prompt
  that says they are not instructions.
- **Nothing important in memory or on the local disk.** An idle app is
  stopped; what must survive is in `efp_app.storage`.
- **No timers in the page.** A tab that polls keeps the app from ever being
  idle.

## Tests

`sdk/tests/test_examples.py` runs every example against a stand-in Portal:

```
cd sdk
python -m pytest tests/test_examples.py
```

The FastAPI examples run for real, the Flask one when Flask is installed, and
the Streamlit one against a small stand-in for the library. Read the tests
when you want to see what a connector or the model answers on the wire.
