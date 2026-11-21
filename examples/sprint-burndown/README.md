# Sprint burndown

Type a Jira project key and choose a board and a sprint: the app draws the
burndown (the ideal line and what was really left), the issues by status, the
load of each assignee, and lists what was created after the sprint started.

**It shows**

- a Streamlit dashboard: one Python file, Streamlit's own charts fed with
  pandas data frames;
- the Jira Agile API through a connector (`connectors.get`): boards, sprints,
  the issues of a sprint with only the fields the charts need;
- `st.cache_data` with a time to live, so that a rerun does not ask Jira
  again;
- arithmetic kept in plain functions that are tested without Streamlit.

**Turn on in the Portal:** the Jira connector (read-only is enough), on the
app's page.

## Try it

```
pip install efp-app-sdk -r examples/sprint-burndown/requirements.txt
efp-app dev examples/sprint-burndown
```

Open http://127.0.0.1:8000/. The app needs the token in `.env` to reach
Jira; without it the page loads and says that the app is not set up.

The app calls three endpoints of the Agile API. To see what your Jira
answers, try them in [setup-check](../setup-check/) first (with ids of your
own):

```
/rest/agile/1.0/board?projectKeyOrId=ABC
/rest/agile/1.0/board/84/sprint?state=active,closed&startAt=0
/rest/agile/1.0/sprint/37/issue?fields=summary,status,assignee,issuetype,created,resolutiondate,customfield_10016&startAt=0&maxResults=100
```

## Make it yours

1. Copy this directory into your own repository.
2. Set `STORY_POINTS_FIELD` at the top of `app.py` to the field that holds
   story points in your Jira. It differs between installations:
   `/rest/api/2/field` lists every field with its id (look for "Story
   Points" or "Story point estimate"). While no issue has a value in that
   field, the app counts issues instead and says so.
3. `efp-app check`, push, register the directory as an app in the Portal,
   turn on Jira and press Deploy.

## Things to notice

- **A rerun must not ask again.** Streamlit runs the whole script on every
  interaction: choosing another sprint runs it from the top. The three
  functions that ask Jira are wrapped in `st.cache_data(ttl=300)`: for five
  minutes the same question gets the kept answer, for every visitor.
  Changing the sprint costs one call (that sprint's issues), changing it
  back costs none.
- **The visitor is not part of the key.** `visitor=` goes with every
  connector call so that the Portal's usage page shows who caused it. In a
  cached function the parameter is named `_visitor`: Streamlit leaves
  arguments that start with an underscore out of the cache key, so visitors
  share the kept answers.
- **A refusal is not kept.** Jira's "no" (an unknown project, a Kanban board
  that has no sprints, a connector it will not let in) arrives as a status;
  `ask_jira()` raises it, and `st.cache_data` keeps no exceptions, so the
  next run asks again.
- **Never "my sprint".** Every visitor reads Jira as your connector does. The
  app therefore asks for a project key, never for the current user's boards,
  and names assignees as Jira names them. Think about who may open the app
  before you turn the connector on.
- **The key is checked before it is used.** A project key must look like one
  before it becomes part of a query; board and sprint ids come from Jira's
  own answers.
- **Names are data.** Sprint names, goals and Jira's error messages are shown
  with their Markdown characters escaped; summaries are shown in a table,
  which draws text as text.
- **What the lines mean.** The burndown is built from two dates of every
  issue: it is open from the start of the sprint (or from its creation, when
  it was created later) until its resolution date. That needs one call and
  no issue history. The price: an issue that existed before and was pulled
  into the running sprint counts from the start, and "added after the sprint
  started" lists only issues created after it. For the exact story, read
  each issue's changelog (`expand=changelog`), which costs far more.
- **Weekends.** The ideal line falls evenly over every day of the sprint.
  Skip the days your team does not work in `burndown()` if you prefer that.
- The Agile API is the same in Jira Cloud, Server and Data Center. Boards
  that are not Scrum boards have no sprints: Jira says so, and the page
  shows its words.
