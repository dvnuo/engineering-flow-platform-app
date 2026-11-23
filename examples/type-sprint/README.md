# Type sprint

Sixty seconds of typing what engineers type: `git`, `kubectl`, `docker`,
shell, `curl`, SQL and Python one-liners, by topic or mixed. The page shows
words per minute and accuracy as you type and marks a wrong character where
it stands. After the minute you get a chart of your speed, the line that cost
you the most, your last 30 sprints as a curve, and a leaderboard of every
member's best sprint with at least 90 percent accuracy.

**It shows**

- a result the server works out itself, from data it owns (`measure`): the
  page reports which lines were typed, never a speed;
- refusing what cannot be true: a speed nobody types, a sprint shorter than
  a sprint, lengths that do not add up;
- a member's history in one object of their own (`players/<visitor id>.json`);
- data that ships with the app (`snippets.json`), checked when the app starts;
- charts written as SVG by hand, and typing logic in a module without a page
  in it (`static/sprint.js`), tested under node;
- a page built on the UI kit ([docs/UI_KIT.md](../../docs/UI_KIT.md)) whose
  motion follows the typing: a finished line lifts away and the next one
  rises, a wrong key nudges the line, the last ten seconds turn red, and the
  result counts up while its bars grow, with confetti for a new best.

**Turn on in the Portal:** nothing. The app uses no model and no connector,
so it costs nothing to run.

## Try it

```
pip install efp-app-sdk -r examples/type-sprint/requirements.txt
efp-app dev examples/type-sprint
```

Open http://127.0.0.1:8000/. On your own machine there is no gateway and
nobody signs in, so say who you are: copy `.env.example` to `.env` in this
directory and add the line `EFP_DEV_VISITOR=alice`. Type a sprint or two and
watch the history grow; with `EFP_DEV_VISITOR=anonymous` you type as a guest,
whose sprints are not kept. Local results are files under `.efp-data/`.

## Make it yours

1. Copy this directory into your own repository.
2. Put your own lines into `snippets.json`: the commands of your build, your
   deployment, your test runner. A topic has an `id`, a `label` and its
   lines; a line is 5 to 80 printable ASCII characters. The app refuses to
   start with a file that breaks those rules and says which line it was.
3. Give it another look if you like: link
   `/_efp/lib/daisyui@5.8.2/themes.css` in `static/index.html` and set
   `data-theme="dracula"` (or any daisyUI theme) on `<html>`. The line, the
   charts and the board follow its colours.
4. `efp-app check`, push, register the directory as an app in the Portal and
   press Deploy.

## Things to notice

- **The server computes nothing it cannot check.** A page that sent "I
  typed 80 words a minute" would be believed or not; there is nothing to
  check. This page sends the ids of the lines it finished, and the server,
  which has the same `snippets.json`, adds up their lengths itself. Then it
  refuses the impossible: more right characters than right keys, bars of the
  speed chart that do not add up to the total, more than 250 words a minute,
  a result that arrives sooner than 60 seconds after the sprint was opened
  (by the server's clock).
- **And it does not pretend to check the rest.** Whether a person pressed
  those keys is beyond the server; a script can type too. The accuracy is
  the page's count. The board is for fun, and the README of
  [bug-squash](../bug-squash/) says the same about its scores.
- **One object per member, and why that is enough here.** Storage has no
  transactions, so a history that many members append to must not be one
  object. `players/<visitor id>.json` is written only by its member, and a
  member has one open sprint at a time (starting a sprint gives up the one
  before), so "read, add, save" has nobody to collide with. The board reads
  every member's object, which is fine for a team.
- **No paste, and a cursor that stays at the end.** The text box refuses
  `paste` and `drop`, and `sprint.js` also refuses a change that adds a
  whole word at once, for the ways around the first two.
- **The clock is the time, not a count of frames.** The sprint ends when
  60 seconds have passed since the first key, read from
  `performance.now()`. `requestAnimationFrame` only redraws the numbers, so
  a tab that was hidden ends its sprint when it is looked at again, and
  nothing ticks when no sprint runs.
- **Text from a file is still text.** Lines reach the page with
  `textContent`, one element per character, so a line such as
  `'{{.Names}}'` or `<hosts.txt` is shown, not interpreted.
