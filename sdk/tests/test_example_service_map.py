"""The ``service-map`` example: a dependency map drawn from a JSON file, with no backend.

What can be wrong in such a page is in ``public/lib/``, free of the DOM, and
is run here with node: the check of the data file (``graph.js``, validate),
reachability and cycles (compared with a plain search written in Python),
the forces of the layout (``layout.js``) and the pan and zoom arithmetic
(``view.js``). The sample data and the page are read as files.
"""

import json
import re
import shutil
import subprocess

import pytest

from conftest import EXAMPLES

APP = EXAMPLES / "service-map"
PUBLIC = APP / "public"

pytestmark = pytest.mark.skipif(not APP.is_dir(), reason="needs a checkout of the repository (examples/)")

PRELUDE = r"""
import * as graph from "./public/lib/graph.js";
import * as layout from "./public/lib/layout.js";
import * as view from "./public/lib/view.js";
import { readFileSync } from "node:fs";
const data = JSON.parse(readFileSync(new URL("./data.json", import.meta.url), "utf-8"));
const sample = JSON.parse(readFileSync(new URL("./public/services.json", import.meta.url), "utf-8"));
// {a: ["b"]} as the services of a data file.
const services = (edges) => Object.entries(edges).map(([name, depends_on]) => ({ name, team: "T", tier: 1, depends_on }));
const indexOf = (edges) => graph.buildIndex(graph.validate({ services: services(edges) }).services);
// The layout of a list of services, as the page builds it.
const layoutOf = (list, options = {}) => {
  const index = graph.buildIndex(list);
  const number = new Map(list.map((service, position) => [service.name, position]));
  const level = graph.levels(index);
  const edges = list.flatMap((service) => service.depends_on.map((target) => [number.get(service.name), number.get(target)]));
  return layout.createLayout(list.length, edges, { levels: list.map((service) => level.get(service.name)), ...options });
};
const places = (made) => made.nodes.map((node) => [node.x, node.y]);
"""


def run_js(tmp_path, body, data=None):
    """Run JavaScript against the example's modules with node and return what ``body`` returns.

    ``body`` is the inside of an async function; it sees ``graph``,
    ``layout``, ``view``, the sample data as ``sample``, the helpers of
    ``PRELUDE`` and ``data``. The modules are written for the browser and
    have no package.json, so node would take them for CommonJS: they are
    copied next to one that says "module".
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


def sample_services():
    return json.loads((PUBLIC / "services.json").read_text(encoding="utf-8"))["services"]


def reach_plainly(start, edges):
    """(direct, transitive) from ``start`` along ``edges``, by a plain search."""
    direct = sorted(set(edges[start]))
    seen, todo = {start, *direct}, list(direct)
    while todo:
        for following in edges[todo.pop()]:
            if following not in seen:
                seen.add(following)
                todo.append(following)
    return direct, sorted(seen - {start} - set(direct))


def cycles_plainly(edges):
    """Every elementary cycle once, as a tuple that starts at its alphabetically first member."""
    found = set()

    def walk(start, path):
        for following in edges[path[-1]]:
            if following == start:
                found.add(tuple(path))
            elif following > start and following not in path:
                walk(start, path + [following])

    for name in edges:
        walk(name, [name])
    return found


# --- The app as files ------------------------------------------------------------------------------------


def test_service_map_is_a_page_and_a_data_file():
    assert not list(APP.rglob("*.py")) and not (APP / "requirements.txt").exists() and not (APP / ".env.example").exists()
    manifest = json.loads((APP / "efp-app.json").read_text(encoding="utf-8"))
    assert manifest["type"] == "static" and manifest["entry"] == "public"
    page = (PUBLIC / "index.html").read_text(encoding="utf-8")
    assert 'href="style.css"' in page and '<script type="module" src="app.js">' in page

    app = (PUBLIC / "app.js").read_text(encoding="utf-8")
    scripts = sorted(PUBLIC.rglob("*.js"))
    together = "".join(script.read_text(encoding="utf-8") for script in scripts)
    assert 'fetch("services.json")' in app and together.count("fetch(") == 1  # the one request: the data file, by a relative address
    for call in ("XMLHttpRequest", "WebSocket", "sendBeacon", "EventSource"):
        assert call not in together
    for script in scripts:
        for address in re.findall(r'^import [^"]*"([^"]+)";$', script.read_text(encoding="utf-8"), flags=re.MULTILINE):
            target = (script.parent / address).resolve()
            assert address.startswith("./") and target.is_file() and PUBLIC.resolve() in target.parents, (script.name, address)

    ids = set(re.findall(r'\bid="([^"]+)"', page))
    wanted = set(re.findall(r'byId\("([^"]+)"\)', app))
    assert wanted <= ids and {"map", "viewport", "edges", "nodes", "panel", "search", "team", "tier"} <= wanted
    assert {"cycle-list", "cycles", "problem-list", "problems"} <= ids and '"cycle-list"' in app and '"problem-list"' in app
    # A link from the data file opens only as a web address, and without handing the new page a way back.
    assert 'parsed.protocol === "https:" || parsed.protocol === "http:"' in app and 'rel = "noopener noreferrer"' in app
    # The simulation is stepped per frame and can end; with reduced motion it is laid out once.
    assert "requestAnimationFrame(frame)" in app and "settle(layout)" in app
    assert "(prefers-reduced-motion: reduce)" in app and "prefers-reduced-motion" in (PUBLIC / "style.css").read_text(encoding="utf-8")


def test_the_sample_data_is_a_believable_landscape_with_one_small_cycle(tmp_path):
    listed = sample_services()
    names = [service["name"] for service in listed]
    assert 15 <= len(listed) <= 25 and len(set(names)) == len(names)
    for service in listed:
        assert set(service) == {"name", "team", "tier", "description", "links", "depends_on"}
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", service["name"]) and service["team"] and service["tier"] in (1, 2, 3)
        assert len(service["description"]) > 20 and service["links"]
        for link in service["links"]:
            assert set(link) == {"title", "url"} and re.match(r"https://[a-z]+\.example\.com/", link["url"])  # invented, on a reserved domain
        assert set(service["depends_on"]) <= set(names) - {service["name"]} and len(set(service["depends_on"])) == len(service["depends_on"])
    assert len({service["team"] for service in listed}) >= 4 and {service["tier"] for service in listed} == {1, 2, 3}
    edges = {service["name"]: service["depends_on"] for service in listed}
    assert cycles_plainly(edges) == {("pricing", "promotions")}

    found = run_js(
        tmp_path,
        r"""
        const checked = graph.validate(sample);
        const index = graph.buildIndex(checked.services);
        const searched = graph.findCycles(index);
        return { problems: checked.problems, names: checked.services.map((service) => service.name), searched, keys: Array.from(graph.cycleEdges(searched.cycles)).sort() };
        """,
    )
    assert found["problems"] == [] and found["names"] == names  # the page has nothing to complain about
    assert found["searched"] == {"cycles": [["pricing", "promotions"]], "more": False}
    assert found["keys"] == ["pricing>promotions", "promotions>pricing"]


# --- The data file, checked ------------------------------------------------------------------------------


def test_a_broken_data_file_is_drawn_as_far_as_it_is_sound_and_the_rest_is_named(tmp_path):
    broken = {
        "services": [
            {"name": "ledger", "team": "Payments", "tier": 1, "depends_on": ["postgres", "ledgerr", "ledger", "postgres", 7]},
            {"name": "postgres", "team": "Platform", "tier": 9, "links": [{"title": "Runbook"}, {"title": "Docs", "url": "https://wiki.example.com/pg"}, "x"]},
            {"name": "Check Out", "team": "Payments", "tier": 1},
            {"name": "ledger", "team": "Other", "tier": 2},
            {"team": "Nameless", "tier": 1},
            "not an object",
            {"name": "audit", "tier": 2, "description": 5, "depends_on": "ledger"},
        ]
    }
    found = run_js(
        tmp_path,
        r"""
        const many = { services: Array.from({ length: graph.MAX_SERVICES + 5 }, (_, position) => ({ name: "s" + position, team: "T", tier: 1 })) };
        return {
          broken: graph.validate(data),
          shapes: [null, [], "text", 7, {}, { services: {} }, { services: "x" }].map((file) => graph.validate(file)),
          empty: graph.validate({ services: [] }),
          many: [graph.validate(many).services.length, graph.validate(many).problems.length],
          guesses: [graph.closestName("ledgerr", ["ledger", "orders"]), graph.closestName("ordrs", ["ledger", "orders"]), graph.closestName("xyz", ["ledger", "orders"]), graph.closestName("a", ["b"])],
        };
        """,
        data=broken,
    )
    kept = found["broken"]["services"]
    assert kept == [
        {"name": "ledger", "team": "Payments", "tier": 1, "description": "", "links": [], "depends_on": ["postgres"]},
        {"name": "postgres", "team": "Platform", "tier": 3, "description": "", "links": [{"title": "Docs", "url": "https://wiki.example.com/pg"}], "depends_on": []},
        {"name": "audit", "team": "No team", "tier": 2, "description": "", "links": [], "depends_on": []},
    ]
    problems = found["broken"]["problems"]
    # One sentence per defect, each naming what it is about.
    for words in (
        ("ledger", "ledgerr", "did you mean"),  # the typo, with the name it probably meant
        ("ledger", "itself"),
        ("ledger", "postgres", "twice"),
        ("ledger", "7"),
        ("postgres", "9", "tier"),
        ("postgres", "link"),
        ("Check Out",),
        ("ledger", "more than once"),
        ("Entry 5", "name"),
        ("Entry 6",),
        ("audit", "depends_on", "list"),
    ):
        assert [problem for problem in problems if all(word in problem for word in words)], words
    assert len(problems) == 12 and all(problem.endswith(".") for problem in problems)

    for shape in found["shapes"]:
        assert shape["services"] == [] and len(shape["problems"]) == 1  # not a list of services: said once
    assert found["empty"] == {"services": [], "problems": []}
    assert found["many"] == [200, 1]
    assert found["guesses"] == ["ledger", "orders", "", ""]


# --- Reachability, cycles, levels ------------------------------------------------------------------------


def test_what_depends_on_what_agrees_with_a_plain_search(tmp_path):
    listed = sample_services()
    found = run_js(
        tmp_path,
        r"""
        const index = graph.buildIndex(graph.validate(sample).services);
        const around = Object.fromEntries(index.names.map((name) => [name, graph.neighbourhood(index, name)]));
        const loop = indexOf({ a: ["b"], b: ["c"], c: ["a", "d"], d: [] });
        return { around, loop: graph.neighbourhood(loop, "a"), alone: graph.neighbourhood(loop, "d"), missing: graph.neighbourhood(loop, "nobody") };
        """,
    )
    needs = {service["name"]: service["depends_on"] for service in listed}
    used_by = {name: [other for other in needs if name in needs[other]] for name in needs}
    for name in needs:
        direct, transitive = reach_plainly(name, needs)
        assert found["around"][name]["dependencies"] == {"direct": direct, "transitive": transitive}, name
        direct, transitive = reach_plainly(name, used_by)
        assert found["around"][name]["dependents"] == {"direct": direct, "transitive": transitive}, name
    checkout = found["around"]["checkout"]
    assert checkout["dependencies"]["direct"] == ["cart", "identity", "orders", "payments", "pricing"] and "postgres-main" in checkout["dependencies"]["transitive"]
    assert checkout["dependents"] == {"direct": ["api-gateway"], "transitive": ["mobile-bff", "web-shop"]}
    assert len(found["around"]["postgres-main"]["dependents"]["transitive"]) > 10  # what stands on the database
    # In a cycle a service reaches itself, and is still not listed as its own dependency.
    assert found["loop"] == {"dependencies": {"direct": ["b"], "transitive": ["c", "d"]}, "dependents": {"direct": ["c"], "transitive": ["b"]}}
    assert found["alone"]["dependencies"] == {"direct": [], "transitive": []} and found["alone"]["dependents"] == {"direct": ["c"], "transitive": ["a", "b"]}
    assert found["missing"] == {"dependencies": {"direct": [], "transitive": []}, "dependents": {"direct": [], "transitive": []}}


def test_cycles_are_found_once_each_and_the_search_gives_up_in_time(tmp_path):
    graphs = {
        "none": {"a": ["b", "c"], "b": ["c"], "c": []},
        "triangle": {"web": ["api"], "api": ["db", "queue"], "queue": ["worker"], "worker": ["api"], "db": []},
        "eight": {"m": ["a", "x"], "a": ["b"], "b": ["m"], "x": ["y"], "y": ["m"]},
        "nested": {"a": ["b"], "b": ["a", "c"], "c": ["d"], "d": ["a", "c"]},
        "complete": {name: [other for other in "abcd" if other != name] for name in "abcd"},
        "two": {"a": ["b"], "b": ["a"], "c": ["d"], "d": ["c"], "e": ["a", "c"]},
    }
    found = run_js(
        tmp_path,
        r"""
        const answers = {};
        for (const [name, edges] of Object.entries(data)) answers[name] = graph.findCycles(indexOf(edges), 100);
        // 60 services that all depend on each other: more cycles than there are atoms.
        const tangle = {};
        for (let i = 0; i < 60; i += 1) tangle["s" + i] = Array.from({ length: 60 }, (_, j) => "s" + j).filter((name) => name !== "s" + i);
        const started = Date.now();
        const capped = graph.findCycles(indexOf(tangle));
        const few = graph.findCycles(indexOf(data.complete), 5);
        return { answers, capped: [capped.cycles.length, capped.more, Date.now() - started], few: [few.cycles.length, few.more],
                 keys: Array.from(graph.cycleEdges(answers.triangle.cycles)).sort() };
        """,
        data=graphs,
    )
    for name, edges in graphs.items():
        answer = found["answers"][name]
        cycles = [tuple(cycle) for cycle in answer["cycles"]]
        assert len(set(cycles)) == len(cycles) and set(cycles) == cycles_plainly(edges), name
        assert answer["more"] is False
        for cycle in cycles:
            assert cycle[0] == min(cycle)  # written from its first member, so it has one spelling
            assert all(following in edges[here] for here, following in zip(cycle, cycle[1:] + cycle[:1]))
    assert found["answers"]["none"]["cycles"] == [] and found["answers"]["triangle"]["cycles"] == [["api", "queue", "worker"]]
    assert len(found["answers"]["complete"]["cycles"]) == 20 and len(found["answers"]["two"]["cycles"]) == 2
    assert found["keys"] == ["api>queue", "queue>worker", "worker>api"]
    count, more, waited = found["capped"]
    assert count == 20 and more is True and waited < 5000  # a list of twenty, a note that there are more, and no hung page
    assert found["few"] == [5, True]


def test_levels_put_entry_points_on_top_and_a_cycle_on_one_line(tmp_path):
    listed = sample_services()
    found = run_js(
        tmp_path,
        r"""
        const index = graph.buildIndex(graph.validate(sample).services);
        const loop = indexOf({ web: ["a"], a: ["b"], b: ["c"], c: ["a", "db"], db: [] });
        return {
          levels: Object.fromEntries(graph.levels(index)), groups: Object.fromEntries(graph.components(index)),
          loop: Object.fromEntries(graph.levels(loop)), loopGroups: Object.fromEntries(graph.components(loop)),
          empty: Array.from(graph.levels(graph.buildIndex([]))),
        };
        """,
    )
    levels, groups = found["levels"], found["groups"]
    needs = {service["name"]: service["depends_on"] for service in listed}
    used = {target for targets in needs.values() for target in targets}
    assert {name for name, level in levels.items() if level == 0} == set(needs) - used  # what nothing depends on
    assert groups["pricing"] == groups["promotions"] and len(set(groups.values())) == len(needs) - 1
    for name, targets in needs.items():
        for target in targets:
            if groups[name] == groups[target]:
                assert levels[name] == levels[target]  # the two of the cycle, side by side
            else:
                assert levels[target] > levels[name], (name, target)  # every other arrow points down
    assert found["loop"] == {"web": 0, "a": 1, "b": 1, "c": 1, "db": 2}
    assert len({found["loopGroups"][name] for name in "abc"}) == 1 and found["empty"] == []


# --- The layout ------------------------------------------------------------------------------------------


def test_the_layout_settles_without_overlap_and_is_the_same_every_time(tmp_path):
    found = run_js(
        tmp_path,
        r"""
        const list = graph.validate(sample).services;
        const made = layoutOf(list);
        const start = places(made);
        const moving = layout.step(made);
        const afterOne = [places(made), made.alpha];
        let steps = 1;
        while (layout.step(made)) steps += 1;
        const still = [layout.step(made), JSON.stringify(places(made))];
        layout.step(made);
        const twin = layoutOf(list);
        const settledSteps = layout.settle(twin);
        const edges = made.edges.map(([from, to]) => [from, to, Math.hypot(made.nodes[from].x - made.nodes[to].x, made.nodes[from].y - made.nodes[to].y), made.nodes[to].y - made.nodes[from].y]);
        return {
          start, moving, afterOne, steps, settledSteps, still: [still[0], still[1] === JSON.stringify(places(made))],
          end: places(made), same: JSON.stringify(places(twin)) === JSON.stringify(places(made)), twinAlpha: twin.alpha,
          edges, room: made.room, min: layout.ALPHA_MIN, box: layout.bounds(made), partBox: layout.bounds(made, [0, 1]), emptyBox: layout.bounds(layoutOf([])),
        };
        """,
    )
    start, end = found["start"], found["end"]
    assert len(set(map(tuple, start))) == len(start) == 24  # nobody starts on top of somebody else
    assert found["moving"] is True and found["afterOne"][0] != start and found["afterOne"][1] < 1  # one step: things move, it cools
    assert 50 < found["steps"] < 600 and found["settledSteps"] == found["steps"]  # it ends; the page stops asking for frames
    assert found["still"] == [False, True]  # settled: another step says so and moves nothing
    assert found["same"] is True and found["twinAlpha"] == 0  # no randomness: one file, one picture
    assert all(abs(value) < 5000 for place in end for value in place)

    width, height = found["room"]["width"], found["room"]["height"]
    for one in range(len(end)):
        for two in range(one + 1, len(end)):
            apart = abs(end[one][0] - end[two][0]) >= width - 0.01 or abs(end[one][1] - end[two][1]) >= height - 0.01
            assert apart, (one, two)  # room for every circle and its label
    lengths = [edge[2] for edge in found["edges"]]
    everyone = [((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 for index, a in enumerate(end) for b in end[index + 1 :]]
    assert sum(lengths) / len(lengths) < sum(everyone) / len(everyone)  # what is connected ends up closer together
    assert sum(1 for edge in found["edges"] if edge[3] > 0) >= 0.85 * len(found["edges"])  # arrows mostly point down
    box = found["box"]
    assert all(box["left"] < x < box["right"] and box["top"] < y < box["bottom"] for x, y in end)
    assert found["partBox"]["right"] - found["partBox"]["left"] < box["right"] - box["left"]
    assert found["emptyBox"]["right"] > found["emptyBox"]["left"]  # nothing to show is still a box to fit


def test_the_layout_follows_the_levels_and_what_the_visitor_holds(tmp_path):
    found = run_js(
        tmp_path,
        r"""
        const chain = layoutOf(graph.validate({ services: services({ a: ["b"], b: ["c"], c: ["d"], d: [] }) }).services);
        layout.settle(chain);

        // Two services on one spot (as after a careless drag) come apart.
        const pile = layout.createLayout(2, []);
        pile.nodes[1].x = pile.nodes[0].x; pile.nodes[1].y = pile.nodes[0].y;
        layout.settle(pile);

        // A node the visitor holds stays where it was put, and the others make room.
        const held = layoutOf(graph.validate(sample).services);
        layout.settle(held);
        const before = places(held);
        held.nodes[3].fixed = true; held.nodes[3].x = before[5][0]; held.nodes[3].y = before[5][1];
        layout.reheat(held);
        const warm = held.alpha;
        const moved = layout.step(held);
        layout.settle(held);
        const after = places(held);

        const stronger = layoutOf(graph.validate(sample).services, { forces: { repulsion: 0.12 } });
        layout.settle(stronger);
        const spread = (made) => { const box = layout.bounds(made); return (box.right - box.left) * (box.bottom - box.top); };
        const plain = layout.createLayout(3, [[0, 1], [1, 2]]);
        return {
          chain: places(chain).map((place) => place[1]), pile: places(pile), pileRoom: pile.room,
          warm, moved, pinned: [after[3], before[5]], pushed: Math.hypot(after[5][0] - before[5][0], after[5][1] - before[5][1]),
          wider: spread(stronger) > spread(held), forces: [held.forces, stronger.forces.repulsion, layout.FORCES],
          plain: [layout.settle(plain) > 0, places(plain).flat().every(Number.isFinite), plain.heights],
        };
        """,
    )
    a, b, c, d = found["chain"]
    assert a < b < c < d and d - a > 200  # a chain of dependencies reads from top to bottom
    (x1, y1), (x2, y2) = found["pile"]
    assert abs(x1 - x2) >= found["pileRoom"]["width"] - 0.01 or abs(y1 - y2) >= found["pileRoom"]["height"] - 0.01
    assert found["warm"] >= 0.3 and found["moved"] is True  # moving a node wakes the simulation up again
    assert found["pinned"][0] == found["pinned"][1] and found["pushed"] > 30  # it stays; the one that was there gave way
    held_forces, stronger_repulsion, defaults = found["forces"]
    assert found["wider"] is True and held_forces == defaults and stronger_repulsion == 0.12  # a force can be turned up
    assert found["plain"] == [True, True, None]  # without levels: still a layout, just without a direction


# --- Pan, zoom, filters ----------------------------------------------------------------------------------


def test_pan_and_zoom_keep_what_the_visitor_points_at(tmp_path):
    found = run_js(
        tmp_path,
        r"""
        const start = { x: 40, y: -25, k: 0.8 };
        const under = view.toMap(start, 310, 220);
        const zoomed = [1.25, 0.5, 3, 100, 0.0001].map((factor) => { const next = view.zoomAt(start, factor, 310, 220); return [next.k, view.toMap(next, 310, 220)]; });
        const box = { left: -300, top: -100, right: 500, bottom: 700 };
        const fitted = view.fitView(box, 900, 600);
        const small = view.fitView({ left: 0, top: 0, right: 10, bottom: 10 }, 900, 600);
        const corner = (fit, x, y) => [fit.x + x * fit.k, fit.y + y * fit.k];
        // Two fingers 100 apart spread to 200 apart while their middle moves 30 to the right.
        const fingers = [[{ x: 300, y: 200 }, { x: 400, y: 200 }], [{ x: 280, y: 200 }, { x: 480, y: 200 }]];
        const pinched = view.pinch(start, fingers[0], fingers[1]);
        return {
          under, zoomed, limits: [view.MIN_ZOOM, view.MAX_ZOOM],
          fitted, centre: view.toMap(fitted, 450, 300), corners: [corner(fitted, box.left, box.top), corner(fitted, box.right, box.bottom)],
          small: [small.k, view.toMap(small, 450, 300)],
          panned: view.pan(start, 15, -5),
          pinched: [pinched.k, view.toMap(start, 350, 200), view.toMap(pinched, 380, 200)],
          still: view.pinch(start, fingers[0], fingers[0]),
        };
        """,
    )
    low, high = found["limits"]
    under = found["under"]
    for factor, (scale, point) in zip([1.25, 0.5, 3, 100, 0.0001], found["zoomed"]):
        assert scale == pytest.approx(min(high, max(low, 0.8 * factor)))  # within the limits
        assert point["x"] == pytest.approx(under["x"]) and point["y"] == pytest.approx(under["y"])  # the spot under the pointer stays
    fitted = found["fitted"]
    assert found["centre"] == pytest.approx({"x": 100, "y": 300})  # the middle of the box in the middle of the area
    (left, top), (right, bottom) = found["corners"]
    assert left >= 24 - 0.01 and top >= 24 - 0.01 and right <= 900 - 24 + 0.01 and bottom <= 600 - 24 + 0.01  # all of it, with a margin
    assert fitted["k"] == pytest.approx((600 - 48) / 800)
    assert found["small"][0] == 1.25 and found["small"][1] == pytest.approx({"x": 5, "y": 5})  # a tiny map is not blown up
    assert found["panned"] == {"x": 55, "y": -30, "k": 0.8}
    scale, before, after = found["pinched"]
    assert scale == pytest.approx(1.6) and after == pytest.approx(before)  # twice as far apart, and the middle point followed the fingers
    assert found["still"] == pytest.approx({"x": 40, "y": -25, "k": 0.8})


def test_filters_by_team_tier_and_name(tmp_path):
    found = run_js(
        tmp_path,
        r"""
        const list = graph.validate(sample).services;
        const names = (filters) => list.filter((service) => graph.matches(service, filters)).map((service) => service.name);
        return {
          all: names({}).length, payments: names({ team: "Payments" }), critical: names({ team: "Payments", tier: 1 }), third: names({ tier: 3 }),
          search: [names({ query: "pay" }), names({ query: "  SHOP " }), names({ query: "zzz" }), names({ team: "Data", query: "search" })],
        };
        """,
    )
    listed = sample_services()
    assert found["all"] == len(listed)
    assert found["payments"] == [service["name"] for service in listed if service["team"] == "Payments"] and len(found["payments"]) >= 4
    assert found["critical"] == [service["name"] for service in listed if service["team"] == "Payments" and service["tier"] == 1]
    assert found["third"] == ["reporting"]
    assert found["search"] == [["payments"], ["web-shop"], [], ["search-index"]]  # part of a name, capitals and spaces forgiven
