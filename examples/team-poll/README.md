# Team poll

One question, a few options, and one vote for every member. Everybody who may
open the app sees the result; a vote can be changed or withdrawn.

**It shows**

- who is visiting (`get_visitor`), and answering 401 to a visitor who is not
  signed in (`require_visitor`);
- `efp_app.storage` used by many visitors at once;
- a page built on the UI kit ([docs/UI_KIT.md](../../docs/UI_KIT.md)) that
  shows what changed and nothing else: each answer is compared with the one
  before, so the bars glide to their new share, the numbers count, your vote
  lands with a tick (and a small burst the first time), and a row that
  somebody else's vote moved lights up after Refresh.

**Turn on in the Portal:** nothing. The app uses no model and no connector,
so it costs nothing to run.

## Try it

```
pip install efp-app-sdk -r examples/team-poll/requirements.txt
efp-app dev examples/team-poll
```

Open http://127.0.0.1:8000/. On your own machine there is no gateway and
nobody signs in, so say who you are: copy `.env.example` to `.env` in this
directory and add the line `EFP_DEV_VISITOR=alice`. Vote (click an option or
press its number), then change the line to `EFP_DEV_VISITOR=bob`, start the
app again and vote for another option: back as alice, press Refresh and
watch the result move. With `EFP_DEV_VISITOR=anonymous` you see the result
without being able to vote. (Once `.env` holds the app's token, you
are the owner of the app without that line.) Local votes are files under
`.efp-data/`.

## Make it yours

1. Copy this directory into your own repository.
2. Write your question into `poll.json`. Give it a new `"id"` whenever you
   ask something new: the votes are kept per id, so the count starts at zero.
   For another look, set `data-theme="cupcake"` (or any daisyUI theme) on
   `<html>` in `static/index.html` and link
   `/_efp/lib/daisyui@5.8.2/themes.css`
   ([docs/UI_KIT.md](../../docs/UI_KIT.md#make-it-yours)).
3. `efp-app check`, push, register the directory as an app in the Portal and
   press Deploy.

## Things to notice

- **One object per voter.** Storage has no transactions. If all votes lived
  in one `votes.json`, two members voting at the same moment would each read
  the old file and one vote would be lost. Here every visitor writes only
  `votes/<poll id>/<visitor id>.json`, so there is nothing to collide on.
- **Identity comes from the gateway.** The vote is filed under `visitor.id`,
  taken from the signed `X-EFP-Visitor` header, never from anything the
  browser sends in the request body.
- **No polling.** The page loads the result on demand. A timer that asks
  every few seconds would keep the app busy for as long as one tab is open,
  and the host stops apps only when they are idle.
- **Animate the difference, not the page.** The rows are made once and only
  refilled; `show` compares the new answer with the last one to decide what
  moves. The options keep their order, because a row that jumped under the
  pointer would get the wrong vote.
- With the audience "anyone", visitors who are not signed in can look but not
  vote. For every other audience all visitors are members.
