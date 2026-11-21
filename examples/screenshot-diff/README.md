# Screenshot diff

For everyone who maintains UI or mobile tests: choose (or drop, or paste) the
expected screenshot and the one the test run produced, and see exactly what
changed. A tolerance slider and an "ignore anti-aliasing" switch separate
real changes from rendering noise; the changed regions are listed and
outlined; the result can be downloaded as a PNG for the bug report.

**It shows**

- image processing in a `"type": "static"` app: the File API and a canvas
  read the pixels, and the comparison runs in the visitor's browser;
- heavy work cut into slices, so a 20-megapixel pair does not freeze the
  tab;
- the comparison kept apart from the page (`public/lib/diff.js` works on
  plain typed arrays), so node can test it.

**Turn on in the Portal:** nothing. No model, no connector, no storage, and
no process on the host.

## Try it

```
efp-app dev examples/screenshot-diff
```

Open http://127.0.0.1:8000/. (A local preview with Python's own file server;
on the host no process runs at all.) Press "Try two sample screenshots",
then move the tolerance below 6 (the header turns out to be a shade
lighter), switch "Ignore anti-aliasing" off (the edge of one avatar lights
up) and walk through the four views.

## Make it yours

1. Copy this directory into your own repository.
2. Set your team's defaults in `public/index.html` (the tolerance, the view
   that is checked) and in `public/lib/diff.js` (`CELL`, how close two
   changes must be to form one region; the colours of the diff). Delete
   `public/sample.js` and its button if you do not want the samples.
3. `efp-app check`, push, register the directory as an app in the Portal and
   press Deploy.

## Things to notice

- **Nothing is uploaded.** Screenshots of a product show customer data,
  unreleased features and internal addresses. They are read with the File
  API, compared in the tab and forgotten when it closes. "Download" hands a
  file made in the tab to the browser; it is not a request either. A static
  app has no backend, so there is nowhere the images could go.
- **What that costs.** No backend also means no memory: the app cannot keep
  a baseline, remember yesterday's run or tell who is looking (the gateway
  knows, a file does not). And every file under `public/` can be read by
  every visitor, so it must hold no secret. A tool that stores baselines
  per test needs a backend and `efp_app.storage`.
- **Work in slices.** `step()` compares a few rows and returns; the page
  runs it for 12 milliseconds, lets the browser draw a frame
  (`requestAnimationFrame`) and goes on. A comparison that was overtaken by
  a newer one (the slider moved again) stops. The same loop written in one
  go would freeze the tab for seconds on a large pair.
- **Limits that are stated.** Images over 25 megapixels are refused with a
  sentence: three copies of one would not fit a tab's memory. Images of
  different sizes are not refused: the part both cover is compared and the
  rest is marked, because "the screenshot got 20 pixels taller" is a result
  too.
- **Colour is not the only sign.** Changed regions are outlined and listed
  with their position and size; the list is reachable with the keyboard,
  and so are the divider and the opacity (plain range inputs).
- **The core has no DOM.** `lib/diff.js` takes `{width, height, data}` as
  `getImageData()` returns it and gives numbers back. That is what makes it
  testable with node, and reusable in a worker or on a server later.
- **Relative URLs, also between modules** (`import ... from "./lib/diff.js"`):
  the app is served under `/<slug>/` once deployed.
