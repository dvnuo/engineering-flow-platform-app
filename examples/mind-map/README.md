# Mind map

An editor for mind maps. Every member has maps of their own: add a child with
Tab and a sibling with Enter, rename in place, fold a branch, colour it, drag
a node to another parent, move around and zoom, and export the result as an
SVG or PNG picture or as a Markdown outline. A map can be made visible to
everyone who can open the app; the others then see it read-only and can take
a copy.

**It shows**

- one stored object per map, `maps/<owner id>/<map id>.json`, written only
  by the member the gateway signed in (`get_visitor`);
- a version number that refuses a stale save with 409, for the same member in
  two tabs;
- a tree checked on the server before it is stored: size, number of nodes,
  lengths, unique ids, one root, no loops;
- a second object that is only a list (`shared/...`) and may be wrong for a
  moment, because two saves are not one transaction;
- a page of several ES modules without a build step, with the tree and its
  layout as plain functions that are tested with node.

**Turn on in the Portal:** nothing. The app uses no model and no connector,
so it costs nothing to run.

## Try it

```
pip install efp-app-sdk -r examples/mind-map/requirements.txt
efp-app dev examples/mind-map
```

Open http://127.0.0.1:8000/. On your own machine there is no gateway and
nobody signs in, so say who you are: copy `.env.example` to `.env` in this
directory and add the line `EFP_DEV_VISITOR=alice`. Create a map, open it in
a second tab as well, change and save it in both: the second save is refused
and the page offers to reload the map or to keep yours as a copy. With
`EFP_DEV_VISITOR=anonymous` you see what a guest sees: the shared maps, read
only. To see "Shared maps", share a map as alice, then start the app as
`EFP_DEV_VISITOR=bob`: every name is a member of its own. Local maps are
files under `.efp-data/`.

## Make it yours

1. Copy this directory into your own repository.
2. Set the limits at the top of `main.py` (`MAX_NODES`, `MAX_MAPS`,
   `MAX_MAP_BYTES`) and the colours in `COLORS`, `static/tree.js` and
   `static/render.js`.
3. `efp-app check`, push, register the directory as an app in the Portal and
   press Deploy.

## Things to notice

- **Nobody can name another owner.** A map is saved under `visitor.id` from
  the signed `X-EFP-Visitor` header. The routes that write take a map id and
  nothing else, so there is no request that could reach another member's map,
  and two members never write the same object.
- **A version number instead of a transaction.** Storage has no "save only
  if unchanged". The page sends the version it loaded; the server compares it
  with the stored one under a lock and answers 409 when they differ. The lock
  is a plain `threading.Lock`: the host runs one process per app, and that is
  the only reason it is enough.
- **The page is not trusted.** The editor keeps the tree tidy, but the API can
  be called by anything. `clean_nodes` checks every rule again and stores only
  the five fields it knows; `document` stops reading a body at 256 KiB instead
  of trusting it to be small.
- **A list that can be rebuilt.** Which maps are shared is kept as small
  marker objects, so the list does not have to open every map. A marker is
  written after the map, and that second save can fail. So the marker decides
  only what the list shows; whether a map may be read is asked of the map.
- **A map that is not shared does not exist for others.** A private map
  and a missing one get the same 404.
- **The picture is complete in itself.** Colours and fonts are attributes of
  the SVG elements, not style sheet rules, so the exported SVG looks like the
  page; the PNG is that SVG painted on a canvas. Everything is made in the
  browser, from files of this directory.
- **No timers.** Nothing is saved or loaded on a clock. A save goes out when
  you press Save (or Ctrl+S), when you tick "Visible to everyone", when you
  leave the map and when the tab goes into the background.
