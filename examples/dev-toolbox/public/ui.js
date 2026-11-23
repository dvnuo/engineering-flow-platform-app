// What the tools share for building their part of the page, in the look of
// the EFP kit (daisyUI classes, the kit's icons and motion).
//
// Everything a visitor pastes is shown with textContent or as the value of
// a field, never parsed as markup: a pasted "<img onerror=...>" is text.
//
// The kit is handed in by app.js with provide(kit). The tools never import
// it themselves: it lives at /_efp/lib/ on the gateway, and node, which runs
// the tests, can then load every tool without a browser or a gateway.

let kit = null;

export function provide(given) {
  kit = given;
}

function icon(name, className = "size-4") {
  return kit ? kit.icon(name, className) : el("span", { "aria-hidden": "true" });
}

// A motion of the kit (rise, pop, shake, flash), when the kit is there; the kit itself skips it under reduced motion.
export function motion(name, ...args) {
  if (kit) kit[name](...args);
}

// el("button", {type: "button", class: "btn", text: "Format", onclick}, ...children)
export function el(tag, properties = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(properties)) {
    if (name === "text") {
      node.textContent = value;
    } else if (name === "class") {
      node.className = value;
    } else if (name.startsWith("on") && typeof value === "function") {
      node.addEventListener(name.slice(2), value);
    } else if (value === true) {
      node.setAttribute(name, "");
    } else if (value !== false && value !== null && value !== undefined) {
      node.setAttribute(name, String(value));
    }
  }
  node.append(...children.filter((child) => child !== null && child !== undefined && child !== false));
  return node;
}

function withClass(base, properties) {
  const { class: extra = "", ...rest } = properties;
  return { ...rest, class: (base + " " + extra).trim() };
}

// A text area for code and data in JetBrains Mono: no spelling marks, no autocorrect.
export function textarea(properties = {}) {
  return el("textarea", withClass("textarea dt-code w-full", { spellcheck: "false", autocomplete: "off", autocapitalize: "off", rows: 6, ...properties }));
}

// The same inside a pane: the pane draws the border.
export function editor(properties = {}) {
  return el("textarea", withClass("dt-editor", { spellcheck: "false", autocomplete: "off", autocapitalize: "off", rows: 8, ...properties }));
}

export function input(properties = {}) {
  return el("input", withClass("input dt-code w-full", { type: "text", spellcheck: "false", autocomplete: "off", autocapitalize: "off", ...properties }));
}

// A control under its label.
export function field(label, control, hint) {
  return el("label", { class: "dt-field" }, el("span", { class: "dt-label", text: label }), control, hint ? el("span", { class: "dt-hint", text: hint }) : null);
}

// A checkbox with its label; ``input`` is the box itself.
export function checkbox(label, checked = false) {
  const box = el("input", { type: "checkbox", class: "checkbox checkbox-sm checkbox-primary" });
  box.checked = checked;
  return { node: el("label", { class: "dt-check" }, box, el("span", { text: label })), input: box };
}

// A segmented control of radio buttons; ``value`` is the chosen one.
export function radios(name, legend, options, onChange) {
  const inputs = options.map(([value, label], index) => {
    const radio = el("input", { type: "radio", name, value, class: "join-item btn btn-sm", "aria-label": label });
    radio.checked = index === 0;
    radio.addEventListener("change", onChange);
    return radio;
  });
  return {
    node: el("div", { class: "join dt-segmented", role: "radiogroup", "aria-label": legend }, ...inputs),
    get value() {
      return inputs.find((radio) => radio.checked).value;
    },
    set value(wanted) {
      inputs.forEach((radio) => {
        radio.checked = radio.value === wanted;
      });
    },
  };
}

export function select(label, options) {
  return el("select", { class: "select select-sm dt-select", "aria-label": label }, ...options.map(([value, text]) => el("option", { value, text })));
}

const KINDS = { primary: "btn-primary", soft: "btn-soft btn-primary", ghost: "btn-ghost" };

// A small button with an icon: button("Format", {icon: "wand-sparkles", kind: "primary", onClick}).
export function button(label, { icon: name, kind = "ghost", title, onClick } = {}) {
  return el("button", { type: "button", class: "btn btn-sm efp-press " + KINDS[kind], title, onclick: onClick }, name ? icon(name) : null, label);
}

export function row(...children) {
  return el("div", { class: "dt-row" }, ...children);
}

// A card for code: a bar with a title and its buttons, the content below.
export function pane(title, body, ...actions) {
  return el("div", { class: "dt-pane" }, el("div", { class: "dt-pane-head" }, el("span", { class: "dt-pane-title", text: title }), el("div", { class: "dt-pane-actions" }, ...actions)), body);
}

// An empty state inside a pane: what will appear there, and how.
export function empty(name, title, text) {
  return el("div", { class: "efp-empty dt-empty" }, el("span", { class: "efp-empty-icon" }, icon(name, "size-5")), el("strong", { text: title }), el("span", { class: "text-sm", text }));
}

const LOOKS = { ok: ["alert-success", "circle-check"], warn: ["alert-warning", "triangle-alert"], fail: ["alert-error", "circle-alert"] };

// The line that says how it went: say() a plain note, ok() a success, warn() a
// caution, fail() a problem (in an alert, with optional nodes under the text).
// A new kind of answer rises in; the same kind with new words just changes.
export function message() {
  const node = el("div", { class: "dt-message", role: "status", "aria-live": "polite" });
  let shown = "";
  const set = (kind, text, extra) => {
    if (!text) {
      node.replaceChildren();
      shown = "";
      return;
    }
    const look = LOOKS[kind];
    node.replaceChildren(
      look
        ? el("div", { class: "alert alert-soft text-sm dt-alert " + look[0] }, icon(look[1]), el("div", { class: "min-w-0 grid gap-2" }, el("span", { text }), ...extra))
        : el("p", { class: "dt-note", text }),
    );
    if (look && kind !== shown) motion("rise", node.firstChild, { distance: 6, duration: 380 });
    shown = kind;
  };
  return {
    node,
    say: (text) => set("note", text, []),
    ok: (text) => set("ok", text, []),
    warn: (text) => set("warn", text, []),
    fail: (text, ...extra) => set("fail", text, extra),
  };
}

function copyWithSelection(text) {
  // For pages without navigator.clipboard (plain http on a host name).
  const area = el("textarea", { class: "dt-offscreen", "aria-hidden": "true", tabindex: "-1" });
  area.value = text;
  document.body.append(area);
  area.select();
  let done = false;
  try {
    done = document.execCommand("copy");
  } catch (error) {
    done = false;
  }
  area.remove();
  return done;
}

// The tick the kit's busy buttons draw: a stroke that runs in (.efp-tick in the kit's style sheet).
function tick() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "efp-tick");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", "M5 12.5l4.5 4.5L19 7.5");
  svg.append(path);
  return svg;
}

// A button that copies whatever ``getText()`` returns at the moment it is
// pressed, and answers with a tick that draws itself. Both labels share one
// cell, so the button keeps its width when "Copy" becomes "Copied".
export function copyButton(getText, label = "Copy") {
  const mark = el("span", { class: "dt-copy-mark" }, icon("copy"));
  const words = el("span", { class: "dt-copy-words" }, el("span", { text: label }), el("span", { text: "Copied", "aria-hidden": "true" }));
  const button = el("button", { type: "button", class: "btn btn-sm btn-ghost dt-copy efp-press", title: "Copy to the clipboard", "aria-label": label }, mark, words);
  const said = el("span", { class: "sr-only", "aria-live": "polite" });
  button.append(said);
  let timer = 0;
  button.addEventListener("click", async () => {
    const text = getText();
    if (!text) {
      motion("shake", button); // nothing to copy yet
      return;
    }
    let done;
    try {
      await navigator.clipboard.writeText(text);
      done = true;
    } catch (error) {
      done = copyWithSelection(text);
    }
    clearTimeout(timer);
    if (!done) {
      motion("shake", button);
      if (kit) kit.toast("The browser did not let the page copy. Select the text and copy it with the keyboard.", { kind: "error" });
      return;
    }
    button.classList.add("is-done");
    mark.replaceChildren(tick());
    said.textContent = "Copied";
    timer = setTimeout(() => {
      button.classList.remove("is-done");
      mark.replaceChildren(icon("copy"));
      said.textContent = "";
    }, 1600);
  });
  return button;
}

// A table from rows of cells; a cell is text or a node.
export function table(headers, rows, className = "") {
  const head = el("tr", {}, ...headers.map((header) => el("th", { scope: "col", text: header })));
  const body = el("tbody", {}, ...rows.map((cells) => el("tr", {}, ...cells.map((cell) => (typeof cell === "string" ? el("td", { text: cell }) : el("td", {}, cell))))));
  return el("div", { class: "dt-table" }, el("table", { class: "table table-sm " + className }, el("thead", {}, head), body));
}

// Run ``action`` once the visitor has paused typing.
export function debounce(action, wait = 150) {
  let timer = 0;
  return () => {
    clearTimeout(timer);
    timer = setTimeout(action, wait);
  };
}

export function plural(count, word) {
  return count.toLocaleString("en") + " " + word + (count === 1 ? "" : "s");
}
