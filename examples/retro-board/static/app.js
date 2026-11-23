// The page of the board.
//
// Every answer of the backend is the whole board as this visitor may see it,
// and the page draws exactly that. Whose card is whose is not in it (see
// main.py), so there is nothing here to hide. The page is drawn again after
// each action and on "Refresh"; nothing asks the server on a timer. To see
// what the others wrote in the meantime, press Refresh.
//
// The look comes from the EFP kit (/_efp/lib/, docs/UI_KIT.md). The columns
// and the cards are kept by id between two drawings, so that a new card, a
// deleted one or a new order glides into place (kit.animateList) instead of
// the whole board being drawn anew.

import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";
import * as api from "./api.js";
import { byColumn, fileName, plural, toMarkdown, totals } from "./board.js";

const { h } = kit;
const $ = (id) => document.getElementById(id);
const columnsElement = $("columns");
const confirmDialog = $("confirm");

// A colour of the theme and an icon for each column of board.json; columns
// with other ids take the looks of the second list in turn.
const COLUMN_LOOKS = {
  well: { tone: "success", icon: "sun" },
  improve: { tone: "warning", icon: "wrench" },
  ideas: { tone: "secondary", icon: "lightbulb" },
  actions: { tone: "primary", icon: "rocket" },
};
const MORE_LOOKS = [
  { tone: "info", icon: "sparkles" },
  { tone: "accent", icon: "flag" },
  { tone: "error", icon: "heart" },
  { tone: "primary", icon: "star" },
];

let view = null; // the board as the server last sent it
let pastId = null; // the past board that is shown, or null for the current one
let sortMode = "time";
let editing = null; // {id, text, column}: the card whose text is being changed
const drafts = new Map(); // column id -> a card that is being typed
const adding = new Set(); // column ids whose "add a card" form is open
const columnParts = new Map(); // column id -> {section, list, count, add}
const cardItems = new Map(); // card id -> its <li>, kept between drawings
let boardDrawn = null; // the id of the board the columns were made for

// A message above the board: a problem (red) or a plain note. Empty hides it.
function say(text, isProblem) {
  const status = $("status");
  status.hidden = !text;
  status.replaceChildren(
    text
      ? h("div", { class: "alert alert-soft " + (isProblem ? "alert-error" : "alert-info") }, kit.icon(isProblem ? "circle-alert" : "info", "size-5"), h("span", { text }))
      : "",
  );
}

// A question before something that cannot be taken back.
function ask(title, text, ok, { danger = false, icon = "triangle-alert" } = {}) {
  $("confirm-title").textContent = title;
  $("confirm-text").textContent = text;
  $("confirm-ok").textContent = ok;
  $("confirm-ok").className = "btn " + (danger ? "btn-error" : "btn-primary");
  $("confirm-icon").replaceChildren(kit.icon(icon));
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
// usually means that the page was out of date (closed, a card gone). With a
// button, the button shows that it is working (kit.busy).
async function act(work, { button, done, tick = false } = {}) {
  const before = view;
  try {
    view = button ? await kit.busy(button, work, { tick }) : await work();
    say("");
    if (done) kit.toast(done, { kind: "success" });
  } catch (error) {
    try {
      view = await api.readBoard(pastId);
    } catch (again) {
      // keep what is on the page
    }
    say(error.message, true);
  }
  render(null, before);
}

async function load(button) {
  const before = view;
  try {
    view = await (button ? kit.busy(button, () => api.readBoard(pastId)) : api.readBoard(pastId));
    say("");
  } catch (error) {
    if (pastId) {
      pastId = null; // the past board is gone: back to the current one
      return load();
    }
    say(error.message, true);
  }
  render(null, before);
}

// --- One card ---------------------------------------------------------------------------------

function tilt(id) {
  return ((kit.hue(id) % 5) - 2) * 0.35 + "deg";
}

// A "+1" floats up from a vote that was just given.
function plusOne(button) {
  if (kit.reducedMotion()) return;
  const box = button.getBoundingClientRect();
  const card = button.closest(".rb-card").getBoundingClientRect();
  const plus = h("span", { class: "rb-plus", style: { left: box.left - card.left + 6 + "px", top: box.top - card.top - 4 + "px" } }, "+1");
  button.closest(".rb-card").append(plus);
  plus
    .animate([{ transform: "translateY(0) scale(0.8)", opacity: 0 }, { transform: "translateY(-10px) scale(1.1)", opacity: 1, offset: 0.3 }, { transform: "translateY(-26px)", opacity: 0 }], {
      duration: 800,
      easing: kit.EASE,
    })
    .finished.finally(() => plus.remove());
}

function voteButton(card) {
  const vote = h(
    "button",
    {
      type: "button",
      class: "rb-vote",
      dataset: { focus: "vote:" + card.id },
      "aria-pressed": String(card.voted),
      "aria-label": (card.voted ? "Take your vote back. " : "Vote for this card. ") + plural(card.votes, "vote") + " so far.",
    },
    h("span", { class: "rb-dot", "aria-hidden": "true" }),
    h("span", { class: "efp-number" }, String(card.votes)),
  );
  // Out of votes: the dots already given can still be taken back.
  vote.disabled = !view.writable || (!card.voted && view.votes_left === 0);
  vote.addEventListener("click", async () => {
    const giving = !card.voted;
    if (giving) {
      kit.pop(vote, { scale: 1.25 });
      plusOne(vote);
    }
    await act(() => (giving ? api.giveVote(card.id) : api.takeVoteBack(card.id)));
    if (giving && view && view.votes_left === 0) {
      kit.toast("That was your last vote. Take one back to move it.", { kind: "info" });
    }
  });
  return vote;
}

function editForm(card) {
  const text = h("textarea", {
    class: "textarea textarea-sm w-full",
    maxlength: view.limits.text,
    rows: 3,
    dataset: { focus: "edit:" + card.id },
    "aria-label": "Text of the card",
  });
  text.value = editing.text;
  text.addEventListener("input", () => {
    editing.text = text.value;
  });
  const where = h(
    "select",
    { class: "select select-sm grow", "aria-label": "Column" },
    ...view.board.columns.map((column) => h("option", { value: column.id, selected: column.id === editing.column }, column.title)),
  );
  where.addEventListener("change", () => {
    editing.column = where.value;
  });
  const save = h("button", { type: "submit", class: "btn btn-sm btn-primary" }, "Save");
  const cancel = h("button", {
    type: "button",
    class: "btn btn-sm btn-ghost",
    text: "Cancel",
    onclick: () => {
      editing = null;
      render("edit-button:" + card.id);
    },
  });
  const form = h("form", { class: "rb-edit grid gap-2" }, text, h("div", { class: "flex gap-2" }, where, cancel, save));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const wanted = editing;
    editing = null;
    act(() => api.changeCard(card.id, wanted.text, wanted.column), { button: save });
  });
  keys(text, form, () => {
    editing = null;
    render("edit-button:" + card.id);
  });
  return form;
}

function fillCard(item, card) {
  const look = lookOf(card.column);
  item.className = "rb-card" + (card.mine ? " is-mine" : "");
  item.style.setProperty("--rb-tone", `var(--color-${look.tone})`);
  item.style.setProperty("--tilt", tilt(card.id));
  if (editing && editing.id === card.id) {
    item.replaceChildren(editForm(card));
    return;
  }
  const foot = h("div", { class: "rb-foot" }, voteButton(card));
  if (card.author) {
    foot.append(h("span", { class: "rb-author inline-flex items-center gap-1.5 text-xs text-base-content/70 min-w-0" }, kit.avatar(card.author, { size: "1.35rem" }), h("span", { class: "truncate" }, card.mine && !view.revealed ? "Yours" : card.author)));
  }
  if (card.edited) {
    foot.append(h("span", { class: "text-xs text-base-content/50", text: "edited" }));
  }
  if (card.mine && view.writable) {
    const edit = h(
      "button",
      {
        type: "button",
        class: "btn btn-ghost btn-xs btn-square",
        "aria-label": "Edit",
        title: "Edit",
        dataset: { focus: "edit-button:" + card.id },
        onclick: () => {
          editing = { id: card.id, text: card.text, column: card.column };
          render("edit:" + card.id);
        },
      },
      kit.icon("pencil", "size-3.5"),
    );
    const remove = h(
      "button",
      { type: "button", class: "btn btn-ghost btn-xs btn-square text-error", "aria-label": "Delete", title: "Delete", dataset: { focus: "delete:" + card.id } },
      kit.icon("trash-2", "size-3.5"),
    );
    remove.addEventListener("click", async () => {
      if (await ask("Delete this card?", "The card and its votes will be gone.", "Delete", { danger: true, icon: "trash-2" })) {
        act(() => api.deleteCard(card.id), { done: "The card is deleted." });
      }
    });
    foot.append(h("span", { class: "rb-tools" }, edit, remove));
  }
  item.replaceChildren(h("p", { class: "rb-text", text: card.text }), foot); // text, never HTML: a card is text, whatever it looks like
}

// Ctrl+Enter sends the form, Escape gives up.
function keys(field, form, giveUp) {
  field.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      form.requestSubmit();
    } else if (event.key === "Escape") {
      event.preventDefault();
      giveUp();
    }
  });
}

// --- One column ---------------------------------------------------------------------------------

function lookOf(columnId) {
  if (COLUMN_LOOKS[columnId]) return COLUMN_LOOKS[columnId];
  const index = view.board.columns.findIndex((column) => column.id === columnId);
  return MORE_LOOKS[Math.max(0, index) % MORE_LOOKS.length];
}

function makeColumn(column) {
  const look = lookOf(column.id);
  const count = h("span", { class: "rb-count" }, "0");
  const list = h("ul", { class: "rb-cards", "aria-label": column.title });
  const empty = h("p", { class: "text-sm text-base-content/50 text-center py-4" });
  const add = h("div");
  const section = h(
    "section",
    { class: "rb-column", style: { "--rb-tone": `var(--color-${look.tone})` } },
    h("div", { class: "rb-column-head" }, h("span", { class: "rb-column-icon", "aria-hidden": "true" }, kit.icon(look.icon, "size-4")), h("h2", { class: "font-semibold" }, column.title), count),
    list,
    empty,
    add,
  );
  kit.animateList(list);
  return { section, list, count, empty, add };
}

function addArea(column, parts) {
  if (!view.writable) {
    parts.add.replaceChildren();
    return;
  }
  const draft = drafts.get(column.id) || "";
  if (!adding.has(column.id) && !draft) {
    parts.add.replaceChildren(
      h(
        "button",
        {
          type: "button",
          class: "btn btn-sm w-full rb-add-open",
          dataset: { focus: "open:" + column.id },
          onclick: () => {
            adding.add(column.id);
            render("draft:" + column.id);
          },
        },
        kit.icon("plus", "size-4"),
        "Add a card",
      ),
    );
    return;
  }
  const text = h("textarea", {
    class: "textarea textarea-sm w-full",
    maxlength: view.limits.text,
    rows: 3,
    placeholder: "What should the team hear?",
    dataset: { focus: "draft:" + column.id },
    "aria-label": "A new card for " + column.title,
  });
  text.value = draft;
  text.addEventListener("input", () => drafts.set(column.id, text.value));
  const add = h("button", { type: "submit", class: "btn btn-sm btn-primary efp-press", title: "Add (Ctrl+Enter)" }, kit.icon("plus", "size-4"), "Add");
  const close = () => {
    adding.delete(column.id);
    if (!(drafts.get(column.id) || "").trim()) drafts.delete(column.id);
    render("open:" + column.id);
  };
  const form = h(
    "form",
    { class: "rb-add grid gap-2" },
    text,
    h(
      "div",
      { class: "flex items-center gap-2" },
      h("span", { class: "text-xs text-base-content/50 grow" }, h("kbd", { class: "kbd kbd-xs" }, "Ctrl"), " ", h("kbd", { class: "kbd kbd-xs" }, "Enter")),
      h("button", { type: "button", class: "btn btn-sm btn-ghost", text: "Cancel", onclick: close }),
      add,
    ),
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const wanted = text.value.trim();
    if (!wanted) {
      kit.shake(text);
      return;
    }
    drafts.delete(column.id);
    await act(() => api.addCard(column.id, wanted), { button: add });
    focusOn("draft:" + column.id);
  });
  keys(text, form, close);
  parts.add.replaceChildren(form);
}

// --- The whole board ------------------------------------------------------------------------------

function focusOn(key) {
  for (const candidate of columnsElement.querySelectorAll("[data-focus]")) {
    if (candidate.dataset.focus === key) {
      candidate.focus();
      return;
    }
  }
}

function banner() {
  if (!view.current) {
    return { icon: "archive", kind: "alert-info", text: "A past board, kept as it was. It can be read, not changed." };
  }
  if (view.closed) {
    return { icon: "lock", kind: "alert-warning", text: "This board is closed: it can be read, not changed." + (view.is_owner ? " You can reopen it." : "") };
  }
  if (view.revealed) {
    return { icon: "eye", kind: "alert-info", text: "The owner has revealed who wrote the cards." };
  }
  return null;
}

function drawHeader() {
  document.title = view.board.title;
  $("board-title").textContent = view.board.title;
  $("who").textContent = !view.signed_in
    ? "You are not signed in: you can read the board, not add to it."
    : view.revealed
      ? "Signed in as " + view.me.name
      : "Signed in as " + view.me.name + ". Cards are anonymous: only you can tell which ones are yours.";
  if (view.signed_in && !$("me").firstChild) $("me").append(kit.avatar(view.me.name, { size: "2rem" }));
  $("votes-left").hidden = !view.writable;
  const per = view.board.votes_per_member;
  const dots = $("votes-dots");
  if (dots.children.length !== per) dots.replaceChildren(...Array.from({ length: per }, () => h("span")));
  Array.from(dots.children).forEach((dot, index) => dot.classList.toggle("is-used", index >= view.votes_left));
  $("votes-text").textContent = view.votes_left + " of " + plural(per, "vote") + " left";
  $("votes-count").textContent = String(view.votes_left);
  $("votes-left").title = $("votes-text").textContent;
  $("totals").textContent = totals(view);
  const note = banner();
  $("banner").hidden = !note;
  $("banner").replaceChildren(note ? h("div", { class: "alert alert-soft " + note.kind }, kit.icon(note.icon, "size-5"), h("span", { text: note.text })) : "");
  document.body.classList.toggle("rb-closed", view.closed);

  // The earlier boards, with the way back to the current one on top.
  $("past").replaceChildren(
    ...[{ id: "", title: "The current board" }, ...view.past].map((choice) => h("option", { value: choice.id, selected: choice.id === (pastId || "") }, choice.title)),
  );
  $("past-label").hidden = view.past.length === 0;
  $("reveal").hidden = !(view.is_owner && view.current && !view.revealed);
  $("close").hidden = !(view.is_owner && view.current);
  $("close").replaceChildren(kit.icon(view.closed ? "lock-open" : "lock"), h("span", {}, view.closed ? "Reopen the board" : "Close the board"));
}

// Draw everything from "view". Columns and cards are kept by id; the control
// that had the focus gets it back, or the one named by focusKey.
function render(focusKey, before) {
  if (!view) return;
  const active = document.activeElement;
  const keep = focusKey || (active && active.dataset ? active.dataset.focus : null);
  drawHeader();

  if (boardDrawn !== view.board.id) {
    boardDrawn = view.board.id;
    columnParts.clear();
    cardItems.clear();
    columnsElement.replaceChildren(
      ...view.board.columns.map((column) => {
        const parts = makeColumn(column);
        columnParts.set(column.id, parts);
        return parts.section;
      }),
    );
    kit.rise(columnsElement.children, { stagger: 70 });
  }

  const seen = new Set();
  for (const { column, cards } of byColumn(view, sortMode)) {
    const parts = columnParts.get(column.id);
    parts.count.textContent = String(cards.length);
    cards.forEach((card, index) => {
      seen.add(card.id);
      let item = cardItems.get(card.id);
      if (!item) {
        item = h("li", { dataset: { id: card.id } });
        cardItems.set(card.id, item);
      }
      fillCard(item, card);
      if (parts.list.children[index] !== item) parts.list.insertBefore(item, parts.list.children[index] || null);
    });
    parts.empty.hidden = cards.length > 0;
    parts.empty.textContent = view.writable ? "Nothing yet. Be the first." : "Nothing here.";
    addArea(column, parts);
  }
  for (const [id, item] of cardItems) {
    if (!seen.has(id)) {
      cardItems.delete(id);
      item.remove();
    }
  }

  // The owner has just revealed the authors: their names come in one after the other.
  if (before && !before.revealed && view.revealed) {
    kit.appear(columnsElement.querySelectorAll(".rb-author"), { stagger: 45 });
  }
  if (keep) focusOn(keep);
}

// --- The bar above the board ------------------------------------------------------------------

for (const choice of document.querySelectorAll("[data-sort]")) {
  choice.addEventListener("click", () => {
    sortMode = choice.dataset.sort;
    for (const other of document.querySelectorAll("[data-sort]")) other.setAttribute("aria-pressed", String(other === choice));
    render();
  });
}

$("refresh").addEventListener("click", (event) => load(event.currentTarget));

$("past").addEventListener("change", () => {
  pastId = $("past").value || null;
  editing = null;
  load();
});

$("reveal").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const sure = await ask(
    "Reveal the authors?",
    "Everybody who opens the board will see who wrote each card. What has been seen cannot be unseen.",
    "Reveal",
    { danger: true, icon: "eye" },
  );
  if (sure) {
    act(() => api.changeState({ revealed: true }), { button });
  }
});

$("close").addEventListener("click", (event) =>
  act(() => api.changeState({ closed: !view.closed }), { button: event.currentTarget, done: view.closed ? "The board is open again." : "The board is closed." }),
);

$("export").addEventListener("click", () => {
  if (!view) return;
  const url = URL.createObjectURL(new Blob([toMarkdown(view)], { type: "text/markdown" }));
  const link = h("a", { href: url, download: fileName(view) });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  kit.toast("Saved as " + fileName(view), { kind: "success" });
});

// A card that is half typed is worth a question before the page is left.
window.addEventListener("beforeunload", (event) => {
  if (editing || [...drafts.values()].some((text) => text.trim())) {
    event.preventDefault();
    event.returnValue = ""; // older browsers ask only when this is set
  }
});

await kit.ready();
kit.themeSwitch($("theme"));
load();
