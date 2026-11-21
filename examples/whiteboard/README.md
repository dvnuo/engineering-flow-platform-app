# Whiteboard

A board to sketch on with mouse, finger or pen: pen, eraser, line, box,
ellipse, arrow and text, in a few colours and widths, with undo and redo.
"Save" puts the drawing into a gallery that everybody who can open the app
sees; a saved drawing can be opened, continued on the board, downloaded and
deleted by its author.

**It shows**

- binary objects in `efp_app.storage`: `storage.save(key, bytes)` and
  `storage.load(key)`, with a small JSON record next to each picture;
- an upload without a form library: the request body is the file, read with
  an upper bound;
- checking on the server what a browser sent: the PNG signature, its chunks
  and checksums, the dimensions, the size, a count per member;
- a route that sends stored bytes with their content type and a cache header;
- an action for "the author, or the owner of the app", decided on the server;
- pointer events, so one piece of code serves mouse, touch and pen.

**Turn on in the Portal:** nothing. The app uses no model and no connector,
so it costs nothing to run.

## Try it

```
pip install efp-app-sdk -r examples/whiteboard/requirements.txt
efp-app dev examples/whiteboard
```

Open http://127.0.0.1:8000/. On your own machine there is no gateway and
nobody signs in, so say who you are: copy `.env.example` to `.env` in this
directory and add the line `EFP_DEV_VISITOR=alice`. Draw, save, and look at
`.efp-data/objects/drawings/`: a `.png` and a `.json` per drawing. With
`EFP_DEV_VISITOR=anonymous` you can draw and download but not save. The
owner of the app may delete any drawing; who the owner is comes from the
Portal, so locally that needs the app's token in `.env` (and no
`EFP_DEV_VISITOR`: you are then the owner).

## Make it yours

1. Copy this directory into your own repository.
2. Set the limits at the top of `main.py` (`MAX_BYTES`, `MAX_SIDE`,
   `MAX_PER_MEMBER`) and the size of the board, its colours and widths in
   `static/shapes.js` (and the `width` and `height` of the canvas in
   `static/index.html`).
3. `efp-app check`, push, register the directory as an app in the Portal and
   press Deploy.

## Things to notice

- **Storage takes bytes.** `storage.save(key, data)` stores them as they are
  and `storage.load(key)` returns them; `save_json` is only a convenience on
  top. One object can have at most 64 MiB, the app's data has a quota (1 GiB
  unless the platform changed it), and the gateway lets a request of at most
  100 MB through. This app sets its own, much smaller limit, because none of
  the three protects a team gallery from one enthusiastic member.
- **What the browser sends is checked on the server.** The page only ever
  sends a PNG of 1600 by 1000 pixels. But the page runs in the visitor's
  browser, and the API can be called with anything from anywhere. So
  `upload` counts the bytes while they arrive and stops at the limit, and
  `png_size` walks through the file (signature, every chunk with its
  checksum, the dimensions) before a single byte is kept. The server later
  hands these bytes to every visitor as `image/png`; it has to know that this
  is what they are.
- **Two objects, no transaction.** A drawing is a picture and a record. The
  picture is saved first and the record last, and a delete goes the other way
  round. The gallery lists records, so whatever fails in between, it never
  shows a drawing whose picture is missing.
- **A cache header saves requests.** A saved drawing never changes (a new
  save is a new id), so the picture is sent with
  `Cache-Control: private, max-age=31536000, immutable`: the browser asks for
  it once. `private`, because it was served to a signed-in visitor.
- **Who may delete is decided on the server.** The author is the visitor the
  gateway signed in; the owner is the one the Portal names
  (`app.info(cached=True).owner`). The page hides the Delete button for
  everybody else, but that is a courtesy, not the check.
- **The eraser removes, it does not paint white.** The board stays
  transparent and the white paper is added when the picture is made, so undo
  is simply "paint the list of strokes again without the last one".
- **No timers.** The gallery is loaded when the page opens, after a save or a
  delete, and on "Refresh".
