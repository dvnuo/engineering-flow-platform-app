"""examples/planning-poker: a live room over a WebSocket, picks that stay on the server, state that survives a restart.

The backend runs for real against a sandbox data directory (``deployed``). The
WebSocket is driven with the test client's ``websocket_connect``, with the
header the gateway signs on the handshake. The test client is entered as a
context (``with client``): all its connections then share one event loop, as
they do in the one process that serves a deployed app. The page's connection
logic and its wording are plain JavaScript modules, run with node when it is
installed.
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from efp_app import Visitor, errors, storage
from conftest import EXAMPLES
from example_support import client_for, load_module, signed

HERE = EXAMPLES / "planning-poker"
STORY = "ABC-1 Export the report as PDF"
LINK = "https://jira.example.com/browse/ABC-1"

pytestmark = pytest.mark.skipif(not HERE.is_dir(), reason="needs a checkout of the repository (examples/)")


@pytest.fixture
def poker(deployed):
    pytest.importorskip("fastapi")
    return load_module("example_planning_poker", HERE / "main.py")


def mallory():
    return signed(sub=13, username="mallory", name="Mallory")


def open_room(client, title=STORY, link=None, headers=None, prefix=""):
    made = client.post(prefix + "/api/rooms", json={"title": title, "link": link}, headers=headers or signed())
    assert made.status_code == 201, made.text
    return made.json()["id"]


def state(socket):
    """The next message of a connection, which must be the room."""
    message = socket.receive_json()
    assert message["type"] == "state", message
    return message["room"]


def refusal(socket):
    message = socket.receive_json()
    assert message["type"] == "error", message
    return message["code"]


def member(room, name):
    [found] = [item for item in room["members"] if item["name"] == name]
    return found


def closed_with(client, path, headers=None):
    """The close code of a connection that the app accepts and ends at once."""
    from starlette.websockets import WebSocketDisconnect

    with client.websocket_connect(path, headers=headers or {}) as socket:
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
    return closed.value.code


# --- A round, live ------------------------------------------------------------------------------------


def test_planning_poker_keeps_a_pick_on_the_server_until_somebody_reveals(poker):
    with client_for(poker.app) as client:
        room_id = open_room(client, link=LINK)
        with client.websocket_connect("/ws/" + room_id, headers=signed()) as bob:
            alone = state(bob)
            assert (alone["id"], alone["title"], alone["link"], alone["round"], alone["revealed"]) == (room_id, STORY, LINK, 1, False)
            assert alone["deck"] == poker.DECK and alone["you"] == {"id": 12, "name": "Bob", "card": None}
            assert alone["members"] == [{"id": 12, "name": "Bob", "online": True, "picked": False, "card": None}]
            assert alone["result"] is None and alone["history"] == []

            with client.websocket_connect("/ws/" + room_id, headers=mallory()) as her:
                assert [item["name"] for item in state(her)["members"]] == ["Bob", "Mallory"]
                assert len(state(bob)["members"]) == 2  # Bob is told that she came

                bob.send_json({"type": "pick", "card": "5", "member": 13})  # a message cannot speak for somebody else
                mine, hers = state(bob), state(her)
                assert mine["you"]["card"] == "5" and member(mine, "Bob")["card"] is None
                assert member(hers, "Bob") == {"id": 12, "name": "Bob", "online": True, "picked": True, "card": None}
                assert member(hers, "Mallory")["picked"] is False and hers["you"]["card"] is None
                # Apart from the deck, nothing she was sent holds his card.
                assert "5" not in json.dumps([hers["members"], hers["you"], hers["result"], hers["history"]])

                her.send_json({"type": "pick", "card": "13"})
                assert member(state(bob), "Mallory") == {"id": 13, "name": "Mallory", "online": True, "picked": True, "card": None}
                assert state(her)["you"]["card"] == "13"
                her.send_json({"type": "pick", "card": None})  # she takes it back
                assert member(state(bob), "Mallory")["picked"] is False and state(her)["you"]["card"] is None
                her.send_json({"type": "pick", "card": "8"})
                state(bob), state(her)

                # The server knows, and has saved it: memory is not the only place.
                kept = storage.load_json("rooms/%s.json" % room_id)
                assert kept["picks"] == {"12": {"name": "Bob", "card": "5"}, "13": {"name": "Mallory", "card": "8"}}
                assert kept["revealed"] is False

                her.send_json({"type": "reveal"})
                for socket in (bob, her):
                    shown = state(socket)
                    assert shown["revealed"] is True
                    assert [(item["name"], item["card"]) for item in shown["members"]] == [("Bob", "5"), ("Mallory", "8")]
                    assert shown["result"] == {
                        "votes": 2,
                        "spread": [{"card": "5", "count": 1}, {"card": "8", "count": 1}],
                        "average": 6.5,
                        "median": 6.5,
                        "consensus": False,
                    }

                bob.send_json({"type": "pick", "card": "3"})  # too late: only the member who asked hears about it
                assert refusal(bob) == "round_revealed"
                bob.send_json({"type": "reveal"})  # pressed by two members at once: the second finds it done
                assert state(bob)["revealed"] is True and state(her)["revealed"] is True

                bob.send_json({"type": "new_round", "title": " ABC-2  Import ", "link": None})
                for socket in (bob, her):
                    fresh = state(socket)
                    assert (fresh["round"], fresh["revealed"], fresh["title"], fresh["link"]) == (2, False, "ABC-2 Import", None)
                    assert not any(item["picked"] for item in fresh["members"]) and fresh["you"]["card"] is None
                    assert fresh["result"] is None
                    [done] = fresh["history"]
                    assert (done["round"], done["title"], done["link"]) == (1, STORY, LINK)
                    assert done["picks"] == [{"name": "Bob", "card": "5"}, {"name": "Mallory", "card": "8"}]
                    assert done["result"]["average"] == 6.5 and done["result"]["consensus"] is False

            gone = state(bob)  # she closed the page: he is told
            assert [item["name"] for item in gone["members"]] == ["Bob"]

        kept = storage.load_json("rooms/%s.json" % room_id)
        assert (kept["round"], kept["picks"], len(kept["history"]), kept["title"]) == (2, {}, 1, "ABC-2 Import")
        assert poker.ROOMS == {}  # nobody is in the room: it lives in storage alone


def test_planning_poker_gives_the_room_back_after_a_restart(poker):
    with client_for(poker.app) as client:
        room_id = open_room(client)
        with client.websocket_connect("/ws/" + room_id, headers=signed()) as bob:
            state(bob)
            bob.send_json({"type": "pick", "card": "3"})
            state(bob)
            bob.send_json({"type": "reveal"})
            state(bob)
            bob.send_json({"type": "new_round"})  # the same story again
            assert state(bob)["title"] == STORY
            bob.send_json({"type": "pick", "card": "13"})
            assert state(bob)["you"]["card"] == "13"

    # The process ends here: a deployment, or the host stopping an idle app. A new one starts with empty memory.
    restarted = load_module("example_planning_poker_restarted", HERE / "main.py")
    assert restarted.ROOMS == {}
    with client_for(restarted.app) as client:
        with client.websocket_connect("/ws/" + room_id, headers=mallory()) as her:
            back = state(her)
            assert (back["round"], back["revealed"], back["title"]) == (2, False, STORY)
            assert [(done["round"], done["picks"]) for done in back["history"]] == [(1, [{"name": "Bob", "card": "3"}])]
            # His pick of the running round is still there, and still hidden; he is not connected.
            assert member(back, "Bob") == {"id": 12, "name": "Bob", "online": False, "picked": True, "card": None}
            with client.websocket_connect("/ws/" + room_id, headers=signed()) as bob:
                assert state(bob)["you"]["card"] == "13"  # the page that reconnects has its card back
                assert member(state(her), "Bob")["online"] is True


def test_planning_poker_counts_two_tabs_of_one_member_once(poker):
    with client_for(poker.app) as client:
        room_id = open_room(client)
        with client.websocket_connect("/ws/" + room_id, headers=signed()) as first:
            state(first)
            with client.websocket_connect("/ws/" + room_id, headers=signed()) as second:
                assert len(state(second)["members"]) == 1 and len(state(first)["members"]) == 1
                second.send_json({"type": "pick", "card": "2"})
                assert state(first)["you"]["card"] == "2" and state(second)["you"]["card"] == "2"
                assert len(poker.ROOMS[room_id].sockets) == 2
            assert member(state(first), "Bob")["online"] is True  # one tab closed, the member is still here


# --- Who may come in, and what may be said ----------------------------------------------------------------


def test_planning_poker_turns_away_guests_and_unknown_rooms(poker):
    with client_for(poker.app) as client:
        room_id = open_room(client)
        assert closed_with(client, "/ws/" + room_id) == 4401  # no signed header: not signed in
        assert closed_with(client, "/ws/" + room_id, {"X-EFP-Visitor": "v1.forged.forged"}) == 4401
        expired = signed(exp=int(time.time()) - 5)
        assert closed_with(client, "/ws/" + room_id, expired) == 4401
        for unknown in ("0" * 10, "nope", "A" * 10, room_id + "0"):
            assert closed_with(client, "/ws/" + unknown, signed()) == 4404
        assert poker.ROOMS == {}
        assert storage.load_json("rooms/%s.json" % room_id)["picks"] == {}


def test_planning_poker_refuses_messages_it_cannot_read(poker):
    with client_for(poker.app) as client:
        room_id = open_room(client)
        with client.websocket_connect("/ws/" + room_id, headers=signed()) as bob:
            state(bob)
            for text in ("not json", "[1, 2]", '"pick"', "{}", '{"type": "shuffle"}', "[" * 1500, '{"type": "pick", "card": "%s"}' % ("x" * 3000)):
                bob.send_text(text)
                assert refusal(bob) == "bad_message", text[:20]
            bob.send_bytes(b"\x00\x01")
            assert refusal(bob) == "bad_message"
            for message, code in (
                ({"type": "pick", "card": "42"}, "unknown_card"),
                ({"type": "pick", "card": ["5"]}, "unknown_card"),
                ({"type": "reveal"}, "nothing_to_reveal"),
                ({"type": "new_round", "title": "  "}, "bad_title"),
                ({"type": "new_round", "title": "x" * 121}, "bad_title"),
                ({"type": "new_round", "link": "javascript:alert(1)"}, "bad_link"),
            ):
                bob.send_json(message)
                assert refusal(bob) == code, message
            kept = storage.load_json("rooms/%s.json" % room_id)
            assert (kept["round"], kept["picks"], kept["updated_at"]) == (1, {}, kept["created_at"])  # nothing was saved

            bob.send_json({"type": "pick", "card": "?"})  # and the connection is as good as before
            assert state(bob)["you"]["card"] == "?"


def test_planning_poker_leaves_the_room_as_it_was_when_a_save_fails(poker, monkeypatch):
    really_save = storage.save_json

    def full(key, value):
        raise errors.QuotaExceededError("no space left to save %r" % key)

    with client_for(poker.app) as client:
        room_id = open_room(client)
        with client.websocket_connect("/ws/" + room_id, headers=signed()) as bob:
            state(bob)
            bob.send_json({"type": "pick", "card": "5"})
            state(bob)

            monkeypatch.setattr(storage, "save_json", full)
            bob.send_json({"type": "pick", "card": "8"})
            assert bob.receive_json() == {
                "type": "error",
                "code": "quota_exceeded",
                "message": "This app has run out of storage space.",
            }
            monkeypatch.setattr(storage, "save_json", really_save)

            bob.send_json({"type": "reveal"})  # memory never ran ahead of storage: the card is still the saved one
            assert member(state(bob), "Bob")["card"] == "5"
            assert storage.load_json("rooms/%s.json" % room_id)["picks"]["12"]["card"] == "5"


# --- The lobby ---------------------------------------------------------------------------------------------


def stored_file(room_id):
    return Path(os.environ["EFP_APP_DATA_DIR"]) / "objects" / "rooms" / (room_id + ".json")


def test_planning_poker_lobby_opens_and_lists_rooms(poker, monkeypatch):
    client = client_for(poker.app)
    assert client.get("/api/rooms").json() == {
        "signed_in": False,
        "me": None,
        "rooms": [],
        "deck": ["0", "1", "2", "3", "5", "8", "13", "21", "?", "coffee"],
        "limits": {"title": 120, "link": 500},
    }
    guest = client.post("/api/rooms", json={"title": STORY})
    assert guest.status_code == 401 and guest.json()["error"]["code"] == "sign_in_required"

    first = open_room(client, "  ABC-1   Export ", LINK)
    assert re.fullmatch(r"[0-9a-f]{10}", first)
    kept = storage.load_json("rooms/%s.json" % first)
    assert (kept["id"], kept["title"], kept["link"], kept["round"], kept["revealed"]) == (first, "ABC-1 Export", LINK, 1, False)
    assert kept["deck"] == poker.DECK and kept["picks"] == {} and kept["history"] == []
    assert kept["created_by"] == {"id": 12, "name": "Bob"} and kept["created_at"].endswith("+00:00")

    second = open_room(client, "ABC-2 Import", headers=mallory())
    third = open_room(client, "ABC-3 Search")
    for age, room_id in enumerate((third, first, second)):  # third was used last, second longest ago
        used = time.time() - 3600 * age
        os.utime(stored_file(room_id), (used, used))
    listed = client.get("/api/rooms", headers=mallory()).json()
    assert listed["signed_in"] is True and listed["me"] == {"id": 13, "name": "Mallory"}
    assert [room["id"] for room in listed["rooms"]] == [third, first, second]
    assert listed["rooms"][1] == {"id": first, "title": "ABC-1 Export", "round": 1, "rounds_done": 0, "updated_at": kept["updated_at"]}
    monkeypatch.setattr(poker, "LISTED_ROOMS", 2)
    assert [room["id"] for room in client.get("/api/rooms", headers=signed()).json()["rooms"]] == [third, first]
    assert client.get("/api/rooms").json()["rooms"] == []  # the titles of stories are not for guests

    for body, code in (
        ({"title": ""}, "bad_title"),
        ({"title": " \n "}, "bad_title"),
        ({"title": "x" * 121}, "bad_title"),
        ({"title": 7}, "bad_input"),
        ({"link": LINK}, "bad_input"),
        ({"title": STORY, "link": "javascript:alert(1)"}, "bad_link"),
        ({"title": STORY, "link": "ftp://files.example.com/story"}, "bad_link"),
        ({"title": STORY, "link": "https://"}, "bad_link"),
        ({"title": STORY, "link": "//jira.example.com/browse/ABC-1"}, "bad_link"),
        ({"title": STORY, "link": "https://jira.example.com/a b"}, "bad_link"),
        ({"title": STORY, "link": "https://jira.example.com/" + "x" * 500}, "bad_link"),
    ):
        refused = client.post("/api/rooms", json=body, headers=signed())
        assert refused.status_code == 400 and refused.json()["error"]["code"] == code, body
    assert len(storage.list("rooms/")) == 3


def test_planning_poker_clears_away_old_rooms_when_a_new_one_is_opened(poker):
    client = client_for(poker.app)
    old, recent = open_room(client, "Long ago"), open_room(client, "Last week")
    for room_id, days in ((old, 31), (recent, 7)):
        used = time.time() - 86400 * days
        os.utime(stored_file(room_id), (used, used))
    assert len(client.get("/api/rooms", headers=signed()).json()["rooms"]) == 2  # looking removes nothing
    new = open_room(client, "Today")
    assert sorted(item.key for item in storage.list("rooms/")) == sorted("rooms/%s.json" % room_id for room_id in (recent, new))
    with client_for(poker.app) as live:
        assert closed_with(live, "/ws/" + old, signed()) == 4404


def test_planning_poker_turns_a_storage_error_into_a_sentence(poker, monkeypatch):
    def broken(prefix=""):
        raise errors.StorageError("could not list %r: input/output error" % prefix)

    monkeypatch.setattr(storage, "list", broken)
    listing = client_for(poker.app).get("/api/rooms", headers=signed())
    assert listing.status_code == 502 and "input/output" not in listing.text
    assert listing.json()["error"]["message"] == "The app could not read or save its data. Please try again."


# --- A round as plain data -----------------------------------------------------------------------------------


def test_planning_poker_sums_a_round_up(poker):
    deck = poker.DECK
    assert poker.summary(deck, ["8", "3", "5"]) == {
        "votes": 3,
        "spread": [{"card": "3", "count": 1}, {"card": "5", "count": 1}, {"card": "8", "count": 1}],  # in the deck's order
        "average": 5.3,
        "median": 5.0,
        "consensus": False,
    }
    assert poker.summary(deck, ["5", "5", "5"])["consensus"] is True
    assert poker.summary(deck, ["5"])["consensus"] is False  # one card is not an agreement
    unsure = poker.summary(deck, ["?", "coffee", "?"])
    assert (unsure["votes"], unsure["average"], unsure["median"], unsure["consensus"]) == (3, None, None, False)
    assert unsure["spread"] == [{"card": "?", "count": 2}, {"card": "coffee", "count": 1}]
    mixed = poker.summary(deck, ["3", "8", "?"])  # "?" is a vote, not a number
    assert (mixed["votes"], mixed["average"], mixed["median"]) == (3, 5.5, 5.5)
    assert poker.summary(["inf", "nan", "1"], ["inf", "nan", "1"])["average"] == 1.0
    assert poker.summary(deck, []) == {"votes": 0, "spread": [], "average": None, "median": None, "consensus": False}


def test_planning_poker_files_only_revealed_rounds_and_keeps_a_bounded_history(poker, monkeypatch):
    monkeypatch.setattr(poker, "MAX_HISTORY", 2)
    alice = Visitor(id=5, username="alice", name="Alice")
    room = poker.new_room("Story", None, alice)
    poker.pick(room, alice, "3")
    poker.new_round(room, {})  # never revealed: nothing to file, the picks are gone
    assert (room["round"], room["picks"], room["history"]) == (2, {}, [])
    for card in ("1", "2", "3"):
        poker.pick(room, alice, card)
        poker.reveal(room)
        poker.new_round(room, {"title": "Story " + card})
    assert [(done["round"], done["picks"][0]["card"]) for done in room["history"]] == [(3, "2"), (4, "3")]
    assert (room["round"], room["title"], room["revealed"]) == (5, "Story 3", False)
    assert room["deck"] == poker.DECK and room["deck"] is not poker.DECK  # a room keeps a copy of the cards


def test_planning_poker_reads_deck_json_strictly(poker, tmp_path):
    path = tmp_path / "deck.json"

    def read(cards):
        path.write_text(json.dumps({"name": "Mine", "cards": cards}), encoding="utf-8")
        return poker.read_deck(path)

    assert read(["XS", "S", "M", "L", "XL"]) == ["XS", "S", "M", "L", "XL"]
    for bad in (None, "1,2,3", [], ["1"], ["1", "1"], ["1", 2], ["1", ""], ["1", " 2"], ["1", "x" * 9], [str(n) for n in range(25)]):
        with pytest.raises(ValueError, match="deck.json"):
            read(bad)


# --- The page ---------------------------------------------------------------------------------------------------


def test_planning_poker_page_and_socket_work_under_a_prefix(poker):
    # uvicorn --root-path /poker: the path carries the prefix and root_path names it.
    with client_for(poker.app, root_path="/poker") as client:
        page = client.get("/poker/")
        assert page.status_code == 200
        assert 'href="static/style.css"' in page.text and '<script type="module" src="static/app.js">' in page.text
        assert '<a href="./">All rooms</a>' in page.text
        for name in ("app.js", "api.js", "format.js", "socket.js"):
            script = client.get("/poker/static/" + name)
            assert script.status_code == 200 and "javascript" in script.headers["content-type"], name
            imported = re.findall(r'^import .* from "([^"]+)";$', script.text, flags=re.MULTILINE)
            assert all(source.startswith("./") for source in imported), name
        calls = client.get("/poker/static/api.js").text
        assert 'api("GET", "api/rooms")' in calls and re.search(r'api\("[A-Z]+", "/', calls) is None
        # The address of the WebSocket is made from the page's own, with the scheme that matches.
        connection = client.get("/poker/static/socket.js").text
        assert 'new URL("ws/" + encodeURIComponent(roomId), base)' in connection
        assert 'url.protocol === "https:" ? "wss:" : "ws:"' in connection
        assert "socketUrl(document.baseURI, roomId)" in connection
        assert "ws://" not in connection and "wss://" not in connection and "setTimeout(connect, delay)" in connection
        links = client.get("/poker/static/app.js").text
        assert 'location.assign("?room=" + encodeURIComponent(made.id))' in links

        room_id = open_room(client, "Under a prefix", prefix="/poker")
        assert client.get("/poker/api/rooms", headers=signed()).json()["rooms"][0]["id"] == room_id
        with client.websocket_connect("/poker/ws/" + room_id, headers=signed()) as socket:
            assert state(socket)["title"] == "Under a prefix"
            socket.send_json({"type": "pick", "card": "1"})
            assert state(socket)["you"]["card"] == "1"


def run_node(tmp_path, script):
    """Run a script next to a copy of the page's modules; what it prints as JSON."""
    node_binary = shutil.which("node")
    if node_binary is None:
        pytest.skip("node is not installed")
    shutil.copytree(HERE / "static", tmp_path / "static")
    # Without this file node reads .js as CommonJS; a browser reads these files as modules.
    (tmp_path / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    (tmp_path / "check.js").write_text(script, encoding="utf-8")
    done = subprocess.run([node_binary, "check.js"], cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


SOCKET_SCRIPT = """
import { GIVE_UP_AFTER, nextDelay, openRoom, socketUrl } from "./static/socket.js";

const out = {};
out.urls = [
  socketUrl("https://apps.example.com/poker/?room=0a1b2c3d4e#top", "0a1b2c3d4e"),
  socketUrl("http://127.0.0.1:8000/?room=0a1b2c3d4e", "0a1b2c3d4e"),
  socketUrl("https://apps.example.com/poker/", "a/b?c"),
];
out.delays = [0, 1, 2, 3, 4, 5, 9].map(nextDelay);

// A browser, as far as the connection needs one: sockets that the script opens and closes, and timers it runs by hand.
const sockets = [];
const timers = [];
const listeners = {};
class FakeSocket {
  constructor(url) { this.url = url; this.readyState = 0; this.handlers = {}; this.sent = []; sockets.push(this); }
  addEventListener(type, handler) { this.handlers[type] = handler; }
  send(text) { this.sent.push(text); }
  close() { this.readyState = 3; }
  opened() { this.readyState = 1; this.handlers.open({}); }
  dropped(code) { this.readyState = 3; this.handlers.close({ code }); }
}
FakeSocket.OPEN = 1;
globalThis.WebSocket = FakeSocket;
globalThis.document = {
  baseURI: "https://apps.example.com/poker/?room=0a1b2c3d4e", hidden: false,
  addEventListener: (type, handler) => { listeners[type] = handler; }, removeEventListener: () => {},
};
globalThis.window = { addEventListener: (type, handler) => { listeners[type] = handler; }, removeEventListener: () => {} };
globalThis.setTimeout = (callback, delay) => { timers.push({ callback, delay }); return timers.length; };
globalThis.clearTimeout = () => {};
const runTimer = () => timers.pop().callback();

const seen = [];
const states = [];
const room = openRoom("0a1b2c3d4e", {
  onState: (sent) => states.push(sent.round),
  onRefusal: (message) => states.push(message),
  onStatus: (status, delay) => seen.push(delay ? status + " " + delay : status),
});
out.first = [sockets.length, sockets[0].url, room.send({ type: "reveal" })];
sockets[0].opened();
sockets[0].handlers.message({ data: JSON.stringify({ type: "state", room: { round: 3 } }) });
sockets[0].handlers.message({ data: JSON.stringify({ type: "error", message: "No." }) });
sockets[0].handlers.message({ data: "not json" });
out.sent = [room.send({ type: "reveal" }), sockets[0].sent];
out.states = states.slice();

// The connection drops three times in a row: each wait is longer, and one timer is set per attempt.
sockets[0].dropped(1006);
out.oneTimer = timers.length;
runTimer();
sockets[1].dropped(1006);
runTimer();
sockets[2].dropped(1006);
out.whileWaiting = [room.send({ type: "reveal" }), timers.length];
runTimer();
sockets[3].opened();          // back: the next drop starts at one second again
sockets[3].dropped(1012);
runTimer();
out.afterReconnect = seen.slice();
seen.length = 0;

// In a background tab nothing is tried; looking at the tab again connects at once.
document.hidden = true;
sockets[4].dropped(1006);
out.paused = [seen.slice(), timers.length, sockets.length];
document.hidden = false;
listeners.visibilitychange();
out.resumed = sockets.length;
seen.length = 0;

// A server that stays away: after GIVE_UP_AFTER attempts the page stops and waits for the visitor.
for (let attempt = 0; attempt < GIVE_UP_AFTER; attempt += 1) {
  sockets[sockets.length - 1].dropped(1006);
  runTimer();
}
sockets[sockets.length - 1].dropped(1006);
out.gaveUp = [seen[seen.length - 1], timers.length, seen.filter((status) => status.startsWith("waiting")).length];
room.retryNow();
out.retried = sockets[sockets.length - 1].readyState;
seen.length = 0;

// The server says no: that is final.
const before = sockets.length;
sockets[sockets.length - 1].dropped(4404);
listeners.online();
room.retryNow();
out.refused = [seen.slice(), timers.length, sockets.length - before];
console.log(JSON.stringify(out));
"""


def test_planning_poker_page_reconnects_with_a_growing_delay(tmp_path):
    out = run_node(tmp_path, SOCKET_SCRIPT)
    assert out["urls"] == [
        "wss://apps.example.com/poker/ws/0a1b2c3d4e",  # under the prefix, encrypted like the page
        "ws://127.0.0.1:8000/ws/0a1b2c3d4e",
        "wss://apps.example.com/poker/ws/a%2Fb%3Fc",
    ]
    assert out["delays"] == [1000, 2000, 4000, 8000, 16000, 30000, 30000]
    assert out["first"] == [1, "wss://apps.example.com/poker/ws/0a1b2c3d4e", False]  # nothing is sent before it is open
    assert out["sent"] == [True, ['{"type":"reveal"}']] and out["states"] == [3, "No."]
    assert out["oneTimer"] == 1 and out["whileWaiting"] == [False, 1]
    assert out["afterReconnect"] == [
        "connecting",
        "connected",
        "waiting 1000",
        "connecting",
        "waiting 2000",
        "connecting",
        "waiting 4000",
        "connecting",
        "connected",
        "waiting 1000",
        "connecting",
    ]
    assert out["paused"] == [["paused"], 0, 5] and out["resumed"] == 6
    assert out["gaveUp"] == ["stopped", 0, 8] and out["retried"] == 0
    assert out["refused"] == [["missing"], 0, 0]


FORMAT_SCRIPT = """
import * as format from "./static/format.js";

const result = (spread, more) => Object.assign(
  { votes: spread.reduce((sum, row) => sum + row.count, 0), spread, average: null, median: null, consensus: false }, more || {});
const out = {};
out.numbers = [format.number(4), format.number(6.5), format.number(5.25), format.number(null), format.number(undefined)];
out.members = [
  format.memberStatus({ picked: true, online: true, card: null }, false),
  format.memberStatus({ picked: false, online: true, card: null }, false),
  format.memberStatus({ picked: false, online: false, card: null }, false),
  format.memberStatus({ picked: true, online: false, card: "8" }, true),
  format.memberStatus({ picked: false, online: true, card: null }, true),
];
out.verdicts = [
  format.verdict(null),
  format.verdict(result([{ card: "5", count: 3 }], { consensus: true })),
  format.verdict(result([{ card: "5", count: 1 }])),
  format.verdict(result([{ card: "3", count: 1 }, { card: "13", count: 2 }, { card: "?", count: 1 }])),
  format.verdict(result([{ card: "?", count: 1 }, { card: "coffee", count: 1 }])),
];
out.rows = format.spreadRows(result([{ card: "3", count: 1 }, { card: "5", count: 4 }, { card: "8", count: 2 }]));
const entry = { round: 2, picks: [{ name: "Bob", card: "5" }, { name: "Mallory", card: "8" }],
  result: result([{ card: "5", count: 1 }, { card: "8", count: 1 }], { average: 6.5, median: 6.5 }) };
out.lines = [
  format.roundLine(entry), format.picksLine(entry),
  format.roundLine({ round: 1, result: result([{ card: "?", count: 1 }]) }),
  format.roundLine({ round: 3, result: result([{ card: "5", count: 2 }], { average: 5, median: 5, consensus: true }) }),
];
out.links = ["https://jira.example.com/browse/ABC-1", "HTTP://x.example.com", "javascript:alert(1)", "//x.example.com", "", null, 7]
  .map(format.safeLink);
console.log(JSON.stringify(out));
"""


def test_planning_poker_words_what_the_server_sent(tmp_path):
    out = run_node(tmp_path, FORMAT_SCRIPT)
    assert out["numbers"] == ["4", "6.5", "5.3", "-", "-"]
    assert out["members"] == ["has picked", "is thinking", "away", "8", "no card"]
    assert out["verdicts"] == [
        "",
        "Consensus: everybody chose 5.",
        "One card only. Wait for the others, or start a new round.",
        "No consensus: from 3 to 13. Let the lowest and the highest explain, then play another round.",
        "No consensus. Talk it over, then play another round.",
    ]
    assert out["rows"] == [
        {"card": "3", "count": 1, "width": 25},
        {"card": "5", "count": 4, "width": 100},
        {"card": "8", "count": 2, "width": 50},
    ]
    assert out["lines"] == [
        "Round 2: 2 cards, average 6.5, median 6.5, no consensus",
        "Bob 5, Mallory 8",
        "Round 1: 1 card, no consensus",
        "Round 3: 2 cards, average 5, median 5, consensus",
    ]
    assert out["links"] == ["https://jira.example.com/browse/ABC-1", "HTTP://x.example.com", None, None, None, None, None]
