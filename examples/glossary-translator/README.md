# Glossary translator

Translation between English, Simplified Chinese and Traditional Chinese as
written in Hong Kong that keeps to the team's terms: "deployment" is the same
word in every runbook. The owner of the app keeps a glossary of term pairs;
the app puts the entries a text needs into the prompt, streams the
translation and then shows which glossary terms the model really used.
Markdown is kept, and code, identifiers, addresses and file paths are left
untranslated.

**It shows**

- a glossary in `efp_app.storage` that only the app's owner may change: an
  owner-only API (`owner_only`), one object, one writer, with a version;
- import and export as CSV without any upload library;
- a prompt that carries only the glossary entries that occur in the text
  (`applicable`);
- a streamed model call (`llm.astream`) whose answer travels as lines of
  JSON, so the page can tell text from "which terms" and from an error;
- checking afterwards whether the model did what it was told (`missed`);
- a page built on the UI kit ([docs/UI_KIT.md](../../docs/UI_KIT.md)): the
  text and its translation side by side; the glossary terms marked in the
  text as you type (a mirror behind the text area, built as elements, never
  as HTML from data), so you see what will be sent along; the translation
  arriving piece by piece behind a caret; then its terms lighting up one
  after the other and each term's chip turning to "used" or "missing" (the
  check, made visible); the two languages trading places with a glide when
  they are swapped, and the finished translation flying over to be
  translated back; rows of the glossary opening into a form where they
  stand and folding away when removed.

**Turn on in the Portal:** the language model, on the app's page.

## Try it

```
pip install efp-app-sdk -r examples/glossary-translator/requirements.txt
efp-app dev examples/glossary-translator
```

Open http://127.0.0.1:8000/. Without a token in `.env` the page loads, shows
the sample glossary and says that the app is not set up yet. With the token
you are the owner: the glossary can be changed, and translations are real
calls that run on your model provider and count against the app's budget.
Start it with `EFP_DEV_VISITOR=somebody-else` to see what every other visitor
gets. The local glossary is a file under `.efp-data/`.

## Make it yours

1. Copy this directory into your own repository.
2. Put your own languages into `LANGUAGES` and `WRITTEN_AS` in `main.py`,
   and your starting terms into `glossary.sample.json` (ASCII: other scripts
   as `\uXXXX` escapes). Adapt `system_prompt()` to what your texts hold:
   ticket numbers, product names that stay as they are. For another look,
   set `data-theme="nord"` (or any daisyUI theme) on `<html>`
   ([docs/UI_KIT.md](../../docs/UI_KIT.md#make-it-yours)); the marks of the
   terms follow the theme's colours.
3. `efp-app check`, push, register the directory as an app in the Portal,
   turn the model on and press Deploy. Then open the app and save your
   glossary, typed in or imported from a CSV file.

## Things to notice

- **Only the terms a text needs are sent.** A glossary of 500 entries is
  some 5,000 tokens. Sent with every call it would cost more than most of
  the texts, and it would give the model hundreds of rules that do not
  apply. `applicable()` finds the entries whose source term occurs in the
  prose of the text (not inside code or an address) and sends at most 60.
- **What one use costs.** You pay for every visitor. A text has at most
  3,000 characters: about 750 tokens of English, about 3,000 of Chinese,
  plus some 150 tokens of instructions and about 10 for each glossary term.
  The answer is capped in proportion to the text (`output_cap`): 300 tokens
  plus one per character of an English text, two per character of a Chinese
  one, and never more than 6,000. A text that is too long is refused rather
  than cut, and an empty one is answered without a model call.
- **Told is not done.** A model follows a glossary most of the time. When
  the answer is complete the app looks for each term's translation in it and
  the page marks the terms that are missing, so the reader knows where to
  look. The check is plain text matching: a term the model rightly inflected
  can be marked too.
- **A stream that carries more than text.** The answer is lines of JSON:
  the terms first, then the pieces of text, then how it ended. A failure in
  the middle arrives as a line of its own instead of being written into the
  translation, and a refusal (the budget is used up, the model is off) still
  comes before the stream, as a normal JSON error.
- **One object, one writer.** The glossary is a single object,
  `glossary.json`. Storage has no transactions, so an object that several
  people save would lose changes without a trace. Here only the owner
  writes: `owner_only` compares the visitor the gateway signed in with the
  owner the Portal names, on the server. Hiding the editor in the page is
  only a convenience. The version in the object catches the one case that is
  left, the owner's own second tab. For a glossary that many people edit,
  give each entry an object of its own, as `team-poll` does with votes.
- **Importing does not save.** A CSV file becomes rows in the editor, with a
  list of the rows that were skipped and why; the owner looks, then saves.
  The file is posted as it is and read with the standard library, so no
  upload package is needed. Only UTF-8 is accepted: a spreadsheet's plain
  "CSV" is in the code page of the machine that wrote it, and guessing
  between the Chinese ones would fill the glossary with wrong characters.
  The export starts with a byte order mark, which makes spreadsheets read it
  as UTF-8.
- **The text is data.** The system prompt tells the model to translate
  instructions that appear in the text, not to follow them, and the answer
  is put into the page with `textContent`.
- **Say which Chinese.** "Traditional Chinese" alone gets Taiwan's wording as
  often as Hong Kong's, so the prompt names the place. The page sets `lang`
  on the text and on the translation: browsers draw the same character
  differently for `zh-Hans` and `zh-Hant-HK`.
