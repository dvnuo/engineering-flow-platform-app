# Team links

A searchable page of the links a team keeps asking for: dashboards, boards,
runbooks. The links live in `public/links.json`; the page filters them as you
type.

**It shows**

- the simplest kind of app: `"type": "static"`. There is no backend, no
  Python, no token and nothing to pay for. The gateway serves the `public`
  directory and still decides who may open it, so a page for your group
  stays within your group;
- a page built on the UI kit ([docs/UI_KIT.md](../../docs/UI_KIT.md)), which
  every gateway serves, so a static app looks finished without a file of its
  own: cards that rise in when the links arrive, and glide out of the way
  (`kit.animateList`) while you type, so you see what the search took away;
  the words that matched are marked.

**Turn on in the Portal:** nothing.

## Try it

```
efp-app dev examples/team-links
```

Open http://127.0.0.1:8000/. (A local preview with Python's own file server;
on the host no process runs at all.) Press `/` to search, pick a tag, press
`Enter` to open the first link, `Escape` to start again.

## Make it yours

1. Copy this directory into your own repository.
2. Replace the links in `public/links.json`. A link has a `title`, a `url`,
   and optionally a `note` and `tags`.
3. For another look, set `data-theme="nord"` (or any daisyUI theme) on
   `<html>` and link `/_efp/lib/daisyui@5.8.2/themes.css`
   ([docs/UI_KIT.md](../../docs/UI_KIT.md#make-it-yours)); the colours of
   the groups are `TONES` at the top of `public/app.js`.
4. `efp-app check`, push, register the directory as an app in the Portal and
   press Deploy. To change the links later: edit the file, push, Deploy.

## Things to notice

- Every URL of the app's own files is relative (`href="style.css"`,
  `fetch("links.json")`). A leading slash would point outside the app once it
  is served under `/<slug>/`. The one exception is the kit, at `/_efp/lib/`:
  every gateway serves it to every app.
- The script only turns `http` and `https` addresses into links, and puts
  text into the page as text (`kit.h`, `textContent`): even the marks of the
  search are elements made around the words, never HTML. The data file is
  yours today; the habit pays off the day someone else edits it.
- The cards are made once. A search takes cards out and puts them back
  without drawing them again, which is what lets the others glide instead of
  the whole list blinking.
- Nothing the app reads is kept in the browser: all apps share one origin,
  so `localStorage` of one app can be read by every other app. (The kit's
  theme switch keeps one thing there, light or dark, which every app on the
  host shares on purpose.)
