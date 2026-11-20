// The connection to a room: a WebSocket that is opened again when it drops.
//
// The address is built from the page's own address, never written out: the
// app is served under /{slug}/ once deployed, over https, and locally under
// / over http. "ws/abc" relative to the page is right in both places.

export const GIVE_UP_AFTER = 8; // attempts in a row; then the visitor decides
const SIGN_IN_REQUIRED = 4401; // close codes of the backend (main.py)
const NO_SUCH_ROOM = 4404;

export function socketUrl(base, roomId) {
  const url = new URL("ws/" + encodeURIComponent(roomId), base);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.search = "";
  url.hash = "";
  return url.href;
}

// How long to wait before attempt number n (0, 1, 2 ...): 1, 2, 4, 8, 16
// seconds, then 30. A server that is restarting is back within seconds; one
// that is away should not be asked every second by every open tab.
export function nextDelay(attempt) {
  return Math.min(30000, 1000 * 2 ** attempt);
}

// Open the room and keep it open. "handlers" get told:
//   onState(room)            the room, whenever it changed
//   onRefusal(message)       the server turned a request of this page down
//   onStatus(status, delay)  "connecting", "connected", "waiting" (delay in ms), "paused",
//                            "stopped", "signed-out", "missing"
export function openRoom(roomId, handlers) {
  let socket = null;
  let attempt = 0;
  let timer = null;
  let finished = false; // the server said no, or the page asked to close: no more attempts

  function connect() {
    clearTimeout(timer);
    timer = null;
    if (finished) {
      return;
    }
    handlers.onStatus("connecting");
    socket = new WebSocket(socketUrl(document.baseURI, roomId));
    const mine = socket;
    mine.addEventListener("open", () => {
      attempt = 0;
      handlers.onStatus("connected");
    });
    mine.addEventListener("message", (event) => {
      let message = null;
      try {
        message = JSON.parse(event.data);
      } catch (error) {
        return;
      }
      if (message.type === "state") {
        handlers.onState(message.room);
      } else if (message.type === "error") {
        handlers.onRefusal(message.message);
      }
    });
    mine.addEventListener("close", (event) => {
      if (mine !== socket || finished) {
        return;
      }
      socket = null;
      if (event.code === SIGN_IN_REQUIRED || event.code === NO_SUCH_ROOM) {
        finished = true; // asking again would get the same answer
        handlers.onStatus(event.code === SIGN_IN_REQUIRED ? "signed-out" : "missing");
      } else if (document.hidden) {
        // A tab nobody looks at does not keep knocking: every attempt would
        // wake the app. It connects again when it is looked at.
        handlers.onStatus("paused");
      } else if (attempt >= GIVE_UP_AFTER) {
        handlers.onStatus("stopped");
      } else {
        const delay = nextDelay(attempt);
        attempt += 1;
        handlers.onStatus("waiting", delay);
        // One timer per attempt, set when the previous attempt has failed.
        // Never a repeating timer: that would go on behind a page that is
        // connected, or closed.
        timer = setTimeout(connect, delay);
      }
    });
  }

  function retryNow() {
    if (!finished && !socket) {
      attempt = 0;
      connect();
    }
  }

  // Looked at again, or back on the network: try at once.
  const onVisible = () => {
    if (!document.hidden) {
      retryNow();
    }
  };
  document.addEventListener("visibilitychange", onVisible);
  window.addEventListener("online", retryNow);
  connect();

  return {
    retryNow,
    // False when there is no connection to send on; the page then says so.
    send(message) {
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify(message));
        return true;
      }
      return false;
    },
    close() {
      finished = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", retryNow);
      if (socket) {
        socket.close();
      }
    },
  };
}
