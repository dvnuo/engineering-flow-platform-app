# Incident timeline

Paste what an incident left behind: the chat, log lines, somebody's notes.
The model picks out the events (when, who, what, and whether it was the
detection, an action, an escalation, the resolution or a note). The page
draws them as a timeline, lets you correct or remove each one, keeps the
timeline for you and exports a post-mortem skeleton as Markdown.

**It shows**

- a model asked for JSON under a strict contract (`llm.complete`), and an
  answer that is validated before anything is done with it (`events_in`,
  `clean_event`);
- one repair attempt when the answer cannot be used, and a plain "no" after
  that;
- masking secrets before the notes leave the app (`mask_secrets`);
- timelines kept per member in `efp_app.storage`, one object each, saved
  with a version so that a stale save is refused;
- the rules of the page (what a time is, the order, the post-mortem, the
  layout of the downloadable picture) as functions that are tested without
  a browser (`static/timeline.js`);
- a page built on the UI kit ([docs/UI_KIT.md](../../docs/UI_KIT.md)): the
  notes in a small editor with line numbers and a meter of the limit; the
  events dropping onto a time axis one after the other, each dot landing in
  the colour of its kind, while the line of the axis draws itself down;
  cards that open into a form where they stand; a card whose new time puts
  it elsewhere gliding to its place (FLIP), so the eye follows it; a
  removed card folding away, with an Undo.

**Turn on in the Portal:** the language model, on the app's page. Without it
a timeline can still be built by hand, saved and exported.

## Try it

```
pip install efp-app-sdk -r examples/incident-timeline/requirements.txt
efp-app dev examples/incident-timeline
```

Open http://127.0.0.1:8000/. Without a token in `.env` the page loads and
says that the app is not set up yet; "Start by hand" works anyway.
To save one, say who you are: add `EFP_DEV_VISITOR=alice` to `.env` (once
`.env` holds the token you are the owner without that line). With the token,
calls are real: they run on your model provider and count against the app's
budget. Local timelines are files under `.efp-data/`.

## Make it yours

1. Copy this directory into your own repository.
2. Change `KINDS` and `SYSTEM_PROMPT` in `main.py` to the way your team talks
   about incidents (and `KINDS` in `static/timeline.js` and the colour and
   icon of each kind in `LOOKS` at the top of `static/app.js` with them),
   and the sections of `postmortem()` in `static/timeline.js` to your
   post-mortem template. For another look altogether, set
   `data-theme="business"` (or any daisyUI theme) on `<html>`
   ([docs/UI_KIT.md](../../docs/UI_KIT.md#make-it-yours)): the page and the
   colours of the kinds follow.
3. `efp-app check`, push, register the directory as an app in the Portal,
   turn the model on and press Deploy.

## Things to notice

- **A model's "JSON" is text until you have validated it.** The answer may
  arrive in a Markdown fence, with a sentence in front, cut off at the
  length limit, or with a field that is not what was agreed. `events_in`
  removes the fence, parses, and `clean_event` checks the type and the
  length of every field: a time that exists, a text of at most 200
  characters, a kind out of five. What does not fit is dropped and counted,
  never bent into shape, and the page says how many items were dropped.
  Whatever the member saves later goes through the same check.
- **One repair attempt, then an honest "no".** When the answer is not JSON
  or none of its events fits, the model gets its own answer back once, with
  what was wrong. When that fails too, the app says that nothing usable came
  back and shows nothing. It does not loop, and it does not guess.
- **What one use costs.** You pay for every visitor. An extraction sends at
  most 16,000 characters of notes (about 4,000 tokens of English; Chinese
  counts closer to one token per character) and some 300 tokens of
  instructions, and the answer is capped at 3,000 output tokens. A repair is
  a second call: the first answer goes back in (at most 3,000 tokens) and up
  to 3,000 come out again, so a click costs at most two calls and the notes
  are sent once. Empty notes cost nothing. The limits are the constants at
  the top of `main.py`.
- **Masking is a filter, not a guarantee.** Passwords, tokens, keys in URLs
  and key blocks are replaced before the notes go to the model, and the page
  says how many were. Every pattern has an upper bound on what it reads, so
  pasted text cannot slow it down. A chat can still hold customer names or
  anything else a pattern does not know: say so to your team.
- **One object per timeline, and a version in it.** Storage has no
  transactions. Each timeline is `timelines/<member id>/<timeline id>.json`,
  with the member id from the signed `X-EFP-Visitor` header, so members
  never write the same object. One member with two tabs still could: the
  object carries a version, the page sends the version it opened, and a save
  based on an older one is answered with 409 instead of silently replacing
  the newer one.
- **The notes are not kept.** Only the title and the events are saved. The
  pasted text is sent to the model and forgotten.
- **Text from elsewhere stays text.** The notes are data for the model (the
  system prompt says so), events reach the page as text nodes and input
  values (and the picture as SVG text nodes), and the Markdown export
  escapes them, so a line in a chat cannot become a link or an image in
  your post-mortem.
- **Times are taken as written.** No time zone is converted, and times
  without a date sort as one day: for an incident that ran over midnight,
  add the date to its events.
