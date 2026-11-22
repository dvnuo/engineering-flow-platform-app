# Log explainer

Paste the log of a failed build, test run or deployment; the model says what
failed, why it probably failed and what to try next. The answer is streamed.

**It shows**

- one streamed model call (`llm.astream`) behind a FastAPI route;
- how to keep a call cheap: only the end of the log is sent (`end_of`) and
  the answer is capped (`max_output_tokens`);
- masking passwords, tokens and keys before the text leaves the app
  (`mask_secrets`);
- what a visitor sees when the budget is used up or the model is off;
- a page built on the UI kit ([docs/UI_KIT.md](../../docs/UI_KIT.md)) that
  makes the wait feel alive: skeleton lines while the model reads, then each
  piece of the answer inks in from a blur behind a glowing caret, a Stop
  button (or `Escape`) while it writes, and the time it took at the end.

**Turn on in the Portal:** the language model, on the app's page.

## Try it

```
pip install efp-app-sdk -r examples/log-explainer/requirements.txt
efp-app dev examples/log-explainer
```

Open http://127.0.0.1:8000/. Without a token in `.env` the page loads and
the answer says that the app is not set up yet. Paste a log and press
`Ctrl` + `Enter` to explain it. With the token, calls are real:
they run on your model provider and count against the app's budget.

## Make it yours

1. Copy this directory into your own repository.
2. Change `SYSTEM_PROMPT` in `main.py` to your stack: name your build tool,
   your test framework, the mistakes your team makes most. For another look,
   set `data-theme="dim"` (or any daisyUI theme) on `<html>` in
   `static/index.html` and link `/_efp/lib/daisyui@5.8.2/themes.css`
   ([docs/UI_KIT.md](../../docs/UI_KIT.md#make-it-yours)).
3. `efp-app check`, push, register the directory as an app in the Portal,
   turn the model on and press Deploy.

## Things to notice

- **You pay for every visitor.** Calls run on the owner's provider. The two
  limits at the top of `main.py` decide what one click can cost; the daily
  budget on the app's page decides what a day can cost.
- **The refusal comes before the stream.** `await llm.astream(...)` sends the
  request, so "budget used up" or "model switched off" is still a normal JSON
  error with a sentence written for visitors (`exc.user_message`). Only a
  failure in the middle of an answer has to be written into the text.
- **Masking is a filter, not a guarantee.** It catches the usual shapes
  (`password=...`, `Authorization: Bearer ...`, AWS and GitHub keys, private
  key blocks). Tell your visitors not to paste logs that hold customer data.
- **The log is data.** The system prompt says so, and the answer is put into
  the page as text (each piece a `<span>` made with `kit.h`), so nothing a
  log or the model contains is run or rendered.
- **Reading a stream is a loop.** `response.body.getReader()` gives the
  pieces as they come, and an `AbortController` is how Stop ends the
  request. On the server, `pieces` closes the model's stream in `finally`,
  however the answer ends.
