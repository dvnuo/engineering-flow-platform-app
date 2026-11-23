# Test case writer

From a user story to test scenarios. Name a Jira issue or paste a story: the
model writes scenarios in Gherkin (Feature, Scenario, Given / When / Then)
for the main path, the negative cases and the edges, or a plain table of test
cases. The answer streams into the page and can be downloaded as a `.feature`
file. It is a draft for a tester to review, not a finished test plan.

**It shows**

- a Gradio page laid out with `gr.Blocks`, with an answer that streams (a
  generator function);
- the visitor inside an event function (`gr.Request` and `get_visitor`);
- reading one Jira issue through a connector (`connectors.get`), with the
  key checked before it becomes a path;
- a streamed model call (`llm.stream`) whose input is cut and whose output is
  capped by what was asked for;
- what a visitor sees while it works, when the model is off, when the budget
  is used up and when there is nothing to do;
- the look of the EFP kit through Gradio's own theme
  (`gr.themes.Soft(primary_hue="blue")` with the system's fonts, see
  [docs/UI_KIT.md](../../docs/UI_KIT.md#streamlit-and-gradio)). Gradio draws
  the page, so the kit's style sheets and its motion do not apply here.

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
2. Change the two `SYSTEM_PROMPTS` in `app.py` to your house style: your
   tags, your step vocabulary, the language you write tests in. Add the Jira
   fields your stories keep their acceptance criteria in to `FIELDS` and to
   `story_of()`.
3. Change the look in `THEME` in `app.py`: another hue, or another of
   Gradio's themes (`gr.themes.Base`, `Glass`, `Monochrome`, ...). The kit's
   `data-theme` belongs to the HTML examples; a Gradio page takes its theme
   from `launch(theme=...)`. Keep the fonts the system's own: a theme that
   names a web font makes every visitor's browser fetch it from another host.
4. `efp-app check`, push, register the directory as an app in the Portal,
   turn on the model and Jira and press Deploy.

## Things to notice

- **`demo.launch()` names no address.** The host passes the address, the
  port and the path prefix in `GRADIO_SERVER_NAME`, `GRADIO_SERVER_PORT` and
  `GRADIO_ROOT_PATH`, and `launch()` reads them. `launch(server_port=7860)`,
  `launch(root_path="/")` or `share=True` would replace what the host set,
  and the app would not answer under `/<slug>/`. The one argument it gets is
  the theme, which Gradio 6 takes in `launch()` rather than in `gr.Blocks()`
  (hence `gradio>=6` in `requirements.txt`). It stays inside
  `if __name__ == "__main__":`, so the file can be imported by a test.
- **`gr.Blocks(..., analytics_enabled=False)`.** Gradio otherwise reports
  every start to its makers, and with analytics on its `launch()` compares
  the theme with its built-in ones and fails on fonts given as strings
  ("'str' object has no attribute 'name'", Gradio 6.32). The host and
  `efp-app dev` set `GRADIO_ANALYTICS_ENABLED=False` too; the argument
  keeps `python app.py` working.
- **A generator is the stream.** `write_scenarios` yields the text so far, a
  status line and the download button; every yield replaces what the three
  outputs show. `llm.stream()` sends the request when it is called, so "the
  budget is used up" or "the model is off" arrives before the first piece
  and becomes the status line.
- **The visitor comes with the event.** An event function that has a
  `gr.Request` parameter gets the request of that click;
  `get_visitor(request)` reads the visitor the gateway signed into it, and
  `visitor=` on the Jira and model calls lets the Portal's usage page show
  who caused them. There is no global "current visitor".
- **Only a key becomes a path.** What is typed must match `ISSUE_KEY` before
  it is put into `/rest/api/2/issue/<KEY>`, and the same check decides
  whether the key may name the downloaded file.
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
  without a model call. The limits are the constants at the top of `app.py`
  (`MAX_STORY_CHARS`, `OUTPUT_TOKENS`); the daily budget on the app's page
  decides what a day can cost.
- **The story is data, and so is the answer.** The system prompt tells the
  model not to follow instructions inside the story. The answer goes into a
  text box, not into `gr.Markdown`, so nothing the model wrote is rendered;
  a Markdown fence around it is removed and the scenarios are counted,
  because what was asked for is not always what was written.
- **The download is a temporary file.** Gradio hands out files, so the
  finished text is written to the temporary directory, which the host
  empties on every restart. Nothing else is kept: no story and no answer is
  stored by this app.
- The issue is read from `/rest/api/2/issue/<KEY>`, which Jira Server, Data
  Center and Cloud offer with the description as text. Jira Cloud's
  `/rest/api/3/` sends the description as a document tree instead.
