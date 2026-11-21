"""examples/type-sprint: a typing sprint whose result the server works out from the lines it knows.

The server runs for real against the storage of a sandbox host. The sprint
itself is JavaScript (``static/sprint.js``, ``static/charts.js``); it is run
under node when node is on the PATH, and one test types a whole sprint there
and hands its report to the server, so the two sides are checked against each
other.
"""

import json
import re
import shutil
import subprocess

import pytest

from efp_app import errors, storage
from conftest import EXAMPLES
from example_support import client_for, load_module, signed

HERE = EXAMPLES / "type-sprint"
BOB = {"sub": 12, "username": "bob", "name": "Bob"}
EVE = {"sub": 13, "username": "eve", "name": "Eve"}

pytestmark = pytest.mark.skipif(not HERE.is_dir(), reason="needs a checkout of the repository (examples/)")


@pytest.fixture
def sprint(deployed):
    pytest.importorskip("fastapi")
    return load_module("example_type_sprint", HERE / "main.py")


@pytest.fixture
def clock(sprint, monkeypatch):
    """The server's clock, in the test's hands: ``clock["now"] += 61`` is a sprint typed."""
    hands = {"now": 1_800_000_000.0}
    monkeypatch.setattr(sprint, "now", lambda: hands["now"])
    return hands


def spread(chars, bars=12):
    """``chars`` over the bars of the speed chart, as evenly as whole numbers go."""
    return [chars // bars + (1 if bar < chars % bars else 0) for bar in range(bars)]


def typed(sprint, lines, partial=None, mistakes=0, **changes):
    """What the page reports for these lines typed to the end, with ``mistakes`` wrong keys on the way."""
    chars = sum(len(sprint.TEXTS[line]) for line in lines) + (partial[1] if partial else 0)
    body = {
        "completed": list(lines),
        "partial": {"id": partial[0], "chars": partial[1]} if partial else None,
        "keystrokes": chars + mistakes,
        "mistakes": mistakes,
        "buckets": spread(chars),
    }
    body.update(changes)
    return body


def play(client, clock, who, body, topic="git", seconds=61):
    """Open a sprint, let ``seconds`` pass, finish it with ``body``; the answer to the finish."""
    run = client.post("/api/runs", json={"topic": topic}, headers=signed(**who))
    assert run.status_code == 200
    clock["now"] += seconds
    return client.post("/api/runs/%s/finish" % run.json()["id"], json=body, headers=signed(**who))


def refusal(answer):
    assert set(answer.json()) == {"error"} and set(answer.json()["error"]) == {"code", "message"}
    assert answer.json()["error"]["message"].endswith(".")  # a sentence for the visitor
    return answer.status_code, answer.json()["error"]["code"]


# --- The lines ----------------------------------------------------------------------------------


def test_type_sprint_ships_lines_that_can_be_typed_on_any_keyboard(sprint):
    topics = json.loads((HERE / "snippets.json").read_text(encoding="utf-8"))["topics"]
    assert {topic["id"] for topic in topics} >= {"git", "kubectl", "docker", "shell", "curl", "sql", "python"}
    for topic in topics:
        assert topic["label"] and len(topic["snippets"]) >= 10
        assert len(set(topic["snippets"])) == len(topic["snippets"])
        for line in topic["snippets"]:
            assert re.fullmatch(r"[ -~]{5,80}", line) and line == line.strip(), line
    assert sprint.TEXTS["git-01"] == topics[0]["snippets"][0] and sprint.TOPIC_OF["git-01"] == "git"
    assert len(sprint.TEXTS) == sum(len(topic["snippets"]) for topic in topics)


def test_type_sprint_hands_the_lines_to_everybody(sprint):
    client = client_for(sprint.app)
    guest = client.get("/api/snippets").json()
    assert guest["signed_in"] is False and guest["name"] is None
    assert (guest["sprint_seconds"], guest["bucket_seconds"], guest["min_accuracy"], guest["everything"]) == (60, 5, 90, "all")
    git = guest["topics"][0]
    assert git["id"] == "git" and git["label"] == "Git"
    assert git["snippets"][2] == {"id": "git-03", "text": sprint.TEXTS["git-03"]}
    member = client.get("/api/snippets", headers=signed(**BOB)).json()
    assert member["signed_in"] is True and member["name"] == "Bob" and member["topics"] == guest["topics"]


# --- A sprint -----------------------------------------------------------------------------------


def test_type_sprint_works_out_the_speed_from_the_lines_it_knows(sprint, clock):
    client = client_for(sprint.app)
    bob = signed(**BOB)
    assert client.get("/api/history", headers=bob).json() == {"runs": [], "best": None}

    run = client.post("/api/runs", json={"topic": "git"}, headers=bob).json()
    assert re.fullmatch(r"[0-9a-f]{16}", run["id"])
    [opened] = storage.list("runs/")
    assert opened.key == "runs/12/%s.json" % run["id"]  # filed under the member the gateway named
    assert storage.load_json(opened.key) == {"topic": "git", "started": 1_800_000_000.0}

    lines = ["git-01", "git-02", "git-03", "git-04", "git-05"]
    chars = sum(len(sprint.TEXTS[line]) for line in lines) + 7
    # The page may say what it likes about speed and about who typed: neither is read.
    body = typed(sprint, lines, partial=("git-06", 7), mistakes=8, wpm=999, accuracy=100, visitor=13, username="eve")
    clock["now"] += 61
    done = client.post("/api/runs/%s/finish" % run["id"], json=body, headers=bob).json()
    result = done["run"]
    assert result["chars"] == chars == 168 and result["wpm"] == 33.6  # 168 characters are 33.6 words of five
    assert result["accuracy"] == 95.5 and result["mistakes"] == 8 and result["ranked"] is True  # 168 of 176 keys
    assert result["topic"] == "git" and result["at"].endswith("+00:00")
    assert done["improved"] is True and done["best"] == result and done["runs"] == [result]
    assert done["board"]["top"] == [{"rank": 1, "name": "Bob", "wpm": 33.6, "accuracy": 95.5, "topic": "git", "you": True}]
    assert done["board"]["you"] == {"rank": 1, "wpm": 33.6}

    assert [item.key for item in storage.list("")] == ["players/12.json"]  # the open sprint is gone
    assert storage.load_json("players/12.json") == {"username": "bob", "name": "Bob", "best": result, "runs": [result]}
    assert client.get("/api/history", headers=bob).json() == {"runs": [result], "best": result}
    assert client.get("/api/history", headers=signed(**EVE)).json() == {"runs": [], "best": None}  # hers, not his


def test_type_sprint_keeps_thirty_sprints_and_ranks_only_the_accurate_ones(sprint, clock):
    client = client_for(sprint.app)
    lines = ["git-01", "git-02", "git-03", "git-04"]
    first = play(client, clock, BOB, typed(sprint, lines, mistakes=4)).json()
    assert first["run"]["ranked"] is True and first["improved"] is True

    # Faster and sloppier: 161 right keys in 181 are 89 percent. Kept in the history, not on the board.
    sloppy = play(client, clock, BOB, typed(sprint, lines + ["git-05"], mistakes=20)).json()
    assert sloppy["run"]["wpm"] > first["run"]["wpm"] and sloppy["run"]["accuracy"] == 89.0 and sloppy["run"]["ranked"] is False
    assert sloppy["improved"] is False and sloppy["best"] == first["run"] and len(sloppy["runs"]) == 2
    assert sloppy["board"]["you"] == {"rank": 1, "wpm": first["run"]["wpm"]}

    exact = play(client, clock, BOB, typed(sprint, ["git-01"], mistakes=3)).json()  # 27 of 30 keys: exactly 90 percent
    assert exact["run"]["accuracy"] == 90.0 and exact["run"]["ranked"] is True and exact["improved"] is False

    better = play(client, clock, BOB, typed(sprint, lines + ["git-05", "git-06"], mistakes=2)).json()
    assert better["improved"] is True and better["best"] == better["run"] and len(better["runs"]) == 4

    kept = storage.load_json("players/12.json")
    kept["runs"] = [dict(kept["runs"][0], at="2026-01-%02dT08:00:00+00:00" % day) for day in range(1, 31)]
    storage.save_json("players/12.json", kept)
    latest = play(client, clock, BOB, typed(sprint, ["git-01"])).json()
    assert len(latest["runs"]) == 30 and latest["runs"][-1] == latest["run"]  # the last thirty
    assert latest["runs"][0]["at"].startswith("2026-01-02")  # the oldest made room
    assert latest["best"] == better["run"]  # the best outlives the thirty


def test_type_sprint_refuses_what_cannot_be_true(sprint, clock):
    client = client_for(sprint.app)
    lines = ["git-01", "git-02", "git-03"]
    chars = sum(len(sprint.TEXTS[line]) for line in lines)
    many = list(sprint.TEXTS)[:40]  # more than 1250 characters: over 250 words in the minute
    impossible = (
        typed(sprint, lines, completed=lines + ["git-99"]),  # a line that does not exist
        typed(sprint, lines + ["sql-01"]),  # a line of another topic: it was never on the screen
        typed(sprint, lines, partial=("sql-01", 3)),
        typed(sprint, lines, partial=("git-04", len(sprint.TEXTS["git-04"]))),  # a whole line is a completed one
        typed(sprint, lines, buckets=spread(chars + 1)),  # the bars do not add up to the lines
        typed(sprint, lines, buckets=spread(chars)[:11] + [0]),
        typed(sprint, lines, buckets=spread(chars, 11)),  # eleven bars for twelve times five seconds
        typed(sprint, lines, buckets=[chars + 5, -5] + [0] * 10),
        typed(sprint, lines, keystrokes=chars - 1),  # fewer keys than characters
        typed(sprint, lines, mistakes=5, keystrokes=chars + 4),  # more right characters than right keys
        typed(sprint, lines, mistakes=chars + 1, keystrokes=chars),
        typed(sprint, lines, keystrokes=25 * 60 + 1),  # more keys than fingers
    )
    for body in impossible:
        assert refusal(play(client, clock, BOB, body)) == (400, "implausible"), body
    assert sum(len(sprint.TEXTS[line]) for line in many) > 1250
    assert refusal(play(client, clock, BOB, typed(sprint, many), topic="all")) == (400, "implausible")
    assert refusal(play(client, clock, BOB, typed(sprint, []))) == (400, "nothing_typed")
    assert storage.list("") == []  # refused, and used up: the same sprint cannot be sent again with other numbers

    malformed = (
        {},
        {"completed": lines},
        typed(sprint, lines, completed="git-01"),
        typed(sprint, lines, keystrokes=-1),
        dict(typed(sprint, lines), mistakes="few"),
        typed(sprint, lines, buckets=["a"] * 12),
        dict(typed(sprint, lines), partial={"id": "git-04"}),
        typed(sprint, lines, completed=["git-01"] * 401),
    )
    for body in malformed:
        assert refusal(play(client, clock, BOB, body)) == (400, "bad_input"), body
    assert storage.list("players/") == []

    assert play(client, clock, BOB, typed(sprint, lines + ["sql-01", "python-03"]), topic="all").status_code == 200  # everything
    assert play(client, clock, BOB, typed(sprint, lines, keystrokes=chars + 40), topic="git").status_code == 200  # retyped a lot


def test_type_sprint_refuses_a_sprint_that_is_too_short_too_old_or_not_yours(sprint, clock):
    client = client_for(sprint.app)
    body = typed(sprint, ["git-01", "git-02"])
    assert refusal(play(client, clock, BOB, body, seconds=20)) == (400, "too_soon")
    assert storage.list("") == []
    assert play(client, clock, BOB, body, seconds=59).status_code == 200  # two clocks never agree exactly
    assert play(client, clock, BOB, body, seconds=60 + 500).status_code == 200  # the clock starts with the first key
    assert refusal(play(client, clock, BOB, body, seconds=60 + 700)) == (400, "expired")

    assert client.post("/api/runs", json={"topic": "git"}).status_code == 401  # a guest types without a sprint on the server
    assert client.get("/api/history").status_code == 401
    assert client.post("/api/runs/%s/finish" % ("a" * 16), json=body).status_code == 401
    for topic in ("cobol", "", "../players", "ALL"):
        assert refusal(client.post("/api/runs", json={"topic": topic}, headers=signed(**BOB))) == (400, "unknown_topic")
    assert refusal(client.post("/api/runs", json={"topic": ["git"]}, headers=signed(**BOB))) == (400, "bad_input")

    run = client.post("/api/runs", json={"topic": "git"}, headers=signed(**BOB)).json()["id"]
    clock["now"] += 61
    assert refusal(client.post("/api/runs/%s/finish" % run, json=body, headers=signed(**EVE))) == (404, "no_such_run")
    for bad in ("nope", "A" * 16, "0" * 17, "players.12"):  # only a run id becomes part of a key
        assert refusal(client.post("/api/runs/%s/finish" % bad, json=body, headers=signed(**BOB))) == (400, "bad_run")
    assert client.post("/api/runs/%s/finish" % run, json=body, headers=signed(**BOB)).status_code == 200
    assert refusal(client.post("/api/runs/%s/finish" % run, json=body, headers=signed(**BOB))) == (404, "no_such_run")  # once
    assert len(storage.load_json("players/12.json")["runs"]) == 3

    # A member has one open sprint: starting again gives up the one before.
    first = client.post("/api/runs", json={"topic": "git"}, headers=signed(**BOB)).json()["id"]
    second = client.post("/api/runs", json={"topic": "sql"}, headers=signed(**BOB)).json()["id"]
    assert [item.key for item in storage.list("runs/")] == ["runs/12/%s.json" % second]
    clock["now"] += 61
    assert refusal(client.post("/api/runs/%s/finish" % first, json=body, headers=signed(**BOB))) == (404, "no_such_run")


def test_type_sprint_board_lists_every_members_best_ranked_sprint(sprint, clock):
    client = client_for(sprint.app)
    for member in range(1, 24):
        best = {"wpm": 30.0 + member, "accuracy": 95.0, "topic": "git", "at": "2026-11-%02dT09:00:00+00:00" % member}
        storage.save_json("players/%d.json" % (100 + member), {"name": "Member %d" % member, "best": best, "runs": [best]})
    tied = {"wpm": 53.0, "accuracy": 99.0, "topic": "sql", "at": "2026-11-30T09:00:00+00:00"}
    storage.save_json("players/200.json", {"name": "Careful", "best": tied, "runs": [tied]})  # as fast as Member 23, more accurate
    storage.save_json("players/201.json", {"name": "Sloppy", "best": None, "runs": [dict(tied, ranked=False)]})  # never ranked
    storage.save_json("players/202.json", ["not", "an", "object"])

    board = client.get("/api/leaderboard", headers=signed(sub=102, username="m2", name="Member 2")).json()
    assert board["players"] == 24 and len(board["top"]) == 20 and board["signed_in"] is True
    assert [entry["name"] for entry in board["top"][:3]] == ["Careful", "Member 23", "Member 22"]
    assert board["top"][0] == {"rank": 1, "name": "Careful", "wpm": 53.0, "accuracy": 99.0, "topic": "sql", "you": False}
    assert board["you"] == {"rank": 23, "wpm": 32.0} and not any(entry["you"] for entry in board["top"])

    guest = client.get("/api/leaderboard").json()
    assert guest["signed_in"] is False and guest["you"] is None and guest["top"] == board["top"]

    lines = ["git-%02d" % number for number in range(1, 11)]
    mine = play(client, clock, {"sub": 102, "username": "m2", "name": "Member 2"}, typed(sprint, lines, mistakes=1)).json()
    assert mine["improved"] is True and mine["run"]["wpm"] > 53 and mine["board"]["you"]["rank"] == 1
    assert mine["runs"][0]["wpm"] == 32.0 and len(mine["runs"]) == 2  # the history that was there is kept


def test_type_sprint_turns_a_storage_failure_into_a_sentence(sprint, monkeypatch):
    client = client_for(sprint.app)

    def gone(prefix=""):
        raise errors.StorageUnavailableError("cannot reach the storage service at /run/efp/storage.sock")

    monkeypatch.setattr(storage, "list", gone)
    for answer in (client.get("/api/leaderboard"), client.post("/api/runs", json={"topic": "git"}, headers=signed(**BOB))):
        assert answer.status_code == 502
        assert answer.json() == {"error": {"code": "storage_unavailable", "message": errors.StorageUnavailableError().user_message}}
        assert "storage.sock" not in answer.text  # the detail stays in the log
    assert client.get("/api/snippets").status_code == 200  # what needs no storage still works


def test_type_sprint_page_works_under_a_prefix(sprint):
    # uvicorn --root-path /type: the path carries the prefix and root_path names it.
    client = client_for(sprint.app, root_path="/type")
    page = client.get("/type/")
    assert page.status_code == 200
    assert 'href="static/style.css"' in page.text and '<script type="module" src="static/app.js">' in page.text
    assert 'autocomplete="off"' in page.text and 'spellcheck="false"' in page.text
    for name in ("app.js", "sprint.js", "charts.js"):
        script = client.get("/type/static/" + name)
        assert script.status_code == 200 and "javascript" in script.headers["content-type"]  # a module must be served as one
    script = client.get("/type/static/app.js").text
    for call in ('ask("api/snippets")', 'ask("api/runs", { topic })', 'ask("api/history")', 'ask("api/leaderboard")'):
        assert call in script, call
    assert 'ask("api/runs/" + finished + "/finish", report(done))' in script
    assert '"paste", "drop"' in script and "insertFromPaste" in script  # typed, not pasted
    assert "setTimeout" not in script  # the clock is the time; requestAnimationFrame only redraws it
    assert len(client.get("/type/api/snippets").json()["topics"]) == 7
    assert client.get("/type/api/leaderboard").json()["top"] == []


# --- The sprint and the charts, under node ---------------------------------------------------------


def page_copy(tmp_path):
    """A copy of the page's modules that node can import; the node to run, or a skipped test.

    Node takes a ``.js`` file for an ES module only inside a package that says
    so; the example has no package.json (it needs no npm), so the copy gets one.
    """
    runner = shutil.which("node")
    if runner is None:
        pytest.skip("node is not on the PATH")
    work = tmp_path / "page"
    if not work.exists():
        shutil.copytree(HERE / "static", work / "static")
        shutil.copy(HERE / "snippets.json", work / "snippets.json")
        (work / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    return runner, work


def node(tmp_path, script):
    """Run ``script`` next to the page's modules; what it prints, decoded."""
    runner, work = page_copy(tmp_path)
    (work / "check.mjs").write_text(PRELUDE + script, encoding="utf-8")
    done = subprocess.run([runner, "check.mjs"], cwd=work, capture_output=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


PRELUDE = """
import { readFileSync } from "node:fs";
import * as sprint from "./static/sprint.js";
import * as charts from "./static/charts.js";
const data = JSON.parse(readFileSync("./snippets.json", "utf8"));
const print = (value) => console.log(JSON.stringify(value));

// The lines of a topic with the ids the server gives them.
const lines = (topic) => data.topics
  .filter((entry) => topic === "all" || entry.id === topic)
  .flatMap((entry) => entry.snippets.map((text, index) => ({ id: entry.id + "-" + String(index + 1).padStart(2, "0"), text })));

// Type like a person until the time is up: a key every `gap` ms, and every
// `slip`-th key a wrong one that is taken back with Backspace.
function typist(state, startAt, gap, slip) {
  let now = startAt;
  let box = "";
  for (let keys = 1; !state.over; keys += 1) {
    if (slip && keys % slip === 0) {
      box = sprint.type(state, box + "~", now);
      box = sprint.type(state, box.slice(0, -1), now + gap);
      now += 2 * gap;
    } else {
      box = sprint.type(state, box + state.current.text[box.length], now);
      now += gap;
    }
  }
  return state;
}
"""


def test_type_sprint_counts_keys_mistakes_and_lines(tmp_path):
    seen = node(
        tmp_path,
        """
        const two = [{ id: "a-01", text: "git status" }, { id: "a-02", text: "ls -la" }];
        const state = sprint.newSprint(two, 60, 5, sprint.randomSource(1));
        const first = state.current;
        const out = { tickBefore: sprint.tick(state, 999999), marksBefore: sprint.marks(state).slice(0, 2) };

        let box = sprint.type(state, "", 500);  // an empty box does not start the clock
        out.empty = state.startedAt;
        box = sprint.type(state, first.text[0], 1000);
        out.started = state.startedAt;
        box = sprint.type(state, box + "#", 1100);  // a wrong key
        out.wrong = { box, marks: sprint.marks(state).slice(0, 3), keys: state.keystrokes, mistakes: state.mistakes,
                      net: sprint.netChars(state), accuracy: sprint.accuracy(state) };
        box = sprint.type(state, box + first.text[2], 1200);  // typing on does not fix what is behind
        out.on = { net: sprint.netChars(state), marks: sprint.marks(state).slice(0, 4) };
        box = sprint.type(state, box.slice(0, 1), 1300);  // Backspace, twice
        out.back = { box, keys: state.keystrokes, mistakes: state.mistakes };
        out.paste = { box: sprint.type(state, box + "past", 1400), refused: state.refusedPaste, keys: state.keystrokes };
        box = sprint.type(state, box + first.text.slice(1, 4), 1500);  // three at once: an input method, not a paste
        out.three = { box, keys: state.keystrokes };

        // To the end of the line, the last character wrong: nothing fits behind it.
        for (const character of first.text.slice(4, -1)) box = sprint.type(state, box + character, 1600);
        box = sprint.type(state, box + "#", 1700);
        const full = state.keystrokes;
        out.beyond = { box: sprint.type(state, box + "x", 1800) === box, keys: state.keystrokes === full, done: state.completed.length };
        box = sprint.type(state, box.slice(0, -1), 1900);
        box = sprint.type(state, box + first.text.slice(-1), 2000);  // the line is right: the next one comes
        out.done = { box, completed: state.completed, next: state.current.id, net: sprint.netChars(state),
                     worst: sprint.worstLine(state), report: sprint.report(state) };

        out.late = { box: sprint.type(state, "l", 1000 + 60000), over: state.over, keys: state.keystrokes === full + 1 };
        out.first = first;
        print(out);
        """,
    )
    first = seen["first"]
    other = "a-02" if first["id"] == "a-01" else "a-01"
    assert seen["tickBefore"] is False and seen["marksBefore"] == ["next", "ahead"]  # no key yet: no clock
    assert seen["empty"] is None and seen["started"] == 1000
    wrong = seen["wrong"]
    assert (wrong["box"], wrong["marks"]) == (first["text"][0] + "#", ["right", "wrong", "next"])
    assert (wrong["keys"], wrong["mistakes"], wrong["net"], wrong["accuracy"]) == (2, 1, 1, 50)
    assert seen["on"] == {"net": 1, "marks": ["right", "wrong", "right", "next"]}  # right characters behind a wrong one do not count
    assert seen["back"] == {"box": first["text"][0], "keys": 3, "mistakes": 1}  # taking back costs no key and forgives nothing
    assert seen["paste"] == {"box": first["text"][0], "refused": True, "keys": 3}
    assert seen["three"] == {"box": first["text"][:4], "keys": 6}
    assert seen["beyond"] == {"box": True, "keys": True, "done": 0}
    done = seen["done"]
    assert done["box"] == "" and done["completed"] == [first["id"]] and done["next"] == other
    assert done["net"] == len(first["text"]) and done["worst"] == {"id": first["id"], "mistakes": 2}
    assert done["report"]["completed"] == [first["id"]] and done["report"]["partial"] is None
    assert done["report"]["mistakes"] == 2 and done["report"]["keystrokes"] == len(first["text"]) + 3
    assert done["report"]["buckets"] == [len(first["text"])] + [0] * 11
    assert seen["late"] == {"box": "", "over": True, "keys": True}  # after the minute nothing is typed


def test_type_sprint_books_characters_in_the_five_seconds_they_were_typed(tmp_path):
    seen = node(
        tmp_path,
        """
        const state = sprint.newSprint([{ id: "a-01", text: "kubectl get pods -n qa -o wide" }], 60, 5, sprint.randomSource(1));
        const text = state.current.text;
        const out = {};
        let box = "";
        for (let index = 0; index < 10; index += 1) box = sprint.type(state, box + text[index], 1000 + index * 100);
        for (let index = 10; index < 14; index += 1) box = sprint.type(state, box + text[index], 7000);
        out.typed = state.buckets.slice(0, 4);
        box = sprint.type(state, box.slice(0, 8), 12000);  // six characters taken back, eleven seconds in
        out.takenBack = { buckets: state.buckets.slice(0, 4), net: sprint.netChars(state), partial: sprint.report(state).partial };
        box = sprint.type(state, box + text[8], 1000 + 59500);
        out.last = state.buckets[11];
        out.series = sprint.speedSeries([0, 25, 50], 5);
        out.rounding = [sprint.tenth(34.649), sprint.tenth(34.66), sprint.tenth(89.06)];
        out.speeds = [sprint.wordsPerMinute(300, 60000), sprint.wordsPerMinute(5, 0)];

        const queue = sprint.lineQueue(["a", "b", "c", "d", "e"], sprint.randomSource(9));
        const drawn = Array.from({ length: 50 }, () => queue());
        out.rounds = Array.from({ length: 10 }, (_, round) => new Set(drawn.slice(round * 5, round * 5 + 5)).size);
        out.twice = drawn.some((line, index) => index > 0 && line === drawn[index - 1]);
        out.orders = new Set(Array.from({ length: 10 }, (_, round) => drawn.slice(round * 5, round * 5 + 5).join(""))).size;
        const again = sprint.lineQueue(["a", "b", "c", "d", "e"], sprint.randomSource(9));
        out.same = Array.from({ length: 50 }, () => again()).join("") === drawn.join("");
        print(out);
        """,
    )
    assert seen["typed"] == [10, 4, 0, 0]
    # Deleting takes from the latest bar first; no bar goes below zero and they still add up.
    assert seen["takenBack"] == {"buckets": [8, 0, 0, 0], "net": 8, "partial": {"id": "a-01", "chars": 8}}
    assert seen["last"] == 1  # the last bar takes what is typed in the last second
    assert seen["series"] == [0, 60, 120]  # 25 characters in five seconds are 60 words a minute
    assert seen["rounding"] == [34.6, 34.7, 89.1] and seen["speeds"] == [60, 0]
    assert seen["rounds"] == [5] * 10 and seen["twice"] is False  # every line once before any comes again
    assert seen["orders"] > 3 and seen["same"] is True  # shuffled, and the same seed shuffles the same way


def test_type_sprint_a_sprint_typed_in_the_page_is_what_the_server_works_out(sprint, clock, tmp_path):
    seen = node(
        tmp_path,
        """
        const state = typist(sprint.newSprint(lines("all"), 60, 5, sprint.randomSource(2026)), 5000, 150, 17);
        const sloppy = typist(sprint.newSprint(lines("sql"), 60, 5, sprint.randomSource(7)), 0, 120, 6);
        print({
          report: sprint.report(state), local: sprint.localResult(state, 90), bars: state.buckets,
          worst: sprint.worstLine(state),
          sloppyReport: sprint.report(sloppy), sloppyLocal: sprint.localResult(sloppy, 90),
        });
        """,
    )
    report, local = seen["report"], seen["local"]
    assert 300 < local["chars"] == sum(seen["bars"]) and min(seen["bars"]) > 20  # a steady typist: every bar is filled
    assert report["keystrokes"] - report["mistakes"] == local["chars"]  # every wrong key was taken back
    assert seen["worst"]["mistakes"] >= 2

    client = client_for(sprint.app)
    answer = play(client, clock, BOB, report, topic="all")
    assert answer.status_code == 200
    assert {key: answer.json()["run"][key] for key in local} == local  # the page and the server agree to the decimal
    assert answer.json()["run"]["ranked"] is True and 60 < local["wpm"] < 80

    slow = play(client, clock, BOB, seen["sloppyReport"], topic="sql").json()  # every sixth key wrong: about 83 percent
    assert {key: slow["run"][key] for key in seen["sloppyLocal"]} == seen["sloppyLocal"]
    assert slow["run"]["ranked"] is False and slow["best"] == answer.json()["run"]
    assert refusal(play(client, clock, BOB, seen["sloppyReport"], topic="git")) == (400, "implausible")  # typed in another topic


def test_type_sprint_charts_are_arithmetic_and_plain_svg(tmp_path):
    seen = node(
        tmp_path,
        """
        const box = { left: 10, top: 0, width: 100, height: 50 };
        const out = {
          tops: [charts.axisTop([]), charts.axisTop([0]), charts.axisTop([19.9]), charts.axisTop([20]), charts.axisTop([61.2, 40])],
          bars: charts.barsIn([0, 20, 40, 80], box, 40),
          points: charts.pointsIn([0, 40, 20], box, 40),
          single: charts.pointsIn([20], box, 40),
          path: charts.pathThrough(charts.pointsIn([0, 40, 20], box, 40)),
        };

        // A stand-in for the document: elements that remember what they were given.
        const made = [];
        globalThis.document = { createElementNS(namespace, name) {
          const element = { namespace, name, attributes: {}, children: [], textContent: "",
            setAttribute(key, value) { this.attributes[key] = value; },
            append(...nodes) { this.children.push(...nodes); },
            replaceChildren(...nodes) { this.children = nodes; } };
          made.push(element);
          return element;
        } };
        const count = (svg, name) => svg.children.filter((child) => child.name === name).length;
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        charts.drawSpeed(svg, [48, 60, 0, 72], 5, 45.2);
        const rects = svg.children.filter((child) => child.name === "rect");
        out.speed = { viewBox: svg.attributes.viewBox, rects: rects.length, lines: count(svg, "line"),
                      titles: rects.map((rect) => rect.children[0].textContent),
                      heights: rects.map((rect) => Number(rect.attributes.height)),
                      texts: svg.children.filter((child) => child.name === "text").map((child) => child.textContent) };

        const runs = [
          { at: "1", wpm: 40, ranked: true }, { at: "2", wpm: 58, ranked: false }, { at: "3", wpm: 52, ranked: true },
        ];
        charts.drawHistory(svg, runs, runs[2], (run) => "sprint " + run.at);
        const dots = svg.children.filter((child) => child.name === "circle");
        out.history = { rects: count(svg, "rect"), paths: count(svg, "path"), classes: dots.map((dot) => dot.attributes.class),
                        titles: dots.map((dot) => dot.children[0].textContent),
                        rising: Number(dots[1].attributes.cy) < Number(dots[2].attributes.cy) };
        charts.drawHistory(svg, runs.slice(0, 1), null, () => "");
        out.one = { paths: count(svg, "path"), dots: count(svg, "circle") };
        out.namespaces = [...new Set(made.map((element) => element.namespace))];
        print(out);
        """,
    )
    assert seen["tops"] == [20, 20, 20, 20, 80]
    assert seen["bars"] == [
        {"x": 13, "y": 50, "width": 19, "height": 0},
        {"x": 38, "y": 25, "width": 19, "height": 25},
        {"x": 63, "y": 0, "width": 19, "height": 50},
        {"x": 88, "y": 0, "width": 19, "height": 50},  # above the axis: cut at the top, not drawn outside
    ]
    assert seen["points"] == [{"x": 10, "y": 50}, {"x": 60, "y": 0}, {"x": 110, "y": 25}]
    assert seen["single"] == [{"x": 60, "y": 25}] and seen["path"] == "M10 50L60 0L110 25"

    speed = seen["speed"]
    assert speed["viewBox"] == "0 0 480 180" and speed["rects"] == 4 and speed["lines"] == 4  # three grid lines and the average
    assert speed["titles"][0] == "0 to 5 s: 48 words a minute" and speed["titles"][3] == "15 to 20 s: 72 words a minute"
    assert speed["heights"][2] == 0 and speed["heights"][3] == max(speed["heights"])
    assert speed["texts"] == ["0", "40", "80", "0 s", "20 s", "average 45.2"]
    history = seen["history"]
    assert history["rects"] == 0 and history["paths"] == 1  # drawing again replaces what was there
    assert history["classes"] == ["dot", "dot hollow", "dot best"] and history["titles"] == ["sprint 1", "sprint 2", "sprint 3"]
    assert history["rising"] is True  # the faster sprint is higher up
    assert seen["one"] == {"paths": 0, "dots": 1}
    assert seen["namespaces"] == ["http://www.w3.org/2000/svg"]


def test_type_sprint_page_script_parses(tmp_path):
    # sprint.js and charts.js are run by the tests above; app.js needs a page, so it is only parsed.
    runner, work = page_copy(tmp_path)
    done = subprocess.run([runner, "--check", "static/app.js"], cwd=work, capture_output=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stderr
