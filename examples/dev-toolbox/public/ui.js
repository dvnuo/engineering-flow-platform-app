// What the tools share for building their part of the page.
//
// Everything a visitor pastes is shown with textContent or as the value of
// a field, never parsed as markup: a pasted "<img onerror=...>" is text.

// el("button", {type: "button", class: "primary", text: "Format"}, ...children)
export function el(tag, properties = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(properties)) {
    if (name === "text") {
      node.textContent = value;
    } else if (name === "class") {
      node.className = value;
    } else if (value === true) {
      node.setAttribute(name, "");
    } else if (value !== false && value !== null && value !== undefined) {
      node.setAttribute(name, String(value));
    }
  }
  node.append(...children);
  return node;
}

// A text area for code and data: no spelling marks, no autocorrect.
export function textarea(properties = {}) {
  return el("textarea", { spellcheck: "false", autocomplete: "off", autocapitalize: "off", rows: 6, ...properties });
}

// A control under its label.
export function field(label, control) {
  return el("label", { class: "field" }, el("span", { class: "label", text: label }), control);
}

// A checkbox with its label; ``input`` is the box itself.
export function checkbox(label, checked = false) {
  const input = el("input", { type: "checkbox" });
  input.checked = checked;
  return { node: el("label", { class: "check" }, input, el("span", { text: label })), input };
}

// A group of radio buttons; ``value`` is the chosen one.
export function radios(name, legend, options, onChange) {
  const inputs = options.map(([value], index) => {
    const input = el("input", { type: "radio", name, value });
    input.checked = index === 0;
    input.addEventListener("change", onChange);
    return input;
  });
  const node = el("fieldset", { class: "choice" }, el("legend", { class: "label", text: legend }));
  options.forEach(([, label], index) => node.append(el("label", { class: "check" }, inputs[index], el("span", { text: label }))));
  return {
    node,
    get value() {
      return inputs.find((input) => input.checked).value;
    },
    set value(wanted) {
      inputs.forEach((input) => {
        input.checked = input.value === wanted;
      });
    },
  };
}

export function select(label, options) {
  const node = el("select", { "aria-label": label });
  for (const [value, text] of options) {
    node.append(el("option", { value, text }));
  }
  return node;
}

// The line that says how it went: say() for a plain note, fail() for a problem.
export function message() {
  const node = el("p", { class: "status", role: "status" });
  const set = (text, problem) => {
    node.textContent = text;
    node.classList.toggle("problem", problem);
  };
  return { node, say: (text) => set(text, false), fail: (text) => set(text, true) };
}

function copyWithSelection(text) {
  // For pages without navigator.clipboard (plain http on a host name).
  const area = el("textarea", { class: "offscreen", "aria-hidden": "true", tabindex: "-1" });
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

// A button that copies whatever ``getText()`` returns at the moment it is pressed.
export function copyButton(getText, label = "Copy") {
  const button = el("button", { type: "button", class: "copy", "aria-live": "polite", text: label });
  button.addEventListener("click", async () => {
    const text = getText();
    if (!text) {
      return;
    }
    let done;
    try {
      await navigator.clipboard.writeText(text);
      done = true;
    } catch (error) {
      done = copyWithSelection(text);
    }
    button.textContent = done ? "Copied" : "Could not copy";
    setTimeout(() => {
      button.textContent = label;
    }, 1500);
  });
  return button;
}

// A table from rows of cells; a cell is text or a node.
export function table(headers, rows, className = "") {
  const head = el("tr");
  for (const header of headers) {
    head.append(el("th", { scope: "col", text: header }));
  }
  const body = el("tbody");
  for (const row of rows) {
    const line = el("tr");
    for (const cell of row) {
      line.append(typeof cell === "string" ? el("td", { text: cell }) : el("td", {}, cell));
    }
    body.append(line);
  }
  return el("div", { class: "scroll" }, el("table", { class: className }, el("thead", {}, head), body));
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
