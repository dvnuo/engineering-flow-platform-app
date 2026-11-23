// EFP kit 1.0.0: small helpers on top of the shared libraries. docs/UI_KIT.md
// describes each one; efp-kit.css holds the classes they use.
//
//   import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";
//   await kit.ready();                      // icons drawn, motion loaded
//   kit.toast("Saved", { kind: "success" });
//   await kit.busy(button, () => save());   // spinner, then a tick
//
// The libraries load on first use, from next to this file: a page pays only
// for what it uses, and every app on the host shares the browser's copy.
// Every animation asks reducedMotion() first and skips to the end state.

export const VERSION = "1.0.0";

const LIBRARIES = {
  motion: { url: "../motion@14.14.0/motion.js", global: "Motion" },
  lucide: { url: "../lucide@1.77.0/lucide.min.js", global: "lucide" },
  confetti: { url: "../canvas-confetti@1.9.4/confetti.js", global: "confetti" },
  sortable: { url: "../sortablejs@1.15.7/Sortable.min.js", global: "Sortable" },
  echarts: { url: "../echarts@6.1.0/echarts.min.js", global: "echarts" },
  autoAnimate: { url: "../auto-animate@0.10.2/auto-animate.js", module: true },
};

const loading = new Map();

/** A shared library by name (motion, lucide, confetti, sortable, echarts, autoAnimate), loaded once. */
export function use(name) {
  const library = LIBRARIES[name];
  if (!library) return Promise.reject(new Error("efp-kit: there is no library called " + name));
  if (!loading.has(name)) {
    const url = new URL(library.url, import.meta.url).href;
    let promise;
    if (library.module) promise = import(url).then((module) => module.default || module);
    else if (window[library.global]) promise = Promise.resolve(window[library.global]);
    else promise = loadScript(url).then(() => window[library.global]);
    loading.set(name, promise);
  }
  return loading.get(name);
}

function loadScript(url) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = url;
    script.async = true;
    script.onload = resolve;
    script.onerror = () => reject(new Error("efp-kit: could not load " + url));
    document.head.append(script);
  });
}

// Icons and motion are wanted on nearly every page: start loading them now.
use("lucide").catch(() => {});

/** Resolves once the icons on the page are drawn. Await it before the first render. */
export async function ready() {
  await icons(document);
}

// ---- Small things ----------------------------------------------------------------

/** True when the visitor asked the system for less motion. */
export function reducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

const supportsLinear = typeof CSS !== "undefined" && CSS.supports("animation-timing-function", "linear(0, 1)");

/** Easings for element.animate(): EASE (fast out), SPRING (settles with a little overshoot), BOUNCE (more). */
export const EASE = "cubic-bezier(0.16, 1, 0.3, 1)";
export const SPRING = supportsLinear
  ? "linear(0, 0.027, 0.094, 0.185, 0.289, 0.397, 0.502, 0.599, 0.687, 0.763, 0.828, 0.881, 0.924, 0.957, 0.983, 1.001, 1.014, 1.022, 1.026, 1.028, 1.028, 1.027, 1.024, 1.021, 1.018, 1.015, 1.012, 1.01, 1.008, 1.006, 1.004, 1.003, 1.002, 1.001, 1)"
  : "cubic-bezier(0.34, 1.36, 0.64, 1)";
export const BOUNCE = supportsLinear
  ? "linear(0, 0.044, 0.155, 0.303, 0.466, 0.626, 0.77, 0.891, 0.985, 1.054, 1.097, 1.12, 1.126, 1.12, 1.106, 1.087, 1.066, 1.046, 1.028, 1.013, 1.001, 0.993, 0.987, 0.985, 0.984, 0.985, 0.987, 0.989, 0.992, 0.994, 0.997, 0.998, 1, 1.001, 1.002, 1.001, 1)"
  : "cubic-bezier(0.34, 1.56, 0.64, 1)";

function elements(targets) {
  if (!targets) return [];
  if (typeof targets === "string") return Array.from(document.querySelectorAll(targets));
  if (targets instanceof Element) return [targets];
  return Array.from(targets).filter((item) => item instanceof Element);
}

function settle(animation) {
  return animation.finished.catch(() => {});
}

/**
 * h("button.btn.btn-primary", { onclick, type: "button" }, "Save", h("span", {}, "!"))
 *
 * An element: "tag.class.class" as the first argument; then properties
 * (class, text, html is not offered: use text and children), dataset, style
 * (an object), on<event> handlers, and anything else as an attribute; then
 * children: strings become text, null and false are skipped, arrays are
 * flattened. Nothing is ever parsed as HTML.
 */
export function h(selector, props, ...children) {
  const [tag, ...classes] = selector.split(".");
  const element = document.createElement(tag || "div");
  if (classes.length) element.className = classes.join(" ");
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") element.className = [element.className, value].filter(Boolean).join(" ");
    else if (key === "text") element.textContent = value;
    else if (key === "dataset") Object.assign(element.dataset, value);
    else if (key === "style" && typeof value === "object") {
      for (const [name, style] of Object.entries(value)) {
        if (name.startsWith("--")) element.style.setProperty(name, style);
        else element.style[name] = style;
      }
    } else if (key.startsWith("on") && typeof value === "function") element.addEventListener(key.slice(2), value);
    else if (value === true) element.setAttribute(key, "");
    else element.setAttribute(key, String(value));
  }
  const add = (child) => {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) child.forEach(add);
    else element.append(child instanceof Node ? child : document.createTextNode(String(child)));
  };
  children.forEach(add);
  return element;
}

// ---- Icons (lucide) ------------------------------------------------------------------

let lucideLibrary = null;
use("lucide").then((library) => (lucideLibrary = library), () => {});

const ICON_ATTRIBUTES = { "stroke-width": 1.9, "aria-hidden": "true" };

/** Draw every <i data-lucide="name"> inside root (default: the page). */
export async function icons(root = document) {
  try {
    const library = await use("lucide");
    library.createIcons({ root, attrs: ICON_ATTRIBUTES });
  } catch (error) {
    console.warn(error);
  }
}

function pascal(name) {
  return name.replace(/(^|-)([a-z0-9])/g, (_, __, letter) => letter.toUpperCase());
}

/**
 * An icon element by its lucide name (https://lucide.dev/icons), for example
 * icon("sparkles", "size-4"). Drawn at once when lucide is loaded (after
 * ready()); otherwise a placeholder that the next icons() call draws.
 */
export function icon(name, className = "") {
  const node = lucideLibrary && lucideLibrary.icons ? lucideLibrary.icons[pascal(name)] : null;
  if (node) {
    const svg = lucideLibrary.createElement(node, ICON_ATTRIBUTES);
    svg.classList.add("lucide", "lucide-" + name);
    if (className) svg.classList.add(...className.split(/\s+/).filter(Boolean));
    return svg;
  }
  const placeholder = h("i", { "data-lucide": name, class: className });
  // Drawn once lucide has arrived, if the caller has put it into the page by
  // then; one put in later waits for the next icons() call.
  use("lucide").then(
    () => queueMicrotask(() => placeholder.isConnected && icons(placeholder.parentElement)),
    () => {},
  );
  return placeholder;
}

// ---- Motion --------------------------------------------------------------------------

/** The elements rise into place one after the other. */
export function rise(targets, { delay = 0, stagger = 45, distance = 14, duration = 620 } = {}) {
  if (reducedMotion()) return Promise.resolve();
  return Promise.all(
    elements(targets).map((element, index) =>
      settle(
        element.animate(
          [
            { opacity: 0, transform: `translateY(${distance}px) scale(0.985)`, filter: "blur(4px)" },
            { opacity: 1, transform: "none", filter: "blur(0px)" },
          ],
          { duration, delay: delay + index * stagger, easing: EASE, fill: "backwards" },
        ),
      ),
    ),
  );
}

/** A quick bounce in scale: something counted, something arrived. */
export function pop(targets, { scale = 1.18, duration = 560 } = {}) {
  if (reducedMotion()) return Promise.resolve();
  return Promise.all(
    elements(targets).map((element) =>
      settle(element.animate([{ transform: "scale(1)" }, { transform: `scale(${scale})`, offset: 0.3 }, { transform: "scale(1)" }], { duration, easing: EASE })),
    ),
  );
}

/** Appears with a bounce, from small: a new badge, a new card. */
export function appear(targets, { duration = 620, delay = 0, stagger = 40 } = {}) {
  if (reducedMotion()) return Promise.resolve();
  return Promise.all(
    elements(targets).map((element, index) =>
      settle(
        element.animate([{ opacity: 0, transform: "scale(0.6)" }, { opacity: 1, transform: "scale(1)" }], {
          duration,
          delay: delay + index * stagger,
          easing: BOUNCE,
          fill: "backwards",
        }),
      ),
    ),
  );
}

/** A short shake: no, wrong, not allowed. */
export function shake(targets) {
  if (reducedMotion()) return Promise.resolve();
  const frames = [0, -9, 8, -6, 5, -3, 2, 0].map((x) => ({ transform: `translateX(${x}px)` }));
  return Promise.all(elements(targets).map((element) => settle(element.animate(frames, { duration: 480, easing: "ease-out" }))));
}

/** A ring that fades: this changed (for example because somebody else changed it). */
export function flash(targets, color = "var(--color-primary)") {
  if (reducedMotion()) return Promise.resolve();
  const ring = (alpha, size) => `0 0 0 ${size}px color-mix(in oklab, ${color} ${alpha}%, transparent)`;
  return Promise.all(
    elements(targets).map((element) =>
      settle(element.animate([{ boxShadow: ring(45, 0) }, { boxShadow: ring(0, 12) }], { duration: 900, easing: EASE })),
    ),
  );
}

/**
 * Count the number in element up (or down) to value. format turns a number
 * into the text shown (default: whole numbers with separators).
 */
export function countUp(element, value, { from, duration = 900, format } = {}) {
  const show = format || ((number) => Math.round(number).toLocaleString("en"));
  const start = from !== undefined ? from : Number(element.dataset.efpValue || 0);
  element.dataset.efpValue = String(value);
  if (reducedMotion() || start === value) {
    element.textContent = show(value);
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    const began = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - began) / duration);
      const eased = 1 - Math.pow(1 - t, 4);
      element.textContent = show(start + (value - start) * eased);
      if (t < 1) requestAnimationFrame(step);
      else resolve();
    };
    requestAnimationFrame(step);
  });
}

/** Run update inside a View Transition where the browser has them, else just run it. */
export function transition(update) {
  if (!document.startViewTransition || reducedMotion()) {
    update();
    return Promise.resolve();
  }
  return document.startViewTransition(update).finished.catch(() => {});
}

/** Children of parent animate themselves when added, removed or moved (AutoAnimate). */
export async function animateList(parent, { duration = 280 } = {}) {
  const autoAnimate = await use("autoAnimate");
  return autoAnimate(parent, { duration, easing: EASE });
}

/** Drag to reorder (SortableJS) with the kit's look; options go to Sortable.create. */
export async function sortable(list, options = {}) {
  const Sortable = await use("sortable");
  return Sortable.create(list, {
    animation: 220,
    easing: EASE,
    ghostClass: "efp-sort-ghost",
    chosenClass: "efp-sort-chosen",
    dragClass: "efp-sort-drag",
    ...options,
  });
}

// ---- Colours of the theme ------------------------------------------------------

const probe = typeof document !== "undefined" ? document.createElement("canvas").getContext("2d", { willReadFrequently: true }) : null;

/**
 * A colour of the theme as "rgb(r, g, b)" (or "rgba(...)" with alpha), for
 * code that cannot read CSS colours such as oklch(): charts, confetti, canvas.
 * name is a daisyUI colour (primary, base-content, ...) or any CSS colour.
 */
export function color(name, alpha = 1) {
  const value = /^[a-z0-9-]+$/.test(name) && !CSS.supports("color", name) ? `var(--color-${name})` : name;
  const holder = h("span", { style: { color: value, display: "none" } });
  document.body.append(holder);
  const computed = getComputedStyle(holder).color;
  holder.remove();
  if (!probe) return computed;
  probe.clearRect(0, 0, 1, 1);
  probe.fillStyle = computed;
  probe.fillRect(0, 0, 1, 1);
  const [r, g, b] = probe.getImageData(0, 0, 1, 1).data;
  return alpha < 1 ? `rgba(${r}, ${g}, ${b}, ${alpha})` : `rgb(${r}, ${g}, ${b})`;
}

/** Calls listener whenever the light or dark theme changes (the system's or data-theme). */
export function onThemeChange(listener) {
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  media.addEventListener("change", listener);
  const observer = new MutationObserver(listener);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => {
    media.removeEventListener("change", listener);
    observer.disconnect();
  };
}

const THEME_KEY = "efp-kit-theme";
const THEME_STATES = [
  { value: "", icon: "sun-moon", label: "Theme: as the system" },
  { value: "efp", icon: "sun", label: "Theme: light" },
  { value: "efp-dark", icon: "moon", label: "Theme: dark" },
];
// Every switch on the page shows the same state: a page may have one in
// each of its views.
const themeButtons = new Set();
let themeIndex = -1;

function showTheme(animate) {
  const state = THEME_STATES[themeIndex];
  if (state.value) document.documentElement.setAttribute("data-theme", state.value);
  else document.documentElement.removeAttribute("data-theme");
  for (const button of themeButtons) {
    button.replaceChildren(icon(state.icon, "size-5"));
    button.setAttribute("aria-label", state.label);
    button.title = state.label;
    if (animate && button.isConnected && !reducedMotion()) {
      button.firstChild.animate([{ transform: "rotate(-90deg) scale(0.4)", opacity: 0 }, { transform: "none", opacity: 1 }], { duration: 520, easing: BOUNCE });
    }
  }
}

/**
 * A button that switches between the system's choice, light and dark, and
 * remembers it in this browser. Give it an element; it fills in the icon.
 * Several buttons on one page switch together.
 */
export function themeSwitch(button) {
  if (themeIndex < 0) {
    let saved = "";
    try {
      saved = localStorage.getItem(THEME_KEY) || "";
    } catch (_) {}
    themeIndex = Math.max(0, THEME_STATES.findIndex((state) => state.value === saved));
  }
  if (themeButtons.has(button)) return;
  themeButtons.add(button);
  button.addEventListener("click", () => {
    themeIndex = (themeIndex + 1) % THEME_STATES.length;
    try {
      localStorage.setItem(THEME_KEY, THEME_STATES[themeIndex].value);
    } catch (_) {}
    transition(() => showTheme(true));
  });
  showTheme(false);
}

// ---- Toasts --------------------------------------------------------------------------

const TOAST_ICONS = { info: "info", success: "circle-check", warning: "triangle-alert", error: "circle-x" };
let toastRegion = null;

/**
 * A message in the corner that goes away by itself (unless the pointer rests
 * on it). kind: info, success, warning, error. action: { label, onClick }.
 * timeout 0 keeps it until it is closed. Returns a function that closes it.
 */
export function toast(message, { kind = "info", timeout = 4200, action } = {}) {
  if (!toastRegion || !toastRegion.isConnected) {
    toastRegion = h("div.efp-toasts", { role: "status", "aria-live": "polite" });
    document.body.append(toastRegion);
  }
  const close = () => {
    if (slot.classList.contains("is-leaving")) return;
    clearTimeout(timer);
    slot.classList.add("is-leaving");
    const gone = () => slot.remove();
    if (reducedMotion()) return gone();
    card.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateX(28px) scale(0.96)" }], {
      duration: 240,
      easing: "ease-in",
      fill: "forwards",
    });
    setTimeout(gone, 320);
  };
  const card = h(
    "div.alert.alert-soft.efp-toast",
    { class: "alert-" + kind, role: kind === "error" ? "alert" : null, style: { "--efp-toast-ms": timeout + "ms" } },
    icon(TOAST_ICONS[kind] || "info", "size-5"),
    h("span", { class: "text-sm leading-snug", text: message }),
    h(
      "div.flex.items-center.gap-1",
      {},
      action ? h("button.btn.btn-sm.btn-ghost", { type: "button", text: action.label, onclick: () => (action.onClick(), close()) }) : null,
      h("button.btn.btn-sm.btn-ghost.btn-square", { type: "button", "aria-label": "Close", onclick: () => close() }, icon("x", "size-4")),
    ),
    timeout ? h("span.efp-toast-timer", { "aria-hidden": "true" }) : null,
  );
  const slot = h("div.efp-toast-slot", {}, h("div", {}, card));
  toastRegion.append(slot);
  while (toastRegion.children.length > 4) toastRegion.firstElementChild.remove();
  if (!reducedMotion()) {
    card.animate([{ opacity: 0, transform: "translateY(18px) scale(0.94)" }, { opacity: 1, transform: "none" }], { duration: 560, easing: SPRING });
  }
  // The timer stops while the pointer rests on the toast, as its bar does.
  let timer = 0;
  let left = timeout;
  let since = 0;
  const run = () => {
    if (!timeout) return;
    since = performance.now();
    timer = setTimeout(close, left);
  };
  card.addEventListener("mouseenter", () => {
    clearTimeout(timer);
    left -= performance.now() - since;
  });
  card.addEventListener("mouseleave", run);
  run();
  return close;
}

// ---- Busy buttons --------------------------------------------------------------------

// The tick that busy() draws: a path whose stroke runs in (.efp-tick).
function tickMark() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "efp-tick");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", "M5 12.5l4.5 4.5L19 7.5");
  svg.append(path);
  return svg;
}

/**
 * Run work() (a function returning a promise) while button shows that it is
 * working: same size, a spinner instead of the label, then a tick on
 * success or a shake on failure. Clicks in the meantime are ignored. Returns
 * what work() returns; a failure is passed on for the caller to report.
 * { tick: false } skips the tick (for a button that is about to go away).
 */
export async function busy(button, work, { tick = true } = {}) {
  if (!button || button.classList.contains("efp-busy")) return undefined;
  // Neither "disabled" nor aria-disabled: daisyUI greys both out. .efp-busy takes
  // the pointer away, aria-busy tells assistive technology, a second call returns early.
  button.style.setProperty("--efp-busy-color", getComputedStyle(button).color);
  const mark = h("span.efp-busy-mark", { "aria-hidden": "true" }, h("span.loading.loading-spinner.loading-sm"));
  button.classList.add("efp-busy");
  button.setAttribute("aria-busy", "true");
  button.append(mark);
  const restore = () => {
    mark.remove();
    button.classList.remove("efp-busy");
    button.removeAttribute("aria-busy");
  };
  try {
    const result = await work();
    if (tick && button.isConnected && !reducedMotion()) {
      mark.replaceChildren(tickMark());
      await new Promise((resolve) => setTimeout(resolve, 650));
    }
    restore();
    return result;
  } catch (error) {
    restore();
    shake(button);
    throw error;
  }
}

// ---- People ----------------------------------------------------------------------------

/** A hue (0-359) that stays the same for the same name. */
export function hue(name) {
  let hash = 0;
  for (const char of String(name)) hash = (hash * 31 + char.codePointAt(0)) >>> 0;
  return hash % 360;
}

/** Up to two letters for a name: "alice" -> "AL", "Bob Smith" -> "BS". */
export function initials(name) {
  const words = String(name || "?").trim().split(/[\s._-]+/).filter(Boolean);
  if (words.length > 1) return (words[0][0] + words[1][0]).toUpperCase();
  return (words[0] || "?").slice(0, 2).toUpperCase();
}

/** A round avatar with initials on a colour of its own: avatar("alice", { size: "2.5rem" }). */
export function avatar(name, { size, title = name } = {}) {
  return h("span.efp-avatar", { title, style: { "--h": hue(name), ...(size ? { "--efp-avatar-size": size } : {}) } }, initials(name));
}

/** "just now", "3 minutes ago", "yesterday", ... for a Date or an ISO string. */
export function timeAgo(value) {
  const then = value instanceof Date ? value : new Date(value);
  const seconds = (then.getTime() - Date.now()) / 1000;
  if (Math.abs(seconds) < 45) return "just now";
  const format = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  const units = [
    ["year", 31536000],
    ["month", 2592000],
    ["week", 604800],
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ];
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) return format.format(Math.round(seconds / size), unit);
  }
  return format.format(Math.round(seconds), "second");
}

// ---- Celebration -----------------------------------------------------------------------

/**
 * Confetti in the theme's colours. from: an element to burst out of
 * (default: two bursts from the lower corners). Nothing under reduced motion.
 */
export async function celebrate({ from, particles = 140 } = {}) {
  if (reducedMotion()) return;
  const confetti = await use("confetti");
  const colors = ["primary", "secondary", "accent", "success", "warning"].map((name) => rgbToHex(color(name)));
  if (from) {
    const box = from.getBoundingClientRect();
    const origin = { x: (box.left + box.width / 2) / innerWidth, y: (box.top + box.height / 2) / innerHeight };
    confetti({ particleCount: particles, spread: 80, startVelocity: 42, origin, colors, scalar: 0.95, ticks: 220 });
    return;
  }
  const burst = { particleCount: Math.round(particles / 2), spread: 62, startVelocity: 58, colors, ticks: 240 };
  confetti({ ...burst, angle: 60, origin: { x: 0, y: 0.85 } });
  confetti({ ...burst, angle: 120, origin: { x: 1, y: 0.85 } });
}

function rgbToHex(rgb) {
  const [r, g, b] = rgb.match(/\d+/g).map(Number);
  return "#" + [r, g, b].map((part) => part.toString(16).padStart(2, "0")).join("");
}

// ---- Charts (ECharts) -----------------------------------------------------------------

function chartTheme() {
  const text = color("base-content");
  const muted = color("base-content", 0.55);
  const line = color("base-content", 0.1);
  const font = getComputedStyle(document.documentElement).fontFamily;
  const axis = {
    axisLine: { lineStyle: { color: line } },
    axisTick: { show: false },
    axisLabel: { color: muted, fontFamily: font },
    splitLine: { lineStyle: { color: line, type: [4, 4] } },
    nameTextStyle: { color: muted },
  };
  return {
    color: ["primary", "secondary", "accent", "info", "success", "warning", "error"].map((name) => color(name)),
    backgroundColor: "transparent",
    textStyle: { fontFamily: font, color: text },
    title: { textStyle: { color: text, fontWeight: 600 } },
    legend: { textStyle: { color: muted }, icon: "roundRect", itemWidth: 12, itemHeight: 8 },
    tooltip: {
      backgroundColor: color("base-100"),
      borderColor: line,
      borderWidth: 1,
      padding: [8, 12],
      textStyle: { color: text, fontFamily: font, fontSize: 12 },
      extraCssText: "border-radius: 12px; box-shadow: 0 12px 32px -8px rgba(15, 23, 42, 0.28); backdrop-filter: blur(8px);",
    },
    categoryAxis: axis,
    valueAxis: axis,
    timeAxis: axis,
    animationDuration: 900,
    animationEasing: "cubicOut",
    animationDurationUpdate: 650,
    animationEasingUpdate: "cubicInOut",
  };
}

/**
 * An ECharts chart in element, in the kit's colours and fonts, that follows
 * the element's size and the light or dark theme. option is an ordinary
 * ECharts option (https://echarts.apache.org/en/option.html). Returns the
 * chart; chart.setOption(...) updates it with an animation.
 */
export async function chart(element, option) {
  const echarts = await use("echarts");
  const theme = "efp-" + Math.random().toString(36).slice(2);
  const still = reducedMotion();
  // Every option given so far, to draw the chart again in the colours of a new theme.
  const given = [[still ? { animation: false, ...option } : option]];
  const draw = (animate) => {
    echarts.registerTheme(theme, chartTheme());
    const instance = echarts.init(element, theme);
    given.forEach((args, index) => {
      const [first, ...rest] = args;
      instance.setOption(index === 0 && !animate ? { ...first, animation: false } : first, ...rest);
    });
    return instance;
  };
  const holder = { target: draw(true) };
  new ResizeObserver(() => holder.target.resize()).observe(element);
  onThemeChange(() => {
    holder.target.dispose();
    holder.target = draw(false);
  });
  // The caller keeps one object even when a theme change replaces the chart.
  return new Proxy(holder, {
    get(target, key) {
      if (key === "setOption") {
        return (...args) => {
          given.push(args);
          return target.target.setOption(...args);
        };
      }
      const value = target.target[key];
      return typeof value === "function" ? value.bind(target.target) : value;
    },
  });
}
