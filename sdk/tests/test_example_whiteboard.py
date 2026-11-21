"""examples/whiteboard: pictures as binary objects, an upload that is checked, a gallery with an owner.

The backend runs for real against a sandbox data directory (``deployed``); the
Portal is the fake one where the owner of the app matters. The strokes and
the painting are plain JavaScript modules, run with node when it is installed.
"""

import json
import os
import re
import shutil
import struct
import subprocess
import zlib

import pytest

from efp_app import errors, storage
from conftest import EXAMPLES
from example_support import APP_INFO, client_for, load_module, owner, signed

HERE = EXAMPLES / "whiteboard"
SIGNATURE = b"\x89PNG\r\n\x1a\n"

pytestmark = pytest.mark.skipif(not HERE.is_dir(), reason="needs a checkout of the repository (examples/)")


@pytest.fixture
def whiteboard(deployed):
    pytest.importorskip("fastapi")
    return load_module("example_whiteboard", HERE / "main.py")


def chunk(kind, data):
    """One chunk of a PNG file: length, type, data, CRC of type and data."""
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def header(width, height):
    return chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))  # 8 bits, RGB


def png(width=16, height=10, noise=False):
    """A real PNG: red, or random pixels (which do not compress) for a file of a known size."""
    rows = b"".join(b"\x00" + (os.urandom(3 * width) if noise else b"\xff\x00\x00" * width) for _ in range(height))
    return SIGNATURE + header(width, height) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def eve():
    return signed(sub=13, username="eve", name="Eve")


def send(client, picture, headers=None, **query):
    headers = dict(signed() if headers is None else headers, **{"Content-Type": "image/png"})
    return client.post("/api/drawings", params=query, content=picture, headers=headers)


def keys(prefix="drawings/"):
    return [item.key for item in storage.list(prefix)]


# --- Bytes in, bytes out ------------------------------------------------------------------------------


def test_whiteboard_keeps_a_drawing_as_bytes_with_a_record_next_to_them(whiteboard):
    client = client_for(whiteboard.app)
    picture = png(16, 10)

    saved = send(client, picture, title="  Login   flow ", author="eve", id="0000000000000000")
    assert saved.status_code == 201
    drawing = saved.json()
    assert re.fullmatch(r"[0-9a-f]{16}", drawing["id"]) and drawing["id"] != "0000000000000000"
    assert drawing == {
        "id": drawing["id"],
        "author": {"id": 12, "name": "Bob"},  # the visitor of the signed header, whatever the query claims
        "title": "Login flow",
        "width": 16,  # read from the file, not taken from the browser
        "height": 10,
        "bytes": len(picture),
        "created_at": drawing["created_at"],
        "mine": True,
        "can_delete": True,
    }
    assert keys() == ["drawings/12/%s.json" % drawing["id"], "drawings/12/%s.png" % drawing["id"]]
    assert storage.load("drawings/12/%s.png" % drawing["id"]) == picture  # the bytes as they arrived
    record = storage.load_json("drawings/12/%s.json" % drawing["id"])
    assert record["author"] == {"id": 12, "username": "bob", "name": "Bob"}
    assert record["title"] == "Login flow" and record["bytes"] == len(picture) and record["created_at"].endswith("+00:00")

    # The picture comes back as what it is, and the browser may keep it for good.
    for headers in (signed(), eve(), {}):
        served = client.get("/api/drawings/12/%s/image" % drawing["id"], headers=headers)
        assert served.status_code == 200 and served.content == picture
        assert served.headers["content-type"] == "image/png"
        assert served.headers["cache-control"] == "private, max-age=31536000, immutable"

    assert send(client, png(4, 3), headers=eve()).json()["title"] == "Untitled"
    listed = client.get("/api/drawings", headers=eve())
    gallery = listed.json()
    assert gallery["signed_in"] is True and gallery["me"] == {"id": 13, "name": "Eve"}
    assert gallery["limits"] == {"bytes": 2 * 1024 * 1024, "side": 4096, "per_member": 24, "title": 80}
    by_title = {found["title"]: found for found in gallery["drawings"]}
    assert set(by_title) == {"Login flow", "Untitled"}
    assert by_title["Login flow"]["mine"] is False and by_title["Login flow"]["can_delete"] is False
    assert by_title["Untitled"]["mine"] is True and by_title["Untitled"]["can_delete"] is True
    assert '"bob"' not in listed.text and "username" not in listed.text  # the display name is all that leaves

    guest = client.get("/api/drawings").json()
    assert guest["signed_in"] is False and guest["me"] is None and len(guest["drawings"]) == 2
    assert not any(found["mine"] or found["can_delete"] for found in guest["drawings"])


def test_whiteboard_lists_the_newest_drawing_first_and_skips_what_is_not_a_record(whiteboard):
    client = client_for(whiteboard.app)
    for number, day in enumerate(("2026-11-18", "2026-11-20", "2026-11-19")):
        drawing_id = "%016x" % number
        record = {"id": drawing_id, "author": {"id": 12, "name": "Bob"}, "title": day, "created_at": day + "T08:00:00+00:00"}
        storage.save_json("drawings/12/%s.json" % drawing_id, record)
    storage.save_json("drawings/12/ffffffffffffffff.json", ["not", "a", "record"])
    storage.save("drawings/12/eeeeeeeeeeeeeeee.png", png())  # bytes without a record: a save that failed half way
    gallery = client.get("/api/drawings").json()
    assert [found["title"] for found in gallery["drawings"]] == ["2026-11-20", "2026-11-19", "2026-11-18"]


# --- What is refused ------------------------------------------------------------------------------------


def corrupted(picture):
    """The same file with one byte of the pixel data changed: its checksum no longer fits."""
    place = picture.index(b"IDAT") + 6
    return picture[:place] + bytes([picture[place] ^ 0xFF]) + picture[place + 1 :]


@pytest.mark.parametrize(
    "body, code",
    [
        (b"", "not_a_png"),
        (b"<html><script>alert(1)</script></html>", "not_a_png"),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 64, "not_a_png"),  # a JPEG
        (SIGNATURE, "not_a_png"),
        (png()[:-5], "not_a_png"),  # cut off
        (png() + b"<script>alert(1)</script>", "not_a_png"),  # something after the end
        (corrupted(png()), "not_a_png"),
        (SIGNATURE + chunk(b"IEND", b""), "not_a_png"),  # no header
        (SIGNATURE + header(16, 10) + chunk(b"IEND", b""), "not_a_png"),  # no pixels
        (SIGNATURE + chunk(b"IHDR", b"\x00" * 12) + chunk(b"IDAT", b"x") + chunk(b"IEND", b""), "not_a_png"),
        (SIGNATURE + chunk(b"IDAT", b"x") + header(16, 10) + chunk(b"IEND", b""), "not_a_png"),  # header not first
        (SIGNATURE + header(0, 10) + chunk(b"IDAT", b"x") + chunk(b"IEND", b""), "bad_size"),
        (SIGNATURE + header(4097, 10) + chunk(b"IDAT", b"x") + chunk(b"IEND", b""), "bad_size"),
        (SIGNATURE + header(10, 2**31) + chunk(b"IDAT", b"x") + chunk(b"IEND", b""), "bad_size"),
    ],
)
def test_whiteboard_keeps_only_what_is_a_png_of_sensible_size(whiteboard, body, code):
    refused = send(client_for(whiteboard.app), body)
    assert refused.status_code == 400 and refused.json()["error"]["code"] == code
    assert refused.json()["error"]["message"].endswith(".")
    assert keys() == []


def test_whiteboard_refuses_guests_long_titles_and_large_uploads(whiteboard, monkeypatch):
    client = client_for(whiteboard.app)
    guest = send(client, png(), headers={})
    assert guest.status_code == 401 and guest.json()["error"]["code"] == "sign_in_required"
    long_title = send(client, png(), title="x" * 81)
    assert long_title.status_code == 400 and long_title.json()["error"]["code"] == "bad_title"
    assert send(client, png(), title="x" * 80).status_code == 201

    monkeypatch.setattr(whiteboard, "MAX_BYTES", 3000)
    large = png(64, 64, noise=True)  # a correct PNG; only its size is wrong
    assert len(large) > 3000 > len(png())
    announced = send(client, large)
    assert announced.status_code == 413 and announced.json()["error"]["code"] == "too_large"
    # No Content-Length (a chunked upload): the bytes are counted while they arrive.
    streamed = client.post("/api/drawings", content=iter([large[:2000], large[2000:]]), headers=signed())
    assert streamed.status_code == 413
    assert send(client, png()).status_code == 201
    assert len(keys()) == 4  # two drawings, each a picture and a record


def test_whiteboard_limits_the_drawings_of_one_member(whiteboard, monkeypatch):
    monkeypatch.setattr(whiteboard, "MAX_PER_MEMBER", 2)
    client = client_for(whiteboard.app)
    first = send(client, png()).json()
    assert send(client, png()).status_code == 201
    full = send(client, png())
    assert full.status_code == 409 and full.json()["error"]["code"] == "limit_reached"
    assert len(keys("drawings/12/")) == 4
    assert send(client, png(), headers=eve()).status_code == 201  # the count is per member

    assert client.delete("/api/drawings/12/" + first["id"], headers=signed()).json() == {"deleted": first["id"]}
    assert send(client, png()).status_code == 201


# --- Who may delete --------------------------------------------------------------------------------------


def test_whiteboard_lets_the_author_and_the_owner_of_the_app_delete(whiteboard, portal):
    portal.on("GET", "/app", APP_INFO)  # the owner is Alice, id 5
    client = client_for(whiteboard.app)
    bobs = send(client, png()).json()
    eves = send(client, png(), headers=eve()).json()
    url = "/api/drawings/12/" + bobs["id"]

    assert client.delete(url).status_code == 401
    other = client.delete(url, headers=eve())
    assert other.status_code == 403 and other.json()["error"]["code"] == "not_yours"
    assert client.delete(url + "/image", headers=signed()).status_code == 405  # there is no such route
    for missing in ("/api/drawings/12/" + "0" * 16, "/api/drawings/13/" + bobs["id"], "/api/drawings/12/not-an-id"):
        assert client.delete(missing, headers=signed()).status_code == 404
        assert client.get(missing + "/image").status_code == 404
    assert client.delete("/api/drawings/bob/" + bobs["id"], headers=signed()).status_code == 400
    assert len(keys()) == 4

    seen_by_owner = client.get("/api/drawings", headers=owner()).json()["drawings"]
    assert [(found["mine"], found["can_delete"]) for found in seen_by_owner] == [(False, True), (False, True)]
    assert client.delete(url, headers=owner()).json() == {"deleted": bobs["id"]}
    assert keys() == ["drawings/13/%s.json" % eves["id"], "drawings/13/%s.png" % eves["id"]]
    assert client.get(url + "/image").status_code == 404
    assert portal.count("GET", "/app") == 1  # who the owner is was asked once

    assert client.delete("/api/drawings/13/" + eves["id"], headers=eve()).status_code == 200  # an author needs no Portal
    assert keys() == [] and portal.count("GET", "/app") == 1


def test_whiteboard_knows_no_owner_while_the_portal_cannot_be_asked(whiteboard):
    # A deployed app without a token (or a Portal that is away): the gallery works, and nobody is the owner.
    client = client_for(whiteboard.app)
    drawing = send(client, png()).json()
    refused = client.delete("/api/drawings/12/" + drawing["id"], headers=owner())
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "not_yours"
    assert client.get("/api/drawings", headers=owner()).json()["drawings"][0]["can_delete"] is False
    assert whiteboard._owner_unknown_until > 0  # and the question is not repeated on every request
    assert client.delete("/api/drawings/12/" + drawing["id"], headers=signed()).status_code == 200


def test_whiteboard_never_shows_half_a_drawing(whiteboard, monkeypatch):
    client = client_for(whiteboard.app)
    kept = send(client, png()).json()
    really_delete, really_save = storage.delete, storage.save_json

    # Saving: the record is the second of two saves. When it fails, the bytes are taken back.
    def full(key, value):
        raise errors.QuotaExceededError("no space left to save %r" % key)

    monkeypatch.setattr(storage, "save_json", full)
    refused = send(client, png())
    assert refused.status_code == 502
    assert refused.json()["error"] == {"code": "quota_exceeded", "message": "This app has run out of storage space."}
    assert keys() == ["drawings/12/%s.json" % kept["id"], "drawings/12/%s.png" % kept["id"]]
    monkeypatch.setattr(storage, "save_json", really_save)

    # Deleting: the record goes first. When the picture cannot be removed, the gallery is already right.
    def stuck(key):
        if key.endswith(".png"):
            raise errors.StorageError("could not delete %r: input/output error" % key)
        return really_delete(key)

    monkeypatch.setattr(storage, "delete", stuck)
    failed = client.delete("/api/drawings/12/" + kept["id"], headers=signed())
    assert failed.status_code == 502 and "input/output" not in failed.text  # the detail stays in the log
    assert failed.json()["error"]["message"] == "The app could not read or save its data. Please try again."
    assert client.get("/api/drawings").json()["drawings"] == []
    assert keys() == ["drawings/12/%s.png" % kept["id"]]


# --- The page ---------------------------------------------------------------------------------------------


def test_whiteboard_page_and_its_modules_work_under_a_prefix(whiteboard):
    # uvicorn --root-path /board: the path carries the prefix and root_path names it.
    client = client_for(whiteboard.app, root_path="/board")
    page = client.get("/board/")
    assert page.status_code == 200
    assert 'href="static/style.css"' in page.text and '<script type="module" src="static/app.js">' in page.text
    assert '<canvas id="board" width="1600" height="1000"' in page.text
    for name in ("app.js", "api.js", "paint.js", "shapes.js"):
        script = client.get("/board/static/" + name)
        assert script.status_code == 200 and "javascript" in script.headers["content-type"], name
        imported = re.findall(r'^import .* from "([^"]+)";$', script.text, flags=re.MULTILINE)
        assert all(source.startswith("./") for source in imported), name
    calls = client.get("/board/static/api.js").text
    assert 'send("api/drawings", {})' in calls and 'send("api/drawings?" + new URLSearchParams({ title })' in calls
    assert '"api/drawings/" + authorId + "/" + id + "/image"' in calls  # where an <img> finds a picture
    saved = send(client_for(whiteboard.app), png()).json()
    assert client.get("/board/api/drawings/12/%s/image" % saved["id"]).headers["content-type"] == "image/png"
    assert len(client.get("/board/api/drawings").json()["drawings"]) == 1


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


STROKES_SCRIPT = """
import * as shapes from "./static/shapes.js";

const out = {};
const frame = { left: 100, top: 50, width: 800, height: 500 };  // the board at half its size
out.points = [
  shapes.boardPoint(frame, 100, 50), shapes.boardPoint(frame, 500, 300), shapes.boardPoint(frame, 900, 550),
  shapes.boardPoint(frame, -40, 9999),
];
const pen = shapes.startStroke("pen", "#000000", 6, { x: 0, y: 0 });
for (const x of [0.5, 1, 2, 2.5, 10]) {
  shapes.extendStroke(pen, { x, y: 0 });
}
out.pen = pen.points.map((point) => point.x);
out.eraser = shapes.startStroke("eraser", "#000000", 6, { x: 0, y: 0 }).width;
const line = shapes.extendStroke(shapes.startStroke("line", "#b3261e", 3, { x: 10, y: 10 }), { x: 11, y: 10 });
out.keep = [
  shapes.isWorthKeeping(line), shapes.isWorthKeeping(shapes.extendStroke(line, { x: 60, y: 10 })),
  shapes.isWorthKeeping(shapes.startStroke("pen", "#000000", 3, { x: 1, y: 1 })),
  shapes.isWorthKeeping({ tool: "text", text: "  " }), shapes.isWorthKeeping({ tool: "text", text: "hi" }),
];
out.box = shapes.box({ x: 50, y: 80 }, { x: 20, y: 100 });
const head = shapes.arrowHead({ x: 0, y: 0 }, { x: 100, y: 0 }, 3);
out.head = [
  head.length, Math.abs(head[0].x - head[1].x) < 1e-9, Math.abs(head[0].y + head[1].y) < 1e-9,
  head[0].x < 100, Math.round(Math.hypot(head[0].x - 100, head[0].y)),
];
out.font = [shapes.fontSize(3), shapes.fontSize(24)];

const history = shapes.createHistory(5);
out.fresh = [history.canUndo(), history.canRedo(), history.isEmpty()];
history.add({ tool: "pen", id: 1 });
history.add({ tool: "pen", id: 2 });
history.undo();
out.undone = [history.visible().map((stroke) => stroke.id), history.canRedo()];
history.redo();
history.add({ tool: "clear" });
out.cleared = [history.visible().length, history.isEmpty(), history.canUndo()];
history.add({ tool: "pen", id: 3 });
out.afterClear = history.visible().map((stroke) => stroke.id);
history.undo();
history.undo();
out.clearUndone = history.visible().map((stroke) => stroke.id);
history.add({ tool: "pen", id: 4 });
out.noRedo = history.canRedo();
for (let id = 5; id < 12; id += 1) {
  history.add({ tool: "pen", id });
}
out.limited = history.visible().map((stroke) => stroke.id);
console.log(JSON.stringify(out));
"""


def test_whiteboard_strokes_and_undo_are_plain_functions(tmp_path):
    out = run_node(tmp_path, STROKES_SCRIPT)
    assert out["points"] == [{"x": 0, "y": 0}, {"x": 800, "y": 500}, {"x": 1600, "y": 1000}, {"x": 0, "y": 1000}]
    assert out["pen"] == [0, 2, 10]  # points that change nothing are not kept
    assert out["eraser"] == 18
    assert out["keep"] == [False, True, True, False, True]  # a click with a shape tool draws nothing; with the pen, a dot
    assert out["box"] == {"x": 20, "y": 80, "width": 30, "height": 20}
    assert out["head"] == [2, True, True, True, 14]
    assert out["font"] == [20, 62]
    assert out["fresh"] == [False, False, True]
    assert out["undone"] == [[1], True]
    assert out["cleared"] == [0, True, True] and out["afterClear"] == [3]
    assert out["clearUndone"] == [1, 2]  # wiping the board is a step like any other
    assert out["noRedo"] is False
    assert out["limited"] == [7, 8, 9, 10, 11]


PAINT_SCRIPT = """
import { paintAll, paintStroke } from "./static/paint.js";

// A context that writes down what is done to it.
function recorder() {
  const log = [];
  const state = {};
  const context = new Proxy({}, {
    get: (_target, name) => (name in state ? state[name] : (...args) => { log.push([name, ...args]); }),
    set: (_target, name, value) => { state[name] = value; log.push(["set " + name, value]); return true; },
  });
  return { context, log, names: () => log.map((entry) => entry[0]) };
}
const out = {};
const point = (x, y) => ({ x, y });

let paper = recorder();
paintStroke(paper.context, { tool: "eraser", color: "#000000", width: 18, points: [point(0, 0), point(5, 5), point(9, 9)] });
out.eraser = paper.log.filter((entry) => entry[0] === "set globalCompositeOperation").map((entry) => entry[1]);
out.restored = [paper.names()[0], paper.names()[paper.names().length - 1]];
out.curve = paper.names().filter((name) => name === "quadraticCurveTo").length;

paper = recorder();
paintStroke(paper.context, { tool: "pen", color: "#b3261e", width: 6, points: [point(3, 4)] });
out.dot = [paper.names().includes("stroke"), paper.log.find((entry) => entry[0] === "set lineCap")[1],
  paper.log.find((entry) => entry[0] === "set strokeStyle")[1], paper.log.find((entry) => entry[0] === "set lineWidth")[1]];
out.penKeeps = paper.names().includes("set globalCompositeOperation");

paper = recorder();
paintStroke(paper.context, { tool: "arrow", color: "#000000", width: 3, from: point(0, 0), to: point(100, 0) });
out.arrow = [paper.names().filter((name) => name === "moveTo").length, paper.names().filter((name) => name === "lineTo").length];

paper = recorder();
paintStroke(paper.context, { tool: "rectangle", color: "#000000", width: 3, from: point(50, 80), to: point(20, 100) });
out.rectangle = paper.log.find((entry) => entry[0] === "strokeRect");
paper = recorder();
paintStroke(paper.context, { tool: "ellipse", color: "#000000", width: 3, from: point(0, 0), to: point(40, 20) });
out.ellipse = paper.log.find((entry) => entry[0] === "ellipse").slice(1, 5);
paper = recorder();
paintStroke(paper.context, { tool: "text", color: "#2f5fd0", width: 3, at: point(7, 9), text: "<b>hi</b>" });
out.text = [paper.log.find((entry) => entry[0] === "fillText"), paper.log.find((entry) => entry[0] === "set font")[1].startsWith("20px ")];
paper = recorder();
paintStroke(paper.context, { tool: "picture", image: "the image" });
out.picture = paper.log.find((entry) => entry[0] === "drawImage");

paper = recorder();
paintAll(paper.context, [{ tool: "line", color: "#000000", width: 3, from: point(0, 0), to: point(1, 1) }]);
out.all = [paper.log[0], paper.names().filter((name) => name === "stroke").length];
console.log(JSON.stringify(out));
"""


def test_whiteboard_paints_strokes_on_any_canvas(tmp_path):
    out = run_node(tmp_path, PAINT_SCRIPT)
    assert out["eraser"] == ["destination-out"]  # it removes; it does not paint white
    assert out["restored"] == ["save", "restore"] and out["curve"] == 1
    assert out["dot"] == [True, "round", "#b3261e", 6] and out["penKeeps"] is False
    assert out["arrow"] == [2, 3]  # the shaft, then the two sides of the head
    assert out["rectangle"] == ["strokeRect", 20, 80, 30, 20]
    assert out["ellipse"] == [20, 10, 20, 10]
    assert out["text"] == [["fillText", "<b>hi</b>", 7, 9], True]  # text is painted as text
    assert out["picture"] == ["drawImage", "the image", 0, 0, 1600, 1000]
    assert out["all"] == [["clearRect", 0, 0, 1600, 1000], 1]
