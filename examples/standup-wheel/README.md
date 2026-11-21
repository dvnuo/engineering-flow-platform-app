# Stand-up wheel

Who speaks next in the stand-up? A wheel with the team's names. "Spin" picks
somebody who has not spoken in this round; when everybody has, the round is
closed and listed, and the next one starts with a full wheel. The owner of
the app keeps the list of names and marks who is away today.

**It shows**

- a choice made on the server (`spin`), so every page shows the same one and
  nobody can spin until they like the result;
- "create only if absent" on a storage that has no such call: one process,
  one lock, one object per pick;
- an object that only the owner writes, with a version number against a
  stale save (`save_team`);
- owner-only actions checked on the server (`owner_only`), as in
  [setup-check](../setup-check/), and a second audience (`on_the_wheel`):
  the people on the list;
- an SVG wheel turned with `requestAnimationFrame`, and shown at its result
  without any turning when the visitor asks for less motion.

**Turn on in the Portal:** nothing. The app uses no model and no connector,
so it costs nothing to run.

## Try it

```
pip install efp-app-sdk -r examples/standup-wheel/requirements.txt
efp-app dev examples/standup-wheel
```

Open http://127.0.0.1:8000/. The wheel starts with the six names of
`team.json`. On your own machine there is no gateway and nobody signs in, so
say who you are: copy `.env.example` to `.env` in this directory and add the
line `EFP_DEV_VISITOR=alice`. Alice is on the list and can spin; with
`EFP_DEV_VISITOR=zed` you can only watch, and with `anonymous` you are a
guest. Editing the list is for the owner, and who that is only the Portal
knows: once `.env` holds the app's token (and no `EFP_DEV_VISITOR` line), you
are the owner and the form appears. Local data is under `.efp-data/`.

## Make it yours

1. Copy this directory into your own repository.
2. Put your team into `team.json` (the list the wheel starts with), or leave
   it and edit the list on the page after the first deployment. A person has
   the username they sign in to the Portal with, which decides who may spin,
   and the name shown on the wheel.
3. `efp-app check`, push, register the directory as an app in the Portal and
   press Deploy. The audience should be your group; with "anyone", guests
   can watch and see the names.

## Things to notice

- **The server chooses, the page only turns.** If the page chose, everybody
  would have their own result, and a reload would be a second chance. Here
  "Spin" asks the server, which picks with `secrets.choice` among the people
  who are left, and the wheel is turned to that name afterwards. The turning
  is decoration: with "reduce motion" set, the wheel just shows the result.
- **Two people press at once and get one pick.** The page says which pick it
  asks for ("number 3 of round 12"). The first request creates
  `rounds/000012/picks/003.json`; the second finds it there and gets the
  same answer, so both wheels turn to the same name. A pick is written once
  and never replaced.
- **"Only if absent" needs one step, and an app has one process.** Storage
  has no transactions and no conditional save, so "look, then save" from two
  requests can interleave. The host runs exactly one process per app, which
  makes a `threading.Lock` around that step enough. The lock guards the
  decision and holds no data: the picks, the list and the history are in
  storage, because an idle app is stopped and started again.
- **One writer, and a version for their second window.** Only the owner's
  requests write `team.json` in storage. One writer can still collide with
  themselves in two windows, so the form sends the version of the list it
  was filled from and the server refuses to save over a newer one.
- **Owner only, and "on the list" only, checked on the server.** The form is
  hidden from everybody else, but the check is `owner_only`: the visitor the
  gateway signed in, compared with the owner the Portal names. Spinning is
  for the people on the list, compared by the same signed username; nothing
  in a request body says who is asking.
- **Closing a round is safe to repeat and safe to interrupt.** "Start a new
  round" names the round it means, so pressing it twice (or two people
  pressing it) closes one round. The history entry is written before the
  picks are deleted; if the app stops in between, the round is closed and
  the leftover picks are never read again.
- **No polling.** Somebody who watches on their own screen presses
  "Refresh". A page that asked every few seconds would keep the app from
  ever being idle.
