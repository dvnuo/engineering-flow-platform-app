# Release notes

Name a GitHub repository and two tags (or a tag and a branch): the app reads
the commits between them and the model writes a first draft of the release
notes, grouped into features, fixes and the rest.

**It shows**

- reading GitHub through a connector (`connectors.get`);
- a model call (`llm.complete`) whose answer is kept in `efp_app.storage`, so
  the same range is paid for once;
- turning text a visitor types into part of a connector path safely;
- a page built on the UI kit ([docs/UI_KIT.md](../../docs/UI_KIT.md)): the
  repository's newest two tags offered as one click, a skeleton while
  GitHub and the model work, and a draft that rises in line by line, drawn
  from its Markdown (headings with icons, pull request numbers as links)
  without any HTML from the model; a kept draft says "nothing spent".

**Turn on in the Portal:** the GitHub connector (read-only is enough) and the
language model, both on the app's page.

## Try it

```
pip install efp-app-sdk -r examples/release-notes/requirements.txt
efp-app dev examples/release-notes
```

Open http://127.0.0.1:8000/. The app needs the token in `.env`; without it
the page loads and the request answers with a message. Ask for the same range
twice: the second answer says it was kept, and no model call was made. Local
drafts are files under `.efp-data/`.

## Make it yours

1. Copy this directory into your own repository.
2. Change `SYSTEM_PROMPT` in `main.py` to your house style: your headings,
   your language, how you name contributors. When you rename the headings,
   give them icons in `HEADINGS` at the top of `static/app.js`. For another
   look, set `data-theme="emerald"` (or any daisyUI theme) on `<html>` and
   link `/_efp/lib/daisyui@5.8.2/themes.css`
   ([docs/UI_KIT.md](../../docs/UI_KIT.md#make-it-yours)).
3. `efp-app check`, push, register the directory as an app in the Portal,
   turn on GitHub and the model and press Deploy.

## Things to notice

- **Keep what you paid for.** Every model call runs on the owner's provider
  and budget. The draft is saved under a key made from the two commits of the
  range (not the two labels, so a branch that moved on gets a new draft), and
  the next visitor who asks for it gets the saved one. "Write again" skips
  the saved draft on purpose.
- **Typed text becomes a path.** The repository and the two names end up in
  `/repos/<owner>/<name>/compare/<base>...<head>`. They are matched against
  strict patterns first; the SDK and the Portal additionally refuse a path
  that could leave the connector's address.
- **GitHub's "no" is a status.** A repository the connector cannot see is a
  `ConnectorResponse` with `status == 404`; `refusal()` turns it into a
  sentence. Every visitor reads GitHub as your connector does.
- **Large ranges.** GitHub lists at most 250 commits of a range, and the
  Portal cuts an answer above 5 MiB. The app sends the newest 150 subjects to
  the model and says so when a range is too large to compare.
- GitHub Enterprise works the same way: the connector's address is then your
  server's API address.
- **The model's Markdown is drawn, not parsed.** `draw` in `static/app.js`
  knows the few shapes release notes use and builds each one with `kit.h`;
  whatever else the model writes stays text. Copy gives the Markdown as it
  came.
