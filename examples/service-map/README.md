# Service map

Which services does checkout need, and what breaks when the database is
down? An interactive map of an organisation's services and their
dependencies: hover a service to see its neighbours, click it to see what it
depends on and what depends on it (directly, and through others), filter by
team and tier, find a service by name, and see the dependency cycles and the
services the most others stand on. The services live in
`public/services.json`.

**It shows**

- that the map is a data file: change `services.json`, push, Deploy, and
  the page draws the new landscape;
- a data file that is checked before it is drawn, with problems named in
  sentences ("ledgerr is not in the file, did you mean ledger?");
- graphics without a graph library: a force-directed layout written by hand
  and drawn as SVG, with pan, zoom, pinch and drag;
- a page built on the UI kit ([docs/UI_KIT.md](../../docs/UI_KIT.md)), whose
  motion explains the map: it grows level by level out of a spiral, a hover
  lights up the first ring around a service, a choice sends out a ripple and
  runs beads along its arrows in the direction of the dependency, the details
  panel slides in over the map, and a search flies the camera to what it
  found; the numbers under the map count up and the "most depended on" bars
  grow to their length;
- animation that stops when there is nothing left to move, and is skipped
  for visitors who prefer reduced motion.

**Turn on in the Portal:** nothing. No model, no connector, no storage, and
no process on the host.

## Try it

```
efp-app dev examples/service-map
```

Open http://127.0.0.1:8000/. (A local preview with Python's own file server;
on the host no process runs at all.) Click `postgres-main` to see how much
stands on it, then the cycle under the map: `pricing` and `promotions` need
each other. Press `/` and type `pay`, then Enter: the camera flies to
`payments`. Open http://127.0.0.1:8000/#checkout to see that a choice can be
linked.

## Make it yours

1. Copy this directory into your own repository.
2. Replace the services in `public/services.json`. A service has a `name`
   (lower-case letters, digits, dot, dash, underscore), a `team`, a `tier`
   (1, 2 or 3), and optionally a `description`, `links` (each a `title` and
   a `url`) and `depends_on` (names of other services in the file). With no
   services the page says how to add them.
3. Change the look if you like: link `/_efp/lib/daisyui@5.8.2/themes.css`
   and set `data-theme="nord"` (or any daisyUI theme) on `<html>`
   ([docs/UI_KIT.md](../../docs/UI_KIT.md#make-it-yours)). The teams'
   colours are turns of the theme's primary colour (`--team-0` to
   `--team-7` in `public/style.css`), so they follow the theme too.
4. `efp-app check`, push, register the directory as an app in the Portal and
   press Deploy. To change the map later: edit the file, push, Deploy. A
   pipeline that writes the file from your service catalogue keeps it
   current without anybody editing it.

## Things to notice

- **The data file is the app.** There is no backend to restart and nothing
  to migrate: a pull request that changes `services.json` is the whole
  change, with its review and its history. The page loads it with
  `fetch("services.json")`, a relative URL, because the app is served under
  `/<slug>/` once deployed.
- **What a static app cannot do.** It cannot ask Kubernetes, Jira or a
  service catalogue for the truth (no connector), cannot let a visitor save
  a correction (no storage) and does not know who is looking (no identity).
  And `services.json` is public to everyone who may open the app, like
  every file under `public/`: names and links are fine, tokens and
  passwords never are. A map that reads live data needs a backend.
- **A data file is edited by hand, so it is checked.** `validate()` in
  `lib/graph.js` draws what is sound and lists the rest above the map: an
  unknown dependency (with the name it probably meant), a repeated name, a
  tier that is not 1 to 3. A link only becomes a link when it is `http` or
  `https`; everything is put into the page as text.
- **Motion with an end.** The layout is a simulation stepped with
  `requestAnimationFrame`; when it has settled, no further frame is
  requested. The beads on the arrows of a chosen service run six times and
  fade, and the camera's flights are short. So an open tab uses no
  processor. With "reduce motion" set in the system, the layout is worked
  out at once and drawn a single time, and every move jumps to its end.
- **Colour is not the only sign.** What the chosen service needs and what
  needs it differ in colour, direct and indirect differ in line style
  (solid and dashed), and the side panel says the same in words, as lists
  of buttons that work with the keyboard. `Tab` walks through the services,
  `Enter` chooses one, `/` finds one, `+`, `-` and `0` zoom, `Esc` clears.
- **The thinking has no DOM.** Reachability, cycles, the forces of the
  layout and the pan and zoom arithmetic are in `public/lib/` and are
  tested with node; `app.js` only draws, listens and moves the camera.
- **No graph library, on purpose.** The layout is under two hundred lines
  with its comments. A script from a CDN fails on a network that cannot
  reach the CDN and tells it who uses your app; a library copied in is code
  nobody on the team has read. The look comes from the gateway's own
  libraries at `/_efp/lib/` (daisyUI, Tailwind, the EFP kit), which every
  app on the host shares and nothing outside it serves.
