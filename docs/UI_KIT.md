# The UI kit: how an app looks

Every gateway serves a set of browser libraries at `/_efp/lib/`, the same
for every app, with nothing to download from the internet: daisyUI for the
components and themes, Tailwind CSS for layout classes, the EFP kit for the
look the examples share, and libraries for motion, drag and drop, charts and
icons. A page that links them looks finished from its first line, in light
and dark, on a phone and on a wide screen, and anybody who copies it can give
it another look by changing one attribute.

Contents: [Start a page](#start-a-page) - [What is there](#what-is-there) -
[The kit's classes](#the-kits-classes) - [The kit's JavaScript](#the-kits-javascript) -
[Make it yours](#make-it-yours) - [Rules for a good page](#rules-for-a-good-page) -
[Streamlit and Gradio](#streamlit-and-gradio) - [Changing the libraries](#changing-the-libraries)

## Start a page

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>My app</title>
  <link rel="stylesheet" href="/_efp/lib/daisyui@5.8.2/daisyui.css">
  <link rel="stylesheet" href="/_efp/lib/efp-kit@1.0.0/efp-kit.css">
  <script src="/_efp/lib/efp-kit@1.0.0/tailwind-theme.js"></script>
  <script src="/_efp/lib/tailwindcss-browser@4.3.5/tailwindcss.js"></script>
  <link rel="stylesheet" href="static/style.css">
</head>
<body class="efp-page">
  <header class="efp-appbar">
    <span class="efp-mark" aria-hidden="true"><i data-lucide="rocket"></i></span>
    <div>
      <h1 class="font-semibold leading-tight">My app</h1>
      <p class="text-xs text-base-content/60">What it is for</p>
    </div>
    <button type="button" id="theme" class="btn btn-ghost btn-square btn-sm ml-auto"></button>
  </header>
  <main class="efp-container py-8 grid gap-6">
    <section class="efp-surface p-6 efp-rise">
      <h2 class="text-lg font-semibold">Hello</h2>
      <button type="button" id="save" class="btn btn-primary efp-press">Save</button>
    </section>
  </main>
  <script type="module" src="static/app.js"></script>
</body>
</html>
```

```js
// static/app.js
import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";

await kit.ready();                                  // the icons are drawn
kit.themeSwitch(document.getElementById("theme"));  // system, light, dark

document.getElementById("save").addEventListener("click", async (event) => {
  try {
    await kit.busy(event.currentTarget, () => fetch("api/save", { method: "POST" }));
    kit.toast("Saved", { kind: "success" });
  } catch (error) {
    kit.toast("It could not be saved. Try again.", { kind: "error" });
  }
});
```

The order in `<head>` matters: daisyUI, then the kit's style sheet, then
`tailwind-theme.js` right before `tailwindcss.js`, then the app's own style
sheet, which wins over all of them. Paths under `/_efp/lib/` are the one
exception to "relative URLs only" (docs/CONTRACTS.md, section 6.6); the
app's own files keep relative URLs. `efp-app dev` serves `/_efp/lib/` too,
so the page looks the same on a developer's machine. `efp-app check` warns
about a library file or a version that does not exist.

Every example with a page of its own is built this way
([examples/README.md](../examples/README.md)). Read the one closest to your
app next to this page: [team-poll](../examples/team-poll/) is the
shortest, [planning-poker](../examples/planning-poker/) and
[retro-board](../examples/retro-board/) show live team pages,
[build-health](../examples/build-health/) a dashboard with charts, and
[mind-map](../examples/mind-map/) an editor.

## What is there

| Path under `/_efp/lib/` | What | Used as | Licence |
|---|---|---|---|
| `daisyui@5.8.2/daisyui.css` | components (`btn`, `card`, `input`, `modal`, `badge`, `alert`, `table`, `tabs`, `radial-progress`, ...) and the `light` and `dark` themes | a style sheet | MIT |
| `daisyui@5.8.2/themes.css` | all 35 daisyUI themes, for `data-theme="..."` | a style sheet | MIT |
| `tailwindcss-browser@4.3.5/tailwindcss.js` | Tailwind's classes (`flex`, `grid`, `gap-4`, `text-sm`, `md:grid-cols-3`, ...), written by the browser for the classes the page uses | a script | MIT |
| `efp-kit@1.0.0/efp-kit.css` | the "efp" theme, typefaces, the kit's classes | a style sheet | - |
| `efp-kit@1.0.0/tailwind-theme.js` | teaches Tailwind the theme's colours (`ring-primary`, `from-secondary`, `dark:`) | a script, before Tailwind | - |
| `efp-kit@1.0.0/efp-kit.js` | helpers: toasts, busy buttons, motion, charts, icons | an ES module | - |
| `motion@14.14.0/motion.js` | animation with springs, stagger, scroll, in-view (`window.Motion`) | `kit.use("motion")` | MIT |
| `auto-animate@0.10.2/auto-animate.js` | lists that animate additions, removals and moves | `kit.animateList(list)` | MIT |
| `sortablejs@1.15.7/Sortable.min.js` | drag and drop to reorder (`window.Sortable`) | `kit.sortable(list)` | MIT |
| `canvas-confetti@1.9.4/confetti.js` | confetti (`window.confetti`) | `kit.celebrate()` | ISC |
| `echarts@6.1.0/echarts.min.js` | charts (`window.echarts`) | `kit.chart(element, option)` | Apache-2.0 |
| `lucide@1.77.0/lucide.min.js` | 1,600 icons (`<i data-lucide="name">`, https://lucide.dev/icons) | `kit.icons()`, `kit.icon(name)` | ISC |
| `inter@5.3.0/`, `jetbrains-mono@5.3.0/` | the typefaces, Latin; other scripts use the system's fonts | through `efp-kit.css` | OFL-1.1 |

`lib/MANIFEST.json` lists every third-party file with its SHA-256. The
libraries the kit loads by itself (`motion`, `lucide`, `confetti`,
`sortable`, `echarts`, `autoAnimate`) load on first use: a page pays only
for what it uses. A browser keeps each file for a year, and every app on the
host shares that copy.

## The kit's classes

| Class | What it does |
|---|---|
| `efp-page` | on `<body>`: the page colour with soft fields of the theme's colours, and a short fade-in |
| `efp-aurora` | on an empty first child of `<body>`: a slowly drifting glow behind everything |
| `efp-appbar` | the sticky, translucent bar at the top |
| `efp-mark` | the app's icon on a gradient tile, for the bar |
| `efp-container` | the page's width (76rem) with side margins |
| `efp-surface` | a card or panel: background, hairline border, small shadow |
| `efp-glass` | a translucent panel that blurs what is behind it |
| `efp-lift` | lifts on hover |
| `efp-press` | gives a little when pressed |
| `efp-rise` | rises into place when it appears; `style="--i: 2"` delays it by two steps |
| `efp-float` | floats up and down, slowly |
| `efp-shine` | a sheen sweeps across on hover (for a primary button) |
| `efp-gradient-text` | text in the theme's gradient |
| `efp-live` | a dot with a ring that breathes: something is live |
| `efp-empty`, `efp-empty-icon` | an empty state: a floating icon, a sentence, what to do next |
| `efp-avatar`, `efp-avatars` | a round avatar with initials; a row of overlapping ones (made by `kit.avatar`) |
| `efp-number` | figures of equal width, for numbers that change |

The kit also corrects daisyUI in three places, so an app does not have to:
a field with the focus is outlined in the theme's main colour (one with a
colour of its own, such as `input-error`, keeps it); soft and outlined
warnings (`badge-soft badge-warning`, `alert-soft alert-warning`, ...) get
text dark enough to read on a light page; and a busy button shows no ghost
of its label.

Tokens, to use in an app's own style sheet: `--efp-ease-out`,
`--efp-spring`, `--efp-bounce` (easings), `--efp-fast`, `--efp-medium`,
`--efp-slow` (durations), `--efp-line`, `--efp-line-strong` (borders),
`--efp-shadow-sm`, `--efp-shadow-md`, `--efp-shadow-lg`, `--efp-glow`, and
daisyUI's colours: `--color-primary`, `--color-base-100`,
`--color-base-content` and the others.

## The kit's JavaScript

`import * as kit from "/_efp/lib/efp-kit@1.0.0/efp-kit.js";` Every
animation asks `kit.reducedMotion()` first: a visitor who asked the system
for less motion gets the end state at once.

| Name | What it does |
|---|---|
| `ready()` | resolves once the icons of the page are drawn; await it before the first drawing |
| `h(selector, props, ...children)` | an element: `h("button", { class: "btn", onclick }, "Save")`. Text is always text: nothing is parsed as HTML |
| `icon(name, className)`, `icons(root)` | an icon element; draw every `<i data-lucide>` in `root`. After `ready()` an icon is drawn at once; one made earlier is drawn when lucide arrives, if it is in the page by then |
| `toast(message, { kind, timeout, action })` | a message in the corner that goes away by itself; `kind`: info, success, warning, error |
| `busy(button, work, { tick })` | the button shows a spinner (same size) while `work()` runs, then a tick, or a shake on failure |
| `rise(targets)`, `appear(targets)`, `pop(targets)`, `shake(targets)`, `flash(targets)` | entrance, entrance with a bounce, a bounce, "no", "this changed" |
| `countUp(element, value, { format })` | the number counts to its new value |
| `transition(update)` | runs `update` inside a View Transition, where the browser has them |
| `animateList(list)` | the list's children animate when added, removed or moved (AutoAnimate) |
| `sortable(list, options)` | drag to reorder (SortableJS) with the kit's look |
| `chart(element, option)` | an ECharts chart in the theme's colours and fonts that follows its box and the theme; `.setOption()` updates it |
| `celebrate({ from })` | confetti in the theme's colours |
| `avatar(name, { size })`, `hue(name)`, `initials(name)` | an avatar with a colour of its own per name |
| `timeAgo(date)` | "just now", "3 minutes ago", "yesterday" |
| `themeSwitch(button)` | a button that switches system, light and dark, remembered in this browser; several on one page switch together |
| `color(name, alpha)` | a theme colour as `rgb(...)`, for code that cannot read `oklch()` |
| `onThemeChange(listener)` | called when the light or dark theme changes |
| `use(name)` | a library by name, loaded once: `motion`, `lucide`, `confetti`, `sortable`, `echarts`, `autoAnimate` |
| `EASE`, `SPRING`, `BOUNCE` | easings for `element.animate()` |

## Make it yours

- **Another theme.** Link `/_efp/lib/daisyui@5.8.2/themes.css` and set
  `data-theme="cupcake"` (or `retro`, `cyberpunk`, `nord`, `business`, ...
  https://daisyui.com/docs/themes/) on `<html>`. Everything follows: the
  components, the kit's classes, the charts, the cards of planning poker.
  `data-theme="efp"` or `"efp-dark"` fixes the kit's own theme to light or
  dark; without `data-theme` it follows the system.
- **Your own colours.** A block of daisyUI variables in the app's style
  sheet, for example made with https://daisyui.com/theme-generator/:
  `[data-theme="ours"] { color-scheme: light; --color-primary: oklch(60% 0.2 30); ... }`
  and `data-theme="ours"` on `<html>`.
- **Another typeface.** `:root { --font-sans: "Your Font", sans-serif; }` in
  the app's style sheet (the font file goes into the app's own `static/`).
- **Less movement.** Leave out `efp-aurora`, `efp-rise` and the kit's motion
  calls; nothing else depends on them.
- **Faster first paint.** The Tailwind browser build writes its classes when
  the page loads. An app that wants none of that can compile one style
  sheet ahead with Tailwind's standalone command-line tool (a single
  program, no Node.js) and link that instead of `tailwindcss.js`.

## Rules for a good page

The examples keep these; an app written or converted by an agent keeps them
too (`AGENTS.md`).

1. **Every action shows that it is working**: `kit.busy(button, ...)` on the
   button that started it, or a skeleton (`<div class="skeleton h-24">`) in
   the place of what is loading. Nothing waits silently.
2. **Nothing jumps.** The first paint holds the page's shape (skeletons, not
   an empty page); a busy button keeps its size; icons keep their room
   before they are drawn.
3. **An empty state says what to do**: `efp-empty` with an icon, a sentence
   and, when there is one, the button for the next step.
4. **A failure is a sentence**: in an `alert` where it happened, or
   `kit.toast(message, { kind: "error" })`; never a stack trace, never a
   blank page.
5. **Light and dark, phone and desktop.** Use the theme's colours
   (`bg-base-100`, `text-base-content/70`, `--color-primary`), never fixed
   ones, and look at the page at 390 pixels wide.
6. **Motion has a meaning**: something arrived, moved, was counted, was
   done. The kit's helpers skip it under reduced motion; an app's own
   animation does the same (`kit.reducedMotion()`).
7. **No HTML from data.** Build elements with `kit.h` or `textContent`;
   whatever a visitor or another system wrote stays text.
8. **Everything works with the keyboard**: real buttons and links, a visible
   focus, `aria-label` on a button that shows only an icon.

## Streamlit and Gradio

These pages are drawn by their framework, so the libraries above do not
apply. Give them the same colours with the framework's own theme:
`.streamlit/config.toml` (a `[theme]` section with
`primaryColor = "#2f5fe0"` and `font = "sans serif"`, see
[csv-explorer](../examples/csv-explorer/)) or, for Gradio 6,
`demo.launch(theme=gr.themes.Soft(primary_hue="blue", font=[...system fonts...]))`
([test-case-writer](../examples/test-case-writer/)). Gradio 6 takes the theme
in `launch()`, not in `gr.Blocks()`; name the system's fonts, because the
theme's default font is a web font from another host.

## Changing the libraries

`scripts/vendor_libs.py` pins every third-party library to one version,
downloads it from the npm registry (`NPM_REGISTRY` names a mirror), checks
it against the registry's integrity value, and writes `lib/<name>@<version>/`
and `lib/MANIFEST.json`. `python scripts/vendor_libs.py --check` (and
`sdk/tests/test_lib.py`) compares `lib/` with the manifest without the
network.

A file under `lib/` never changes in place: browsers keep it for a year. A
new version of a library, or of the kit, is a new directory; keep the old
one while a published app may link it. The kit's own files
(`lib/efp-kit@<version>/`) are written by hand and are not in the manifest.
