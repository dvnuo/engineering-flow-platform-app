"""The ``screenshot-diff`` example: two images compared in the browser, with no backend.

The comparison is ``public/lib/diff.js``: RGBA bytes in, a mask, counts,
regions and a picture out, no DOM. It is run here with node on images made in
the test, and checked against a plain Python loop over the same bytes. The
page around it is read as text: it must stay inside its directory and must
not be able to send the images anywhere.
"""

import json
import re
import shutil
import subprocess

import pytest

from conftest import EXAMPLES

APP = EXAMPLES / "screenshot-diff"
PUBLIC = APP / "public"

pytestmark = pytest.mark.skipif(not APP.is_dir(), reason="needs a checkout of the repository (examples/)")

#: What every script starts with: the two modules, and a few lines to make test images.
PRELUDE = r"""
import * as diff from "./public/lib/diff.js";
import * as view from "./public/lib/view.js";
import { readFileSync } from "node:fs";
const data = JSON.parse(readFileSync(new URL("./data.json", import.meta.url), "utf-8"));
const WHITE = [255, 255, 255, 255];
const BLACK = [0, 0, 0, 255];
const image = (width, height, fill = WHITE) => {
  const pixels = new Uint8ClampedArray(width * height * 4);
  for (let at = 0; at < pixels.length; at += 4) pixels.set(fill, at);
  return { width, height, data: pixels };
};
const set = (picture, x, y, colour) => picture.data.set(colour, (y * picture.width + x) * 4);
const rect = (picture, left, top, width, height, colour) => {
  for (let y = top; y < top + height; y += 1) for (let x = left; x < left + width; x += 1) set(picture, x, y, colour);
};
const maskAt = (state, x, y) => state.mask[y * state.width + x];
const painted = (state, x, y) => Array.from(state.picture.slice((y * state.width + x) * 4, (y * state.width + x) * 4 + 4));
"""


def run_js(tmp_path, body, data=None):
    """Run JavaScript against the example's modules with node and return what ``body`` returns.

    ``body`` is the inside of an async function; it sees ``diff``, ``view``,
    the helpers of ``PRELUDE`` and ``data``. The modules are written for the
    browser and have no package.json, so node would take them for CommonJS:
    they are copied next to one that says "module".
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("needs node on the PATH")
    work = tmp_path / "js"
    if not work.exists():
        shutil.copytree(PUBLIC, work / "public")
        (work / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    (work / "data.json").write_text(json.dumps(data), encoding="utf-8")
    (work / "run.mjs").write_text(
        PRELUDE + "const answer = await (async () => {\n%s\n})();\nprocess.stdout.write(JSON.stringify(answer === undefined ? null : answer));\n" % body,
        encoding="utf-8",
    )
    done = subprocess.run([node, "run.mjs"], capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(work))
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# --- The app as files ------------------------------------------------------------------------------------


def test_screenshot_diff_is_a_page_that_cannot_upload_anything():
    assert not list(APP.rglob("*.py")) and not (APP / "requirements.txt").exists() and not (APP / ".env.example").exists()
    manifest = json.loads((APP / "efp-app.json").read_text(encoding="utf-8"))
    assert manifest["type"] == "static" and manifest["entry"] == "public"
    page = (PUBLIC / "index.html").read_text(encoding="utf-8")
    assert 'href="style.css"' in page and '<script type="module" src="app.js">' in page
    assert "Nothing is uploaded" in page and "no backend" in page
    assert "Nothing is uploaded" in (APP / "README.md").read_text(encoding="utf-8")
    # A file input outside a form, read by script: there is nothing to submit and nowhere to submit it to.
    assert "<form" not in page and page.count('type="file"') == 2 and " name=" not in "".join(re.findall(r'<input[^>]*type="file"[^>]*>', page))
    scripts = sorted(PUBLIC.rglob("*.js"))
    for script in scripts:
        text = script.read_text(encoding="utf-8")
        for call in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "EventSource", "FormData", "importScripts"):
            assert call not in text, "%s could send the images somewhere (%s)" % (script.name, call)
        for address in re.findall(r'^import [^"]*"([^"]+)";$', text, flags=re.MULTILINE):
            target = (script.parent / address).resolve()  # relative, so it holds under /{slug}/
            assert address.startswith("./") and target.is_file() and PUBLIC.resolve() in target.parents, (script.name, address)

    app = (PUBLIC / "app.js").read_text(encoding="utf-8")
    ids = set(re.findall(r'\bid="([^"]+)"', page))
    for wanted in re.findall(r'byId\("([^"]+)"\)', app):
        assert wanted in ids, wanted  # every element the script asks for is on the page
    for slot in ("expected", "actual"):
        assert {"drop-" + slot, "file-" + slot, "info-" + slot} <= ids
    assert re.findall(r'name="view" value="([a-z]+)"', page) == ["side", "swipe", "overlay", "diff"]
    # In slices with a frame in between, and the result as a PNG made in the tab.
    assert "requestAnimationFrame" in app and "step(state" in app and 'toBlob(' in app and '"image/png"' in app


# --- The comparison --------------------------------------------------------------------------------------


def changed_plainly(one, two, tolerance):
    """Which pixels differ, by the rule of the README: any channel off by more than the tolerance."""
    marks = []
    for at in range(0, len(one), 4):
        visible = one[at + 3] != 0 or two[at + 3] != 0
        marks.append(1 if visible and any(abs(one[at + channel] - two[at + channel]) > tolerance for channel in range(4)) else 0)
    return marks


def test_changed_pixels_agree_with_a_plain_python_loop(tmp_path):
    width, height, seed = 37, 23, 20261120
    expected, actual = [], []
    for _ in range(width * height):
        pixel, other = [], []
        for channel in range(4):
            seed = (seed * 1103515245 + 12345) % 2**31
            value = (seed >> 8) % 256 if channel < 3 else (0 if seed % 9 == 0 else 255)
            # Mostly equal, sometimes off by exactly the amounts around the tolerances that are tried.
            delta = (0, 0, 0, 0, 1, 4, 5, 16, 17, 60)[(seed >> 4) % 10]
            pixel.append(value)
            other.append(max(0, min(255, value + (delta if (seed >> 3) % 2 else -delta))))
        expected += pixel
        actual += other
    tolerances = [0, 1, 4, 5, 16, 17, 59, 60, 255]
    found = run_js(
        tmp_path,
        r"""
        const one = { width: data.width, height: data.height, data: Uint8ClampedArray.from(data.expected) };
        const two = { width: data.width, height: data.height, data: Uint8ClampedArray.from(data.actual) };
        return data.tolerances.map((tolerance) => {
          const state = diff.compare(one, two, { tolerance });
          return { mask: Array.from(state.mask), summary: diff.summarize(state) };
        });
        """,
        data={"width": width, "height": height, "expected": expected, "actual": actual, "tolerances": tolerances},
    )
    counts = []
    for tolerance, result in zip(tolerances, found):
        marks = changed_plainly(expected, actual, tolerance)
        assert result["mask"] == marks, tolerance
        summary = result["summary"]
        assert summary["changed"] == sum(marks) and summary["compared"] == width * height and summary["sameSize"] is True
        assert summary["percent"] == pytest.approx(100 * sum(marks) / (width * height))
        assert summary["identical"] is (sum(marks) == 0)
        counts.append(sum(marks))
    assert counts == sorted(counts, reverse=True) and counts[0] > 300 and counts[-1] == 0  # a higher tolerance never finds more


def test_tolerance_is_per_channel_and_what_cannot_be_seen_is_equal(tmp_path):
    found = run_js(
        tmp_path,
        r"""
        const changedAt = (colourA, colourB, tolerance) => {
          const one = image(3, 3, colourA);
          const two = image(3, 3, colourA);
          set(two, 1, 1, colourB);
          return diff.compare(one, two, { tolerance }).changed;
        };
        const same = image(40, 30);
        return {
          identical: diff.summarize(diff.compare(same, image(40, 30))),
          red: [9, 10, 11].map((tolerance) => changedAt([100, 100, 100, 255], [110, 100, 100, 255], tolerance)),
          blue: [9, 10].map((tolerance) => changedAt([100, 100, 100, 255], [100, 100, 90, 255], tolerance)),
          alpha: [9, 10].map((tolerance) => changedAt([100, 100, 100, 255], [100, 100, 100, 245], tolerance)),
          spread: changedAt([100, 100, 100, 255], [108, 92, 108, 255], 8),  // three channels off by 8 each: still within 8 per channel
          hidden: changedAt([0, 0, 0, 0], [255, 0, 0, 0], 0),
          appearing: changedAt([255, 0, 0, 0], [255, 0, 0, 1], 0),
        };
        """,
    )
    assert found["identical"] == {
        "width": 40,
        "height": 30,
        "compared": 1200,
        "changed": 0,
        "antialiased": 0,
        "onlyOne": 0,
        "percent": 0,
        "sameSize": True,
        "identical": True,
    }
    assert found["red"] == [1, 0, 0] and found["blue"] == [1, 0] and found["alpha"] == [1, 0]  # at the tolerance is within it
    assert found["spread"] == 0
    assert found["hidden"] == 0  # two fully transparent pixels look the same, whatever colour they carry
    assert found["appearing"] == 1


def test_images_of_different_sizes_are_compared_where_they_overlap(tmp_path):
    found = run_js(
        tmp_path,
        r"""
        const expected = image(50, 40);  // taller
        const actual = image(60, 30);    // wider
        set(actual, 2, 2, BLACK);
        const state = diff.compare(expected, actual);
        const reversed = diff.compare(actual, expected);
        const counts = [0, 0, 0, 0, 0];
        state.mask.forEach((value) => { counts[value] += 1; });
        return {
          summary: diff.summarize(state), counts,
          kinds: [diff.SAME, diff.CHANGED, diff.ANTIALIASED, diff.ONLY_ONE, diff.NEITHER],
          marks: { changed: maskAt(state, 2, 2), same: maskAt(state, 10, 10), right: maskAt(state, 55, 5), below: maskAt(state, 10, 35), corner: maskAt(state, 55, 35) },
          colours: { changed: painted(state, 2, 2), same: painted(state, 10, 10), dark: painted(reversed, 2, 2), right: [painted(state, 55, 5), painted(state, 59, 5)], below: painted(state, 10, 35), corner: painted(state, 55, 35) },
          reversed: diff.summarize(reversed),
          regions: diff.regions(state),
        };
        """,
    )
    same, changed, _blend, only_one, neither = found["kinds"]
    summary = found["summary"]
    assert (summary["width"], summary["height"]) == (60, 40)  # the picture is as large as both together
    assert summary["compared"] == 50 * 30 and summary["changed"] == 1 and summary["sameSize"] is False and summary["identical"] is False
    assert summary["onlyOne"] == 10 * 30 + 50 * 10  # right of the narrow image, below the short one
    assert summary["percent"] == pytest.approx(100 / 1500)  # of what could be compared
    assert found["counts"] == [1499, 1, 0, 800, 100]
    assert found["marks"] == {"changed": changed, "same": same, "right": only_one, "below": only_one, "corner": neither}

    colours = found["colours"]
    assert colours["changed"][3] == 255 and colours["changed"][0] > 200 and colours["changed"][1] < 50  # a strong colour
    red, green, blue, alpha = colours["same"]
    assert red == green == blue and alpha == 255  # the unchanged screen as a pale grey
    assert colours["dark"][0] > 200 and colours["dark"][1] < 50  # the same pixel is changed whichever way round the images go
    assert colours["right"][0] != colours["right"][1] and all(pixel[2] > pixel[0] for pixel in colours["right"])  # striped
    assert colours["below"][3] == 255 and colours["corner"] == [0, 0, 0, 0]  # where neither image is, nothing is painted
    assert found["reversed"]["onlyOne"] == 800 and found["reversed"]["changed"] == 1
    assert found["regions"] == [{"x": 2, "y": 2, "width": 1, "height": 1, "pixels": 1}]  # the striped part is not a "change"


def test_anti_aliasing_is_told_from_a_real_change(tmp_path):
    found = run_js(
        tmp_path,
        r"""
        // A straight edge whose blend column got a little darker, as a renderer on another machine draws it.
        const edge = (blend) => { const picture = image(40, 20); rect(picture, 0, 0, 20, 20, BLACK); rect(picture, 20, 0, 1, 20, [blend, blend, blend, 255]); return picture; };
        // A slanted edge drawn at two positions a fraction of a pixel apart.
        const slanted = (offset) => {
          const picture = image(60, 60);
          for (let y = 0; y < 60; y += 1) for (let x = 0; x < 60; x += 1) {
            const cover = Math.max(0, Math.min(1, 20 + y * 0.3 + offset - x));
            const value = Math.round(255 * (1 - cover));
            set(picture, x, y, [value, value, value, 255]);
          }
          return picture;
        };
        const block = image(40, 40); rect(block, 10, 10, 12, 12, [200, 30, 30, 255]);
        const line = (x) => { const picture = image(40, 20); rect(picture, x, 0, 1, 20, BLACK); return picture; };
        const both = (one, two) => [false, true].map((ignoreAntialiasing) => { const state = diff.compare(one, two, { ignoreAntialiasing }); return [state.changed, state.antialiased, diff.regions(state).length]; });
        return {
          edge: both(edge(128), edge(90)),
          slanted: both(slanted(0), slanted(0.4)),
          block: both(image(40, 40), block),
          line: both(line(10), line(13)),
        };
        """,
    )
    assert found["edge"] == [[20, 0, 1], [0, 20, 0]]  # counted, or set aside and then no region at all
    off, on = found["slanted"]
    assert off[0] > 50 and off[1] == 0 and on == [0, off[0], 0]
    assert found["block"] == [[144, 0, 1], [144, 0, 1]]  # a button that appeared is not excused
    assert found["line"] == [[40, 0, 1], [40, 0, 1]]  # and neither is a line that moved


def test_changes_are_grouped_into_regions(tmp_path):
    found = run_js(
        tmp_path,
        r"""
        const base = image(200, 120);
        const changed = image(200, 120);
        rect(changed, 10, 10, 20, 5, BLACK);    // a word ...
        rect(changed, 36, 12, 4, 4, BLACK);     // ... and its last letter, 6 pixels on
        rect(changed, 150, 70, 10, 10, BLACK);  // something else, far away
        set(changed, 199, 119, BLACK);          // a single pixel in the corner
        const state = diff.compare(base, changed);
        // Two single pixels ``gap`` apart, at every position relative to the grid.
        const pair = (gap) => {
          const counts = new Set();
          for (let x = 0; x < 2 * diff.CELL; x += 1) {
            const picture = image(120, 40);
            set(picture, 20 + x, 20, BLACK);
            set(picture, 20 + x + gap, 20, BLACK);
            counts.add(diff.regions(diff.compare(image(120, 40), picture)).length);
          }
          return Array.from(counts).sort();
        };
        const diagonal = image(60, 60); set(diagonal, 11, 11, BLACK); set(diagonal, 12, 12, BLACK);
        return {
          cell: diff.CELL, regions: diff.regions(state), changed: state.changed,
          near: [1, 5, diff.CELL].map(pair), far: [2 * diff.CELL, 40].map(pair),
          diagonal: diff.regions(diff.compare(image(60, 60), diagonal)),
          none: diff.regions(diff.compare(base, image(200, 120))),
        };
        """,
    )
    assert found["regions"] == [
        {"x": 10, "y": 10, "width": 30, "height": 6, "pixels": 116},  # the word and its letter, one box
        {"x": 150, "y": 70, "width": 10, "height": 10, "pixels": 100},
        {"x": 199, "y": 119, "width": 1, "height": 1, "pixels": 1},
    ]  # largest first, each box tight around its pixels
    assert sum(region["pixels"] for region in found["regions"]) == found["changed"] == 217
    assert found["near"] == [[1], [1], [1]]  # up to CELL apart: always one region, wherever the grid falls
    assert found["far"] == [[2], [2]]  # from twice CELL: never
    assert found["diagonal"] == [{"x": 11, "y": 11, "width": 2, "height": 2, "pixels": 2}]  # across a corner of the grid
    assert found["none"] == []


def test_the_work_is_done_in_slices_that_add_up_to_the_whole(tmp_path):
    found = run_js(
        tmp_path,
        r"""
        const one = image(300, 211, [240, 240, 240, 255]);
        const two = image(310, 205, [240, 240, 240, 255]);
        for (let at = 0; at < 2000; at += 1) set(two, (at * 37) % 300, (at * 53) % 205, [(at * 7) % 256, 0, 200, 255]);
        const options = { tolerance: 10, ignoreAntialiasing: true };
        const whole = diff.compare(one, two, options);
        const fresh = diff.createComparison(one, two, options);
        const untouched = [fresh.row, fresh.changed, fresh.mask.every((value) => value === 0)];
        const first = [diff.step(fresh, 16), fresh.row];
        const sliced = [1, 7, 16, 64, 1000].map((rows) => {
          const state = diff.createComparison(one, two, options);
          let steps = 1;
          let largest = 0;
          for (let before = 0; !diff.step(state, rows); before = state.row) { steps += 1; largest = Math.max(largest, state.row - before); }
          return {
            rows, steps, largest, row: state.row,
            same: state.changed === whole.changed && state.antialiased === whole.antialiased && state.onlyOne === whole.onlyOne &&
              Buffer.from(state.mask).equals(Buffer.from(whole.mask)) && Buffer.from(state.picture.buffer).equals(Buffer.from(whole.picture.buffer)) &&
              JSON.stringify(diff.regions(state)) === JSON.stringify(diff.regions(whole)),
          };
        });
        return { untouched, first, sliced, changed: whole.changed, blends: whole.antialiased, height: whole.height, again: diff.step(whole, 16) };
        """,
    )
    assert found["untouched"] == [0, 0, True]  # starting a comparison does none of the work
    assert found["first"] == [False, 16]  # sixteen rows and back to the page
    assert found["height"] == 211 and found["changed"] > 500 and found["blends"] > 500  # a pair with plenty of both kinds
    for sliced in found["sliced"]:
        assert sliced["same"] is True, sliced["rows"]  # the same mask, picture, counts and regions as in one go
        assert sliced["steps"] == -(-211 // sliced["rows"]) and sliced["largest"] <= sliced["rows"] and sliced["row"] == 211
    assert found["again"] is True  # a finished comparison stays finished


def test_limits_are_sentences_and_the_zoom_stays_in_range(tmp_path):
    found = run_js(
        tmp_path,
        r"""
        const up = [0.03];
        while (up.length < 40 && view.stepZoom(up[up.length - 1], 1) !== up[up.length - 1]) up.push(view.stepZoom(up[up.length - 1], 1));
        const down = [16];
        while (down.length < 40 && view.stepZoom(down[down.length - 1], -1) !== down[down.length - 1]) down.push(view.stepZoom(down[down.length - 1], -1));
        return {
          max: diff.MAX_PIXELS,
          sizes: [[1, 1], [5000, 5000], [5001, 5000], [100000, 300], [0, 10], [10, 0]].map(([width, height]) => diff.sizeProblem(width, height)),
          percent: [0, 0.0001, 0.004, 0.4234, 9.999, 12.345, 99.99, 100].map(diff.percentText),
          fit: [[1000, 500, 500, 500], [500, 1000, 500, 500], [100, 100, 500, 500], [100000, 10, 500, 500], [0, 0, 500, 500], [300, 300, 0, 0]].map((numbers) => view.fitZoom(...numbers)),
          up, down, steps: view.ZOOM_STEPS,
          between: [view.stepZoom(0.37, 1), view.stepZoom(0.37, -1), view.stepZoom(1, 1), view.stepZoom(1, -1)],
          text: [view.zoomText(0.375), view.zoomText(1), view.zoomText(16)],
          clamp: [view.clamp(-5, 0, 100), view.clamp(50, 0, 100), view.clamp(500, 0, 100)],
        };
        """,
    )
    assert found["max"] == 25000000
    fine, at_the_limit, too_large, too_wide, empty, flat = found["sizes"]
    assert fine == "" and at_the_limit == ""
    assert too_large and too_wide and empty and flat and "25" in too_large  # refused in words, before any memory is taken
    assert found["percent"][0] == "0%" and found["percent"][-1] == "100%"
    assert all(text != "0%" for text in found["percent"][1:]) and found["percent"][3] == "0.42%"  # a difference is never rounded away
    assert all(text != "100%" and not text.startswith("100") for text in found["percent"][:-1])  # nor rounded up to "everything"
    assert found["fit"][:3] == [0.5, 0.5, 1]  # fitted, and never blown up past 100%
    assert 0 < found["fit"][3] < 0.1 and found["fit"][4] == 1 and found["fit"][5] == 1
    steps = found["steps"]
    assert found["up"][1:] == steps and found["down"] == steps[::-1]  # every step once, then it stops at the end
    assert steps == sorted(steps) and 1 in steps
    assert found["between"] == [0.5, 0.25, 1.5, 0.75]
    assert found["text"] == ["38%", "100%", "1600%"] and found["clamp"] == [0, 50, 100]
