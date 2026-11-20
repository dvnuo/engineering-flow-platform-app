"""examples/bug-squash: a game in the page, and a server that accepts only what can be true.

The server runs for real against the storage of a sandbox host. The rules of
the game are JavaScript (``static/game.js``); they are run under node when
node is on the PATH, and one test plays a whole run there and hands its
result to the server, so the two sides are checked against each other.
"""

import json
import re
import shutil
import subprocess
import threading

import pytest

from efp_app import Visitor, errors, storage
from conftest import EXAMPLES
from example_support import client_for, load_module, signed

HERE = EXAMPLES / "bug-squash"
RULES = json.loads((HERE / "rules.json").read_text(encoding="utf-8"))
BOB = {"sub": 12, "username": "bob", "name": "Bob"}
EVE = {"sub": 13, "username": "eve", "name": "Eve"}

pytestmark = pytest.mark.skipif(not HERE.is_dir(), reason="needs a checkout of the repository (examples/)")


@pytest.fixture
def squash(deployed):
    pytest.importorskip("fastapi")
    return load_module("example_bug_squash", HERE / "main.py")


@pytest.fixture
def clock(squash, monkeypatch):
    """The server's clock, in the test's hands: ``clock["now"] += 48`` is a run played."""
    hands = {"now": 1_800_000_000.0}
    monkeypatch.setattr(squash, "now", lambda: hands["now"])
    return hands


def result(score=310, **changes):
    """A result that can be true: 11 squashes worth 230 points before the combo, a best combo of 6."""
    summary = {
        "squashed": {"typo": 5, "flaky": 4, "legacy": 2},
        "features_squashed": 0,
        "shipped": 1,
        "escaped": 3,
        "misses": 2,
        "best_combo": 6,
    }
    summary.update(changes)
    return {"score": score, "summary": summary}


def play(client, clock, who, body, seconds=48):
    """Open a run, let ``seconds`` pass, finish it with ``body``; the answer to the finish."""
    headers = signed(**who)
    run = client.post("/api/runs", headers=headers)
    assert run.status_code == 200
    clock["now"] += seconds
    return client.post("/api/runs/%s/finish" % run.json()["id"], json=body, headers=signed(**who))


def refusal(answer):
    assert set(answer.json()) == {"error"} and set(answer.json()["error"]) == {"code", "message"}
    assert answer.json()["error"]["message"].endswith(".")  # a sentence for the visitor
    return answer.status_code, answer.json()["error"]["code"]


# --- The server ---------------------------------------------------------------------------------


def test_bug_squash_ranks_a_member_from_the_start_of_a_run_to_the_board(squash, clock):
    client = client_for(squash.app)
    bob = signed(**BOB)

    assert client.get("/api/rules", headers=bob).json() == {"rules": RULES, "signed_in": True, "name": "Bob"}
    empty = client.get("/api/leaderboard", headers=bob).json()
    assert empty == {"top": [], "players": 0, "you": None, "signed_in": True}

    run = client.post("/api/runs", headers=bob).json()
    assert re.fullmatch(r"[0-9a-f]{16}", run["id"]) and 0 <= run["seed"] < 2**32
    [opened] = storage.list("runs/")
    assert opened.key == "runs/12/%s.json" % run["id"]  # filed under the member the gateway named
    assert storage.load_json(opened.key) == {"seed": run["seed"], "started": 1_800_000_000.0}

    clock["now"] += 48  # the countdown and the run
    done = client.post("/api/runs/%s/finish" % run["id"], json=result(), headers=bob).json()
    assert done["accepted"] is True and done["improved"] is True and done["score"] == 310
    assert done["top"] == [{"rank": 1, "name": "Bob", "score": 310, "you": True}]
    assert done["you"] == {"rank": 1, "score": 310} and done["players"] == 1
    assert storage.list("runs/") == []  # a finished run is gone
    kept = storage.load_json("best/12.json")
    assert kept["score"] == 310 and kept["username"] == "bob" and kept["name"] == "Bob" and kept["at"].endswith("+00:00")
    assert kept["summary"] == result()["summary"]

    better = play(client, clock, EVE, result(400)).json()
    assert better["you"] == {"rank": 1, "score": 400}
    assert [(entry["name"], entry["score"], entry["you"]) for entry in better["top"]] == [("Eve", 400, True), ("Bob", 310, False)]

    worse = play(client, clock, BOB, result(120)).json()  # a weaker run does not replace the best
    assert worse["accepted"] is True and worse["improved"] is False and worse["you"] == {"rank": 2, "score": 310}
    assert storage.load_json("best/12.json")["score"] == 310
    assert sorted(item.key for item in storage.list("")) == ["best/12.json", "best/13.json"]


def test_bug_squash_lets_a_guest_play_without_ranking_them(squash, clock):
    client = client_for(squash.app)
    assert client.get("/api/rules").json() == {"rules": RULES, "signed_in": False, "name": None}
    assert client.post("/api/runs").status_code == 401  # no run on the server: the page plays without one
    assert client.post("/api/runs/%s/finish" % ("a" * 16), json=result()).status_code == 401
    assert storage.list("") == []

    play(client, clock, BOB, result())
    board = client.get("/api/leaderboard").json()
    assert board["signed_in"] is False and board["you"] is None
    assert board["top"] == [{"rank": 1, "name": "Bob", "score": 310, "you": False}]


def test_bug_squash_finishes_a_run_once_and_only_for_its_member(squash, clock):
    client = client_for(squash.app)
    bob, eve = signed(**BOB), signed(**EVE)
    run = client.post("/api/runs", headers=bob).json()["id"]
    clock["now"] += 48

    # Eve cannot finish Bob's run, and trying does not use it up.
    assert refusal(client.post("/api/runs/%s/finish" % run, json=result(), headers=eve)) == (404, "no_such_run")
    # The body names nobody: the member is the one the gateway signed in.
    named = dict(result(), visitor=13, username="eve")
    assert client.post("/api/runs/%s/finish" % run, json=named, headers=signed(**BOB)).status_code == 200
    assert [item.key for item in storage.list("best/")] == ["best/12.json"]
    assert refusal(client.post("/api/runs/%s/finish" % run, json=result(999), headers=signed(**BOB))) == (404, "no_such_run")
    assert storage.load_json("best/12.json")["score"] == 310

    for bad in ("nope", "A" * 16, "0" * 15, "0" * 17, "best.12"):  # only a run id becomes part of a key
        assert refusal(client.post("/api/runs/%s/finish" % bad, json=result(), headers=signed(**BOB))) == (400, "bad_run")

    # A member has one open run: starting again gives up the run before.
    first = client.post("/api/runs", headers=signed(**BOB)).json()["id"]
    second = client.post("/api/runs", headers=signed(**BOB)).json()["id"]
    assert [item.key for item in storage.list("runs/")] == ["runs/12/%s.json" % second]
    clock["now"] += 48
    assert refusal(client.post("/api/runs/%s/finish" % first, json=result(), headers=signed(**BOB))) == (404, "no_such_run")
    assert client.post("/api/runs/%s/finish" % second, json=result(), headers=signed(**BOB)).status_code == 200


def test_bug_squash_ranks_a_run_once_when_two_requests_finish_it_together(squash, clock):
    run = client_for(squash.app).post("/api/runs", headers=signed(**BOB)).json()["id"]
    clock["now"] += 48
    bob = Visitor(id=12, username="bob", name="Bob")
    start = threading.Barrier(8)
    accepted, turned_away = [], []

    def finish(score):
        start.wait()
        try:
            accepted.append(squash.finish_run(run, squash.Result(**result(score)), bob)["score"])
        except (squash.Refused, errors.StorageError) as exc:
            turned_away.append(exc)

    threads = [threading.Thread(target=finish, args=(300 + number,)) for number in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert len(accepted) + len(turned_away) == 8
    # storage.delete() tells one of them "it was there". (Windows alone can
    # refuse to delete a file another thread is reading; then nobody is told.)
    in_use = [exc for exc in turned_away if not isinstance(exc, squash.Refused)]
    assert len(accepted) == 1 or (in_use and not accepted)
    assert all(exc.code == "no_such_run" for exc in turned_away if isinstance(exc, squash.Refused))
    if accepted:
        assert storage.load_json("best/12.json")["score"] == accepted[0] and storage.list("runs/") == []


def test_bug_squash_refuses_a_run_that_is_too_short_or_too_old(squash, clock):
    client = client_for(squash.app)
    assert refusal(play(client, clock, BOB, result(), seconds=5)) == (400, "too_soon")
    assert storage.list("") == []  # refused, and used up: the same run cannot be sent again with other numbers
    assert play(client, clock, BOB, result(), seconds=RULES["run_seconds"] - 1).status_code == 200  # two clocks never agree exactly
    assert play(client, clock, EVE, result(), seconds=RULES["run_seconds"] + 500).status_code == 200  # a long pause
    assert refusal(play(client, clock, EVE, result(999), seconds=RULES["run_seconds"] + 700)) == (400, "expired")
    assert storage.load_json("best/13.json")["score"] == 310


def test_bug_squash_refuses_numbers_that_cannot_belong_together(squash, clock):
    client = client_for(squash.app)
    spawns, top = squash.MAX_SPAWNS, squash.MAX_SCORE
    everything = {"squashed": {"typo": 0, "flaky": 0, "legacy": spawns}, "shipped": 0, "escaped": 0, "best_combo": spawns}
    assert play(client, clock, BOB, result(top, **everything)).status_code == 200  # the run nobody plays is the limit

    impossible = (
        result(top + 1, **everything),  # above what the rules can give
        result(466),  # 230 points at a combo of 6 are at most 460, and 5 for the shipped feature
        result(squashed={"typo": spawns, "flaky": 1, "legacy": 0}),  # more bugs than a run has
        result(escaped=spawns),
        result(best_combo=12),  # a longer combo than there were squashes
        result(squashed={"typo": 5, "flaky": 4}),  # not the bugs of this game
        result(squashed={"typo": 5, "flaky": 4, "legacy": 2, "feature": 9}),
        result(squashed={"typo": 12, "flaky": 4, "legacy": -5}),
    )
    for body in impossible:
        assert refusal(play(client, clock, EVE, body)) == (400, "implausible"), body
    malformed = (
        {},
        {"score": 310},
        result(-1),
        result("many"),
        result(shipped=-1),
        result(misses=10**9),
        result(squashed="all of them"),
        {"score": 310, "summary": []},
    )
    for body in malformed:
        assert refusal(play(client, clock, EVE, body)) == (400, "bad_input"), body
    assert [item.key for item in storage.list("best/")] == ["best/12.json"]  # nothing of Eve's was kept

    assert play(client, clock, EVE, result(465)).status_code == 200  # exactly what these squashes can give
    assert play(client, clock, EVE, result(0, squashed={"typo": 0, "flaky": 0, "legacy": 0}, best_combo=0)).status_code == 200


def test_bug_squash_lists_the_top_twenty_and_says_where_you_stand(squash, clock):
    client = client_for(squash.app)
    for member in range(1, 26):
        at = "2026-11-%02dT09:00:00+00:00" % member
        storage.save_json("best/%d.json" % (100 + member), {"score": 1000 - member * 10, "name": "Member %d" % member, "at": at})
    storage.save_json("best/300.json", {"score": 990, "name": "Late", "at": "2026-11-30T09:00:00+00:00"})  # the same score, later
    storage.save_json("best/301.json", {"score": "a lot", "name": "Broken"})  # not a score: left out
    storage.save_json("best/302.json", ["not", "an", "object"])

    board = client.get("/api/leaderboard", headers=signed(sub=123, username="m23", name="Member 23")).json()
    assert board["players"] == 26 and len(board["top"]) == 20
    assert [entry["name"] for entry in board["top"][:3]] == ["Member 1", "Late", "Member 2"]
    assert [entry["rank"] for entry in board["top"]] == list(range(1, 21))
    assert board["you"] == {"rank": 24, "score": 770} and not any(entry["you"] for entry in board["top"])
    assert client.get("/api/leaderboard", headers=signed(sub=300)).json()["you"] == {"rank": 2, "score": 990}

    member = {"sub": 123, "username": "m23", "name": "Member 23"}
    improved = play(client, clock, member, result(995, squashed={"typo": 5, "flaky": 4, "legacy": 20}))
    assert improved.json()["improved"] is True and improved.json()["you"] == {"rank": 1, "score": 995}


def test_bug_squash_turns_a_storage_failure_into_a_sentence(squash, clock, monkeypatch):
    client = client_for(squash.app)

    def full(key, value):
        raise errors.QuotaExceededError("the app's data is over its quota of 1024 MiB")

    monkeypatch.setattr(storage, "save_json", full)
    answer = client.post("/api/runs", headers=signed(**BOB))
    assert answer.status_code == 502
    assert answer.json() == {"error": {"code": "quota_exceeded", "message": errors.QuotaExceededError().user_message}}
    assert "1024" not in answer.text  # the detail stays in the log


def test_bug_squash_page_works_under_a_prefix(squash):
    # uvicorn --root-path /squash: the path carries the prefix and root_path names it.
    client = client_for(squash.app, root_path="/squash")
    page = client.get("/squash/")
    assert page.status_code == 200
    assert 'href="static/style.css"' in page.text and '<script type="module" src="static/app.js">' in page.text
    for name in ("app.js", "game.js", "render.js"):
        script = client.get("/squash/static/" + name)
        assert script.status_code == 200 and "javascript" in script.headers["content-type"]  # a module must be served as one
    script = client.get("/squash/static/app.js").text
    assert 'ask("api/rules")' in script and 'ask("api/runs", {})' in script and 'ask("api/leaderboard")' in script
    assert 'ask("api/runs/" + finished.id + "/finish"' in script
    assert 'from "./game.js"' in script and 'from "./render.js"' in script  # relative imports stay inside the app
    assert client.get("/squash/api/rules").json()["rules"] == RULES
    assert client.get("/squash/api/leaderboard").json()["top"] == []


def test_bug_squash_page_pauses_in_a_hidden_tab_and_offers_a_calm_variant():
    script = (HERE / "static" / "app.js").read_text(encoding="utf-8")
    assert "requestAnimationFrame(frame)" in script and "setTimeout" not in script
    assert '"visibilitychange"' in script and "document.hidden" in script
    assert "(prefers-reduced-motion: reduce)" in script and '"pointerdown"' in script and "ResizeObserver" in script
    assert not list(HERE.rglob("*.png")) and not list(HERE.rglob("*.svg"))  # drawn, not loaded


# --- The rules, under node ------------------------------------------------------------------------


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
        shutil.copy(HERE / "rules.json", work / "rules.json")
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
import * as game from "./static/game.js";
const rules = JSON.parse(readFileSync("./rules.json", "utf8"));
const view = { width: 1000, height: 600, unit: 30 };
const friend = (bug) => Boolean(rules.kinds[bug.kind].friend);
const print = (value) => console.log(JSON.stringify(value));

// Strike a bug where it is.
function strike(state, bug) {
  const at = game.position(state, bug);
  return game.hitAt(state, at.x * view.width, at.y * view.height, view);
}

// Play a run in steps of `stepMs`; `each(state)` may click after every step.
function run(seed, options, stepMs, each) {
  const state = game.newGame(rules, seed, options);
  while (!state.over) {
    game.step(state, stepMs);
    if (each) each(state);
  }
  return state;
}

// A state with exactly one bug of a kind on the board, for the scoring rules.
function withOne(kind, seed = 1) {
  for (;; seed += 1) {
    const state = game.newGame(rules, seed, {});
    game.step(state, 1);
    if (state.bugs.length === 1 && state.bugs[0].kind === kind) {
      game.step(state, 1500);  // well onto the board
      state.bugs = state.bugs.filter((bug) => bug.kind === kind).slice(0, 1);
      state.schedule = state.schedule.slice(0, state.spawned);  // and nothing else arrives
      return state;
    }
  }
}
"""


def test_bug_squash_server_and_page_derive_the_same_limits(squash, tmp_path):
    seen = node(
        tmp_path,
        """
        const times = game.spawnTimes(rules);
        print({
          spawns: times.length, first: times[0], last: times[times.length - 1],
          max: game.maxScore(rules),
          multipliers: Array.from({ length: 40 }, (_, combo) => game.multiplier(rules, combo)),
        });
        """,
    )
    assert seen["spawns"] == squash.MAX_SPAWNS and seen["max"] == squash.MAX_SCORE
    assert seen["first"] == 0 and seen["last"] < RULES["run_seconds"] * 1000
    assert seen["multipliers"] == [squash.multiplier(combo) for combo in range(40)]
    assert seen["multipliers"][:6] == [1, 1, 1, 1, 1, 2] and max(seen["multipliers"]) == RULES["combo"]["max_multiplier"]


def test_bug_squash_a_seed_decides_the_run(tmp_path):
    seen = node(
        tmp_path,
        """
        const kinds = (seed, options, stepMs) => {
          const seen = [];
          const state = run(seed, options, stepMs, (now) => {
            for (const bug of now.bugs) seen[bug.id] = bug.kind + (bug.forward ? ">" : "<") + bug.lane.toFixed(4);
          });
          return { seen, summary: game.summary(state), score: state.score };
        };
        const first = kinds(42, {}, 16);
        print({
          first,
          again: kinds(42, {}, 16),
          slowFrames: kinds(42, {}, 33),
          calm: kinds(42, { calm: true }, 16).seen,
          other: kinds(43, {}, 16).seen,
          kinds: [...new Set(first.seen.map((text) => text.split(/[<>]/)[0]))].sort(),
        });
        """,
    )
    first = seen["first"]
    assert len(first["seen"]) > 50 and None not in first["seen"]
    assert seen["again"] == first  # the same seed: the same bugs on the same lanes
    assert seen["slowFrames"] == first  # at 30 frames a second as at 60: nothing depends on the frame rate
    assert seen["calm"] == first["seen"]  # the calm variant has the same bugs, sitting still
    assert seen["other"] != first["seen"]
    assert seen["kinds"] == sorted(RULES["kinds"])  # all four kinds turn up in a run
    # Nobody played: every bug got away or was still on the board, every feature that left shipped.
    summary = first["summary"]
    assert summary["squashed"] == {"typo": 0, "flaky": 0, "legacy": 0} and summary["best_combo"] == 0
    assert summary["escaped"] > 20 and summary["shipped"] > 0
    assert 0 <= first["score"] <= summary["shipped"] * RULES["kinds"]["feature"]["ship_bonus"]


def test_bug_squash_scores_by_the_rules(tmp_path):
    seen = node(
        tmp_path,
        """
        const out = {};

        let state = withOne("typo");
        out.squash = strike(state, state.bugs[0]);
        out.afterSquash = { score: state.score, combo: state.combo, left: state.bugs.length };
        out.miss = game.hitAt(state, 5, 5, view);
        out.afterMiss = { combo: state.combo, misses: state.counts.misses };

        state = withOne("legacy");
        state.combo = 7;
        out.crack = strike(state, state.bugs[0]);
        out.afterCrack = { score: state.score, combo: state.combo, left: state.bugs.length };
        out.second = strike(state, state.bugs[0]);
        out.afterSecond = { score: state.score, combo: state.combo, best: state.bestCombo, left: state.bugs.length };

        state = withOne("feature");
        state.score = 20; state.combo = 9;
        out.feature = strike(state, state.bugs[0]);
        out.afterFeature = { score: state.score, combo: state.combo, summary: game.summary(state), accuracy: game.accuracy(state) };

        state = withOne("flaky");
        state.score = 3;
        game.step(state, 20000);  // it crosses the board untouched
        out.escaped = { score: state.score, escaped: state.counts.escaped, events: state.events.map((event) => event.type) };

        state = withOne("feature");
        game.step(state, 20000);
        out.shipped = { score: state.score >= rules.kinds.feature.ship_bonus, shipped: state.counts.shipped };

        // The keyboard: every bug on the board carries a letter of its own.
        state = game.newGame(rules, 5, {});
        while (state.time < 30000) game.step(state, 16);
        const letters = state.bugs.map((bug) => bug.key);
        const target = state.bugs.find((bug) => !friend(bug) && rules.kinds[bug.kind].hits === 1);
        out.keys = { all: letters.length, different: new Set(letters).size, struck: game.hitKey(state, target.key).type,
                     nobody: game.hitKey(state, "?").type, over: (state.over = true, game.hitKey(state, "A")) };
        print(out);
        """,
    )
    kinds = RULES["kinds"]
    assert seen["squash"]["type"] == "squash" and seen["squash"]["points"] == kinds["typo"]["points"] and seen["squash"]["times"] == 1
    assert seen["afterSquash"] == {"score": 10, "combo": 1, "left": 0}
    assert seen["miss"]["type"] == "miss" and seen["afterMiss"] == {"combo": 0, "misses": 1}  # a click on nothing breaks the combo

    assert seen["crack"]["type"] == "crack" and seen["afterCrack"] == {"score": 0, "combo": 7, "left": 1}  # armour: kept, not raised
    assert seen["second"]["type"] == "squash" and seen["second"]["times"] == 2 and seen["second"]["points"] == 80
    assert seen["afterSecond"] == {"score": 80, "combo": 8, "best": 8, "left": 0}

    assert seen["feature"]["type"] == "feature" and seen["feature"]["points"] == kinds["feature"]["points"]
    assert seen["afterFeature"]["score"] == 0 and seen["afterFeature"]["combo"] == 0  # 20 - 30 is 0, not -10
    assert seen["afterFeature"]["summary"]["features_squashed"] == 1 and seen["afterFeature"]["accuracy"] == 0

    assert seen["escaped"] == {"score": 0, "escaped": 1, "events": ["escaped"]}
    assert seen["shipped"] == {"score": True, "shipped": 1}
    assert seen["keys"]["all"] > 3 and seen["keys"]["different"] == seen["keys"]["all"]
    assert seen["keys"]["struck"] == "squash" and seen["keys"]["nobody"] == "miss" and seen["keys"]["over"] is None


def test_bug_squash_moves_bugs_by_the_clock_and_keeps_them_still_in_calm_mode(tmp_path):
    seen = node(
        tmp_path,
        """
        const track = (options) => {
          const state = game.newGame(rules, 3, options);
          game.step(state, 1);
          const bug = state.bugs[0];
          const path = [];
          for (let step = 0; step < 6; step += 1) {
            path.push(game.position(state, bug));
            game.step(state, 400);
          }
          return { path, life: bug.life, forward: bug.forward };
        };
        const wide = track({});
        print({ wide, tall: track({ axis: "y" }), calm: track({ calm: true }) });
        """,
    )
    wide, tall, calm = seen["wide"], seen["tall"], seen["calm"]
    along = [point["x"] for point in wide["path"]]
    assert along == sorted(along, reverse=not wide["forward"]) and len(set(along)) == 6  # it crosses from one side to the other
    assert not 0 <= along[0] <= 1  # and comes in from outside the board
    assert [point["y"] for point in tall["path"]] == along  # a tall screen: the same crawl from top to bottom
    assert len({(point["x"], point["y"]) for point in calm["path"]}) == 1  # calm: where it appeared is where it stays
    assert all(0.1 <= point["x"] <= 0.9 and 0.1 <= point["y"] <= 0.9 for point in calm["path"])
    assert calm["life"] < wide["life"]  # and it stays for a shorter time, which is its difficulty


def test_bug_squash_a_run_played_in_the_page_is_accepted_by_the_server(squash, clock, tmp_path):
    played = node(
        tmp_path,
        """
        // A player who never misses: every 350 ms the oldest bug on the board that is not a feature.
        let waited = 0;
        const state = run(2026, {}, 16, (now) => {
          waited += 16;
          const target = now.bugs.find((bug) => !friend(bug) && Math.abs(game.position(now, bug).x - 0.5) < 0.48);
          if (waited >= 350 && target) {
            waited = 0;
            strike(now, target);
          }
        });
        print({ score: state.score, summary: game.summary(state), accuracy: game.accuracy(state) });
        """,
    )
    assert played["accuracy"] == 100 and played["summary"]["features_squashed"] == 0
    assert 1000 < played["score"] < squash.MAX_SCORE  # a very good run is still far from the limit
    assert squash.doubt(played["score"], squash.Summary(**played["summary"])) is None
    answer = play(client_for(squash.app), clock, BOB, {"score": played["score"], "summary": played["summary"]})
    assert answer.status_code == 200 and answer.json()["you"] == {"rank": 1, "score": played["score"]}


def test_bug_squash_draws_every_kind_with_plain_canvas_calls(tmp_path):
    seen = node(
        tmp_path,
        """
        import { drawSample, drawScene, EFFECT_MS } from "./static/render.js";

        // A stand-in for a 2D context: it knows the calls a browser knows and counts them.
        const known = ["save", "restore", "translate", "rotate", "setTransform", "beginPath", "closePath", "moveTo", "lineTo",
                       "quadraticCurveTo", "ellipse", "fill", "stroke", "fillRect", "clearRect", "fillText", "setLineDash"];
        const calls = {};
        const context = new Proxy({}, {
          get(target, name) {
            if (known.includes(name)) return (...args) => {
              if (args.some((arg) => typeof arg === "number" && !Number.isFinite(arg))) throw new Error(name + " got " + args);
              calls[name] = (calls[name] || 0) + 1;
            };
            if (name in target) return target[name];
            throw new Error("not a canvas call: " + String(name));
          },
          set(target, name, value) { target[name] = value; return true; },
        });

        const full = { ...view, ratio: 2 };
        for (const options of [{}, { calm: true }, { axis: "y" }]) {
          const state = game.newGame(rules, 11, options);
          game.step(state, 30000);
          const legacy = state.bugs.find((bug) => bug.kind === "legacy");
          if (legacy) strike(state, legacy);  // a cracked shell
          const effects = [
            { at: state.time - 100, x: 0.5, y: 0.5, text: "+25", tone: "good", ring: true },
            { at: state.time - EFFECT_MS, x: 0, y: 0, text: "miss", tone: "plain" },
          ];
          drawScene(context, full, state, effects, { dark: false, letters: true });
          drawScene(context, full, state, effects, { dark: true, letters: false });
        }
        drawScene(context, full, null, [], { dark: false });
        for (const kind of [...Object.keys(rules.kinds), "a kind of your own"]) drawSample(context, kind, 56, 2, true);
        print(calls);
        """,
    )
    assert seen["fill"] > 50 and seen["stroke"] > 20 and seen["fillText"] > 5 and seen["setLineDash"] >= 2
    assert seen["save"] == seen["restore"]  # nothing leaks from one bug into the next


def test_bug_squash_page_script_parses(tmp_path):
    # game.js and render.js are run by the tests above; app.js needs a page, so it is only parsed.
    runner, work = page_copy(tmp_path)
    done = subprocess.run([runner, "--check", "static/app.js"], cwd=work, capture_output=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stderr
