// Two made-up phone screens, drawn on a canvas, for trying the tool without
// any files. The "actual" one differs in the ways a real test run does:
//
// - the header is a shade lighter (6 of 255 per channel): gone at the
//   default tolerance, visible when the slider goes below 6;
// - one avatar sits half a pixel to the right, so only the soft edge of its
//   circle differs: that is what "Ignore anti-aliasing" is for;
// - a badge appeared, one line of text changed, the button moved and
//   changed colour: real differences, each a region of its own.

const FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif";
const AVATARS = ["#8a63d2", "#2f9e77", "#c9862a", "#c8507c"];

function rounded(context, x, y, width, height, radius) {
  context.beginPath();
  if (context.roundRect) {
    context.roundRect(x, y, width, height, radius);
  } else {
    context.rect(x, y, width, height);
  }
  context.fill();
}

export function drawSample(variant) {
  const actual = variant === "actual";
  const canvas = document.createElement("canvas");
  canvas.width = 360;
  canvas.height = 640;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.textBaseline = "middle";

  context.fillStyle = "#f4f5f7";
  context.fillRect(0, 0, 360, 640);
  context.fillStyle = actual ? "#2a5fd0" : "#2459ca";
  context.fillRect(0, 0, 360, 64);
  context.fillStyle = "#ffffff";
  context.font = "600 20px " + FONT;
  context.fillText("Orders", 20, 32);
  if (actual) {
    context.fillStyle = "#e5484d";
    context.beginPath();
    context.arc(322, 32, 12, 0, Math.PI * 2);
    context.fill();
    context.fillStyle = "#ffffff";
    context.font = "600 13px " + FONT;
    context.textAlign = "center";
    context.fillText("3", 322, 33);
    context.textAlign = "left";
  }

  const rows = [
    ["Order 1042", "Delivered"],
    ["Order 1043", actual ? "Delayed" : "On its way"],
    ["Order 1044", "Being packed"],
    ["Order 1045", "Placed"],
  ];
  rows.forEach(([title, state], row) => {
    const top = 84 + row * 84;
    context.fillStyle = "#ffffff";
    rounded(context, 16, top, 328, 68, 10);
    context.fillStyle = AVATARS[row];
    context.beginPath();
    context.arc(50 + (actual && row === 2 ? 0.5 : 0), top + 34, 18, 0, Math.PI * 2);
    context.fill();
    context.fillStyle = "#1c2026";
    context.font = "600 15px " + FONT;
    context.fillText(title, 82, top + 24);
    context.fillStyle = "#5d6673";
    context.font = "13px " + FONT;
    context.fillText(state, 82, top + 46);
  });

  const buttonTop = actual ? 548 : 540;
  context.fillStyle = actual ? "#1f9d6b" : "#2459ca";
  rounded(context, 60, buttonTop, 240, 48, 24);
  context.fillStyle = "#ffffff";
  context.font = "600 16px " + FONT;
  context.textAlign = "center";
  context.fillText("Track all orders", 180, buttonTop + 24);
  return canvas;
}
