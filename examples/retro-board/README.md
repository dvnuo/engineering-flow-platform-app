# Retro board

A board for the sprint retrospective: columns such as "Went well" and "To
improve", cards that every member adds, and a few dot votes per member to
find out what matters most. Cards are anonymous until the owner of the app
reveals the authors; the owner can also close the board. The board can be
sorted by votes and exported as Markdown, and earlier boards stay readable.

**It shows**

- anonymity that holds on the wire: a card's author is sent to its author
  and to nobody else (`view` in `main.py`);
- one stored object per card and one per (card, voter), so that members who
  write at the same moment cannot overwrite each other;
- a limit per member (votes, cards) counted from storage keys;
- actions only the owner of the app may take, checked on the server
  (`owner_only`);
- settings in a file next to the code (`board.json`), remembered in storage
  so that an old board survives a new one;
- a board built on the UI kit ([docs/UI_KIT.md](../../docs/UI_KIT.md)):
  columns with a colour and an icon each, cards like notes, dot votes that
  pop, and cards that glide into place when one is added, deleted or the
  order changes.

**Turn on in the Portal:** nothing. The app uses no model and no connector,
so it costs nothing to run.

## Try it

```
pip install efp-app-sdk -r examples/retro-board/requirements.txt
efp-app dev examples/retro-board
```

Open http://127.0.0.1:8000/. On your own machine there is no gateway and
nobody signs in, so say who you are: copy `.env.example` to `.env` in this
directory and add the line `EFP_DEV_VISITOR=alice`. Add cards, vote, sort,
export. With `EFP_DEV_VISITOR=anonymous` you see the board as a guest: all
cards, no names, nothing to change. Revealing and closing are for the owner
of the app, and who that is comes from the Portal: locally they need the
app's token in `.env` (without `EFP_DEV_VISITOR` you are then the owner).
To see cards "of the others", add some as alice, then start the app as
`EFP_DEV_VISITOR=bob`: every name is a member of its own, and bob sees
alice's cards without her name.

## Make it yours

1. Copy this directory into your own repository.
2. Write your columns and the number of votes into `board.json`. For the
   next sprint give it a new `"id"`: the board starts empty, and the old one
   moves to the "Past boards" list, read-only.
3. Pick the colour (a colour of the theme) and the icon (a lucide name,
   https://lucide.dev/icons) of each column in `COLUMN_LOOKS` at the top of
   `static/app.js`, or give the whole board another look with a daisyUI
   theme ([docs/UI_KIT.md](../../docs/UI_KIT.md#make-it-yours)).
4. `efp-app check`, push, register the directory as an app in the Portal and
   press Deploy.

## Things to notice

- **What the server does not send, the page cannot leak.** The page of a
  member never receives who wrote the other cards. Leaving the name out when
  the page is drawn, or hiding it with a style sheet, would look the same and
  be worth nothing: the browser's tools show every answer. The one place that
  decides is `view`, on the server. Votes work the same way: a count per
  card, and whether you voted.
- **Anonymous to members, not to the platform.** A card is stored with its
  author (the app needs it for "edit and delete your own", and for the
  reveal). The owner of the app does not see authors in the page either,
  until they reveal them for everybody at once.
- **One object per card, one per vote.** Storage has no transactions. A
  shared `board.json` in storage would lose a card whenever two members add
  one at the same moment. Here a card is `cards/<board>/<card id>.json`, and
  a vote is `votes/<board>/<card id>/<voter id>.json`: voting twice writes
  the same object twice, and two voters never touch the same one.
- **Counting without reading.** The key of a vote names the card and the
  voter, so the number of votes on a card and the votes a member has left
  come from a listing of keys. No vote is ever opened.
- **A limit needs a lock, and the lock is local.** "Has this member votes
  left?" and "save the vote" must not be interleaved with the same question
  from another tab. A `threading.Lock` is enough, because the host runs one
  process per app.
- **The owner is checked on the server.** `owner_only` compares the visitor
  the gateway signed in with `app.info(cached=True).owner`. The page shows
  the two buttons to the owner only, but that is a courtesy.
- **A new id is a new board.** The settings of a board are copied into
  storage with its first card (`index/<board id>.json`). When `board.json`
  later describes the next sprint, the old cards, votes and columns are
  still there under the old id.
- **The export cannot say more than the page.** It is made in the browser
  from what the server sent, so it holds names only after the reveal.
- **No timers.** The board is loaded when the page opens, after each action
  and on "Refresh". A page that asked every few seconds would keep the app
  from ever being idle.
- **Kept by id, not drawn anew.** Each answer is the whole board, but the
  page keeps a column's and a card's element by id and only refills it, so
  that `kit.animateList` can move it to its new place and the keyboard
  focus stays where it was.
