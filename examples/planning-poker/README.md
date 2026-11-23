# Planning poker

Estimate a story together. A member opens a room for a story and sends its
link to the team; everybody picks a card, and nobody sees the others' cards
until somebody presses Reveal. Then the room shows the spread, the average,
the median and whether everybody agrees. "New round" files the round in the
room's history and starts the next one. The page is live: what one member
does, the others see at once.

**It shows**

- a WebSocket in an app: the visitor is read from the headers of the
  handshake (`get_visitor(websocket)`), and the address is built from the
  page's own location;
- live state in memory with its copy in `efp_app.storage`: one object per
  room, saved under the room's lock before anybody is told;
- a secret kept on the server: a pick is sent as "has picked" until the
  round is revealed;
- a page that reconnects by itself, with a growing delay, and says whether
  it is connected;
- tidying up without a scheduler: old rooms go when a new one is created;
- a page built on the UI kit ([docs/UI_KIT.md](../../docs/UI_KIT.md)): a
  round table, a hand of cards, a result that counts up, in light and dark,
  and motion only for what changed: a card lands on the table, the cards
  turn over one after the other, confetti when everybody agrees.

**Turn on in the Portal:** nothing. The app uses no model and no connector,
so it costs nothing to run.

## Try it

```
pip install efp-app-sdk -r examples/planning-poker/requirements.txt
efp-app dev examples/planning-poker
```

Open http://127.0.0.1:8000/. On your own machine there is no gateway and
nobody signs in, so say who you are: copy `.env.example` to `.env` in this
directory and add the line `EFP_DEV_VISITOR=alice`. Open a room, pick a card,
reveal. Then stop the app (Ctrl+C) and start it again while the page is open:
the page says that the connection is lost, connects again by itself, and the
room is as it was, history and your hidden pick included. Every tab of one
run is the same member (the name in `EFP_DEV_VISITOR`); to see hidden picks
of others, deploy the app and send the room's link to a colleague.

## Make it yours

1. Copy this directory into your own repository.
2. Write your cards into `deck.json` (T-shirt sizes work as well; cards that
   are not numbers count as votes, not in the average).
3. Give it another look if you like: link
   `/_efp/lib/daisyui@5.8.2/themes.css` in `static/index.html` and set
   `data-theme="retro"` (or any daisyUI theme) on `<html>`; the cards and the
   table take its colours. `--pp-card-w` in `static/style.css` sets the size
   of the cards.
4. `efp-app check`, push, register the directory as an app in the Portal and
   press Deploy.

## Things to notice

- **An open WebSocket keeps the app awake.** The host stops an app nobody
  uses, and a connection that is open is use. That is the honest exception
  to "an idle app is stopped", and it is the right one: the app runs exactly
  as long as somebody is in a room. Polling would be worse on both counts.
  Every tab would knock every few seconds, the app could never stop while
  one tab is left open anywhere, and a pick would still show up late.
- **Memory is not where the room lives.** A restart, a deployment or a stop
  wipes memory. So every change is saved first (`rooms/<room id>.json`),
  then put into memory, then sent out: what the members are told is what a
  restart brings back. Only who is connected right now is kept in memory
  alone, because after a restart that is nobody.
- **One process, one registry, one lock per room.** The host runs one
  process per app, so the dictionary `ROOMS` is the truth about who is
  connected, and an `asyncio.Lock` per room puts its changes in order. Two
  members never write the same object at the same moment, although storage
  has no transactions.
- **Who is on the other end is decided at the handshake.** The gateway signs
  the WebSocket request like any other. The visitor is read once, when the
  connection opens; no message can claim to be somebody else. A guest is
  accepted and closed at once with a code of the app's own (4401), because
  a browser cannot see why a handshake was refused but does see a close
  code.
- **Hidden means not sent.** Before the reveal the server sends, for the
  others, `"picked": true` and no card. The page could not show a pick early
  if it wanted to.
- **The address of the WebSocket comes from the page.**
  `new URL("ws/" + id, document.baseURI)` with `ws:` or `wss:` by the page's
  own scheme. A written-out address would work locally and break under
  `/<slug>/`.
- **Reconnecting, politely.** After a drop the page waits 1, 2, 4 ... up to
  30 seconds (a `setTimeout` per attempt, never a repeating timer), gives up
  after a while and offers a button, and does not try at all while its tab is
  in the background.
- **`uvicorn[standard]` in `requirements.txt`.** Plain `uvicorn` has no
  WebSocket library; the `standard` extra brings one. Without it the page
  loads and the room never connects.
- **No scheduler.** Rooms nobody touched for 30 days are removed when
  somebody creates a new one: housekeeping happens in a request, because
  nothing else is guaranteed to run.
- **Motion from the difference of two states.** `draw(previous)` in
  `static/app.js` compares what the server sent with what it sent before:
  only a pick that is new lands, only a reveal turns the cards over. The
  seats are kept by member id, so nothing is drawn anew that did not
  change. The kit skips every animation for a visitor who asked the system
  for less motion.
