// EFP kit 1.0.0: teaches Tailwind the daisyUI theme. Load it right BEFORE
// tailwindcss.js (both are ordinary scripts, in <head>):
//
//   <script src="/_efp/lib/efp-kit@1.0.0/tailwind-theme.js"></script>
//   <script src="/_efp/lib/tailwindcss-browser@4.3.5/tailwindcss.js"></script>
//
// daisyui.css already has bg-, text- and border- classes for its colours.
// This block lets every other Tailwind class use them too: ring-primary,
// outline-accent, from-primary to-secondary, shadow-primary/30, fill-error,
// divide-base-content/10 ... "inline reference" makes each class read the
// daisyUI variable itself (so data-theme still switches everything) without
// Tailwind declaring a variable of the same name over daisyUI's.
//
// The dark: variant follows the kit's themes: data-theme="efp-dark", or no
// data-theme and a system that asks for dark. With another daisyUI theme,
// prefer its colours (bg-base-100, text-base-content ...), which follow any
// theme by themselves.
(function () {
  var colors = [
    "base-100", "base-200", "base-300", "base-content",
    "primary", "primary-content", "secondary", "secondary-content",
    "accent", "accent-content", "neutral", "neutral-content",
    "info", "info-content", "success", "success-content",
    "warning", "warning-content", "error", "error-content",
  ];
  var lines = colors.map(function (name) {
    return "  --color-" + name + ": var(--color-" + name + ");";
  });
  ["selector", "field", "box"].forEach(function (name) {
    lines.push("  --radius-" + name + ": var(--radius-" + name + ");");
  });
  var style = document.createElement("style");
  style.type = "text/tailwindcss";
  style.textContent =
    "@theme inline reference {\n" + lines.join("\n") + "\n}\n" +
    "@custom-variant dark {\n" +
    "  &:where([data-theme=efp-dark], [data-theme=efp-dark] *) { @slot; }\n" +
    "  @media (prefers-color-scheme: dark) {\n" +
    "    &:where(:root:not([data-theme]), :root:not([data-theme]) *) { @slot; }\n" +
    "  }\n" +
    "}\n";
  (document.currentScript || document.head.lastChild).after(style);
})();
