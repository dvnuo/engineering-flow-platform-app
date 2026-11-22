"""examples/retro-board: anonymous cards, a few votes per member, an owner who reveals and closes.

The backend runs for real against a sandbox data directory (``deployed``); the
Portal is the fake one where the owner of the app matters. The page's sorting
and its Markdown export are a plain JavaScript module, run with node when it
is installed.
"""

import json
import re
import shutil
import subprocess

import pytest

from efp_app import errors, storage
from conftest import EXAMPLES
from example_support import APP_INFO, client_for, load_module, owner, signed

HERE = EXAMPLES / "retro-board"
CARD_FIELDS = {"id", "column", "text", "created_at", "edited", "votes", "voted", "mine", "author"}

pytestmark = pytest.mark.skipif(not HERE.is_dir(), reason="needs a checkout of the repository (examples/)")


@pytest.fixture
def retro(deployed):
    pytest.importorskip("fastapi")
    return load_module("example_retro_board", HERE / "main.py")


def mallory():
    return signed(sub=13, username="mallory", name="Mallory")


def add(client, column, text, headers=None, **more):
    answer = client.post("/api/cards", json=dict({"column": column, "text": text}, **more), headers=headers or signed())
    assert answer.status_code == 201, answer.text
    return answer.json()


def board(client, headers=None, **query):
    answer = client.get("/api/board", params=query, headers=headers or {})
    assert answer.status_code == 200, answer.text
    return answer.json()


def card(view, text):
    [found] = [item for item in view["cards"] if item["text"] == text]
    return found


def keys(prefix=""):
    return [item.key for item in storage.list(prefix)]


# --- Anonymous on the wire ---------------------------------------------------------------------------


def test_retro_board_sends_an_author_to_nobody_but_the_author(retro, portal):
    portal.on("GET", "/app", APP_INFO)  # the owner is Alice, id 5
    client = client_for(retro.app)
    bob = signed()

    start = board(client)
    assert start["board"] == {
        "id": "sprint-42",
        "title": "Sprint 42 retrospective",
        "votes_per_member": 5,
        "columns": [
            {"id": "well", "title": "Went well"},
            {"id": "improve", "title": "To improve"},
            {"id": "ideas", "title": "Ideas"},
            {"id": "actions", "title": "Actions"},
        ],
    }
    assert (start["current"], start["writable"], start["signed_in"], start["me"]) == (True, False, False, None)
    assert start["cards"] == [] and start["past"] == [] and start["votes_left"] == 0 and start["is_owner"] is False
    assert start["limits"] == {"text": 500, "cards": 40}
    assert keys() == []  # looking writes nothing

    add(client, "well", "The release went out on time", author={"id": 13, "name": "Mallory"}, id="0" * 16)
    hers = card(add(client, "improve", "  Standups\r\nrun too long  ", mallory()), "Standups\nrun too long")
    assert hers["mine"] is True and hers["author"] == "Mallory" and hers["column"] == "improve"
    assert re.fullmatch(r"[0-9a-f]{16}", hers["id"])

    # The server knows who wrote a card: it needs that for "your own", and for the reveal.
    kept = storage.load_json("cards/sprint-42/%s.json" % hers["id"])
    assert kept["author"] == {"id": 13, "username": "mallory", "name": "Mallory"} and kept["text"] == "Standups\nrun too long"
    assert sorted(keys())[-1] == "index/sprint-42.json" and len(keys("cards/sprint-42/")) == 2

    # Bob gets his own card with his name and Mallory's without one. Nothing in the answer names her.
    seen = client.get("/api/board", headers=bob)
    assert "mallory" not in seen.text.lower() and '"id":13' not in seen.text.replace(" ", "")
    view = seen.json()
    assert view["me"] == {"id": 12, "name": "Bob"} and view["writable"] is True and view["votes_left"] == 5
    assert all(set(item) == CARD_FIELDS for item in view["cards"])
    mine = card(view, "The release went out on time")
    assert (mine["mine"], mine["author"], mine["column"]) == (True, "Bob", "well")  # the body named nobody
    assert (card(view, "Standups\nrun too long")["mine"], card(view, "Standups\nrun too long")["author"]) == (False, None)

    for headers in ({}, owner()):  # a guest, and the owner of the app before the reveal
        answer = client.get("/api/board", headers=headers)
        assert "mallory" not in answer.text.lower() and '"Bob"' not in answer.text
        assert [item["author"] for item in answer.json()["cards"]] == [None, None]
    assert board(client, owner())["is_owner"] is True and view["is_owner"] is False

    # Only the owner reveals, and then everybody sees every author.
    assert client.post("/api/board/state", json={"revealed": True}).status_code == 401
    refused = client.post("/api/board/state", json={"revealed": True}, headers=bob)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "owner_only"
    assert board(client, bob)["revealed"] is False
    revealed = client.post("/api/board/state", json={"revealed": True}, headers=owner())
    assert revealed.status_code == 200 and revealed.json()["revealed"] is True
    for headers in (bob, mallory(), {}):
        assert sorted(item["author"] for item in board(client, headers)["cards"]) == ["Bob", "Mallory"]
    state = storage.load_json("state/sprint-42.json")
    assert (state["revealed"], state["closed"], state["changed_by"]) == (True, False, "alice")
    assert portal.count("GET", "/app") == 1  # who the owner is was asked once


def test_retro_board_lets_a_member_change_only_their_own_cards(retro, monkeypatch):
    client = client_for(retro.app)
    bob = signed()
    mine = card(add(client, "well", "Pairing helped"), "Pairing helped")
    hers = card(add(client, "ideas", "A demo day", mallory()), "A demo day")

    changed = client.put("/api/cards/" + mine["id"], json={"text": " Pairing helped a lot ", "column": "actions"}, headers=bob)
    assert changed.status_code == 200
    now = card(changed.json(), "Pairing helped a lot")
    assert (now["id"], now["column"], now["edited"], now["mine"]) == (mine["id"], "actions", True, True)
    assert storage.load_json("cards/sprint-42/%s.json" % mine["id"])["edited_at"].endswith("+00:00")
    kept_column = client.put("/api/cards/" + mine["id"], json={"text": "Pairing"}, headers=bob).json()
    assert card(kept_column, "Pairing")["column"] == "actions"  # a change of the text alone leaves the column

    for refused in (
        client.put("/api/cards/" + hers["id"], json={"text": "Mine now"}, headers=bob),
        client.delete("/api/cards/" + hers["id"], headers=bob),
    ):
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "not_yours"
    assert storage.load_json("cards/sprint-42/%s.json" % hers["id"])["text"] == "A demo day"

    for missing in ("0" * 16, "nope", "A" * 16):
        assert client.put("/api/cards/" + missing, json={"text": "x"}, headers=bob).status_code == 404
        assert client.delete("/api/cards/" + missing, headers=bob).status_code == 404
        assert client.put("/api/cards/%s/vote" % missing, headers=bob).status_code == 404

    for body, code in (
        ({"column": "nowhere", "text": "x"}, "unknown_column"),
        ({"column": "well", "text": "  \n "}, "bad_text"),
        ({"column": "well", "text": "x" * 501}, "bad_text"),
        ({"column": "well", "text": "x" * 5000}, "bad_input"),
        ({"column": "well"}, "bad_input"),
        ({"column": "well", "text": 7}, "bad_input"),
        (["well", "x"], "bad_input"),
    ):
        refused = client.post("/api/cards", json=body, headers=bob)
        assert refused.status_code == 400 and refused.json()["error"]["code"] == code, body
    assert client.put("/api/cards/" + mine["id"], json={"text": "x", "column": "nowhere"}, headers=bob).status_code == 400
    assert client.post("/api/cards", json={"column": "well", "text": "x"}).status_code == 401
    assert len(keys("cards/")) == 2

    assert [item["text"] for item in client.delete("/api/cards/" + mine["id"], headers=bob).json()["cards"]] == ["A demo day"]
    assert keys("cards/") == ["cards/sprint-42/%s.json" % hers["id"]]

    monkeypatch.setattr(retro, "MAX_CARDS", 2)
    add(client, "well", "one")
    add(client, "well", "two")
    full = client.post("/api/cards", json={"column": "well", "text": "three"}, headers=bob)
    assert full.status_code == 409 and full.json()["error"]["code"] == "limit_reached"
    add(client, "well", "hers", mallory())  # the limit is per member


# --- Votes --------------------------------------------------------------------------------------------


def test_retro_board_gives_every_member_a_few_votes(retro, monkeypatch):
    monkeypatch.setitem(retro.BOARD, "votes_per_member", 2)
    client = client_for(retro.app)
    bob, her = signed(), mallory()
    view = [add(client, "well", text) for text in ("first", "second", "third")][-1]
    first, second, third = (card(view, text)["id"] for text in ("first", "second", "third"))

    voted = client.put("/api/cards/%s/vote" % first, headers=her)
    assert voted.status_code == 200 and voted.json()["votes_left"] == 1
    assert (card(voted.json(), "first")["votes"], card(voted.json(), "first")["voted"]) == (1, True)
    assert keys("votes/") == ["votes/sprint-42/%s/13.json" % first]  # one object for this card and this voter

    again = client.put("/api/cards/%s/vote" % first, headers=her).json()  # the same dot twice is one dot
    assert again["votes_left"] == 1 and card(again, "first")["votes"] == 1 and len(keys("votes/")) == 1

    assert client.put("/api/cards/%s/vote" % second, headers=her).json()["votes_left"] == 0
    spent = client.put("/api/cards/%s/vote" % third, headers=her)
    assert spent.status_code == 409 and spent.json()["error"]["code"] == "no_votes_left"
    assert len(keys("votes/")) == 2

    # The others get a count, never who voted; their own votes are untouched.
    seen = client.get("/api/board", headers=bob)
    assert "mallory" not in seen.text.lower() and all(set(item) == CARD_FIELDS for item in seen.json()["cards"])
    assert seen.json()["votes_left"] == 2 and [item["voted"] for item in seen.json()["cards"]] == [False, False, False]
    assert sorted(item["votes"] for item in seen.json()["cards"]) == [0, 1, 1]
    both = client.put("/api/cards/%s/vote" % first, headers=bob).json()  # an author may vote for the own card
    assert card(both, "first")["votes"] == 2 and both["votes_left"] == 1

    back = client.delete("/api/cards/%s/vote" % first, headers=her).json()
    assert back["votes_left"] == 1 and card(back, "first")["votes"] == 1
    assert client.put("/api/cards/%s/vote" % third, headers=her).json()["votes_left"] == 0

    # A card that is deleted takes its votes along: they are free again.
    assert client.delete("/api/cards/" + second, headers=bob).status_code == 200
    assert keys("votes/sprint-42/%s/" % second) == []
    assert board(client, her)["votes_left"] == 1
    # A vote that outlived its card (a delete that failed half way) counts for nobody.
    storage.save_json("votes/sprint-42/%s/13.json" % second, {"at": "2026-11-20T10:00:00+00:00"})
    assert board(client, her)["votes_left"] == 1
    assert client.put("/api/cards/%s/vote" % first, headers=her).json()["votes_left"] == 0

    assert client.put("/api/cards/%s/vote" % first).status_code == 401
    assert client.delete("/api/cards/%s/vote" % first).status_code == 401
    assert board(client)["votes_left"] == 0  # a guest has none


# --- The owner ------------------------------------------------------------------------------------------


def test_retro_board_can_be_closed_and_reopened_by_the_owner(retro, portal):
    portal.on("GET", "/app", APP_INFO)
    client = client_for(retro.app)
    bob = signed()
    mine = card(add(client, "well", "Before closing"), "Before closing")
    client.put("/api/cards/%s/vote" % mine["id"], headers=bob)

    assert client.post("/api/board/state", json={"closed": True}, headers=bob).status_code == 403
    closed = client.post("/api/board/state", json={"closed": True}, headers=owner()).json()
    assert (closed["closed"], closed["writable"], closed["revealed"]) == (True, False, False)
    for refused in (
        client.post("/api/cards", json={"column": "well", "text": "Too late"}, headers=bob),
        client.put("/api/cards/" + mine["id"], json={"text": "Changed"}, headers=bob),
        client.delete("/api/cards/" + mine["id"], headers=bob),
        client.put("/api/cards/%s/vote" % mine["id"], headers=mallory()),
        client.delete("/api/cards/%s/vote" % mine["id"], headers=bob),
    ):
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "closed"
    still = board(client, bob)
    assert [(item["text"], item["votes"]) for item in still["cards"]] == [("Before closing", 1)] and still["writable"] is False

    reopened = client.post("/api/board/state", json={"closed": False}, headers=owner()).json()
    assert reopened["closed"] is False and reopened["revealed"] is False
    add(client, "well", "After reopening")
    assert client.post("/api/board/state", json={"closed": "yes"}, headers=owner()).status_code == 400
    assert portal.count("GET", "/app") == 1


def test_retro_board_without_the_portal_has_no_owner_and_says_why(retro):
    # A deployed app whose token is missing: the board works, the owner's buttons are not offered,
    # and the owner's action answers with the SDK's sentence instead of "you are not the owner".
    client = client_for(retro.app)
    add(client, "well", "Works without the Portal")
    assert board(client, owner())["is_owner"] is False
    refused = client.post("/api/board/state", json={"closed": True}, headers=owner())
    assert refused.status_code == 502 and refused.json()["error"]["code"] == "not_configured"
    assert refused.json()["error"]["message"].startswith("This app is not set up yet.")
    assert board(client)["closed"] is False


def test_retro_board_turns_a_storage_error_into_a_sentence(retro, monkeypatch):
    def full(key, value):
        raise errors.QuotaExceededError("no space left to save %r" % key)

    monkeypatch.setattr(storage, "save_json", full)
    refused = client_for(retro.app).post("/api/cards", json={"column": "well", "text": "x"}, headers=signed())
    assert refused.status_code == 502
    assert refused.json()["error"] == {"code": "quota_exceeded", "message": "This app has run out of storage space."}


# --- board.json ------------------------------------------------------------------------------------------


def test_retro_board_starts_afresh_with_a_new_id_and_keeps_the_old_board(retro, monkeypatch):
    client = client_for(retro.app)
    bob = signed()
    old = card(add(client, "ideas", "From sprint 42"), "From sprint 42")
    client.put("/api/cards/%s/vote" % old["id"], headers=bob)
    assert storage.load_json("index/sprint-42.json") == retro.BOARD

    # The next sprint: board.json gets a new id (and here, other columns).
    monkeypatch.setitem(retro.BOARD, "id", "sprint-43")
    monkeypatch.setitem(retro.BOARD, "title", "Sprint 43 retrospective")
    monkeypatch.setitem(retro.BOARD, "columns", [{"id": "keep", "title": "Keep"}, {"id": "drop", "title": "Drop"}])
    fresh = board(client, bob)
    assert fresh["board"]["id"] == "sprint-43" and fresh["cards"] == [] and fresh["votes_left"] == 5
    assert fresh["past"] == [{"id": "sprint-42", "title": "Sprint 42 retrospective"}]

    past = board(client, bob, board="sprint-42")
    assert (past["current"], past["writable"]) == (False, False)
    assert [column["id"] for column in past["board"]["columns"]] == ["well", "improve", "ideas", "actions"]
    assert [(item["text"], item["column"], item["votes"], item["mine"]) for item in past["cards"]] == [
        ("From sprint 42", "ideas", 1, True)
    ]
    assert board(client, board="sprint-42")["cards"][0]["author"] is None  # still anonymous for the others

    # Everything that writes goes to the current board; the old card is out of reach.
    assert client.put("/api/cards/" + old["id"], json={"text": "Rewritten"}, headers=bob).status_code == 404
    assert client.put("/api/cards/%s/vote" % old["id"], headers=bob).status_code == 404
    assert client.post("/api/cards", json={"column": "ideas", "text": "x"}, headers=bob).status_code == 400
    add(client, "keep", "From sprint 43")
    assert len(keys("cards/sprint-43/")) == 1 and len(keys("cards/sprint-42/")) == 1
    assert keys("index/") == ["index/sprint-42.json", "index/sprint-43.json"]
    assert board(client, board="sprint-43")["current"] is True

    for unknown in ("sprint-41", "..", "a b", "x" * 65, "cards/sprint-42"):
        answer = client.get("/api/board", params={"board": unknown})
        assert answer.status_code == 404 and answer.json()["error"]["code"] == "not_found", unknown

    # A column that board.json no longer has: its cards move to the first one instead of vanishing.
    storage.save_json("cards/sprint-43/%s.json" % ("b" * 16), dict(storage.load_json(keys("cards/sprint-42/")[0]), column="ideas"))
    assert sorted(item["column"] for item in board(client)["cards"]) == ["keep", "keep"]


def test_retro_board_reads_board_json_strictly(retro, tmp_path):
    good = {"id": "s-1", "columns": [{"id": "a", "title": "A"}]}
    path = tmp_path / "board.json"

    def read(**changes):
        path.write_text(json.dumps(dict(good, **changes)), encoding="utf-8")
        return retro.read_settings(path)

    assert read() == {"id": "s-1", "title": "s-1", "votes_per_member": 5, "columns": [{"id": "a", "title": "A"}]}
    assert read(votes_per_member=0, title="T", extra=1) == dict(read(), votes_per_member=0, title="T")
    for bad in (
        {"id": "sprint 42"},
        {"id": "a/b"},
        {"id": ""},
        {"id": 42},
        {"columns": []},
        {"columns": "well"},
        {"columns": [{"id": "a", "title": "A"}] * 2},
        {"columns": [{"id": "a b", "title": "A"}]},
        {"columns": [{"id": "a"}]},
        {"columns": [{"id": "c%d" % number, "title": "C"} for number in range(9)]},
        {"votes_per_member": "five"},
        {"votes_per_member": -1},
        {"votes_per_member": True},
    ):
        with pytest.raises(ValueError, match="board.json"):
            read(**bad)


# --- The page ---------------------------------------------------------------------------------------------


def test_retro_board_page_and_its_modules_work_under_a_prefix(retro):
    # uvicorn --root-path /retro: the path carries the prefix and root_path names it.
    client = client_for(retro.app, root_path="/retro")
    page = client.get("/retro/")
    assert page.status_code == 200
    assert 'href="static/style.css"' in page.text and '<script type="module" src="static/app.js">' in page.text
    for name in ("app.js", "api.js", "board.js"):
        script = client.get("/retro/static/" + name)
        assert script.status_code == 200 and "javascript" in script.headers["content-type"], name
        imported = re.findall(r'^import .* from "([^"]+)";$', script.text, flags=re.MULTILINE)
        # The app's own modules relatively; the shared libraries at the one address every gateway serves.
        assert all(source.startswith(("./", "/_efp/lib/")) for source in imported), name
    calls = client.get("/retro/static/api.js").text
    assert 'api("POST", "api/cards", { column, text })' in calls and '"api/board"' in calls
    assert re.search(r'api\("[A-Z]+", "/', calls) is None  # no request starts at the top of the host
    assert client.get("/retro/api/board").json()["board"]["id"] == "sprint-42"


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


BOARD_SCRIPT = """
import { byColumn, fileName, plural, sortCards, toMarkdown, totals } from "./static/board.js";

const card = (id, column, text, votes, minute, more) => Object.assign(
  { id, column, text, votes, voted: false, mine: false, author: null, edited: false, created_at: "2026-11-20T10:0" + minute + ":00+00:00" },
  more || {}
);
const view = {
  board: { id: "sprint-42", title: "Sprint 42 retrospective", votes_per_member: 5,
    columns: [{ id: "well", title: "Went well" }, { id: "improve", title: "To improve" }, { id: "ideas", title: "Ideas" }] },
  revealed: false,
  cards: [
    card("c", "well", "Third written", 2, 3),
    card("a", "well", "First written", 0, 1, { mine: true, author: "Bob" }),
    card("b", "well", "Second written\\nwith a second line", 2, 2),
    card("d", "improve", "<b>Alone</b>", 1, 4),
  ],
};
const out = {};
out.time = sortCards(view.cards, "time").map((item) => item.id);
out.votes = sortCards(view.cards, "votes").map((item) => item.id);
out.untouched = view.cards.map((item) => item.id);
out.columns = byColumn(view, "votes").map(({ column, cards }) => [column.id, cards.map((item) => item.id)]);
out.totals = [totals(view), totals({ cards: [card("x", "well", "x", 1, 1)] }), plural(0, "vote")];
out.anonymous = toMarkdown(view);
out.revealed = toMarkdown({ ...view, revealed: true, cards: view.cards.map((item) => ({ ...item, author: item.author || "Mallory" })) });
out.names = [fileName(view), fileName({ board: { id: "a/b c" } }), fileName({ board: { id: "" } })];
console.log(JSON.stringify(out));
"""


def test_retro_board_sorts_and_exports_what_the_server_sent(tmp_path):
    out = run_node(tmp_path, BOARD_SCRIPT)
    assert out["time"] == ["a", "b", "c", "d"]
    assert out["votes"] == ["b", "c", "d", "a"]  # the most votes first, the older card first among equals
    assert out["untouched"] == ["c", "a", "b", "d"]  # sorting returns a new list
    assert out["columns"] == [["well", ["b", "c", "a"]], ["improve", ["d"]], ["ideas", []]]
    assert out["totals"] == ["4 cards, 5 votes", "1 card, 1 vote", "0 votes"]
    assert out["anonymous"] == (
        "# Sprint 42 retrospective\n\n"
        "## Went well\n\n"
        "- Second written\n  with a second line (2 votes)\n"
        "- Third written (2 votes)\n"
        "- First written\n\n"  # the visitor's own card carries no name: the file may travel
        "## To improve\n\n"
        "- <b>Alone</b> (1 vote)\n\n"
        "## Ideas\n\n"
        "Nothing.\n"
    )
    assert "- Third written (2 votes, Mallory)\n- First written (Bob)\n" in out["revealed"]
    assert out["names"] == ["sprint-42.md", "a-b-c.md", "retro.md"]
