"""examples/standup-wheel: the server chooses who speaks next, the owner keeps the list.

The app runs for real against a stand-in Portal (which names the owner) and
the storage of a sandbox host. The geometry of the wheel is JavaScript
(``static/wheel.js``) and is run under node when node is on the PATH.
"""

import json
import shutil
import subprocess
import threading

import pytest

from efp_app import Visitor, errors, storage
from conftest import EXAMPLES
from example_support import APP_INFO, client_for, load_module, owner, signed

HERE = EXAMPLES / "standup-wheel"
STARTER = ["alice", "bob", "carol", "dave", "erin", "frank"]
ZED = {"sub": 20, "username": "zed", "name": "Zed"}  # a member who is not on the list
CAROL = {"sub": 14, "username": "carol", "name": "Carol"}

pytestmark = pytest.mark.skipif(not HERE.is_dir(), reason="needs a checkout of the repository (examples/)")


@pytest.fixture
def wheel(deployed, portal):
    """The app on a host; the Portal says that Alice owns it. Bob (``signed()``) is on the starter list."""
    pytest.importorskip("fastapi")
    portal.on("GET", "/app", APP_INFO)
    return load_module("example_standup_wheel", HERE / "main.py")


def spin(client, pick, round=1, headers=None):
    """Ask for a pick, as Bob unless ``headers`` says who (``{}``: nobody the gateway knows)."""
    return client.post("/api/spin", json={"round": round, "pick": pick}, headers=signed() if headers is None else headers)


def refusal(answer):
    assert set(answer.json()) == {"error"} and set(answer.json()["error"]) == {"code", "message"}
    assert answer.json()["error"]["message"].endswith(".")  # a sentence for the visitor
    return answer.status_code, answer.json()["error"]["code"]


def team(*people, version=0):
    """The body of a save: ``("bob", "Bob")``, ``("dave", "Dave", True)`` for somebody who is away."""
    return {
        "version": version,
        "members": [{"username": person[0], "name": person[1], "away": len(person) > 2 and person[2]} for person in people],
    }


# --- Watching ------------------------------------------------------------------------------------


def test_standup_wheel_shows_the_round_to_everybody_and_says_what_they_may_do(wheel, portal):
    client = client_for(wheel.app)
    guest = client.get("/api/state").json()
    assert guest["you"] == {"signed_in": False, "name": None, "on_list": False, "owner": False, "may_spin": False}
    assert guest["team"]["starter"] is True and guest["team"]["version"] == 0  # team.json, until the owner saves a list
    assert [member["username"] for member in guest["team"]["members"]] == STARTER
    assert guest["team"]["members"][1] == {"username": "bob", "name": "Bob", "away": False}
    assert guest["round"] == {"number": 1, "picks": [], "remaining": STARTER, "complete": False}
    assert guest["history"] == []

    you = lambda headers: client.get("/api/state", headers=headers).json()["you"]  # noqa: E731
    assert you(signed()) == {"signed_in": True, "name": "Bob", "on_list": True, "owner": False, "may_spin": True}
    assert you(signed(**ZED)) == {"signed_in": True, "name": "Zed", "on_list": False, "owner": False, "may_spin": False}
    assert you(owner()) == {"signed_in": True, "name": "Alice", "on_list": True, "owner": True, "may_spin": True}
    assert you(signed(sub=30, username="BOB", name="Bob B"))["on_list"] is True  # a list typed by hand: case does not matter
    assert portal.count("GET", "/app") == 1  # who the owner is was asked once
    assert storage.list("") == []  # looking writes nothing


# --- Spinning ------------------------------------------------------------------------------------


def test_standup_wheel_picks_on_the_server_among_those_who_have_not_spoken(wheel, monkeypatch):
    client = client_for(wheel.app)
    chance = wheel.secrets.choice
    offered = []

    def last_of(candidates):
        offered.append([person["username"] for person in candidates])
        return candidates[-1]

    monkeypatch.setattr(wheel.secrets, "choice", last_of)
    first = spin(client, 1).json()
    assert first["created"] is True
    picked = first["pick"]
    assert (picked["number"], picked["username"], picked["name"], picked["by"]) == (1, "frank", "Frank", "Bob")
    kept = storage.load_json("rounds/000001/picks/001.json")
    assert kept == {"username": "frank", "name": "Frank", "by": "Bob", "at": first["pick"]["at"]} and kept["at"].endswith("+00:00")
    assert first["state"]["round"] == {"number": 1, "picks": [first["pick"]], "remaining": STARTER[:5], "complete": False}

    # The body names nobody and chooses nothing: who spun is the signed visitor, who is next is the server's choice.
    wish = {"round": 1, "pick": 2, "username": "alice", "by": "Zed", "visitor": 20}
    forged = client.post("/api/spin", json=wish, headers=signed(**CAROL))
    assert forged.json()["pick"]["username"] == "erin" and forged.json()["pick"]["by"] == "Carol"
    assert offered == [STARTER, STARTER[:5]]  # Frank was not offered a second time

    monkeypatch.setattr(wheel.secrets, "choice", chance)  # the real chance from here on
    for number in (3, 4, 5, 6):
        assert spin(client, number).status_code == 200
    done = client.get("/api/state").json()["round"]
    assert sorted(pick["username"] for pick in done["picks"]) == STARTER  # everybody once
    assert done["remaining"] == [] and done["complete"] is True
    assert [pick["number"] for pick in done["picks"]] == [1, 2, 3, 4, 5, 6]
    assert [item.key for item in storage.list("")] == ["rounds/000001/picks/%03d.json" % number for number in range(1, 7)]
    assert refusal(spin(client, 7)) == (409, "round_complete")


def test_standup_wheel_two_people_pressing_at_once_get_one_pick(wheel):
    client = client_for(wheel.app)
    first = spin(client, 1).json()
    again = spin(client, 1, headers=signed(**CAROL)).json()  # Carol's page had not seen Bob's spin yet
    assert again["created"] is False and again["pick"] == first["pick"]  # the same spin: both wheels turn to one name
    assert len(storage.list("rounds/")) == 1 and again["state"]["round"]["picks"] == [first["pick"]]

    # And really at once: twelve requests for pick 2 start together.
    start = threading.Barrier(12)
    answers = []

    def press(number):
        start.wait()
        visitor = Visitor(id=100 + number, username="bob", name="Presser %d" % number)
        answers.append(wheel.spin(wheel.Spin(round=1, pick=2), visitor))

    threads = [threading.Thread(target=press, args=(number,)) for number in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert len(answers) == 12
    assert len({answer["pick"]["username"] for answer in answers}) == 1  # one name for all twelve
    assert [answer["created"] for answer in answers].count(True) == 1  # made once
    assert len({answer["pick"]["by"] for answer in answers}) == 1 and len(storage.list("rounds/")) == 2
    assert answers[0]["pick"]["username"] != first["pick"]["username"]


def test_standup_wheel_refuses_a_page_that_is_out_of_step(wheel):
    client = client_for(wheel.app)
    assert refusal(spin(client, 2)) == (409, "out_of_step")  # pick 2 before there is a pick 1
    assert refusal(spin(client, 1, round=2)) == (409, "out_of_step")  # a round that has not begun
    spin(client, 1)
    assert refusal(spin(client, 3)) == (409, "out_of_step")
    for body in ({"round": 1, "pick": 0}, {"round": 0, "pick": 1}, {"round": 1, "pick": 42}, {"round": 1}, {"pick": "next"}, []):
        answer = client.post("/api/spin", json=body, headers=signed())
        assert refusal(answer) == (400, "bad_input"), body
    assert len(storage.list("rounds/")) == 1

    # A pick is written once: an object that is in the way is never replaced.
    storage.save_json("rounds/000001/picks/002.json", ["damaged"])
    assert refusal(spin(client, 2)) == (409, "out_of_step")
    assert storage.load_json("rounds/000001/picks/002.json") == ["damaged"]


def test_standup_wheel_lets_only_the_people_on_the_list_spin(wheel):
    client = client_for(wheel.app)
    assert refusal(client.post("/api/spin", json={"round": 1, "pick": 1})) == (401, "sign_in_required")
    assert refusal(spin(client, 1, headers=signed(**ZED))) == (403, "not_on_the_list")
    assert refusal(client.post("/api/round/next", json={"round": 1})) == (401, "sign_in_required")
    assert refusal(client.post("/api/round/next", json={"round": 1}, headers=signed(**ZED))) == (403, "not_on_the_list")
    assert storage.list("") == []
    assert client.get("/api/state", headers=signed(**ZED)).status_code == 200  # watching is for everybody

    # The owner takes herself off the wheel and can still spin it.
    saved = client.put("/api/team", json=team(("bob", "Bob"), ("carol", "Carol")), headers=owner())
    assert saved.json()["you"] == {"signed_in": True, "name": "Alice", "on_list": False, "owner": True, "may_spin": True}
    assert spin(client, 1, headers=owner()).json()["pick"]["username"] in ("bob", "carol")
    assert refusal(spin(client, 2, headers=signed(sub=17, username="dave", name="Dave"))) == (403, "not_on_the_list")  # no longer on it


# --- The owner's list ------------------------------------------------------------------------------


def test_standup_wheel_list_is_saved_by_the_owner_alone(wheel):
    client = client_for(wheel.app)
    wanted = team((" Bob ", "  Bob   the  Builder "), ("carol", ""), ("dave", "Dave", True))
    assert refusal(client.put("/api/team", json=wanted)) == (401, "sign_in_required")
    assert refusal(client.put("/api/team", json=wanted, headers=signed())) == (403, "owner_only")  # on the list is not enough
    assert storage.list("") == []

    saved = client.put("/api/team", json=wanted, headers=owner()).json()
    assert saved["team"] == {
        "version": 1,
        "starter": False,
        "members": [
            {"username": "Bob", "name": "Bob the Builder", "away": False},
            {"username": "carol", "name": "carol", "away": False},  # no name: the username is shown
            {"username": "dave", "name": "Dave", "away": True},
        ],
    }
    assert saved["round"]["remaining"] == ["Bob", "carol"]  # who is away is not on the wheel
    kept = storage.load_json("team.json")
    assert kept["version"] == 1 and kept["updated_by"] == "alice" and kept["updated_at"].endswith("+00:00")
    assert kept["members"][2] == {"username": "dave", "name": "Dave", "away_round": 1}
    assert client.get("/api/state", headers=signed()).json()["you"]["on_list"] is True  # "Bob" on the list is bob

    # The owner's other window still shows version 0: its save would undo this one without knowing of it.
    assert refusal(client.put("/api/team", json=team(("zed", "Zed")), headers=owner())) == (409, "stale")
    assert storage.load_json("team.json") == kept
    emptied = client.put("/api/team", json=team(version=1), headers=owner()).json()
    assert emptied["team"]["version"] == 2 and emptied["team"]["members"] == [] and emptied["round"]["complete"] is False
    assert refusal(spin(client, 1, headers=owner())) == (409, "nobody")


def test_standup_wheel_checks_the_list_before_it_is_saved(wheel):
    client = client_for(wheel.app)
    crowd = [("member%d" % number, "Member %d" % number) for number in range(41)]
    refused = (
        (team(*crowd), "too_many"),
        (team(("bob smith", "Bob")), "bad_username"),
        (team(("", "Nobody")), "bad_username"),
        (team(("../team", "Dots")), "bad_username"),
        (team(("b" * 65, "Long")), "bad_username"),
        (team(("bob", "Bob"), ("carol", "Carol"), ("BOB", "Another Bob")), "twice"),
        (team(("bob", "B" * 41)), "bad_name"),
        (team(("bob", "Bob\u0007")), "bad_name"),
    )
    for body, code in refused:
        assert refusal(client.put("/api/team", json=body, headers=owner())) == (400, code), body
    nameless = {"version": 0, "members": [{"name": "No username"}]}
    for body in ({}, {"version": 0}, {"version": -1, "members": []}, nameless, {"version": 0, "members": "everybody"}):
        assert refusal(client.put("/api/team", json=body, headers=owner())) == (400, "bad_input"), body
    assert storage.list("") == []
    assert client.put("/api/team", json=team(*crowd[:40]), headers=owner()).status_code == 200  # forty fit on a wheel
    assert client.put("/api/team", json=team(("r.o-b_1@example", "Zo\u00eb \u738b"), version=1), headers=owner()).status_code == 200


# --- Rounds --------------------------------------------------------------------------------------


def test_standup_wheel_closes_a_round_once_and_lists_it(wheel):
    client = client_for(wheel.app)
    assert client.post("/api/round/next", json={"round": 1}, headers=signed()).json()["closed"] is False  # nothing to close yet
    first, second = spin(client, 1).json()["pick"], spin(client, 2).json()["pick"]

    closed = client.post("/api/round/next", json={"round": 1}, headers=signed(**CAROL)).json()
    assert closed["closed"] is True
    assert closed["state"]["round"] == {"number": 2, "picks": [], "remaining": STARTER, "complete": False}
    left = [name.capitalize() for name in STARTER if name not in (first["username"], second["username"])]
    [listed] = closed["state"]["history"]
    assert listed == {"round": 1, "closed_at": listed["closed_at"], "order": [first["name"], second["name"]], "left": left}
    kept = storage.load_json("history/000001.json")
    assert kept["closed_by"] == "Carol" and kept["order"] == listed["order"] and kept["closed_at"].endswith("+00:00")
    assert [item.key for item in storage.list("")] == ["history/000001.json"]  # the picks of a closed round are gone

    # Bob pressed the same button a moment later: his page still meant round 1.
    late = client.post("/api/round/next", json={"round": 1}, headers=signed()).json()
    assert late["closed"] is False and late["state"]["round"]["number"] == 2 and len(storage.list("history/")) == 1
    assert refusal(spin(client, 3)) == (409, "out_of_step")  # and so did his wheel
    assert spin(client, 1, round=2).status_code == 200

    # Should the app stop between writing the history and deleting the picks, the leftovers are never read.
    storage.save_json("rounds/000001/picks/001.json", {"username": "bob", "name": "Bob"})
    assert len(client.get("/api/state").json()["round"]["picks"]) == 1


def test_standup_wheel_away_lasts_for_one_round_and_history_shows_the_last_ten(wheel):
    client = client_for(wheel.app)
    client.put("/api/team", json=team(("bob", "Bob"), ("carol", "Carol"), ("dave", "Dave", True)), headers=owner())
    for number in (1, 2):
        assert spin(client, number).json()["pick"]["username"] in ("bob", "carol")
    state = client.get("/api/state").json()
    assert state["round"]["complete"] is True and state["round"]["remaining"] == []  # Dave is away: the round is over without him
    assert refusal(spin(client, 3)) == (409, "round_complete")

    after = client.post("/api/round/next", json={"round": 1}, headers=signed()).json()["state"]
    assert after["round"]["remaining"] == ["bob", "carol", "dave"]  # a new round, a new day
    assert after["team"]["members"][2] == {"username": "dave", "name": "Dave", "away": False}
    assert after["history"][0]["left"] == []  # who was away did not "not speak"

    for number in range(2, 13):
        spin(client, 1, round=number)
        assert client.post("/api/round/next", json={"round": number}, headers=signed()).json()["closed"] is True
    listed = client.get("/api/state").json()
    assert listed["round"]["number"] == 13 and [closed["round"] for closed in listed["history"]] == list(range(12, 2, -1))
    assert len(listed["history"][0]["order"]) == 1 and len(listed["history"][0]["left"]) == 2
    assert len(storage.list("history/")) == 12  # all are kept, the last ten are shown


# --- Around the app ------------------------------------------------------------------------------


def test_standup_wheel_without_a_token_nobody_is_the_owner(deployed):
    pytest.importorskip("fastapi")
    client = client_for(load_module("example_standup_wheel_plain", HERE / "main.py").app)
    you = client.get("/api/state", headers=owner()).json()["you"]
    assert you == {"signed_in": True, "name": "Alice", "on_list": True, "owner": False, "may_spin": True}
    assert spin(client, 1).status_code == 200  # the wheel needs no Portal
    answer = client.put("/api/team", json=team(("bob", "Bob")), headers=owner())
    assert answer.status_code == 502 and answer.json()["error"]["code"] == "not_configured"
    assert answer.json()["error"]["message"].startswith("This app is not set up yet.")


def test_standup_wheel_on_a_developers_machine(monkeypatch):
    pytest.importorskip("fastapi")
    client = client_for(load_module("example_standup_wheel_dev", HERE / "main.py").app)
    monkeypatch.setenv("EFP_DEV_VISITOR", "alice")
    you = client.get("/api/state").json()["you"]
    assert you == {"signed_in": True, "name": "alice", "on_list": True, "owner": False, "may_spin": True}
    assert spin(client, 1, headers={}).status_code == 200 and storage.mode() == "local"
    monkeypatch.setenv("EFP_DEV_VISITOR", "zed")
    assert refusal(spin(client, 2, headers={})) == (403, "not_on_the_list")
    monkeypatch.setenv("EFP_DEV_VISITOR", "anonymous")
    assert refusal(spin(client, 2, headers={})) == (401, "sign_in_required")
    assert len(client.get("/api/state").json()["round"]["picks"]) == 1


def test_standup_wheel_turns_a_storage_failure_into_a_sentence(wheel, monkeypatch):
    client = client_for(wheel.app)
    listing = storage.list

    def gone(prefix=""):
        raise errors.StorageUnavailableError("cannot reach the storage service at /run/efp/storage.sock")

    monkeypatch.setattr(storage, "list", gone)
    for answer in (client.get("/api/state"), spin(client, 1)):
        assert answer.status_code == 502
        assert answer.json() == {"error": {"code": "storage_unavailable", "message": errors.StorageUnavailableError().user_message}}
        assert "storage.sock" not in answer.text  # the detail stays in the log
    monkeypatch.setattr(storage, "list", listing)
    assert spin(client, 1).status_code == 200  # the lock was let go: the next spin is not stuck behind the failed one


def test_standup_wheel_page_works_under_a_prefix(wheel):
    # uvicorn --root-path /wheel: the path carries the prefix and root_path names it.
    client = client_for(wheel.app, root_path="/wheel")
    page = client.get("/wheel/")
    assert page.status_code == 200
    assert 'href="static/style.css"' in page.text and '<script type="module" src="static/app.js">' in page.text
    assert '<svg id="wheel"' in page.text and 'aria-live="polite"' in page.text
    for name in ("app.js", "wheel.js"):
        script = client.get("/wheel/static/" + name)
        assert script.status_code == 200 and "javascript" in script.headers["content-type"]  # a module must be served as one
    script = client.get("/wheel/static/app.js").text
    for call in ('ask("api/state")', 'ask("api/spin", "POST"', 'ask("api/round/next", "POST"', 'ask("api/team", "PUT"'):
        assert call in script, call
    assert "requestAnimationFrame(frame)" in script and "(prefers-reduced-motion: reduce)" in script
    assert "setTimeout" not in script  # nothing asks again by itself: "Refresh" does
    assert client.get("/wheel/api/state").json()["round"]["number"] == 1
    style = client.get("/wheel/static/style.css").text
    assert "transition" not in style and "animation" not in style  # nothing moves by CSS: the script's check covers every turn


# --- The geometry, under node ----------------------------------------------------------------------


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
        (work / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    return runner, work


def node(tmp_path, script):
    """Run ``script`` next to the page's modules; what it prints, decoded."""
    runner, work = page_copy(tmp_path)
    prelude = 'import * as wheel from "./static/wheel.js";\nconst print = (value) => console.log(JSON.stringify(value));\n'
    (work / "check.mjs").write_text(prelude + script, encoding="utf-8")
    done = subprocess.run([runner, "check.mjs"], cwd=work, capture_output=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_standup_wheel_stops_at_the_name_the_server_chose(tmp_path):
    seen = node(
        tmp_path,
        """
        // Every slice of every wheel, from every position, with the stop anywhere in the slice.
        let wrong = 0, short = 0, long = 0, checked = 0;
        for (let count = 1; count <= 40; count += 1) {
          for (let index = 0; index < count; index += 1) {
            for (const from of [0, 17.3, 359.99, 1234.5]) {
              for (const offset of [-0.35, 0, 0.35]) {
                const to = wheel.landingAngle(from, index, count, 5, offset);
                checked += 1;
                if (wheel.sliceAtPointer(to, count) !== index) wrong += 1;
                if (to < from + 5 * 360) short += 1;
                if (to >= from + 6 * 360) long += 1;
              }
            }
          }
        }
        const steps = Array.from({ length: 11 }, (_, step) => wheel.easeOut(step / 10));
        print({
          checked, wrong, short, long, steps,
          outside: [wheel.easeOut(-1), wheel.easeOut(2)],
          path: [wheel.angleAt(40, 1840, 0), wheel.angleAt(40, 1840, 1), wheel.angleAt(40, 1840, 0.5) > 940],
          six: wheel.landingAngle(0, 5, 6, 5, 0),
          spans: [wheel.sliceSpan(0, 4), wheel.sliceSpan(3, 4)],
          atTop: [wheel.sliceAtPointer(0, 4), wheel.sliceAtPointer(-10, 4), wheel.sliceAtPointer(10, 4), wheel.sliceAtPointer(360, 4)],
        });
        """,
    )
    assert seen["checked"] > 9000 and (seen["wrong"], seen["short"], seen["long"]) == (0, 0, 0)
    steps = seen["steps"]
    assert steps[0] == 0 and steps[-1] == 1 and steps == sorted(steps) and seen["outside"] == [0, 1]
    assert steps[1] - steps[0] > 5 * (steps[-1] - steps[-2])  # fast at first, slow at the end
    assert seen["path"] == [40, 1840, True]
    assert seen["six"] == 1830  # five turns and 30 degrees bring the last of six slices to the top
    assert seen["spans"] == [{"start": 0, "end": 90, "middle": 45}, {"start": 270, "end": 360, "middle": 315}]
    assert seen["atTop"] == [0, 0, 3, 0]  # turned clockwise by 10 degrees, the last slice is under the pointer


def test_standup_wheel_draws_slices_that_can_be_told_apart_and_names_that_fit(tmp_path):
    seen = node(
        tmp_path,
        """
        const clashes = [];
        for (let count = 2; count <= 40; count += 1) {
          for (let index = 0; index < count; index += 1) {
            const colour = wheel.sliceColour(index, count);
            const next = wheel.sliceColour((index + 1) % count, count);
            if (colour < 0 || colour >= wheel.SLICE_COLOURS || colour === next) clashes.push([count, index]);
          }
        }
        print({
          clashes, alone: wheel.sliceColour(0, 1),
          quarter: wheel.wedgePath(100, 0, 90), wide: wheel.wedgePath(100, 0, 240), whole: wheel.wedgePath(100, 0, 360),
          labels: [wheel.fitLabel("Bob", 6), wheel.fitLabel("Maximiliane Musterfrau", 6), wheel.fitLabel("Maximiliane Musterfrau", 40),
                   wheel.fitLabel("\\u738b\\u5c0f\\u660e", 6), Array.from(wheel.fitLabel("\\ud83d\\ude00".repeat(20), 6)).length],
          sizes: [1, 8, 9, 16, 17, 28, 29, 40].map((count) => wheel.labelSize(count)),
        });
        """,
    )
    assert seen["clashes"] == [] and seen["alone"] == 0  # no two neighbours share a colour, round the whole wheel
    assert seen["quarter"] == "M0 0L0 -100A100 100 0 0 1 100 0Z"
    assert " 0 1 1 " in seen["wide"]  # more than half a turn: the long way round
    assert seen["whole"].count("A") == 2 and "L" not in seen["whole"]  # one slice is the whole disc, drawn as two halves
    assert seen["labels"][:2] == ["Bob", "Maximiliane..."] and seen["labels"][2] == "Maximiliane Mu..."
    assert seen["labels"][3] == "\u738b\u5c0f\u660e" and seen["labels"][4] == 14  # cut by character, not in the middle of one
    assert seen["sizes"] == sorted(seen["sizes"], reverse=True) and seen["sizes"][0] > seen["sizes"][-1]


def test_standup_wheel_page_script_parses(tmp_path):
    # wheel.js is run by the tests above; app.js needs a page, so it is only parsed.
    runner, work = page_copy(tmp_path)
    done = subprocess.run([runner, "--check", "static/app.js"], cwd=work, capture_output=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stderr
