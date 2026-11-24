# Test case writer

From a user story to test scenarios. Name a Jira issue or paste a story: the
model writes scenarios in Gherkin (Feature, Scenario, Given / When / Then)
for the main path, the negative cases and the edges, or a table of test
cases. The answer is shown while it is written, then in its form: Gherkin
with its keywords and tags marked, or the table as a table, with a count of
main, negative and edge cases. It can be copied or downloaded as a
`.feature` (or `.md`) file. It is a draft for a tester to review, not a
finished test plan.

**It shows**

- reading one Jira issue through a connector (`connectors.get`), with the
  key checked before it becomes a path;
- a streamed model call (`llm.astream`) whose input is cut and whose output
  is capped by what was asked for;
- an answer stream that says what each line is (lines of JSON: a start, the
  pieces, then the finished text or an error), read by the page whatever
  pieces the network cuts it into;
- what the page learns before anybody types (`app.info()`: is the model on,
  is the budget used up, is Jira on), and what a visitor sees while it
  works, when the model is off, when the budget is used up and when there
  is nothing to do;
- what a model wrote shown in its form but always as text: `kit.h` and
  `textContent`, never HTML, for marked Gherkin and for a table;
- a file made in the browser from what is on the page, so the server keeps
  nothing.

**Turn on in the Portal:** the language model and, for reading issues by key,
the Jira connector (read-only is enough), both on the app's page. Without
Jira the app still works with pasted stories.

## Try it

```
pip install efp-app-sdk -r examples/test-case-writer/requirements.txt
efp-app dev examples/test-case-writer
```

Open http://127.0.0.1:8000/. Without a token in `.env` the page loads and
says that the app is not set up yet. With the token, calls are real: they run
on your model provider, count against the app's budget and read your Jira.

## Make it yours

1. Copy this directory into your own repository.
2. Change the two `SYSTEM_PROMPTS` in `main.py` to your house style: your
   tags, your step vocabulary, the language you write tests in. Add the Jira
   fields your stories keep their acceptance criteria in to `FIELDS` and to
   `story_of()`.
3. Change what can be asked for: `MIN_SCENARIOS`, `DEFAULT_SCENARIOS`,
   `MAX_SCENARIOS` and `OUTPUT_TOKENS` at the top of `main.py`. The page reads
   the limits from `api/state`, so the slider follows.
4. For another look, link `/_efp/lib/daisyui@5.8.2/themes.css` and set
   `data-theme` on `<html>` (docs/UI_KIT.md); the colours of the kinds of
   cases are in `static/style.css` (`.tc-main`, `.tc-negative`, `.tc-edge`).
5. `efp-app check`, push, register the directory as an app in the Portal,
   turn on the model and Jira and press Deploy.

## Things to notice

- **The stream says what each line is.** `api/write` answers with lines of
  JSON: `{"start": ...}`, then `{"text": ...}` for every piece, then
  `{"done": true, ...}` with the finished text, or `{"error": ...}`. A
  failure in the middle of an answer cannot change the status any more, and
  a Gherkin file is no place to write an error message into, so it travels
  as a line of its own. `llm.astream()` sends the request when it is
  awaited, so "the budget is used up" or "the model is off" arrives before
  the first line and becomes an ordinary JSON error with its status.
- **The network cuts the stream wherever it likes.** `LineReader` in
  `static/scenarios.js` keeps the half of a line until the rest arrives; a
  half line at the very end is an error, not text. That file has no DOM and
  no network in it, so the tests run it under node.
- **Only a key becomes a path.** What is typed must match `ISSUE_KEY` before
  it is put into `/rest/api/2/issue/<KEY>`, and the same check decides
  whether the key may name the downloaded file. The page checks with the
  same rule first, only to spare a request the server would refuse.
- **The connector is the owner's account.** Every visitor reads Jira as your
  connector does, so they can read any issue that account can read by typing
  its key. Think about who may open the app. Jira's own "no" (404 for an
  unknown issue) arrives as `response.status` and becomes a sentence in
  `refusal()`.
- **What one use costs.** You pay for every visitor. Reading an issue is one
  Jira call and no model call. Writing sends the first 6,000 characters of
  the story (about 1,500 tokens of English, more for Chinese) and some 200
  tokens of instructions; the answer is capped at 300 tokens plus 170 for
  each scenario asked for (110 for a table row), so at most 1,320 output
  tokens for six scenarios and 2,340 for twelve. An empty story is answered
  without a model call. The limits are the constants at the top of `main.py`
  (`MAX_STORY_CHARS`, `OUTPUT_TOKENS`); the daily budget on the app's page
  decides what a day can cost.
- **The story is data, and so is the answer.** The system prompt tells the
  model not to follow instructions inside the story. The page shows what the
  model wrote in its form, but builds every element with `kit.h` and
  `textContent`: a `<b>` in a table cell stays the text `<b>`, and only a
  `<br>` inside a cell, which models write for a line break, becomes one. A
  Markdown fence around the answer is removed and the scenarios are counted,
  because what was asked for is not always what was written.
- **Nothing is kept.** The download is made in the browser from the
  finished text (`Blob`, `download=`); the server stores no story and no
  answer, and a reload starts again from an empty page.
- **When the model cannot be used, the page says so first.** `api/state`
  asks the Portal on every page load: the owner may have turned the model
  on since. Write is off while the model is, and reading an issue still
  works.
- The issue is read from `/rest/api/2/issue/<KEY>`, which Jira Server, Data
  Center and Cloud offer with the description as text. Jira Cloud's
  `/rest/api/3/` sends the description as a document tree instead.
