# Team links

A searchable page of the links a team keeps asking for: dashboards, boards,
runbooks. The links live in `public/links.json`; the page filters them as you
type.

**It shows** the simplest kind of app: `"type": "static"`. There is no
backend, no Python, no token and nothing to pay for. The gateway serves the
`public` directory and still decides who may open it, so a page for your
group stays within your group.

**Turn on in the Portal:** nothing.

## Try it

```
efp-app dev examples/team-links
```

Open http://127.0.0.1:8000/. (A local preview with Python's own file server;
on the host no process runs at all.)

## Make it yours

1. Copy this directory into your own repository.
2. Replace the links in `public/links.json`. A link has a `title`, a `url`,
   and optionally a `note` and `tags`.
3. `efp-app check`, push, register the directory as an app in the Portal and
   press Deploy. To change the links later: edit the file, push, Deploy.

## Things to notice

- Every URL in the page and the script is relative (`href="style.css"`,
  `fetch("links.json")`). A leading slash would point outside the app once it
  is served under `/<slug>/`.
- The script only turns `http` and `https` addresses into links, and puts
  text into the page with `textContent`. The data file is yours today; the
  habit pays off the day someone else edits it.
- Nothing is kept in the browser. All apps share one origin, so
  `localStorage` of one app can be read by every other app.
