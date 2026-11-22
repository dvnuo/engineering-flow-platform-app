# Jira digest

Type a project key and a number of days: the app lists the issues that were
updated in that time and, on request, lets the model write a short digest of
them for the team.

**It shows**

- reading another system through a connector (`connectors.get`), and what its
  own errors look like;
- a plain model call (`llm.complete`) that is skipped when there is nothing
  to sum up;
- how to do "my issues" when the connector is not the visitor;
- Flask under a path prefix: every URL of the app comes from `url_for()`;
  the shared libraries at `/_efp/lib/` are the one fixed path;
- a page built on the UI kit ([docs/UI_KIT.md](../../docs/UI_KIT.md)) whose
  motion says something: the issues of a project rise in one after the
  other, and a new search of the same project moves the ones that stay and
  brings in the others (`kit.animateList`), so the eye sees what changed;
  the bar of statuses grows; while the model writes, a line of colour runs
  along the digest's card, and its text arrives line by line.

**Turn on in the Portal:** the Jira connector (read-only is enough) and the
language model, both on the app's page.

## Try it

```
pip install efp-app-sdk -r examples/jira-digest/requirements.txt
efp-app dev examples/jira-digest
```

Open http://127.0.0.1:8000/. The app needs the token in `.env` to reach
Jira; without it the page loads and the search answers with a message.

## Make it yours

1. Copy this directory into your own repository.
2. Adapt `jql()` and `FIELDS` in `app.py` to what your team looks at: a
   component, a sprint, a status. Adapt `SYSTEM_PROMPT` to the digest you
   want to read.
3. Change the look if you like: a daisyUI theme on `<html>`
   (`data-theme="corporate"`, [docs/UI_KIT.md](../../docs/UI_KIT.md#make-it-yours)).
   The colours of the statuses are the theme's: add your Jira's status
   names to `STATUS_TONES` in `static/app.js`.
4. `efp-app check`, push, register the directory as an app in the Portal,
   turn on Jira and the model and press Deploy.

## Things to notice

- **The connector is the owner's account.** Every visitor reads Jira as your
  connector does: they see what that account may see, and JQL's
  `currentUser()` is that account for all of them. "Only mine" therefore puts
  the visitor's Portal username into the search as a name
  (`assignee = "bob"`), and the field stays editable for teams whose Jira
  names differ. Think about who may open the app before you turn a connector
  on.
- **Jira's "no" is a status, not an exception.** An unknown project is a
  `ConnectorResponse` with `status == 400` and Jira's reason in the body
  (`refusal()` turns it into a sentence). The SDK raises only when the call
  itself could not be made: the connector is off, the Portal is unreachable.
- **What the visitor types never reaches JQL unchecked.** The project key and
  the name must match a strict pattern before they are quoted into the
  search.
- **The digest searches again on the server.** The page does not post the
  list it shows; what goes to the model is what Jira says, not what a browser
  claims. With no issues there is no model call and nothing is spent.
- **What the model wrote stays text.** The page splits the digest into
  lines and makes a line that starts with `-` or `*` an item of a list; every
  line goes in through `textContent`. Nothing the model or Jira wrote is
  parsed as HTML or Markdown, so an issue whose summary holds markup shows
  that markup as it is.
- The search uses `/rest/api/2/search`, which Jira Server and Data Center
  offer. Jira Cloud names it `/rest/api/3/search/jql`.
