# Ask the wiki

Type a question: the app searches Confluence for pages that match, hands the
best ones to the model and shows an answer that names its sources, with links
to the pages.

**It shows**

- a Streamlit app: one Python file, no page or script to write;
- searching Confluence through a connector (`connectors.get`);
- a streamed model call (`llm.stream` with `st.write_stream`) that may only
  use what was found;
- skipping the model call when nothing was found.

**Turn on in the Portal:** the Confluence connector (read-only is enough) and
the language model, both on the app's page.

## Try it

```
pip install efp-app-sdk -r examples/confluence-ask/requirements.txt
efp-app dev examples/confluence-ask
```

Open http://127.0.0.1:8000/. The app needs the token in `.env` to reach
Confluence and the model.

## Make it yours

1. Copy this directory into your own repository and put your Portal username
   in `"owners"` of `efp-app.json`.
2. Set `SPACES` at the top of `app.py` to the spaces the app should answer
   from, and tune `MAX_PAGES` and `MAX_CHARS_PER_PAGE`: more text means
   better answers and a higher cost per question.
3. `efp-app check`, push, register the directory as an app in the Portal,
   turn on Confluence and the model and press Deploy.

## Things to notice

- **Every visitor reads the wiki as your connector does.** A page that
  account can read can end up in an answer for anybody who may open the app.
  `SPACES` is the simple way to keep answers to what is meant for everybody.
- **The answer is tied to its sources.** The system prompt allows nothing
  but the numbered sources and asks for a number after each statement; the
  page lists the sources with links. An answer nobody can check is a rumour.
- **Pages are data.** What a wiki page says goes to the model as a source,
  not as an instruction, and the prompt says so. Titles are escaped before
  they are shown as Markdown.
- **Only words go into the search.** The question is reduced to its letters
  and digits before it becomes part of the CQL query.
- **One search, one call.** This is the smallest useful version. Better
  retrieval (asking the model for search terms first, reading more pages in
  pieces) costs more calls: measure before you add them.
- The search uses `/rest/api/content/search`, relative to the connector's
  address. For Confluence Cloud that address ends in `/wiki`.
