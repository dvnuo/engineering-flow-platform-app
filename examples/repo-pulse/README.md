# Repo pulse

Type a repository as `owner/name`: the app draws a year of commits as a
calendar, the weekdays and hours they are made in, the top contributors, and
the open pull requests by how long they have been waiting, the ten oldest
with links.

**It shows**

- reading GitHub through a connector (`connectors.get`), four well-known
  endpoints;
- an answer that is neither a yes nor a no: GitHub's 202, "still computing";
- text a visitor types that becomes a connector path and a storage key;
- answers kept in `efp_app.storage` per repository and chart for an hour,
  with a "Refresh" for signed-in members;
- Flask under a path prefix (`url_for()` everywhere) and charts drawn by hand
  as SVG.

**Turn on in the Portal:** the GitHub connector (read-only is enough), on the
app's page.

## Try it

```
pip install efp-app-sdk -r examples/repo-pulse/requirements.txt
efp-app dev examples/repo-pulse
```

Open http://127.0.0.1:8000/. The app needs the token in `.env` to reach
GitHub; without it the page loads and says that the app is not set up. Local
answers are files under `.efp-data/`.

The app calls four endpoints. To see what your GitHub answers, try them in
[setup-check](../setup-check/) first:

```
/repos/acme/app/pulls?state=open&per_page=100&sort=created&direction=asc
/repos/acme/app/contributors?per_page=20
/repos/acme/app/stats/commit_activity
/repos/acme/app/stats/punch_card
```

## Make it yours

1. Copy this directory into your own repository.
2. Set `OWNERS` at the top of `app.py` to the organisations whose
   repositories the app may show, and adapt `AGE_BUCKETS` to what "old" means
   for a pull request in your team.
3. `efp-app check`, push, register the directory as an app in the Portal,
   turn on GitHub and press Deploy.

## Things to notice

- **202 is not an error.** GitHub computes statistics in the background and
  answers 202 with an empty body until they are ready. The status arrives as
  `response.status`: the chart says "GitHub is still computing this, try
  again in a moment", the other charts are drawn, and nothing is kept for
  that chart. There is no timer that asks again: the visitor does, with a
  button, and that costs one call because the rest is in storage.
- **404 means "not for this connector".** Every visitor reads GitHub as your
  connector does. A repository that account cannot see answers 404, and the
  page says so. It also means every visitor can look at every repository the
  connector can read: `OWNERS` narrows that, and so does the audience of the
  app.
- **A chart can fail alone.** GitHub answers 403 for a contributor list it
  finds too large, and for a used-up rate limit. That chart says so; the
  others are drawn, and nothing is kept for it.
- **Typed text becomes a path.** `owner/name` ends up in
  `/repos/<owner>/<name>/...` and, in lower case, in the storage key. It is
  matched against a strict pattern first; the SDK and the Portal additionally
  refuse a path that could leave the connector's address.
- **Kept for an hour, one object per chart.** The statistics are expensive
  for GitHub. Each chart of a repository is saved with the time it was asked
  (`pulse/<owner>/<name>/<chart>.json`) and reused for an hour, whoever
  looks; the page shows "last updated". Because each chart has an object of
  its own, a chart that arrives later never overwrites the others.
- **Refresh has a floor.** A signed-in member can ask again, but not more
  than once a minute per chart; the server checks both, not the page.
- **Ages are counted when the page is drawn.** The kept answer holds when
  each pull request was opened; the columns are built from that on every
  request, so an answer that is fifty minutes old still shows the right ages.
- **A hundred is not all.** The app asks for one page of open pull requests,
  the oldest first, and says so when the page is full instead of calling it
  the whole list.
- **Titles and names are data.** They reach the page through `textContent`;
  a link is used only when it starts with `https://` or `http://`.
- **Every chart has its numbers.** The arrow keys read a chart mark by mark,
  and "The numbers" under each one holds the same values as a table.
- GitHub Enterprise works the same way: the connector's address is then your
  server's API address. Hours of the punch card are those of each commit's
  own time zone; days of the calendar are counted in UTC.
