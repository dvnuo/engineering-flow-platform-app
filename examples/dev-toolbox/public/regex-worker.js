// Runs one regular expression per message, away from the page. If the
// pattern never finishes, the page ends this worker (lib/regex.js,
// createRunner) and the tab stays alive.

import { findMatches } from "./lib/regex.js";

self.onmessage = (event) => {
  const { id, pattern, flags, text } = event.data;
  self.postMessage({ id, result: findMatches(pattern, flags, text) });
};

// The runner starts its clock only now: loading this file is not the pattern's fault.
self.postMessage({ ready: true });
