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
- pointer events, so one piece of code serves mouse, touch and pen;
- a studio built on the UI kit ([docs/UI_KIT.md](../../docs/UI_KIT.md)),
  with the gallery as a rail beside the board, in light and dark: paper in
  the theme's colour, inks chosen to read on light and on dark paper, and
  motion that says what happened: the pill behind the tool in use slides
  to the next tool, a ring under the pointer shows how wide the line will
  be, a saved picture flies from the board into the gallery, a wiped board
  is swept away, and the gallery's cards rise in.

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
   `MAX_PER_MEMBER`) and the size of the board, its fixed inks and widths in
   `static/shapes.js` (and the `width` and `height` of the canvas in
   `static/index.html`). The inks that come from the theme are `INKS` at the
   top of `static/app.js`; the paper is `--wb-paper` in `static/style.css`.
3. Give the page another look with a daisyUI theme: link
   `/_efp/lib/daisyui@5.8.2/themes.css` in `static/index.html` and set
   `data-theme="lofi"` (or any theme) on `<html>`. The palette, the paper
   and the inks named after the theme follow it
   ([docs/UI_KIT.md](../../docs/UI_KIT.md#make-it-yours)).
4. `efp-app check`, push, register the directory as an app in the Portal and
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
  transparent and the paper is added when the picture is made, so undo is
  simply "paint the list of strokes again without the last one".
- **Paper and inks, chosen on purpose.** The paper is the theme's, light or
  dark, and a saved picture is the board as it was seen. So every ink has to
  read on both papers. "Ink" is the theme's text colour, and a stroke keeps
  the word, not the colour: drawn again in the other theme it flips from
  near black to near white. Primary and Secondary are kept by name the same
  way. Red, orange, green and blue are fixed middle tones with a contrast of
  at least 3.5 to 1 on white and on the dark paper alike, so they mean the
  same to everyone who opens the picture. The theme's accent is left out: in
  the light theme it is too pale for white paper. A saved picture that is
  continued on the board brings its own paper, and "Ink" then takes
  whichever of the theme's two colours stands out from it.
- **No timers.** The gallery is loaded when the page opens, after a save or a
  delete, and on "Refresh".
