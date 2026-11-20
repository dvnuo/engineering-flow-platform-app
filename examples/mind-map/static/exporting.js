// Exports: the map as an SVG file, a PNG file and a Markdown outline. All
// three are made in the browser from what is on the page; the server is not
// asked for anything.

import { layoutTree } from "./layout.js";
import { measureText, standalone } from "./render.js";
import { toMarkdown } from "./tree.js";

const MAX_PNG_SIDE = 8000; // browsers refuse a canvas much larger than this

// A file name from a title: letters and digits kept, everything else a dash.
export function fileName(title, extension) {
  const stem = String(title)
    .normalize("NFKD")
    .replace(/[^A-Za-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
  return (stem || "mind-map") + "." + extension;
}

function download(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  // The download has started by the next turn of the event loop.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// The whole map, every branch open, whatever is collapsed on the page.
function wholeDrawing(nodes) {
  const open = nodes.map((node) => (node.collapsed ? { ...node, collapsed: false } : node));
  const drawing = standalone(open, layoutTree(open, measureText));
  return { ...drawing, text: new XMLSerializer().serializeToString(drawing.svg) };
}

export function exportSvg(nodes, title) {
  const drawing = wholeDrawing(nodes);
  download(new Blob([drawing.text], { type: "image/svg+xml" }), fileName(title, "svg"));
}

export function exportMarkdown(nodes, title) {
  download(new Blob([toMarkdown(nodes)], { type: "text/markdown" }), fileName(title, "md"));
}

// The PNG is the SVG painted on a canvas: the browser loads the SVG text as
// an image (from memory, nothing is fetched) and the canvas turns it into
// pixels, two per unit so that it stays sharp when enlarged.
export function exportPng(nodes, title) {
  const drawing = wholeDrawing(nodes);
  const scale = Math.min(2, MAX_PNG_SIDE / drawing.width, MAX_PNG_SIDE / drawing.height);
  const url = URL.createObjectURL(new Blob([drawing.text], { type: "image/svg+xml" }));
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(drawing.width * scale));
      canvas.height = Math.max(1, Math.round(drawing.height * scale));
      canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      canvas.toBlob((blob) => {
        if (!blob) {
          reject(new Error("The picture could not be made."));
          return;
        }
        download(blob, fileName(title, "png"));
        resolve();
      }, "image/png");
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("The picture could not be made."));
    };
    image.src = url;
  });
}
