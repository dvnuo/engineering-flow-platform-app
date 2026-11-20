// The page of the board.
//
// Every answer of the backend is the whole board as this visitor may see it,
// and the page draws exactly that. Whose card is whose is not in it (see
// main.py), so there is nothing here to hide. The page is drawn again after
// each action and on "Refresh"; nothing asks the server on a timer. To see
// what the others wrote in the meantime, press Refresh.

import * as api from "./api.js";
import { byColumn, fileName, plural, toMarkdown, totals } from "./board.js";

const $ = (id) => document.getElementById(id);
const columns = $("columns");
const status = $("status");
const confirmDialog = $("confirm");

let view = null; // the board as the server last sent it
let pastId = null; // the past board that is shown, or null for the current one
let editing = null; // {id, text, column}: the card whose text is being changed
const drafts = new Map(); // column id -> a card that is being typed

function say(text, isProblem) {
  status.textContent = text || "";
  status.classList.toggle("problem", Boolean(isProblem));
}

function make(tag, className, text) {
  const made = document.createElement(tag);
  if (className) {
    made.className = className;
  }
  if (text !== undefined) {
    made.textContent = text;
  }
  return made;
}

function button(label, onClick, className) {
  const made = make("button", className, label);
  made.type = "button";
  made.addEventListener("click", onClick);
  return made;
}

// A question before something that cannot be taken back.
function ask(title, text, ok, danger) {
  $("confirm-title").textContent = title;
  $("confirm-text").textContent = text;
  $("confirm-ok").textContent = ok;
  $("confirm-ok").classList.toggle("danger", Boolean(danger));
  confirmDialog.returnValue = "";
  confirmDialog.showModal();
  return new Promise((resolve) => {
    confirmDialog.addEventListener("close", () => resolve(confirmDialog.returnValue === "ok"), { once: true });
  });
}

$("confirm-cancel").addEventListener("click", () => confirmDialog.close("cancel"));

// --- Talking to the backend ------------------------------------------------------------

// Run one action. Its answer is the new board; a refusal is shown as the
// sentence it came with, and the board is loaded again, because a refusal
// usually means that the page was out of date (closed, a card gone).
async function act(work, done) {
  try {
    view = await work();
    say(done || "");
  } catch (error) {
    try {
      view = await api.readBoard(pastId);
    } catch (again) {
      // keep what is on the page
    }
    say(error.message, true);
  }
  render();
}

async function load(message) {
  try {
    view = await api.readBoard(pastId);
    say(message || "");
  } catch (error) {
    if (pastId) {
      pastId = null; // the past board is gone: back to the current one
      return load(error.message);
    }
    say(error.message, true);
  }
  render();
}

// --- Drawing the board -------------------------------------------------------------------

function voteButton(card) {
  const vote = make("button", "vote");
  vote.type = "button";
  vote.dataset.focus = "vote:" + card.id;
  vote.setAttribute("aria-pressed", String(card.voted));
  vote.setAttribute(
    "aria-label",
    (card.voted ? "Take your vote back. " : "Vote for this card. ") + plural(card.votes, "vote") + " so far."
  );
  vote.append(make("span", "dot"), make("span", "", String(card.votes)));
  // Out of votes: the dots already given can still be taken back.
  vote.disabled = !view.writable || (!card.voted && view.votes_left === 0);
  vote.addEventListener("click", () => act(() => (card.voted ? api.takeVoteBack(card.id) : api.giveVote(card.id))));
  return vote;
}

function editForm(card) {
  const form = make("form", "edit");
  const text = make("textarea");
  text.value = editing.text;
  text.maxLength = view.limits.text;
  text.rows = 3;
  text.dataset.focus = "edit:" + card.id;
  text.setAttribute("aria-label", "Text of the card");
  text.addEventListener("input", () => {
    editing.text = text.value;
  });
  const where = make("select");
  where.setAttribute("aria-label", "Column");
  for (const column of view.board.columns) {
    const option = make("option", "", column.title);
    option.value = column.id;
    option.selected = column.id === editing.column;
    where.append(option);
  }
  where.addEventListener("change", () => {
    editing.column = where.value;
  });
  const save = make("button", "primary", "Save");
  save.type = "submit";
  const cancel = button("Cancel", () => {
    editing = null;
    render();
  });
  form.append(text, where, save, cancel);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const wanted = editing;
    editing = null;
    act(() => api.changeCard(card.id, wanted.text, wanted.column));
  });
  submitWithCtrlEnter(text, form);
  return form;
}

function cardItem(card) {
  const item = make("li", card.mine ? "card mine" : "card");
  if (editing && editing.id === card.id) {
    item.append(editForm(card));
    return item;
  }
  item.append(make("p", "card-text", card.text)); // textContent: a card is text, whatever it looks like
  const foot = make("div", "card-foot");
  foot.append(voteButton(card));
  const by = card.mine ? "Yours" : card.author || "";
  foot.append(make("span", "muted by", by + (card.edited ? (by ? ", " : "") + "edited" : "")));
  if (card.mine && view.writable) {
    const edit = button("Edit", () => {
      editing = { id: card.id, text: card.text, column: card.column };
      render("edit:" + card.id);
    });
    edit.dataset.focus = "edit-button:" + card.id;
    const remove = button(
      "Delete",
      async () => {
        if (await ask("Delete this card?", "The card and its votes will be gone.", "Delete", true)) {
          act(() => api.deleteCard(card.id));
        }
      },
      "danger"
    );
    remove.dataset.focus = "delete:" + card.id;
    foot.append(edit, remove);
  }
  item.append(foot);
  return item;
}

function submitWithCtrlEnter(field, form) {
  field.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
}

function addForm(column) {
  const form = make("form", "add");
  const text = make("textarea");
  text.value = drafts.get(column.id) || "";
  text.maxLength = view.limits.text;
  text.rows = 2;
  text.placeholder = "Add a card";
  text.dataset.focus = "draft:" + column.id;
  text.setAttribute("aria-label", "A new card for " + column.title);
  text.addEventListener("input", () => drafts.set(column.id, text.value));
  const add = make("button", "", "Add");
  add.type = "submit";
  add.title = "Add (Ctrl+Enter)";
  form.append(text, add);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const wanted = text.value.trim();
    if (!wanted) {
      return;
    }
    drafts.delete(column.id);
    act(() => api.addCard(column.id, wanted)).then(() => focusOn("draft:" + column.id));
  });
  submitWithCtrlEnter(text, form);
  return form;
}

function focusOn(key) {
  for (const candidate of columns.querySelectorAll("[data-focus]")) {
    if (candidate.dataset.focus === key) {
      candidate.focus();
      return;
    }
  }
}

function banner() {
  if (!view.current) {
    return "A past board, kept as it was. It can be read, not changed.";
  }
  if (view.closed) {
    return "This board is closed: it can be read, not changed." + (view.is_owner ? " You can reopen it." : "");
  }
  if (view.revealed) {
    return "The owner has revealed who wrote the cards.";
  }
  return "";
}

// Draw everything again from "view". The control that had the focus gets it
// back (they are all made anew), or the one named by focusKey.
function render(focusKey) {
  if (!view) {
    return;
  }
  const active = document.activeElement;
  const keep = focusKey || (active && active.dataset ? active.dataset.focus : null);
  document.title = view.board.title;
  $("board-title").textContent = view.board.title;
  $("who").textContent = !view.signed_in
    ? "You are not signed in: you can read the board, not add to it."
    : view.revealed
      ? "Signed in as " + view.me.name + "."
      : "Signed in as " + view.me.name + ". Cards are anonymous: only you can tell which ones are yours.";
  $("votes-left").hidden = !view.writable;
  $("votes-left").textContent = view.votes_left + " of " + plural(view.board.votes_per_member, "vote") + " left";
  $("totals").textContent = totals(view);
  $("banner").textContent = banner();
  $("banner").hidden = !banner();

  // The earlier boards, with the way back to the current one on top.
  $("past").replaceChildren(
    ...[{ id: "", title: "The current board" }, ...view.past].map((choice) => {
      const option = make("option", "", choice.title);
      option.value = choice.id;
      option.selected = choice.id === (pastId || "");
      return option;
    })
  );
  $("past-label").hidden = view.past.length === 0;

  $("reveal").hidden = !(view.is_owner && view.current && !view.revealed);
  $("close").hidden = !(view.is_owner && view.current);
  $("close").textContent = view.closed ? "Reopen the board" : "Close the board";

  columns.replaceChildren(
    ...byColumn(view, $("sort").value).map(({ column, cards }) => {
      const section = make("section", "column");
      const heading = make("h2", "", column.title);
      heading.append(" ", make("span", "count", String(cards.length)));
      const list = make("ul", "cards");
      list.append(...cards.map(cardItem));
      section.append(heading, list);
      if (!cards.length) {
        section.append(make("p", "empty", view.writable ? "Nothing yet. Be the first." : "Nothing here."));
      }
      if (view.writable) {
        section.append(addForm(column));
      }
      return section;
    })
  );
  if (keep) {
    focusOn(keep);
  }
}

// --- The bar above the board ------------------------------------------------------------------

$("sort").addEventListener("change", () => render());
$("refresh").addEventListener("click", () => load());

$("past").addEventListener("change", () => {
  pastId = $("past").value || null;
  editing = null;
  load();
});

$("reveal").addEventListener("click", async () => {
  const sure = await ask(
    "Reveal the authors?",
    "Everybody who opens the board will see who wrote each card. What has been seen cannot be unseen.",
    "Reveal",
    true
  );
  if (sure) {
    act(() => api.changeState({ revealed: true }));
  }
});

$("close").addEventListener("click", () => act(() => api.changeState({ closed: !view.closed })));

$("export").addEventListener("click", () => {
  if (!view) {
    return;
  }
  const url = URL.createObjectURL(new Blob([toMarkdown(view)], { type: "text/markdown" }));
  const link = make("a");
  link.href = url;
  link.download = fileName(view);
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

// A card that is half typed is worth a question before the page is left.
window.addEventListener("beforeunload", (event) => {
  if (editing || [...drafts.values()].some((text) => text.trim())) {
    event.preventDefault();
    event.returnValue = ""; // older browsers ask only when this is set
  }
});

load();
