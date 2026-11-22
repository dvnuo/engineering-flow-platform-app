# Build health

One row per Jenkins job: its last 30 builds as a strip of squares, how often
it succeeds, how long it takes, and whether it is broken or flaky right now.
A line of totals sums up the whole list.

**It shows**

- reading Jenkins through a connector (`connectors.get`), asking only for the
  fields the page draws;
- keeping an answer in `efp_app.storage` for every visitor to share, with the
  time it was asked;
- a "Refresh" that only a signed-in member may use (`require_visitor`), and
  not more than once a minute;
- the strips of builds and the sparklines drawn by hand as SVG (the
  arithmetic is in `static/charts.mjs`, tested with node), and two overview
  charts with ECharts through `kit.chart`, in the theme's colours: nothing
  from another host (the libraries are the gateway's own, see
  [docs/UI_KIT.md](../../docs/UI_KIT.md));
- a dashboard that looks finished from its first paint: skeletons where
  the numbers will be, tiles that count up, squares that pop in one after
  the other.

**Turn on in the Portal:** the Jenkins connector (read-only is enough), on
the app's page.

## Try it

```
pip install efp-app-sdk -r examples/build-health/requirements.txt
efp-app dev examples/build-health
```

Open http://127.0.0.1:8000/. The app needs the token in `.env` to reach
Jenkins; without it the page loads and says that the app is not set up. Local
answers are files under `.efp-data/`.

The app makes one kind of call, once per job of `jobs.json`. To see what your
Jenkins answers, try it in [setup-check](../setup-check/) first:

```
/job/team/job/web-app/job/main/api/json?tree=builds[number,result,duration,timestamp,building,url]{0,30}
```

## Make it yours

1. Copy this directory into your own repository.
2. List your jobs in `jobs.json`: a `name` for the page and the job's `path`
   as its folders and name (`team/web-app/main` for the job at
   `/job/team/job/web-app/job/main`). At most 24 jobs.
3. Change the look if you like: a daisyUI theme
   ([docs/UI_KIT.md](../../docs/UI_KIT.md#make-it-yours)) changes the colours
   of the results too, since success, unstable and failure are the theme's
   success, warning and error.
4. `efp-app check`, push, register the directory as an app in the Portal,
   turn on Jenkins and press Deploy.

## Things to notice

- **Many visitors, one call.** A dashboard is opened by many people who all
  look at the same builds. What Jenkins says about a job is saved in
  `builds/<job>.json` with the time it was asked and reused for five minutes
  (`FRESH_SECONDS`), whoever looks. Without that, every open tab would be a
  burst of calls on a server that has builds to run.
- **Refresh has a floor.** The button asks again, but a job that was asked
  less than a minute ago is not asked again, and only a signed-in member can
  press it. A cache that anybody can empty at will is no cache.
- **One object per job.** Storage has no transactions: two saves of one
  object, and one is lost. Each job has an object of its own, so asking about
  one job never overwrites what is known about another.
- **Ask for what you draw.** Jenkins sends every field of every build unless
  it is told otherwise. `tree=builds[number,result,...]{0,30}` keeps the
  answer to a few kilobytes; a body over 5 MiB would be cut by the Portal.
- **A job's trouble stays in its row.** A job Jenkins does not know (404) or
  will not show to the connector (401, 403) is a status, not an exception:
  its row says so and the other rows are drawn as usual. Only a failure of
  the call itself (the connector is off, the Portal cannot be reached) fails
  the page, with a sentence.
- **A visitor never names a path.** The paths come from `jobs.json` and are
  checked when the app starts. Every visitor reads Jenkins as your connector
  does, so list only jobs that everybody who may open the app may see.
- **Colour is not the only sign.** A failed build carries a cross, an
  unstable one a stroke, an aborted one is hollow and a running one is a dot;
  every square can be reached with the keyboard and says what it is.
- Links to builds are built from the connector's address and the checked
  path. A job name with other characters than letters, digits, `.`, `_` and
  `-` (a space, or the `%2F` of a multibranch job) is refused by
  `read_jobs()`: widen `SEGMENT` and quote the name when you need one.
