# CSV explorer

Upload a CSV file and look into it: a preview, a summary of every column
(type, missing and distinct values, minimum, maximum, mean), a filter, a
chart builder (bar, line, scatter, histogram; a group-by with count, sum or
mean) and a download of the filtered or grouped rows. When the owner has the
language model switched on, "Ask" answers questions about the data in words.

**It shows**

- a Streamlit app on an upload, with limits the script enforces itself
  (size, rows, columns) and a delimiter and an encoding that are detected,
  with a fallback and a way to choose;
- `st.cache_data`, so that a rerun does not parse the file again;
- data that is deliberately not stored: the file lives in the visitor's
  session;
- a feature that is offered only when the app has the model
  (`app.info().llm.enabled`);
- a streamed model call (`llm.stream`) that gets the column summary and at
  most 20 sample rows, never the whole file;
- logic in plain functions (`tabular.py`) next to a thin page (`app.py`).

**Turn on in the Portal:** nothing. The language model is optional: turn it
on, on the app's page, to get the "Ask" tab.

## Try it

```
pip install efp-app-sdk -r examples/csv-explorer/requirements.txt
efp-app dev examples/csv-explorer
```

Open http://127.0.0.1:8000/ and tick the sample, or upload a file of your
own. Without a token in `.env` everything works except "Ask", and the page
says at its foot that the app is not set up yet. With the token and the
model on, a question is a real call: it runs on your model provider and
counts against the app's budget.

## Make it yours

1. Copy this directory into your own repository.
2. Set the limits at the top of `tabular.py` (and `maxUploadSize` in
   `.streamlit/config.toml` with `MAX_FILE_BYTES`) to what your files need,
   and replace `sample.csv` with a file of your own team. Add the encodings
   your spreadsheets write to `ENCODINGS`.
3. `efp-app check`, push, register the directory as an app in the Portal and
   press Deploy. Turn the model on if visitors should be able to ask.

## Things to notice

- **Nothing is stored, and the page says so.** The upload lives in the
  visitor's session, which is in the app's memory: it is gone when the tab
  is closed and when the app is stopped, and the host stops an idle app.
  For a file nobody asked the app to keep that is the right place. What must
  survive belongs in `efp_app.storage`.
- **The script enforces its own limits.** 5 MB, 50,000 rows, 60 columns,
  checked in `tabular.parse` whatever reaches it. `.streamlit/config.toml`
  sets Streamlit's own upload limit to the same 5 MB, which keeps a larger
  file from arriving in memory at all; the script still checks, because a
  setting can be changed without anybody reading the code.
- **A rerun must not redo the work.** Streamlit runs the script from the top
  on every click. `read_table` is wrapped in `st.cache_data`, so the file is
  parsed and summarised once. The cache is bounded (four files, fifteen
  minutes): it lives in the app's memory, which every visitor shares and the
  host caps (1 GiB by default).
- **A rerun must not pay again either.** The answer of the model is kept in
  `st.session_state` and shown again on the next rerun. The model is called
  only when "Ask" is pressed, never because a chart was changed.
- **What one use costs.** Looking costs nothing. A question sends the
  summary of at most 40 columns and at most 20 sample rows: about 1,800
  characters (some 700 tokens) for the sample file with its nine columns,
  and never more than about 12,000 characters for the widest table, plus
  about 130 tokens of instructions. The answer is capped at 500 output
  tokens. The size of the file does not matter: a question about 50,000
  rows costs what a question about 50 costs.
  The price is that the model sees a sample: it can describe the data but
  not count it, and the prompt tells it to say so and to point at the
  group-by.
- **The model is optional.** `model_state()` asks the Portal whether the
  model is on (at most every five minutes) and the "Ask" tab is offered only
  then. Without it the page says in one line why, and everything else works.
- **The answer is text.** It is shown with `st.text`, not as Markdown: an
  answer written from somebody's file must not become a link or an image in
  the page, and nothing in it is ever run. The file is data for the model
  too: the system prompt says not to follow instructions inside it.
- **Detected, with a way out.** A byte order mark decides the encoding;
  otherwise UTF-8 is tried, then GB18030, then the Western code page. Big5
  and GB18030 cannot be told apart by their bytes, so "Reading options" lets
  the visitor choose, and the page says how the file was read.
- **Names from the file never reach the chart library as field names.** The
  charts get their data as `group` and `value` and the column names as axis
  labels, so a column called `a.b` or `x:y` cannot confuse a chart.
- **No pandas.** It comes with Streamlit and may be imported, but for
  50,000 rows the standard library is enough, and `tabular.py` can be tested
  without Streamlit being installed.
