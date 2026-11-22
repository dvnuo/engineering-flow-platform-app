# Dev toolbox

Ten small tools an engineer reaches for every week, on one page: JSON,
Base64, URL encoding, JWT, timestamps, UUIDs, hashes, regular expressions,
text diff and cron. Everything runs in the visitor's browser. Whatever is
pasted (a token, a config file, a customer's log line) is not sent, stored
or logged anywhere, because the app has no backend to send it to.

**It shows**

- a `"type": "static"` app that is a real tool and not only a page of text;
- logic kept in small ES modules that never touch the page (`public/lib/`),
  so node can test them, with the page as a thin layer on top
  (`public/tools/`);
- a tool chosen by the address (`#jwt`): it can be linked, the back button
  works, and nothing has to be remembered in the browser;
- how to run a visitor's regular expression without letting it freeze the
  tab;
- a page built on the UI kit ([docs/UI_KIT.md](../../docs/UI_KIT.md)) with a
  real tool shell: a list with a filter, a command palette (`Ctrl+K`), keys
  to jump between tools, code in JetBrains Mono, answers in alerts where
  they belong. Its motion says what happened: the highlight in the list
  glides to the tool that opened and the tool rises into place, a copy
  button draws a tick, a new result flashes once, the next runs of a cron
  schedule come in one after the other.

**Turn on in the Portal:** nothing. No model, no connector, no storage. The
app costs nothing to run and never starts a process.

## Try it

```
efp-app dev examples/dev-toolbox
```

Open http://127.0.0.1:8000/. (A local preview with Python's own file server;
on the host no process runs at all.) Paste `{"a": [1, 2,]}` into the JSON
tool, a token into the JWT tool, `(a+)+$` and a row of thirty `a` followed
by `!` into the regex tester: the page says what is wrong, or stops the
pattern after a second, instead of hanging. Press `Ctrl+K` and type `cron`,
or `Alt+1` to `Alt+0` to open a tool by its place in the list.

## Make it yours

1. Copy this directory into your own repository.
2. Add the tool your team keeps a script for. Its logic goes into
   `public/lib/<name>.js` (no DOM, so it can be tested), its form into
   `public/tools/<name>.js` (an object with `id`, `title`, `blurb`, `icon`
   (a name from https://lucide.dev/icons), `keywords` for the filter and the
   palette, and `mount(root)`, built with the helpers of `public/ui.js`), and
   one line into `TOOLS` in `public/app.js`. Remove the tools you do not
   need the same way.
3. Change the look if you like: link `/_efp/lib/daisyui@5.8.2/themes.css`
   and set `data-theme="dracula"` (or any daisyUI theme) on `<html>`
   ([docs/UI_KIT.md](../../docs/UI_KIT.md#make-it-yours)).
4. `efp-app check`, push, register the directory as an app in the Portal and
   press Deploy.

## Things to notice

- **No backend is the feature.** A tool people paste secrets into should not
  be able to leak them. Here no request leaves the page after it has
  loaded: there is no `fetch`, no form, no analytics. The app also costs
  nothing, has nothing to patch and cannot be down while the gateway is up.
  The gateway still decides who may open it.
- **What a static app cannot do.** It does not know who is visiting (the
  signed `X-EFP-Visitor` header reaches a backend, not a file), it cannot
  save anything, and it has no model and no connector. It also cannot keep
  a secret: every visitor can read every file under `public/`, comments
  included, so no token, password or internal address belongs there. The
  day a tool needs one of these, it needs a backend: see `team-poll`.
- **Nothing you type is kept in the browser either.** All apps share one
  origin, so `localStorage` of one app can be read by every other app. The
  chosen tool is in the address instead, and what was typed is gone when
  the tab closes. (The kit's theme button remembers light or dark there, a
  choice shared by every app on purpose; it holds nothing you typed.)
- **The tools do not import the kit.** `app.js` hands it to `ui.js` with
  `provide(kit)`. The kit lives at `/_efp/lib/` on the gateway, which node
  cannot reach; this way node still loads every tool in the tests, and a
  tool still gets icons, motion and toasts in the browser.
- **Relative URLs, also between modules.** `import ... from "./lib/json.js"`,
  `new URL("../regex-worker.js", import.meta.url)`: an import that starts
  with `/` breaks under `/<slug>/` exactly as a `src="/..."` does. The kit
  at `/_efp/lib/` is the one exception, because every gateway serves it.
- **A regular expression can run for hours.** `(a+)+$` doubles its work
  with every letter: thirty take many seconds, forty take hours, and
  JavaScript cannot interrupt it.
  So the page never runs a pattern itself: `regex-worker.js` does, in a Web
  Worker, on at most 100,000 characters, and `createRunner` in
  `lib/regex.js` ends the worker when the answer has not come within a
  second. The same goes for any input that can be expensive: bound it, and
  be able to stop.
- **Formatting must not change the data.** `JSON.stringify(JSON.parse(text))`
  turns `12345678901234567890` into `12345678901234567000` and drops a
  repeated key. `lib/json.js` reads the tokens and only changes the white
  space between them. It also knows the line and column of a mistake, which
  browsers report differently or not at all.
- **Text goes into the page as text.** Everything pasted is shown with
  `textContent` or as the value of a field, so a pasted `<img onerror=...>`
  appears as those characters and nothing runs.
- **Decoding is not verifying.** The JWT tool shows what a token says and
  says, in capitals, that the signature is NOT checked. Checking needs the
  issuer's key, and a key in a static file is a key every visitor has.
- **Some browser features need HTTPS.** `crypto.subtle` (the hashes) and the
  clipboard exist only on HTTPS pages and on localhost. The deployed app is
  HTTPS; the page says so in a sentence when a feature is missing.
