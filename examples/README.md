# Example apps

Twenty-six complete apps. Each one is useful as it is, small enough to read
in one sitting, and built to be copied: the code shows the habits that keep
an app correct, cheap and safe, and the README of each says what it teaches.
The starters (`efp-app init`) show the shape of an app; these show what to
build with it.

New here? Read [team-links](team-links/), [team-poll](team-poll/) and
[log-explainer](log-explainer/) in that order, then go to the example closest
to what you want to build. Not sure whether your setup works? Deploy
[setup-check](setup-check/) first. Every example that draws its own page is
built on the UI kit every gateway serves ([docs/UI_KIT.md](../docs/UI_KIT.md)):
it looks finished in light and dark, on a phone and on a wide screen, and its
look changes with one attribute. The Streamlit and Gradio examples take the
same colours through their framework's theme.

Contents: [The examples](#the-examples) -
[Which example shows what](#which-example-shows-what) - [Run one](#run-one) -
[Publish one](#publish-one) - [What they have in common](#what-they-have-in-common) -
[Tests](#tests) - [Writing a new example](#writing-a-new-example)

## The examples

"Turn on in the Portal" is what the owner switches on for the app under
**Settings**; an app with "nothing" there costs nothing to run.

### Start here

| Example | What it is | Type | Uses | Turn on in the Portal |
|---|---|---|---|---|
| [team-links](team-links/) | a searchable page of the team's links | static | nothing: no backend at all | nothing |
| [team-poll](team-poll/) | a poll, one vote per member | FastAPI | visitor, storage | nothing |
| [setup-check](setup-check/) | what the app may use; try a connector path | FastAPI | app info, connectors, model | what you want to check |

### Team tools

| Example | What it is | Type | Uses | Turn on in the Portal |
|---|---|---|---|---|
| [retro-board](retro-board/) | a sprint retrospective board with anonymous cards and dot votes | FastAPI | visitor, storage, app info (the owner) | nothing |
| [planning-poker](planning-poker/) | a live estimation room: hidden picks, reveal, history | FastAPI | visitor, storage, WebSocket | nothing |
| [standup-wheel](standup-wheel/) | who speaks next: a wheel the server spins | FastAPI | visitor, storage, app info | nothing |
| [env-status](env-status/) | are the test environments up? One click checks them all | command | visitor, storage | nothing |

### Drawing and graphics

| Example | What it is | Type | Uses | Turn on in the Portal |
|---|---|---|---|---|
| [mind-map](mind-map/) | an SVG mind-map editor with private and shared maps | FastAPI | visitor, storage | nothing |
| [whiteboard](whiteboard/) | a drawing board whose sketches are kept in a team gallery | FastAPI | visitor, storage (binary objects), app info (the owner) | nothing |
| [service-map](service-map/) | an interactive dependency map of services, from a data file | static | nothing | nothing |
| [screenshot-diff](screenshot-diff/) | compare an expected and an actual screenshot pixel by pixel | static | nothing | nothing |

### Developer tools

| Example | What it is | Type | Uses | Turn on in the Portal |
|---|---|---|---|---|
| [dev-toolbox](dev-toolbox/) | JSON, Base64, URLs, JWT, timestamps, UUIDs, hashes, regular expressions, text diff, cron | static | nothing: everything runs in the browser | nothing |

### Dashboards

| Example | What it is | Type | Uses | Turn on in the Portal |
|---|---|---|---|---|
| [build-health](build-health/) | the last 30 builds of a list of Jenkins jobs: success rate, duration, flaky jobs | FastAPI | Jenkins connector, storage | Jenkins |
| [flaky-tests](flaky-tests/) | which tests flip between passing and failing, as a heatmap over builds | FastAPI | Jenkins connector, storage | Jenkins |
| [repo-pulse](repo-pulse/) | a repository's commit activity, contributors and the age of its open pull requests | Flask | GitHub connector, storage | GitHub |
| [sprint-burndown](sprint-burndown/) | a sprint's burndown, status and load per assignee | Streamlit | Jira connector | Jira |
| [jira-digest](jira-digest/) | recent issues of a project and a digest | Flask | Jira connector, model | Jira, model |

### Tools that use the model

| Example | What it is | Type | Uses | Turn on in the Portal |
|---|---|---|---|---|
| [log-explainer](log-explainer/) | paste a failing log, get it explained | FastAPI | model (streamed) | model |
| [release-notes](release-notes/) | release notes drafted from commits | FastAPI | GitHub connector, model, storage | GitHub, model |
| [confluence-ask](confluence-ask/) | answers from wiki pages, with sources | Streamlit | Confluence connector, model (streamed) | Confluence, model |
| [test-case-writer](test-case-writer/) | from a Jira story (or a pasted one) to Gherkin test scenarios | Gradio | Jira connector, model (streamed) | model; Jira to read issues by key |
| [incident-timeline](incident-timeline/) | from pasted incident notes to a timeline you can correct and export | FastAPI | model (JSON output), storage | model |
| [glossary-translator](glossary-translator/) | translation between English and Chinese that keeps the team's terms | FastAPI | model (streamed), storage | model |
| [csv-explorer](csv-explorer/) | look into a CSV file: summary, charts, questions about the data | Streamlit | model (optional) | model, if questions are wanted |

### Games

| Example | What it is | Type | Uses | Turn on in the Portal |
|---|---|---|---|---|
| [bug-squash](bug-squash/) | a 45-second canvas game with a team leaderboard | FastAPI | visitor, storage | nothing |
| [type-sprint](type-sprint/) | a typing sprint on command lines and code, with your progress over time | FastAPI | visitor, storage | nothing |

## Which example shows what

| You need to | Read |
|---|---|
| publish pages with no backend | [team-links](team-links/), [dev-toolbox](dev-toolbox/), [service-map](service-map/), [screenshot-diff](screenshot-diff/) |
| know who is visiting, and refuse a guest | [team-poll](team-poll/) (`require_visitor`, `get_visitor`) |
| let only the owner do something | [setup-check](setup-check/), [standup-wheel](standup-wheel/), [glossary-translator](glossary-translator/), [retro-board](retro-board/) |
| let many visitors write at the same moment | [team-poll](team-poll/) (one object per writer), [retro-board](retro-board/) (one object per item), [mind-map](mind-map/) and [incident-timeline](incident-timeline/) (a version number that refuses a stale save), [standup-wheel](standup-wheel/) (create only if absent, under one lock) |
| build a page that looks finished: themes, motion, toasts, busy buttons | [planning-poker](planning-poker/), [retro-board](retro-board/), [build-health](build-health/) (the UI kit, [docs/UI_KIT.md](../docs/UI_KIT.md)) |
| animate what changed between two states the server sent | [planning-poker](planning-poker/) (cards that land, turn over, confetti on consensus), [retro-board](retro-board/) (cards kept by id glide to a new order) |
| draw charts with a library | [build-health](build-health/) (ECharts through `kit.chart`, in the theme's colours) |
| keep what the server does not want to reveal | [retro-board](retro-board/) (authors never leave the server), [standup-wheel](standup-wheel/) (the server picks, not the page) |
| store files or images | [whiteboard](whiteboard/) (binary objects, checked uploads, a gallery) |
| handle an upload | [whiteboard](whiteboard/), [csv-explorer](csv-explorer/) |
| update every visitor's page live | [planning-poker](planning-poker/) (WebSocket, state kept in storage) |
| call the model once and show the answer as it is written | [log-explainer](log-explainer/), [glossary-translator](glossary-translator/), [test-case-writer](test-case-writer/) |
| get structured data out of the model | [incident-timeline](incident-timeline/) (ask for JSON, validate every field) |
| keep an answer you paid for | [release-notes](release-notes/) |
| answer only from sources you can show | [confluence-ask](confluence-ask/) |
| keep secrets out of what goes to the model | [log-explainer](log-explainer/), [incident-timeline](incident-timeline/) |
| read Jira | [jira-digest](jira-digest/), [sprint-burndown](sprint-burndown/), [test-case-writer](test-case-writer/) |
| read GitHub | [release-notes](release-notes/), [repo-pulse](repo-pulse/) |
| read Jenkins | [build-health](build-health/), [flaky-tests](flaky-tests/) |
| read Confluence | [confluence-ask](confluence-ask/) |
| try any connector path by hand | [setup-check](setup-check/) |
| turn what a visitor types into a connector path or query safely | [jira-digest](jira-digest/), [release-notes](release-notes/), [repo-pulse](repo-pulse/) |
| handle the other system's "no" (404, 401, 202) | [jira-digest](jira-digest/), [repo-pulse](repo-pulse/), [build-health](build-health/) |
| spare the other system: keep what you fetched | [build-health](build-health/) (for five minutes), [repo-pulse](repo-pulse/) (for an hour), [flaky-tests](flaky-tests/) (for good: a finished build never changes) |
| draw charts without a library | [build-health](build-health/), [flaky-tests](flaky-tests/), [repo-pulse](repo-pulse/), [type-sprint](type-sprint/) (SVG) |
| draw on a canvas | [whiteboard](whiteboard/), [bug-squash](bug-squash/), [screenshot-diff](screenshot-diff/), [service-map](service-map/) |
| write a Flask app (`url_for` everywhere) | [jira-digest](jira-digest/), [repo-pulse](repo-pulse/) |
| write a Streamlit app (reruns, `st.cache_data`) | [confluence-ask](confluence-ask/), [sprint-burndown](sprint-burndown/), [csv-explorer](csv-explorer/) |
| write a Gradio app | [test-case-writer](test-case-writer/) |
| run any other Python server as an app | [env-status](env-status/) (type `command`: the port, the prefix, the visitor, by hand) |
| make requests from the server without letting a visitor choose the address | [env-status](env-status/) |
| check a score or a result the browser reports | [bug-squash](bug-squash/), [type-sprint](type-sprint/) |
| wrap a script or a data task in a page | [csv-explorer](csv-explorer/) |
| put text in another script into ASCII files | [glossary-translator](glossary-translator/) |

## Run one

From a checkout of this repository, in a virtual environment:

```
pip install -e sdk                                  (or: pip install efp-app-sdk)
pip install -r examples/team-poll/requirements.txt
efp-app dev examples/team-poll
```

Installing from the checkout leaves build files in it
(`sdk/efp_app_sdk.egg-info/`, `sdk/build/`); git ignores both.

Open http://127.0.0.1:8000/. The static examples and the ones that use only
the visitor and storage need no Portal: name yourself with
`EFP_DEV_VISITOR=alice` in the example's `.env` (each README says how). The
others call the model or a connector and need the app's token: register the
example as an app in the Portal first (next section), then copy
`.env.example` to `.env` in the example's directory and fill in the token and
the Portal's address. Calls made locally are real: they use your model
provider, count against the app's budget and reach your real Jira.

To see an example the way it is served once deployed, under a path prefix:

```
efp-app dev examples/team-poll --prefix team-poll
```

## Publish one

1. Copy the example's directory into a GitHub repository of your own that
   EFP can read.
2. `efp-app check <directory>`
3. In the Portal: Apps, New app; give the repository, the branch, the
   directory and a name. On the app's page turn on what the tables above
   list, then press Deploy.

## What they have in common

- **Relative URLs in every page** (`fetch("api/poll")`,
  `src="static/app.js"`), and `url_for()` in Flask: the app is served under
  `/<slug>/` once deployed. `efp-app check` finds the slips.
- **Identity from the gateway, on the server.** No example has a login, and
  none takes a user's name from what the page sends.
- **Errors a visitor can read.** Every SDK error becomes
  `{"error": {"code", "message"}}` with `exc.user_message`; the detail goes
  to the log on the app's page. A used-up budget is a sentence, not a crash.
- **`visitor=` on every model and connector call**, so the Portal's usage
  page shows who caused it.
- **A connector is the owner's account.** No example asks another system for
  "my" anything; a person is named explicitly.
- **Text from elsewhere is data.** Logs, issue summaries, commit subjects,
  wiki pages and uploads reach the page through `textContent` and the model
  with a prompt that says they are not instructions. Nothing a model returns
  is executed.
- **Nothing important in memory or on the local disk.** An idle app is
  stopped; what must survive is in `efp_app.storage`, shaped so that two
  visitors cannot lose each other's writes.
- **No timers that call the server.** A tab that polls keeps the app from
  ever being idle. Pages load on demand; the one live example says what its
  open WebSocket costs.
- **Nothing from another host.** No CDN, no web font from the internet.
  Every file a page needs is in the example's directory or among the shared
  libraries the gateway itself serves at `/_efp/lib/`
  ([docs/UI_KIT.md](../docs/UI_KIT.md)): every example that draws its own
  page is built on them.
- **Only the framework in `requirements.txt`.** The host adds the SDK.

## Tests

Every example is tested against a stand-in Portal: `sdk/tests/test_examples.py`
holds the rules all of them follow and the tests of the first seven;
`sdk/tests/test_example_<name>.py` holds each later example's own.

```
cd sdk
python -m pytest tests/test_examples.py tests/test_example_retro_board.py
```

The FastAPI examples run for real, the Flask ones when Flask is installed,
the Streamlit and Gradio ones against small stand-ins for the libraries, and
logic written in JavaScript runs under node when node is installed. Read the
tests when you want to see what a connector or the model answers on the
wire.

To test an app of your own without a Portal, in its own repository, use
`efp_app.testing`: it is part of the SDK and holds the same stand-in Portal
these tests run on, the environment of a deployed app and the signed header
of a visitor ([SDK_REFERENCE.md](../docs/SDK_REFERENCE.md), section 14).
`conftest.py` and `example_support.py` are this repository's own helpers
around it and are not installed with the SDK.

## Writing a new example

An example is read by people and by AI agents who will copy it, so it has to
be right in ways a working demo need not be. `test_examples.py` checks most
of what follows.

1. **Files.** `efp-app.json` (a `description` that starts with `Example: `,
   no `owners`), `README.md`, the app; and for every type but `static`:
   `.env.example`, `.gitignore`, and a `requirements.txt` that lists only the
   framework, unpinned (`fastapi` and `uvicorn`; `flask` and `gunicorn`;
   `streamlit`; `gradio`; nothing for a `command` app on the standard
   library).
2. **README.** A title and a paragraph on what the app is for; `**It
   shows**`; `**Turn on in the Portal:**`; `## Try it` with
   `efp-app dev examples/<name>`; `## Make it yours`; `## Things to notice`:
   the lessons, each with its reason.
3. **ASCII only**, in every file. Text in another script is written as
   escapes.
4. **Self-contained.** Nothing is loaded from another host; no build step.
   The shared libraries at `/_efp/lib/` are the gateway's own and may be
   used.
5. **Relative URLs**, except `/_efp/lib/...`; `efp-app check` reports
   nothing.
6. **JavaScript** builds nodes with `createElement` and `textContent`: never
   `innerHTML`, `insertAdjacentHTML`, `document.write`, `localStorage`,
   `sessionStorage` or `setInterval`. Logic that can be separated from the
   page is in modules that node can run in a test.
7. **The habits of [What they have in common](#what-they-have-in-common)**,
   all of them.
8. **Validation.** Everything a visitor sends is checked before it becomes a
   key, a path, a query, a file or a prompt.
9. **A finished page**: built on the UI kit and keeping its
   [rules for a good page](../docs/UI_KIT.md#rules-for-a-good-page): usable
   at a narrow width and with the keyboard, readable in light and dark,
   quiet for a visitor who prefers reduced motion, every action showing that
   it is working, with an empty state and errors as sentences.
10. **Games are original.** A genre's mechanics, never a recreation of an
    existing game's name, characters, art, levels or look.
11. **Tests** in `sdk/tests/test_example_<name>.py`: what each route answers,
    what lands in storage under which key, exactly what is sent to the
    Portal, the page under a prefix, and every refusal.
12. **A row** in the tables above, and a line in "Which example shows what"
    for each thing the example is the best place to learn.
