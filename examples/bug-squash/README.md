# Bug squash

A 45-second game for the minutes before a meeting starts. Bugs cross the
board; squash them with a click, a tap or the letter they carry. A typo is
plain, a flaky test is fast and never where it was, a legacy bug wears armour
and needs two hits, and a feature is not a bug: squash one and it costs you
points and your combo. Every member's best run is on the leaderboard.

**It shows**

- a game that runs in the page (canvas, `requestAnimationFrame`) with only
  the facts on the server: who played, when the run began, what came out;
- a score that is accepted only when it is plausible (`finish_run`, `doubt`);
- `efp_app.storage` written by many members at once: one object per member;
- a run that can be finished once, with `storage.delete()` as the claim;
- game logic in a module without any page in it (`static/game.js`), tested
  under node, and limits the server derives from the same `rules.json`.

**Turn on in the Portal:** nothing. The app uses no model and no connector,
so it costs nothing to run.

## Try it

```
pip install efp-app-sdk -r examples/bug-squash/requirements.txt
efp-app dev examples/bug-squash
```

Open http://127.0.0.1:8000/. On your own machine there is no gateway and
nobody signs in, so say who you are: copy `.env.example` to `.env` in this
directory and add the line `EFP_DEV_VISITOR=alice`. Play a run and find
yourself on the board; change the line to `EFP_DEV_VISITOR=anonymous` and
start the app again to play as a guest, who is not ranked. Local scores are
files under `.efp-data/`.

## Make it yours

1. Copy this directory into your own repository.
2. Change `rules.json`: the length of a run, how fast the board fills, what
   each kind is worth, the combo. The page and the server both read it, so
   the limits follow by themselves. For a kind of your own, add it there and
   give it a drawing in `static/render.js` and a way of moving in
   `static/game.js` (without them it looks and moves like a typo).
3. `efp-app check`, push, register the directory as an app in the Portal and
   press Deploy.

## Things to notice

- **A score from a browser can always be forged.** The game runs on the
  visitor's machine, and whoever controls the machine controls the score. So
  the server does not pretend to referee. It checks what it can know: the
  run belongs to the member the gateway signed in, it is finished once, it
  took at least as long as a run lasts (by the server's clock), and its
  numbers can belong together and do not exceed what `rules.json` can give.
  That stops a typed-in million; it does not stop a patient cheat. The board
  is for fun. Do not hang a prize on a number that a browser sent.
- **One object per member.** Storage has no transactions: if the whole board
  lived in one `leaderboard.json`, two members finishing in the same second
  would each read the old file and one score would be lost. Here a member
  writes only `best/<visitor id>.json`, so there is nothing to collide on,
  and the board is built by reading them all, which is fine for a team.
- **Finished once, without a lock.** `storage.delete()` says whether there
  was something to delete. Of two requests that finish the same run, only
  one is told "yes"; the other gets "this run is not open". A refused run is
  used up too, so it cannot be sent again with better numbers.
- **One file of rules for both sides.** The page plays by `rules.json` and
  the server computes its limits from it with the same arithmetic. A test
  runs both and compares them, and another plays a whole run under node and
  hands the result to the server.
- **The loop stops when nothing moves.** `requestAnimationFrame` runs only
  during a run. A hidden tab gets no frames, so the run pauses there instead
  of jumping when you come back. Nothing asks the server on a timer; the
  board is loaded when the page opens, after a run and on "Refresh".
- **Less motion is a variant, not a missing page.** With "reduce motion" set
  in the system, calm mode is on: the same bugs with the same worth sit
  still and leave sooner. Letters on the bugs make the game playable from
  the keyboard.
- **Drawn, not loaded.** Every bug is a few ellipses and lines on the canvas
  (`static/render.js`). There is no image file and nothing from another
  host.
