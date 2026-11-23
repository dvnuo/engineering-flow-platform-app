"""examples/mind-map: maps of one's own, a version number against stale saves, a tree checked on the server.

The backend runs for real against a sandbox data directory (``deployed``). The
tree and its layout are plain JavaScript modules; they are run with node when
node is installed.
"""

import json
import re
import shutil
import subprocess

import pytest

from efp_app import errors, storage
from conftest import EXAMPLES
from example_support import client_for, load_module, signed

HERE = EXAMPLES / "mind-map"
ROOT = {"id": "root", "parent": None, "text": "Plan", "color": None, "collapsed": False}

pytestmark = pytest.mark.skipif(not HERE.is_dir(), reason="needs a checkout of the repository (examples/)")


@pytest.fixture
def mind_map(deployed):
    pytest.importorskip("fastapi")
    return load_module("example_mind_map", HERE / "main.py")


def node(node_id, parent="root", text="x", **more):
    return dict({"id": node_id, "parent": parent, "text": text}, **more)


def eve():
    return signed(sub=13, username="eve", name="Eve")


def create(client, title="Plan", headers=None, **more):
    made = client.post("/api/maps", json=dict({"title": title}, **more), headers=headers or signed())
    assert made.status_code == 201, made.text
    return made.json()


def save(client, map_id, version, nodes=None, headers=None, **more):
    body = dict({"title": "Plan", "nodes": nodes or [ROOT], "shared": False, "version": version}, **more)
    return client.put("/api/maps/" + map_id, json=body, headers=headers or signed())


def keys(prefix=""):
    return [item.key for item in storage.list(prefix)]


# --- Maps of one's own ---------------------------------------------------------------------------


def test_mind_map_keeps_every_map_under_the_member_who_is_signed_in(mind_map):
    client = client_for(mind_map.app)
    bob = signed()

    start = client.get("/api/maps").json()
    assert start == {
        "signed_in": False,
        "me": None,
        "mine": [],
        "shared": [],
        "limits": {"maps": 30, "nodes": 500, "title": 80, "text": 200},
    }
    guest = client.post("/api/maps", json={"title": "Plan"})
    assert guest.status_code == 401 and guest.json()["error"]["code"] == "sign_in_required"

    # The body may claim an owner; the map is filed under the visitor of the signed header.
    made = create(client, "  Release   plan ", owner={"id": 13, "name": "Eve"}, id="ffffffffffff", version=7)
    assert made["title"] == "Release plan" and made["version"] == 1 and made["editable"] is True
    assert made["owner"] == {"id": 12, "name": "Bob"} and re.fullmatch(r"[0-9a-f]{12}", made["id"])
    assert made["id"] != "ffffffffffff"
    assert made["nodes"] == [dict(ROOT, text="Release plan")]
    assert keys() == ["maps/12/%s.json" % made["id"]]
    kept = storage.load_json("maps/12/%s.json" % made["id"])
    assert kept["owner"] == {"id": 12, "username": "bob", "name": "Bob"}
    assert kept["shared"] is False and kept["created_at"].endswith("+00:00")

    mine = client.get("/api/maps", headers=bob).json()
    assert mine["signed_in"] is True and mine["me"] == {"id": 12, "name": "Bob"}
    assert [(found["id"], found["title"], found["nodes"]) for found in mine["mine"]] == [(made["id"], "Release plan", 1)]
    assert client.get("/api/maps", headers=eve()).json()["mine"] == []

    renamed = client.patch("/api/maps/" + made["id"], json={"title": "Plan B", "version": 1}, headers=bob)
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Plan B" and renamed.json()["version"] == 2 and renamed.json()["nodes"] == 1
    opened = client.get("/api/maps/12/" + made["id"], headers=bob).json()
    assert opened["title"] == "Plan B" and opened["editable"] is True and opened["nodes"] == made["nodes"]

    # Another member has no way to it: reading, changing and deleting all answer "no such map".
    for refused in (
        client.get("/api/maps/12/" + made["id"], headers=eve()),
        client.get("/api/maps/12/" + made["id"]),
        client.patch("/api/maps/" + made["id"], json={"title": "Mine now", "version": 2}, headers=eve()),
        save(client, made["id"], 2, headers=eve()),
        client.delete("/api/maps/" + made["id"], headers=eve()),
    ):
        assert refused.status_code == 404 and refused.json()["error"]["code"] == "not_found"
    assert storage.load_json("maps/12/%s.json" % made["id"])["title"] == "Plan B"

    assert client.delete("/api/maps/" + made["id"]).status_code == 401
    assert client.delete("/api/maps/" + made["id"], headers=bob).json() == {"deleted": made["id"]}
    assert keys() == []
    assert client.delete("/api/maps/" + made["id"], headers=bob).status_code == 404


def test_mind_map_refuses_a_save_that_started_from_an_older_version(mind_map):
    client = client_for(mind_map.app)
    made = create(client)
    key = "maps/12/%s.json" % made["id"]

    # Two tabs load version 1. The first one saves.
    first = save(client, made["id"], 1, [ROOT, node("a", text="from the first tab")])
    assert first.status_code == 200 and first.json()["version"] == 2 and first.json()["nodes"] == 2

    # The second tab still holds version 1: its save would wipe out the first one's node.
    stale = save(client, made["id"], 1, [ROOT, node("b", text="from the second tab")])
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale"
    assert "Reload" in stale.json()["error"]["message"]
    kept = storage.load_json(key)
    assert kept["version"] == 2 and [found["text"] for found in kept["nodes"]] == ["Plan", "from the first tab"]
    assert client.patch("/api/maps/" + made["id"], json={"title": "Late", "version": 1}, headers=signed()).status_code == 409

    # After a reload it holds version 2 and may save.
    again = save(client, made["id"], 2, [ROOT, node("a"), node("b")])
    assert again.status_code == 200 and again.json()["version"] == 3
    assert storage.load_json(key)["version"] == 3

    for version in (None, "3", 3.0, True, [3]):  # a save always says where it started
        refused = save(client, made["id"], version)
        assert refused.status_code == 400 and refused.json()["error"]["code"] == "bad_input", version
    assert storage.load_json(key)["version"] == 3


def test_mind_map_shows_a_shared_map_to_the_others_read_only(mind_map):
    client = client_for(mind_map.app)
    bob = signed()
    made = create(client)
    tree = [ROOT, node("a", text="Build", color="green"), node("b", parent="a", text="Compile", collapsed=True)]
    assert save(client, made["id"], 1, tree, shared=True).status_code == 200
    assert keys("shared/") == ["shared/12/%s.json" % made["id"]]

    listed = client.get("/api/maps", headers=eve())
    [found] = listed.json()["shared"]
    assert found["id"] == made["id"] and found["title"] == "Plan" and found["nodes"] == 3
    assert found["owner"] == {"id": 12, "name": "Bob"}
    assert '"bob"' not in listed.text and "username" not in listed.text  # the display name is all that leaves
    assert [item["id"] for item in client.get("/api/maps").json()["shared"]] == [made["id"]]  # a guest sees it too

    mine = client.get("/api/maps", headers=bob).json()
    assert mine["shared"] == [] and mine["mine"][0]["shared"] is True  # one's own map is not "shared by others"

    for headers in (eve(), {}):
        seen = client.get("/api/maps/12/" + made["id"], headers=headers)
        assert seen.status_code == 200 and seen.json()["editable"] is False
        assert seen.json()["nodes"] == [
            ROOT,
            {"id": "a", "parent": "root", "text": "Build", "color": "green", "collapsed": False},
            {"id": "b", "parent": "a", "text": "Compile", "color": None, "collapsed": True},
        ]
    assert save(client, made["id"], 2, [ROOT], headers=eve()).status_code == 404  # shared is not writable
    assert len(storage.load_json("maps/12/%s.json" % made["id"])["nodes"]) == 3

    # "Make a copy of my own": a new map under Eve's id, private, version 1.
    copy = create(client, "Plan (copy)", headers=eve(), nodes=tree)
    assert copy["owner"] == {"id": 13, "name": "Eve"} and copy["shared"] is False and len(copy["nodes"]) == 3
    assert keys("maps/13/") == ["maps/13/%s.json" % copy["id"]]

    # Sharing can be taken back from the list; the map then does not exist for the others.
    private = client.patch("/api/maps/" + made["id"], json={"shared": False, "version": 2}, headers=bob)
    assert private.status_code == 200 and private.json()["shared"] is False and private.json()["nodes"] == 3
    assert keys("shared/") == []
    assert client.get("/api/maps/12/" + made["id"], headers=eve()).status_code == 404
    assert client.patch("/api/maps/" + made["id"], json={"shared": "yes", "version": 3}, headers=bob).status_code == 400

    assert client.patch("/api/maps/" + made["id"], json={"shared": True, "version": 3}, headers=bob).status_code == 200
    assert client.delete("/api/maps/" + made["id"], headers=bob).status_code == 200
    assert keys("shared/") == [] and keys("maps/12/") == []


def test_mind_map_list_of_shared_maps_may_be_wrong_and_the_map_decides(mind_map):
    client = client_for(mind_map.app)
    made = create(client)
    # A marker whose map was never shared, as a failure between the two saves could leave it.
    storage.save_json("shared/12/%s.json" % made["id"], dict(made, nodes=1, shared=True))
    storage.save_json("shared/12/000000000000.json", {"id": "000000000000", "owner": {"id": 12, "name": "Bob"}})
    storage.save_json("shared/12/broken.json", ["not", "a", "marker"])

    listed = client.get("/api/maps", headers=eve()).json()["shared"]
    assert sorted(found["id"] for found in listed) == ["000000000000", made["id"]]
    assert client.get("/api/maps/12/" + made["id"], headers=eve()).status_code == 404
    assert client.get("/api/maps/12/000000000000", headers=eve()).status_code == 404

    assert save(client, made["id"], 1).status_code == 200  # the next save of the owner puts the list right
    assert keys("shared/12/") == ["shared/12/000000000000.json", "shared/12/broken.json"]


# --- What the page sends is checked ----------------------------------------------------------------


@pytest.mark.parametrize(
    "nodes",
    [
        [],
        "root",
        [ROOT, "a"],
        [ROOT, node("a b")],
        [ROOT, node("")],
        [ROOT, node("x" * 25)],
        [ROOT, node("a"), node("a")],  # one id twice
        [ROOT, dict(ROOT, id="second")],  # two roots
        [node("a", parent="b"), node("b", parent="a")],  # no root at all
        [ROOT, node("a", parent="nowhere")],
        [ROOT, node("a", parent="b"), node("b", parent="a")],  # a loop beside the tree
        [ROOT, node("a", parent="a")],  # a node that is its own parent
        [ROOT, node("a", parent=5)],
        [ROOT, node("a", text="x" * 201)],
        [ROOT, node("a", text=None)],
        [ROOT, node("a", color="#ff0000")],
    ],
)
def test_mind_map_refuses_what_is_not_one_tree(mind_map, nodes):
    client = client_for(mind_map.app)
    made = create(client)
    refused = save(client, made["id"], 1, nodes) if nodes else client.put(
        "/api/maps/" + made["id"], json={"title": "Plan", "nodes": nodes, "version": 1}, headers=signed()
    )
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "bad_input"
    assert refused.json()["error"]["message"].endswith(".")  # a sentence for the page
    assert client.post("/api/maps", json={"title": "Copy", "nodes": nodes}, headers=signed()).status_code == 400
    kept = storage.load_json("maps/12/%s.json" % made["id"])
    assert kept["version"] == 1 and kept["nodes"] == [ROOT] and len(keys()) == 1


def test_mind_map_enforces_its_limits_on_the_server(mind_map, monkeypatch):
    client = client_for(mind_map.app)
    bob = signed()
    made = create(client)
    url = "/api/maps/" + made["id"]

    full = [ROOT] + [node("n%d" % number) for number in range(mind_map.MAX_NODES - 1)]
    assert save(client, made["id"], 1, full).status_code == 200
    crowded = save(client, made["id"], 2, full + [node("one-more")])
    assert crowded.status_code == 400 and crowded.json()["error"]["code"] == "too_many_nodes"

    for title in ("", "   ", "x" * 81, 5, None):
        refused = save(client, made["id"], 2, title=title)
        assert refused.status_code == 400 and refused.json()["error"]["code"] == "bad_title", title
        assert client.post("/api/maps", json={"title": title}, headers=bob).status_code == 400

    # A body over the limit is refused by its announced length, and when none is announced, while it arrives.
    huge = json.dumps({"title": "Plan", "version": 2, "nodes": [dict(ROOT, padding="x" * mind_map.MAX_MAP_BYTES)]})
    announced = client.put(url, content=huge, headers=dict(bob, **{"Content-Type": "application/json"}))
    assert announced.status_code == 413 and announced.json()["error"]["code"] == "too_large"
    streamed = client.put(url, content=iter([huge.encode("utf-8")]), headers=bob)
    assert streamed.status_code == 413

    for content in (b"{not json", b"[1, 2]", b"\xff\xfe", b"[" * 100000):
        refused = client.put(url, content=content, headers=bob)
        assert refused.status_code == 400 and refused.json()["error"]["code"] == "bad_input", content[:10]

    # Only the five fields of a node are stored, with the text tidied.
    extra = node("a", text="  two\n words ", onclick="alert(1)", collapsed="yes", color=None)
    assert save(client, made["id"], 2, [ROOT, extra]).status_code == 200
    assert storage.load_json("maps/12/%s.json" % made["id"])["nodes"][1] == {
        "id": "a",
        "parent": "root",
        "text": "two words",
        "color": None,
        "collapsed": False,
    }

    # Ids become part of a storage key: anything but twelve hex digits is "no such map".
    for bad_id in ("ABCDEF123456", "abc", "a" * 13, "..%2F..%2Fx"):
        assert client.get("/api/maps/12/" + bad_id, headers=bob).status_code == 404
        assert save(client, bad_id, 1).status_code == 404
    not_a_number = client.get("/api/maps/twelve/" + made["id"], headers=bob)
    assert not_a_number.status_code == 400 and not_a_number.json()["error"]["code"] == "bad_input"

    monkeypatch.setattr(mind_map, "MAX_MAPS", 2)
    create(client, "Second")
    third = client.post("/api/maps", json={"title": "Third"}, headers=bob)
    assert third.status_code == 409 and third.json()["error"]["code"] == "limit_reached"
    assert len(keys("maps/12/")) == 2
    assert create(client, "Hers", headers=eve())["owner"]["id"] == 13  # the limit is per member


def test_mind_map_turns_a_storage_error_into_a_sentence(mind_map, monkeypatch):
    client = client_for(mind_map.app)
    made = create(client)

    def full(key, value):
        raise errors.QuotaExceededError("no space left to save %r" % key)

    monkeypatch.setattr(storage, "save_json", full)
    refused = save(client, made["id"], 1)
    assert refused.status_code == 502
    assert refused.json()["error"] == {"code": "quota_exceeded", "message": "This app has run out of storage space."}
    assert client.post("/api/maps", json={"title": "More"}, headers=signed()).status_code == 502

    def broken(prefix=""):
        raise errors.StorageError("could not list %r: input/output error" % prefix)

    monkeypatch.setattr(storage, "list", broken)
    listing = client.get("/api/maps")
    assert listing.status_code == 502 and "input/output" not in listing.text  # the detail stays in the log
    assert listing.json()["error"]["message"] == "The app could not read or save its data. Please try again."


# --- The page ---------------------------------------------------------------------------------------


def test_mind_map_page_and_its_modules_work_under_a_prefix(mind_map):
    # uvicorn --root-path /maps: the path carries the prefix and root_path names it.
    client = client_for(mind_map.app, root_path="/maps")
    page = client.get("/maps/")
    assert page.status_code == 200
    assert 'href="static/style.css"' in page.text and '<script type="module" src="static/app.js">' in page.text
    for name in ("app.js", "api.js", "tree.js", "layout.js", "render.js", "scene.js", "exporting.js"):
        script = client.get("/maps/static/" + name)
        assert script.status_code == 200 and "javascript" in script.headers["content-type"], name
        imported = re.findall(r'^import .* from "([^"]+)";$', script.text, flags=re.MULTILINE)
        # The app's own modules relatively (they find each other below the prefix); the shared
        # libraries at the one address every gateway serves.
        assert all(source.startswith(("./", "/_efp/lib/")) for source in imported), name
    calls = client.get("/maps/static/api.js").text
    assert 'api("GET", "api/maps")' in calls and 'api("PUT", "api/maps/" + mapId' in calls
    assert re.search(r'api\("[A-Z]+", "/', calls) is None  # no request starts at the top of the host
    assert client.get("/maps/api/maps").json()["signed_in"] is False


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


TREE_SCRIPT = """
import * as tree from "./static/tree.js";

const out = {};
let nodes = [{ id: "root", parent: null, text: "Plan", color: null, collapsed: false }];
const add = (how, at, text) => {
  const made = how(nodes, at, text);
  nodes = made.nodes;
  return made.id;
};
const build = add(tree.addChild, "root", "Build");
const test = add(tree.addChild, "root", "Test");
const review = add(tree.addSibling, build, "Review");
const compile = add(tree.addChild, build, "Compile");
const extra = add(tree.addSibling, "root", "Ship");
out.children = nodes.filter((node) => node.parent === "root").map((node) => node.text);
out.ids = new Set(nodes.map((node) => node.id)).size === nodes.length;
out.markdown = tree.toMarkdown(nodes);

out.cannotMove = [
  tree.moveProblem(nodes, build, compile),
  tree.moveProblem(nodes, "root", build),
  tree.moveProblem(nodes, compile, build),
  tree.moveProblem(nodes, build, "gone"),
].map((reason) => reason !== "");
out.unchanged = tree.moveNode(nodes, build, compile) === nodes && tree.reorder(nodes, "root", 1) === nodes;
const folded = tree.toggleCollapsed(nodes, build);
const moved = tree.moveNode(folded, review, build);
out.moved = [tree.find(moved, review).parent === build, tree.find(folded, build).collapsed, tree.find(moved, build).collapsed];

out.reordered = tree.reorder(nodes, review, -1).filter((node) => node.parent === "root").map((node) => node.text);
const removed = tree.removeBranch(nodes, build);
out.removed = [removed.nodes.map((node) => node.text), removed.select === review];
out.rootStays = tree.removeBranch(nodes, "root").nodes === nodes;

const coloured = tree.setColor(tree.setColor(nodes, build, "green"), extra, "no-such-colour");
out.colours = [tree.branchColor(coloured, compile), tree.branchColor(coloured, build), tree.branchColor(coloured, extra)];
out.folded = [
  tree.find(folded, build).collapsed,
  tree.toggleCollapsed(nodes, compile) === nodes,
  tree.find(tree.addChild(folded, build, "x").nodes, build).collapsed,
];
out.same = tree.rename(nodes, build, "Build") === nodes && tree.setColor(nodes, build, null) === nodes;
out.untouched = nodes.length === 6 && nodes.every((node) => node.color === null && !node.collapsed);
console.log(JSON.stringify(out));
"""


def test_mind_map_tree_changes_are_plain_functions(tmp_path):
    out = run_node(tmp_path, TREE_SCRIPT)
    assert out["children"] == ["Build", "Review", "Test", "Ship"] and out["ids"] is True
    assert out["markdown"] == "# Plan\n\n- Build\n  - Compile\n- Review\n- Test\n- Ship\n"
    assert out["cannotMove"] == [True, True, True, True]  # into its own branch, the centre, where it is, a missing node
    assert out["unchanged"] is True
    assert out["moved"] == [True, True, False]  # the new parent opens up
    assert out["reordered"] == ["Review", "Build", "Test", "Ship"]
    assert out["removed"] == [["Plan", "Review", "Test", "Ship"], True] and out["rootStays"] is True
    assert out["colours"] == ["green", "green", None]  # a colour is the colour of the branch
    assert out["folded"] == [True, True, False]  # a leaf does not fold; a new child opens its parent
    assert out["same"] is True  # a change that changes nothing is not a step to undo
    assert out["untouched"] is True  # every change returned a new list


LAYOUT_SCRIPT = """
import { SIZES, edgePath, fitView, layoutTree, neighbour, wrapLines, zoomAt } from "./static/layout.js";
import { fileName } from "./static/exporting.js";

const measure = (text) => text.length * 7;  // a ruler: seven units a character
const node = (id, parent, text, collapsed) => ({ id, parent, text, color: null, collapsed: Boolean(collapsed) });
const nodes = [node("root", null, "Plan"), node("a", "root", "Build"), node("b", "root", "Test")];
for (let index = 0; index < 5; index += 1) {
  nodes.push(node("a" + index, "a", "step " + index + " of the build with a long description to wrap"));
}
nodes.push(node("b0", "b", "unit"), node("b00", "b0", "fast"), node("b01", "b0", "slow"));

const layout = layoutTree(nodes, measure);
const boxes = [...layout.boxes.values()];
const overlap = (first, second) =>
  first.x < second.x + second.width && second.x < first.x + first.width &&
  first.y < second.y + second.height && second.y < first.y + first.height;
const out = { count: boxes.length, overlaps: 0, order: layout.order[0] };
for (const first of boxes) {
  for (const second of boxes) {
    if (first !== second && overlap(first, second)) {
      out.overlaps += 1;
    }
  }
}
// A node sits in the middle of its branch: of itself and everything below it.
const inBranch = (box, id) => {
  for (let current = box; current; current = layout.boxes.get(current.parent)) {
    if (current.id === id) {
      return true;
    }
  }
  return false;
};
out.centred = ["root", "a", "b", "b0"].every((id) => {
  const branch = boxes.filter((box) => inBranch(box, id));
  const top = Math.min(...branch.map((box) => box.y));
  const bottom = Math.max(...branch.map((box) => box.y + box.height));
  const own = layout.boxes.get(id);
  return branch.length > 1 && Math.abs(own.y + own.height / 2 - (top + bottom) / 2) < 0.001;
});
out.right = boxes.every((box) => !box.parent || box.x === layout.boxes.get(box.parent).x + layout.boxes.get(box.parent).width + SIZES.gapX);
out.inside = boxes.every((box) => box.x >= 0 && box.y >= 0 && box.x + box.width <= layout.width && box.y + box.height <= layout.height + 0.001);
out.wrapped = layout.boxes.get("a0").lines.length > 1 && layout.boxes.get("a0").lines.every((line) => measure(line) <= SIZES.maxTextWidth);
out.edges = layout.edges.length;
out.path = edgePath({ x: 0, y: 0, width: 10, height: 20 }, { x: 50, y: 40, width: 10, height: 20 });

const folded = layoutTree(nodes.map((item) => (item.id === "a" ? { ...item, collapsed: true } : item)), measure);
out.folded = [folded.boxes.size, folded.boxes.get("a").hidden, folded.boxes.has("a0"), folded.height < layout.height];

out.walk = [
  neighbour(layout, "root", "right"), neighbour(layout, "a", "down"), neighbour(layout, "b", "up"),
  neighbour(layout, "a", "left"), neighbour(layout, "root", "left"), neighbour(layout, "b01", "down"),
  neighbour(layout, "a4", "down"), neighbour(layout, "gone", "up"),
];
out.lines = [
  wrapLines("one two three four", 70, measure),
  wrapLines("abcdefghijklmnopqrstuvwxyz", 70, measure),
  wrapLines("", 70, measure),
];
const fitted = fitView(layout, 800, 600);
out.fits = fitted.k > 0 && fitted.k <= 1.25 && fitted.x >= 39 && fitted.y >= 39 &&
  fitted.x + layout.width * fitted.k <= 761 && fitted.y + layout.height * fitted.k <= 561;
const zoomed = zoomAt({ x: 10, y: 20, k: 1 }, 2, 100, 100);
out.zoom = [zoomed, zoomAt({ x: 0, y: 0, k: 1 }, 100, 0, 0).k, zoomAt({ x: 0, y: 0, k: 1 }, 0.001, 0, 0).k];
out.empty = layoutTree([], measure).boxes.size;
out.names = [fileName("Release plan: Q4 / 2026", "svg"), fileName("???", "md"), fileName("x".repeat(100), "png").length];
console.log(JSON.stringify(out));
"""


def test_mind_map_layout_is_tidy(tmp_path):
    out = run_node(tmp_path, LAYOUT_SCRIPT)
    assert out["count"] == 11 and out["order"] == "root" and out["edges"] == 10
    assert out["overlaps"] == 0 and out["inside"] is True
    assert out["centred"] is True and out["right"] is True and out["wrapped"] is True
    assert out["path"] == "M10 10 C30 10 30 50 50 50"
    assert out["folded"] == [6, 5, False, True]  # five nodes hidden behind "+5"
    assert out["walk"] == ["a", "b", "a", "root", None, None, "b0", None]
    assert out["lines"] == [["one two", "three four"], ["abcdefghij", "klmnopqrst", "uvwxyz"], [""]]
    assert out["fits"] is True
    assert out["zoom"] == [{"k": 2, "x": -80, "y": -60}, 3, 0.2]  # the point under the cursor stays put
    assert out["empty"] == 0
    assert out["names"] == ["Release-plan-Q4-2026.svg", "mind-map.md", 64]
