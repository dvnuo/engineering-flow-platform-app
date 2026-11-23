# Flaky tests

Choose a Jenkins job: the app reads the test reports of its last finished
builds and draws test by build as a heatmap, the tests that flip between
passing and failing on top. Beside it: the most flaky tests, the ones that
started to fail lately, and the slowest.

**It shows**

- reading test reports through a connector (`connectors.get`), reduced to
  the four fields the page needs;
- keeping what was fetched in `efp_app.storage` for good: a finished build
  costs one call, once;
- bounded work: so many builds per request, so many rows sent and drawn;
- what to do with a body the Portal cut at 5 MiB (`truncated`);
- a heatmap drawn by hand as SVG that the keyboard can read (the arithmetic
  is in `static/matrix.mjs`, tested with node);
- a page built on the UI kit ([docs/UI_KIT.md](../../docs/UI_KIT.md)) whose
  motion says something: a new answer fills the heatmap in a wave from the
  top left, so the eye follows the builds in their order; pointing at a
  square lights up its row and its build's column together, with the build
  number on top; sorting and filtering move the rows that are already there
  (`kit.animateList`) instead of drawing them anew; the totals count up.

**Turn on in the Portal:** the Jenkins connector (read-only is enough), on
the app's page.

## Try it

```
pip install efp-app-sdk -r examples/flaky-tests/requirements.txt
efp-app dev examples/flaky-tests
```

Open http://127.0.0.1:8000/. The app needs the token in `.env` to reach
Jenkins; without it the page loads and says that the app is not set up. Local
reports are files under `.efp-data/`.

The app makes two kinds of call. To see what your Jenkins answers, try them
in [setup-check](../setup-check/) first (with a build number of your own):

```
/job/team/job/web-app/job/main/api/json?tree=builds[number,result,building,timestamp]{0,15}
/job/team/job/web-app/job/main/128/testReport/api/json?tree=suites[cases[className,name,status,duration]]
```

## Make it yours

1. Copy this directory into your own repository.
2. List your jobs in `jobs.json`: a `name` for the page and the job's `path`
   as its folders and name (`team/web-app/main` for the job at
   `/job/team/job/web-app/job/main`).
3. Change the look if you like: a daisyUI theme on `<html>`
   (`data-theme="business"`, [docs/UI_KIT.md](../../docs/UI_KIT.md#make-it-yours))
   recolours the squares too, since a pass and a failure are the theme's
   success and error.
4. `efp-app check`, push, register the directory as an app in the Portal,
   turn on Jenkins and press Deploy.

## Things to notice

- **Keep what you fetched.** The report of a finished build never changes,
  so it is fetched once, reduced to class, test, status and seconds, and
  saved as `reports/<job>/<build>.json`. The next visit, by anybody, reads it
  from storage: only builds that finished since then cost a call. A build
  that is still running is neither read nor kept.
- **One object per build.** Storage has no transactions, and none are needed
  here: every build has an object of its own, and two visitors who fetch the
  same build at the same moment save the same thing.
- **Too large is an answer.** The Portal cuts a body at 5 MiB, and half a
  report is no report: the build is marked "too large to read" and left out.
  That is also why the call asks for four fields only (`REPORT_TREE`): a full
  report carries the output and the stack trace of every test.
- **No report is an answer too.** A build that recorded no tests answers
  404. That is kept as well, so Jenkins is not asked again on every visit.
  A refusal of the connector (401, 403) is not kept: it may be fixed.
- **Bounded work.** One request reads at most 30 builds, four at a time, and
  sends at most 500 rows; the page draws 100 and says how many it hides. The
  filters and the order of the rows work on what the page already has and
  cost no call.
- **A visitor chooses, never names.** The page sends the key of a job from
  `jobs.json`; the server looks the path up. Every visitor reads Jenkins as
  your connector does, so list only jobs that everybody who may open the app
  may see.
- **Names are data.** Class and test names come from Jenkins and reach the
  page through `textContent`.
- **Colour is not the only sign.** A failed build carries a cross, a skipped
  one is a small square, an absent one a dot. The heatmap is one stop for the
  Tab key: left and right read a row build by build, up and down go from test
  to test, and `/` jumps to the search.
- The reports are those of the JUnit plugin (`suites` of `cases`). Maven
  multi-module and matrix jobs keep theirs under `childReports`: adapt
  `REPORT_TREE` and `compact_report()` for those.
