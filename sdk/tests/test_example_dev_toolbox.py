"""The ``dev-toolbox`` example: a static app whose tools run in the browser.

There is no backend to call, so what is tested is the logic the page is a
thin layer over: the ES modules in ``public/lib/``. They touch no DOM and are
run here with node (``run_js``); where there is an independent source of
truth it comes from Python: ``hashlib`` for the digests, a minute-by-minute
cron clock for the run times, a patch applier and a textbook LCS for the
diff. A few tests read the files themselves: that the page stays inside its
directory (so it works under a path prefix) and never talks to a server.
"""

import datetime
import hashlib
import json
import re
import shutil
import subprocess

import pytest

from conftest import EXAMPLES

APP = EXAMPLES / "dev-toolbox"
PUBLIC = APP / "public"
TOOLS = ("json", "base64", "url", "jwt", "time", "uuid", "hash", "regex", "diff", "cron")
#: Friday 2026-11-20 12:00:00 UTC, the "now" of the tests, in seconds.
NOW = 1795176000

pytestmark = pytest.mark.skipif(not APP.is_dir(), reason="needs a checkout of the repository (examples/)")

IMPORTS = "".join('import * as %s from "./public/lib/%s.js";\n' % (name, name) for name in TOOLS)


def run_js(tmp_path, body, data=None):
    """Run JavaScript against the example's modules with node and return what ``body`` returns.

    ``body`` is the inside of an async function that sees every module of
    ``public/lib/`` under its own name and ``data`` (JSON) as ``data``. The
    modules are written for the browser and have no package.json, so node
    would take them for CommonJS: they are copied next to one that says
    "module".
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
        IMPORTS
        + 'import { readFileSync } from "node:fs";\n'
        + 'const data = JSON.parse(readFileSync(new URL("./data.json", import.meta.url), "utf-8"));\n'
        + "const answer = await (async () => {\n%s\n})();\n" % body
        + "process.stdout.write(JSON.stringify(answer === undefined ? null : answer));\n",
        encoding="utf-8",
    )
    done = subprocess.run([node, "run.mjs"], capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(work))
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def scripts():
    return sorted(PUBLIC.rglob("*.js"))


# --- The app as files ------------------------------------------------------------------------------------


def test_dev_toolbox_is_a_page_without_a_backend():
    assert not list(APP.rglob("*.py")) and not (APP / "requirements.txt").exists() and not (APP / ".env.example").exists()
    manifest = json.loads((APP / "efp-app.json").read_text(encoding="utf-8"))
    assert manifest["type"] == "static" and manifest["entry"] == "public"
    page = (PUBLIC / "index.html").read_text(encoding="utf-8")
    assert 'href="style.css"' in page and '<script type="module" src="app.js">' in page
    assert "<form" not in page  # nothing on the page can be submitted anywhere
    # The promise is made where the visitor reads it, and in the README.
    assert "Nothing leaves this tab" in page and "no backend" in page
    assert "no backend" in (APP / "README.md").read_text(encoding="utf-8")
    for script in scripts():
        text = script.read_text(encoding="utf-8")
        for call in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "EventSource", "importScripts"):
            assert call not in text, "%s could talk to a server (%s)" % (script.name, call)


def test_dev_toolbox_modules_stay_inside_the_served_directory():
    # Under /{slug}/ a module address that starts with "/" or a host leaves the app.
    found = 0
    for script in scripts():
        text = script.read_text(encoding="utf-8")
        addresses = re.findall(r'^import [^"]*"([^"]+)";$', text, flags=re.MULTILINE) + re.findall(r'new URL\("([^"]+)", import\.meta\.url\)', text)
        for address in addresses:
            assert address.startswith(("./", "../")) and address.endswith(".js"), (script.name, address)
            target = (script.parent / address).resolve()
            assert target.is_file() and PUBLIC.resolve() in target.parents, (script.name, address)
            found += 1
    assert found >= 30
    worker = (PUBLIC / "tools" / "regex.js").read_text(encoding="utf-8")
    assert 'new Worker(new URL("../regex-worker.js", import.meta.url), { type: "module" })' in worker


def test_dev_toolbox_logic_loads_without_a_page_and_every_tool_is_listed(tmp_path):
    # Importing lib/ and tools/ in node proves that no module touches the DOM while loading.
    tools = run_js(
        tmp_path,
        r"""
        if (typeof document !== "undefined" || typeof window !== "undefined") throw new Error("this is not a browser");
        const found = [];
        for (const name of data) {
          const tool = (await import("./public/tools/" + name + ".js")).default;
          found.push({ id: tool.id, title: tool.title, blurb: tool.blurb, mount: typeof tool.mount });
        }
        return found;
        """,
        data=TOOLS,
    )
    assert [tool["id"] for tool in tools] == list(TOOLS)  # the id is the address: #jwt opens the JWT tool
    assert all(tool["title"] and tool["blurb"] and tool["mount"] == "function" for tool in tools)
    app = (PUBLIC / "app.js").read_text(encoding="utf-8")
    for name in TOOLS:
        assert 'from "./tools/%s.js";' % name in app
    assert "location.hash" in app and '"hashchange"' in app and 'document.createElement("a")' in app
    assert "NOT verified" in (PUBLIC / "tools" / "jwt.js").read_text(encoding="utf-8")


# --- JSON ------------------------------------------------------------------------------------------------


def test_json_says_where_the_mistake_is(tmp_path):
    cases = {
        '{"a": 1,}': (1, 9),
        '{\n  "a": 1\n  "b": 2\n}': (3, 3),
        "[1, 2": (1, 6),
        "{'a': 1}": (1, 2),
        "{a: 1}": (1, 2),
        '{"a" 1}': (1, 6),
        "[01]": (1, 2),
        "[1.]": (1, 3),
        '["one\ntwo"]': (1, 6),
        '["tab\there"]': (1, 6),
        '["\\x41"]': (1, 3),
        "[1] [2]": (1, 5),
        "[True]": (1, 2),
        "[undefined]": (1, 2),
        '{"a": 1} // done': (1, 10),
        "[\n  1,\n  2,\n]": (4, 1),
        "": (1, 1),
    }
    found = run_js(
        tmp_path,
        r"""
        return {
          scanned: data.map((text) => json.scan(text)),
          shown: json.excerpt('{"a": 1,\n  "b": x}', 16),
          deep: [json.scan("[".repeat(100000)), json.scan("[".repeat(500) + "]".repeat(500)).ok],
        };
        """,
        data=list(cases),
    )
    for (text, (line, column)), result in zip(cases.items(), found["scanned"]):
        assert result["ok"] is False, text
        error = result["error"]
        assert (error["line"], error["column"]) == (line, column), (text, error)
        assert error["message"] and error["offset"] >= 0
        with pytest.raises(ValueError):  # Python agrees that it is not JSON
            json.loads(text)
    # A pointer for the page: the line of the mistake and where the caret goes under it.
    assert found["shown"] == {"text": '  "b": x}', "caret": 7}
    # Nesting without end is refused in a sentence, not with a stack overflow.
    assert found["deep"][0]["ok"] is False and found["deep"][1] is True


def test_json_formatting_changes_only_the_white_space(tmp_path):
    documents = [
        '{"name":"efp","tags":["a","b"],"nested":{"empty":{},"list":[],"n":null,"ok":true},"pi":3.14}',
        "[]",
        '"just a string"',
        " 42 ",
        '[{"a":[[1,[2,[3]]]]},"\\u00e9\\n\\"quoted\\""]',
        '\n{ "spread" : [ 1 ,\n 2 ] ,\t"x" : { } }\n',
    ]
    answer = run_js(
        tmp_path,
        r"""
        return {
          found: data.map((text) => [json.format(text, 2), json.format(text, 4), json.format(text, "\t"), json.minify(text)]),
          exact: [json.format('{"id":12345678901234567890,"price":1.10,"tiny":-1.5E-7,"id":7}'), json.minify('{"id": 12345678901234567890, "id": 7}').text],
        };
        """,
        data=documents,
    )
    found, exact = answer["found"], answer["exact"]
    for text, (two, four, tabs, small) in zip(documents, found):
        value = json.loads(text)
        # Python's own formatter is the reference: the same text, character for character.
        assert two["text"] == json.dumps(value, indent=2), text
        assert four["text"] == json.dumps(value, indent=4), text
        assert tabs["text"] == json.dumps(value, indent="\t"), text
        assert small["text"] == json.dumps(value, separators=(",", ":")), text
    assert found[0][0]["summary"] == {"type": "object", "members": 4, "values": 11, "depth": 3}
    assert found[3][0]["summary"]["type"] == "number" and found[1][0]["summary"] == {"type": "array", "members": 0, "values": 1, "depth": 1}

    # What JSON.parse + JSON.stringify would silently change stays as typed.
    assert exact[0]["text"] == '{\n  "id": 12345678901234567890,\n  "price": 1.10,\n  "tiny": -1.5E-7,\n  "id": 7\n}'
    assert exact[1] == '{"id":12345678901234567890,"id":7}'
    [warning] = exact[0]["warnings"]  # the repeated key is kept, and pointed out
    assert (warning["line"], warning["column"]) == (1, 56) and '"id"' in warning["message"]


# --- Base64 and URL ----------------------------------------------------------------------------------------


def test_base64_is_safe_for_utf8_and_reads_both_alphabets(tmp_path):
    found = run_js(
        tmp_path,
        r"""
        const texts = ["", "a", "ab", "abc", "h\u00e9llo \u20ac", "\u4f60\u597d", "\ud83d\ude00 ok", "??>>~~"];
        const pairs = texts.map((text) => {
          const standard = base64.encodeText(text);
          const urlSafe = base64.encodeText(text, { urlSafe: true });
          return {
            text, standard, urlSafe,
            node: Buffer.from(text, "utf-8").toString("base64"),
            nodeUrl: Buffer.from(text, "utf-8").toString("base64url"),
            back: [base64.decodeText(standard).text, base64.decodeText(urlSafe).text],
          };
        });
        // every byte value and every length up to 300, against node's own Base64
        let bytesAgree = true;
        for (let length = 0; length <= 300; length += 7) {
          const bytes = Uint8Array.from({ length }, (_, index) => (index * 151 + length) & 255);
          const encoded = base64.bytesToBase64(bytes);
          const decoded = base64.base64ToBytes(encoded);
          bytesAgree = bytesAgree && encoded === Buffer.from(bytes).toString("base64") && decoded.ok && Buffer.from(decoded.bytes).equals(Buffer.from(bytes));
        }
        const binary = base64.decodeText("/+7dzA==");
        return {
          pairs, bytesAgree,
          tidy: base64.decodeText(" aMOp\r\n bGxv ").text,
          binary: { text: binary.text, bytes: Array.from(binary.bytes), dump: base64.hexDump(binary.bytes) },
          bad: ["a$b", "YQ=a", "YQ===", "abcde"].map((text) => base64.decodeText(text)),
        };
        """,
    )
    for pair in found["pairs"]:
        assert pair["standard"] == pair["node"] and pair["urlSafe"] == pair["nodeUrl"], pair["text"]
        assert pair["back"] == [pair["text"], pair["text"]]  # accents, CJK and emoji survive the round trip
        assert not set(pair["urlSafe"]) & set("+/=")
    assert found["bytesAgree"] is True
    assert found["tidy"] == "h\u00e9llo"  # white space from a wrapped paste is skipped
    assert found["binary"]["text"] is None and found["binary"]["bytes"] == [0xFF, 0xEE, 0xDD, 0xCC]  # bytes that are not text
    assert found["binary"]["dump"].startswith("00000000  ff ee dd cc")
    assert all(bad["ok"] is False and bad["message"] for bad in found["bad"])


def test_url_tool_encodes_decodes_and_takes_a_url_apart(tmp_path):
    found = run_js(
        tmp_path,
        r"""
        const text = "a b&c=d/\u00e9?#%";
        return {
          encoded: url.encodeComponent(text),
          back: url.decodeComponent(url.encodeComponent(text).text),
          form: [url.decodeComponent("two+words"), url.decodeComponent("two+words", { plusAsSpace: true })],
          bad: [url.decodeComponent("100%"), url.decodeComponent("%zz"), url.decodeComponent("%E9"), url.encodeComponent("\ud800")],
          whole: url.parseUrl(" https://user:pw@example.com:8443/a/b?x=1&y=two+words&x=3&flag&q=%C3%A9&bad=%zz#state=abc&token=t "),
          query: url.parseUrl("?a=1&b=%20"),
          bare: url.parseUrl("a=1&a=2"),
          notUrl: [url.parseUrl("example.com/path"), url.parseUrl("just words"), url.parseUrl("   ")],
        };
        """,
    )
    assert found["encoded"] == {"ok": True, "text": "a%20b%26c%3Dd%2F%C3%A9%3F%23%25"}
    assert found["back"] == {"ok": True, "text": "a b&c=d/\u00e9?#%"}
    assert [answer["text"] for answer in found["form"]] == ["two+words", "two words"]
    assert all(answer["ok"] is False and answer["message"] for answer in found["bad"])

    whole = found["whole"]
    assert whole["ok"] is True and whole["kind"] == "url"
    assert {part["name"]: part["value"] for part in whole["parts"]} == {
        "Scheme": "https",
        "User": "user",
        "Password": "pw",
        "Host": "example.com",
        "Port": "8443",
        "Path": "/a/b",
        "Query": "?x=1&y=two+words&x=3&flag&q=%C3%A9&bad=%zz",
        "Fragment": "#state=abc&token=t",
    }
    # In order, repeats kept, decoded; a pair that cannot be decoded is shown as typed.
    assert [(pair["key"], pair["value"]) for pair in whole["params"]] == [
        ("x", "1"),
        ("y", "two words"),
        ("x", "3"),
        ("flag", ""),
        ("q", "\u00e9"),
        ("bad", "%zz"),
    ]
    assert [(pair["key"], pair["value"]) for pair in whole["fragmentParams"]] == [("state", "abc"), ("token", "t")]
    assert found["query"]["kind"] == "query" and [(pair["key"], pair["value"]) for pair in found["query"]["params"]] == [("a", "1"), ("b", " ")]
    assert [pair["value"] for pair in found["bare"]["params"]] == ["1", "2"]
    assert all(answer["ok"] is False and answer["message"] for answer in found["notUrl"])


# --- JWT, timestamps, UUIDs, hashes ----------------------------------------------------------------------


def test_jwt_is_decoded_with_its_times_and_never_trusted(tmp_path):
    found = run_js(
        tmp_path,
        r"""
        const now = data.now * 1000;
        const part = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");
        const token = (payload, header = { alg: "RS256", typ: "JWT" }, signature = "c2lnbmVk") => part(header) + "." + part(payload) + "." + signature;
        const base = { sub: "bob", name: "B\u00f6b", iat: data.now - 3600 };
        const raw = Buffer.from('{"alg":"HS256"}').toString("base64url") + "." + Buffer.from('{"id":12345678901234567890}').toString("base64url") + ".x";
        return {
          expired: jwt.decodeJwt("Bearer " + token({ ...base, exp: data.now - 7200, nbf: data.now - 9000 }), now),
          wrapped: jwt.decodeJwt(token({ ...base, exp: data.now + 86400 * 2 }).replace(/(.{20})/g, "$1\n"), now),
          early: jwt.decodeJwt(token({ ...base, nbf: data.now + 600, exp: data.now + 900 }), now),
          forever: jwt.decodeJwt(token(base), now),
          forged: jwt.decodeJwt(token({ ...base, admin: true }, { alg: "RS256" }, "bm90LWEtc2lnbmF0dXJl"), now),
          unsigned: jwt.decodeJwt(token(base, { alg: "none" }, ""), now),
          millis: jwt.decodeJwt(token({ exp: (data.now + 60) * 1000 }), now),
          exact: jwt.decodeJwt(raw, now).payloadText,
          bad: ["", "abc", "a.b", "a.b.c.d.e", "!!.e30.x", part([1]) + ".e30.x", "e30." + Buffer.from("not json").toString("base64url") + ".x"].map((text) => jwt.decodeJwt(text, now)),
        };
        """,
        data={"now": NOW},
    )
    expired = found["expired"]
    assert expired["ok"] is True and expired["algorithm"] == "RS256" and expired["signatureBytes"] == 6
    assert expired["header"] == {"alg": "RS256", "typ": "JWT"}
    assert expired["payload"] == {"sub": "bob", "name": "B\u00f6b", "iat": NOW - 3600, "exp": NOW - 7200, "nbf": NOW - 9000}
    assert json.loads(expired["payloadText"]) == expired["payload"] and "\n  " in expired["payloadText"]
    times = {time["claim"]: time for time in expired["times"]}
    assert set(times) == {"exp", "nbf", "iat"}
    assert times["exp"]["iso"] == "2026-11-20T10:00:00Z" and times["exp"]["relative"] == "2 hours ago"
    assert times["iat"]["iso"] == "2026-11-20T11:00:00Z" and times["iat"]["ms"] == (NOW - 3600) * 1000
    assert expired["validity"]["state"] == "expired"

    assert found["wrapped"]["validity"]["state"] == "current" and "in 2 days" in found["wrapped"]["validity"]["sentence"]
    assert found["early"]["validity"]["state"] == "not-yet-valid"
    assert found["forever"]["validity"]["state"] == "no-expiry"
    # A token with any signature decodes just the same: decoding proves nothing about who wrote it.
    assert found["forged"]["ok"] is True and found["forged"]["payload"]["admin"] is True
    assert found["unsigned"]["ok"] is True and found["unsigned"]["algorithm"] == "none" and found["unsigned"]["signatureBytes"] == 0
    [millis] = found["millis"]["times"]
    assert millis["iso"] == "2026-11-20T12:01:00Z" and millis["note"]  # milliseconds are read as such, and remarked on
    assert "12345678901234567890" in found["exact"]
    assert all(answer["ok"] is False and answer["message"] for answer in found["bad"])


def test_timestamps_convert_both_ways(tmp_path):
    found = run_js(
        tmp_path,
        r"""
        const now = data.now * 1000;
        const read = (text, unit) => time.parseInstant(text, unit);
        return {
          same: ["1767225600", "1767225600000", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00.000+00:00", "2026-01-01 08:00:00+08:00",
                 "2025-12-31T19:00:00-0500", "2026-01-01", "2026-01-01t00:00z"].map((text) => read(text)),
          units: [read("1767225600", "milliseconds").ms, read("99999999999999", "seconds").ok, read("-86400").ms, read("1767225600.5").ms, read("2026-01-01T00:00:00.25Z").ms],
          local: [read("2026-07-01T09:30:15").ms, new Date(2026, 6, 1, 9, 30, 15).getTime(), read("2026-07-01T09:30:15").readAs],
          bad: ["", "yesterday", "2026-02-30", "2026-13-01", "2026-01-01T24:00:00Z", "2026-01-01T10:61:00Z", "99999999999999999999999", "0050-01-01"].map((text) => read(text)),
          relative: [0, 3, 59, 60, 3599, 3600, 7300, 86400 * 2, 86400 * 45, 86400 * 800].map((seconds) => time.relativeTime(now - seconds * 1000, now)),
          ahead: [90, 3 * 3600, 86400 * 3].map((seconds) => time.relativeTime(now + seconds * 1000, now)),
          rows: time.describeInstant(now + 250, now + 7200000, 480),
          offsets: [time.isoWithOffset(now, 480), time.isoWithOffset(now, -210), time.isoWithOffset(now, 0), time.offsetText(-570)],
          guess: [1.7e9, -1.7e9, 1.7e12, 1.7e15, 1.7e18].map(time.guessUnit),
        };
        """,
        data={"now": NOW},
    )
    moment = int(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    assert [answer.get("ms") for answer in found["same"]] == [moment] * 8  # eight spellings of one moment
    assert all(answer["readAs"] for answer in found["same"])
    assert found["units"] == [1767225600, False, -86400000, 1767225600500, moment + 250]
    assert found["local"][0] == found["local"][1] and "local" in found["local"][2]  # no offset: the visitor's own zone
    assert all(answer["ok"] is False and answer["message"] for answer in found["bad"])
    assert found["relative"] == ["now", "now", "59 seconds ago", "1 minute ago", "59 minutes ago", "1 hour ago", "2 hours ago", "2 days ago", "1 month ago", "2 years ago"]
    assert found["ahead"] == ["in 1 minute", "in 3 hours", "in 3 days"]
    rows = {row["label"]: row["value"] for row in found["rows"]}
    assert rows["Epoch seconds"] == "%d.250" % NOW and rows["Epoch milliseconds"] == "%d250" % NOW
    assert rows["ISO 8601, UTC"] == "2026-11-20T12:00:00.250Z"
    assert rows["ISO 8601, local (UTC+08:00)"] == "2026-11-20T20:00:00.250+08:00"
    assert rows["Relative to now"] == "1 hour ago"
    assert found["offsets"] == ["2026-11-20T20:00:00+08:00", "2026-11-20T08:30:00-03:30", "2026-11-20T12:00:00+00:00", "-09:30"]
    assert found["guess"] == ["seconds", "seconds", "milliseconds", "microseconds", "nanoseconds"]


def test_uuids_are_version_four_and_come_from_the_random_source(tmp_path):
    found = run_js(
        tmp_path,
        r"""
        const { webcrypto } = await import("node:crypto");
        const random = (length) => webcrypto.getRandomValues(new Uint8Array(length));
        let asked = 0;
        const counting = (length) => { asked += length; return new Uint8Array(length).fill(asked); };
        return {
          hundred: uuid.generate(100, {}, random),
          fixed: [uuid.uuidFromBytes(new Uint8Array(16)), uuid.uuidFromBytes(new Uint8Array(16).fill(255)), uuid.uuidFromBytes(Uint8Array.from({ length: 16 }, (_, index) => index * 17))],
          shaped: [uuid.generate(1, { uppercase: true }, counting)[0], uuid.generate(1, { hyphens: false }, counting)[0]],
          counts: [0, -3, "x", 2.9, 1000].map((count) => uuid.generate(count, {}, random).length),
          asked,
        };
        """,
    )
    version_four = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
    assert len(set(found["hundred"])) == 100 and all(version_four.match(text) for text in found["hundred"])
    assert found["fixed"] == [
        "00000000-0000-4000-8000-000000000000",
        "ffffffff-ffff-4fff-bfff-ffffffffffff",
        "00112233-4455-4677-8899-aabbccddeeff",
    ]
    assert found["shaped"] == ["10101010-1010-4010-9010-101010101010".upper(), "2020202020204020a020202020202020"]
    assert found["counts"] == [1, 1, 1, 2, 100] and found["asked"] == 32  # sixteen bytes per UUID, from the source it was given


def test_hashes_match_hashlib(tmp_path):
    texts = ["", "abc", "The quick brown fox jumps over the lazy dog", "h\u00e9llo \u20ac \U0001f600"]
    found = run_js(
        tmp_path,
        r"""
        const { webcrypto } = await import("node:crypto");
        const results = [];
        for (const text of data) results.push(await hash.digests(text, webcrypto.subtle));
        const bytes = await hash.digests(new Uint8Array([0, 255, 16, 32]), webcrypto.subtle);
        const abc = results[1];
        const sha256 = abc[1].hex;
        let missing = "";
        try { await hash.digests("abc", null); } catch (error) { missing = error.message; }
        return {
          results, bytes, missing,
          match: [sha256, sha256.toUpperCase(), "sha256:" + sha256, "SHA-256: " + sha256, sha256 + "  release.zip", abc[0].hex].map((text) => hash.matchDigest(text, abc)),
          mismatch: hash.matchDigest("0" + sha256.slice(1), abc),
          other: ["", "   ", "xyz", "abcd"].map((text) => hash.matchDigest(text, abc).state),
        };
        """,
        data=texts,
    )
    names = {"SHA-1": "sha1", "SHA-256": "sha256", "SHA-384": "sha384", "SHA-512": "sha512"}
    for text, result in zip(texts, found["results"]):
        assert [row["algorithm"] for row in result] == list(names)
        for row in result:
            assert row["hex"] == hashlib.new(names[row["algorithm"]], text.encode("utf-8")).hexdigest(), (text, row["algorithm"])
    assert found["bytes"][1]["hex"] == hashlib.sha256(bytes([0, 255, 16, 32])).hexdigest()
    assert found["missing"]  # plain http: no crypto.subtle, and the page turns that into a sentence
    assert [answer["state"] for answer in found["match"]] == ["match"] * 6
    assert [answer["algorithm"] for answer in found["match"]] == ["SHA-256"] * 5 + ["SHA-1"]
    assert found["mismatch"] == {"state": "mismatch", "algorithm": "SHA-256"}
    assert found["other"] == ["empty", "empty", "not-hex", "unknown-length"]


# --- The regex tester and its guard ----------------------------------------------------------------------


def test_regex_matches_groups_and_highlight_segments(tmp_path):
    found = run_js(
        tmp_path,
        r"""
        const text = "Released 2026-11-20, patched 2026-12-03.";
        const dates = regex.findMatches("(?<year>\\d{4})-(\\d{2})-(?:\\d{2})", "g", text);
        const cut = regex.boundText("x".repeat(regex.MAX_TEXT + 5));
        return {
          dates,
          joined: regex.segments(text, dates.matches).map((part) => part.text).join("") === text,
          marked: regex.segments(text, dates.matches).filter((part) => part.match !== null).map((part) => [part.match, part.text]),
          first: regex.findMatches("\\d+", "", text).matches.length,
          optional: regex.findMatches("(a)|(b)", "g", "ab").matches.map((match) => match.groups),
          empty: regex.findMatches("x*", "g", "abc").matches.map((match) => [match.index, match.text]),
          astral: regex.findMatches("", "gu", "\ud83d\ude00a").matches.length,
          sticky: regex.findMatches("a", "y", "aab").matches.length,
          capped: [regex.findMatches("a", "g", "a".repeat(regex.MAX_MATCHES + 1)), regex.findMatches("a", "g", "a".repeat(regex.MAX_MATCHES))].map((result) => [result.matches.length, result.more]),
          slow: regex.findMatches("a", "g", "aaaa", { budgetMs: 5, now: (() => { let clock = 0; return () => (clock += 10); })() }),
          names: regex.groupNames("(?<a>x)(?:y)(z)[(](?=q)\\((?<!w)(?<b>k)"),
          bad: [["(", "g"], ["[a-", ""], ["a", "gg"], ["a", "q"], ["(?<n>a)(?<n>b)", ""]].map(([pattern, flags]) => regex.findMatches(pattern, flags, "aaa")),
          cut: [cut.cut, cut.text.length, regex.boundText("short")],
        };
        """,
    )
    dates = found["dates"]
    assert dates["ok"] is True and dates["names"] == ["year", None] and dates["more"] is False
    assert [(match["index"], match["end"], match["text"], match["groups"]) for match in dates["matches"]] == [
        (9, 19, "2026-11-20", ["2026", "11"]),
        (29, 39, "2026-12-03", ["2026", "12"]),
    ]
    assert found["joined"] is True and found["marked"] == [[0, "2026-11-20"], [1, "2026-12-03"]]
    assert found["first"] == 1  # without g there is one match, as in JavaScript
    assert found["optional"] == [["a", None], [None, "b"]]  # a group that took no part is null, not ""
    assert found["empty"] == [[0, ""], [1, ""], [2, ""], [3, ""]]  # empty matches end; they do not loop for ever
    assert found["astral"] == 3 and found["sticky"] == 2  # before the emoji, after it, at the end: never inside it
    assert found["capped"] == [[1000, True], [1000, False]]
    assert found["slow"]["outOfTime"] is True and len(found["slow"]["matches"]) == 1
    assert found["names"] == ["a", None, "b"]
    assert all(answer["ok"] is False and answer["message"] for answer in found["bad"])
    assert found["cut"] == [True, 100000, {"text": "short", "cut": False}]


FAKE_WORKERS = r"""
        const made = [];
        // A worker that loads at once and answers ``answers`` times, then goes silent like a pattern that hangs.
        const spawn = (answers, loads = true) => () => {
          const worker = {
            ended: false, posted: [],
            postMessage(message) {
              worker.posted.push(message);
              if (worker.posted.length <= answers) setTimeout(() => worker.ended || worker.onmessage({ data: { id: message.id, result: "done " + message.text } }), 5);
            },
            terminate() { worker.ended = true; },
          };
          made.push(worker);
          if (loads) setTimeout(() => worker.ended || worker.onmessage({ data: { ready: true } }), 5);
          return worker;
        };
        const timed = async (promise) => { const started = Date.now(); const answer = await promise; return [answer, Date.now() - started]; };
"""


def test_regex_runner_ends_a_worker_that_does_not_answer_in_time(tmp_path):
    found = run_js(
        tmp_path,
        FAKE_WORKERS
        + r"""
        const runner = regex.createRunner(spawn(1), 150);
        const first = await runner.run({ pattern: "a", flags: "g", text: "one" });
        const [stuck, waited] = await timed(runner.run({ pattern: "(a+)+$", flags: "", text: "two" }));
        const afterStuck = made.map((worker) => worker.ended);
        const third = await runner.run({ text: "three" });

        // A newer request ends the one that is still running, and is answered itself.
        const typing = regex.createRunner(spawn(0), 5000);
        const overtaken = typing.run({ text: "old" });
        await new Promise((resolve) => setTimeout(resolve, 30));
        const workersBefore = made.length;
        const newer = typing.run({ text: "new" });
        const overtakenAnswer = await overtaken;
        typing.stop();
        const stopped = await newer;

        const broken = await regex.createRunner(() => { throw new Error("no workers here"); }, 150).run({ text: "x" });
        const crashing = regex.createRunner(() => { const worker = { postMessage() {}, terminate() {} }; setTimeout(() => worker.onerror(new Error("load failed")), 5); return worker; }, 150);
        const crashed = await crashing.run({ text: "x" });
        const [neverLoads, loadWait] = await timed(regex.createRunner(spawn(1, false), 50, 120).run({ text: "x" }));
        return {
          first, stuck, waited, afterStuck, third,
          sent: made[0].posted.map((message) => [message.id, message.text]),
          workers: [workersBefore, made[workersBefore - 1].ended, overtakenAnswer, stopped],
          broken, crashed, neverLoads, loadWait,
        };
        """,
    )
    assert found["first"] == {"ok": True, "result": "done one"}
    assert found["stuck"] == {"ok": False, "reason": "timeout"} and 100 <= found["waited"] < 3000
    assert found["afterStuck"] == [True]  # the only way to stop a running regular expression
    assert found["third"] == {"ok": True, "result": "done three"}  # on a fresh worker
    assert found["sent"] == [[1, "one"], [2, "two"]]
    before, ended, overtaken, stopped = found["workers"]
    assert ended is True and overtaken == {"ok": False, "reason": "replaced"} and stopped == {"ok": False, "reason": "replaced"}
    assert found["broken"] == {"ok": False, "reason": "unavailable"} and found["crashed"] == {"ok": False, "reason": "unavailable"}
    # The budget starts when the worker has loaded; one that never loads is given up on separately.
    assert found["neverLoads"] == {"ok": False, "reason": "unavailable"} and found["loadWait"] >= 100


def test_regex_runner_stops_a_pattern_that_really_hangs(tmp_path):
    # The real thing: (a+)+$ on forty letters would run for hours. It runs in a thread, as regex-worker.js runs
    # it in a Web Worker in the page, and the runner ends the thread after its budget.
    shim = (
        'import { parentPort } from "node:worker_threads";\n'
        'import { findMatches } from "./public/lib/regex.js";\n'
        'parentPort.on("message", ({ id, pattern, flags, text }) => parentPort.postMessage({ id, result: findMatches(pattern, flags, text) }));\n'
        "parentPort.postMessage({ ready: true });\n"
    )
    found = run_js(
        tmp_path,
        r"""
        const { Worker } = await import("node:worker_threads");
        const { writeFileSync } = await import("node:fs");
        writeFileSync(new URL("./shim.mjs", import.meta.url), data.shim);
        let spawned = 0;
        const spawn = () => {
          spawned += 1;
          const thread = new Worker(new URL("./shim.mjs", import.meta.url));
          const worker = { postMessage: (message) => thread.postMessage(message), terminate: () => thread.terminate() };
          thread.on("message", (message) => worker.onmessage({ data: message }));
          thread.on("error", () => worker.onerror());
          return worker;
        };
        const runner = regex.createRunner(spawn, 500, 60000);
        const quick = await runner.run({ pattern: "a+", flags: "g", text: "caaat" });
        const started = Date.now();
        const stuck = await runner.run({ pattern: "(a+)+$", flags: "", text: "a".repeat(40) + "!" });
        const waited = Date.now() - started;
        const after = await runner.run({ pattern: "a+", flags: "g", text: "caaat" });
        runner.stop();
        return { quick: quick.result.matches.map((match) => match.text), stuck, waited, after: after.ok && after.result.matches.length, spawned };
        """,
        data={"shim": shim},
    )
    assert found["quick"] == ["aaa"]
    assert found["stuck"] == {"ok": False, "reason": "timeout"} and 400 <= found["waited"] < 20000
    assert found["after"] == 1 and found["spawned"] == 2  # the tester works again, on a new worker
    worker = (PUBLIC / "regex-worker.js").read_text(encoding="utf-8")
    assert 'from "./lib/regex.js"' in worker and "postMessage({ ready: true })" in worker and "findMatches(pattern, flags, text)" in worker


# --- Text diff ---------------------------------------------------------------------------------------------


def apply_unified(original, patch):
    """Apply a unified diff to a list of lines, checking every context and removed line on the way."""
    lines = patch.splitlines()
    assert lines[0].startswith("--- ") and lines[1].startswith("+++ ")
    result, position = [], 0
    for line in lines[2:]:
        header = re.match(r"^@@ -(\d+),(\d+) \+(\d+),(\d+) @@$", line)
        if header:
            start, count = int(header.group(1)), int(header.group(2))
            first = start - 1 if count else start  # an empty side is addressed by the line before it
            assert first >= position
            result += original[position:first]
            position = first
            assert len(result) == (int(header.group(3)) - 1 if int(header.group(4)) else int(header.group(3)))
        elif line.startswith("+"):
            result.append(line[1:])
        else:
            assert line[0] in " -" and original[position] == line[1:], line
            if line[0] == " ":
                result.append(line[1:])
            position += 1
    return result + original[position:]


def lcs_length(one, two):
    """The textbook table, for comparison."""
    previous = [0] * (len(two) + 1)
    for left in one:
        current = [0]
        for column, right in enumerate(two, 1):
            current.append(previous[column - 1] + 1 if left == right else max(previous[column], current[column - 1]))
        previous = current
    return previous[-1]


def test_diff_is_a_longest_common_subsequence_and_its_patch_applies(tmp_path):
    words = "alpha beta gamma delta epsilon zeta eta theta".split()
    seed = 12345
    pairs = [("", ""), ("one\ntwo\n", "one\ntwo"), ("", "new\nfile"), ("gone\n", ""), ("a\nb\nc\nd", "a\nB\nc\nd\ne")]
    for size in (5, 40, 120):
        before, after = [], []
        for _ in range(size):
            seed = (seed * 1103515245 + 12345) % 2**31
            word = words[seed % len(words)]
            before.append(word)
            if seed % 7 == 0:
                continue  # removed
            after.append(word + "!" if seed % 5 == 0 else word)
            if seed % 11 == 0:
                after.append("added %d" % seed)
        pairs.append(("\n".join(before), "\n".join(after)))
    found = run_js(
        tmp_path,
        r"""
        return data.map(([before, after]) => {
          const result = diff.diffLines(before, after);
          return { ...result, short: diff.unified(result.ops), full: diff.unified(result.ops, { context: Infinity }), none: diff.unified(result.ops, { context: 0 }),
                   hunks: diff.hunks(result.ops).map((hunk) => diff.hunkHeader(hunk)) };
        });
        """,
        data=pairs,
    )
    for (before, after), result in zip(pairs, found):
        old, new = before.splitlines(), after.splitlines()
        assert result["ok"] is True
        ops = result["ops"]
        assert [op["text"] for op in ops if op["type"] != "add"] == old and [op["text"] for op in ops if op["type"] != "del"] == new
        assert [op["before"] for op in ops if op["type"] != "add"] == list(range(1, len(old) + 1))
        assert [op["after"] for op in ops if op["type"] != "del"] == list(range(1, len(new) + 1))
        assert (result["removed"], result["added"]) == (len(old) - result["unchanged"], len(new) - result["unchanged"])
        assert result["unchanged"] == lcs_length(old, new)  # nothing longer could have been kept
        for patch in (result["short"], result["full"], result["none"]):
            if old == new:
                assert patch == ""
            else:
                assert apply_unified(old, patch) == new
    assert found[4]["short"] == "--- original\n+++ changed\n@@ -1,4 +1,5 @@\n a\n-b\n+B\n c\n d\n+e\n"
    assert found[2]["hunks"] == ["@@ -0,0 +1,2 @@"] and found[3]["hunks"] == ["@@ -1,1 +0,0 @@"]


def test_diff_options_and_limits(tmp_path):
    found = run_js(
        tmp_path,
        r"""
        const lines = (count, mark) => Array.from({ length: count }, (_, index) => mark + index).join("\n");
        const far = diff.diffLines(lines(30, "l").replace("l3\n", "L3\n").replace("l25\n", "L25\n"), lines(30, "l"));
        const near = diff.diffLines(lines(30, "l").replace("l3\n", "L3\n").replace("l9\n", "L9\n"), lines(30, "l"));
        return {
          spaces: [diff.diffLines("a  \nb\t", "a\nb"), diff.diffLines("a  \nb\t", "a\nb", { ignoreTrailingSpace: true }), diff.diffLines(" a", "a", { ignoreTrailingSpace: true })].map((result) => result.added + result.removed),
          windows: diff.diffLines("a\r\nb\r\n", "a\nb\n").unchanged,
          far: diff.hunks(far.ops).map((hunk) => [hunk.beforeStart, hunk.beforeCount, hunk.afterStart, hunk.afterCount]),
          near: diff.hunks(near.ops).length,
          wide: diff.hunks(far.ops, 12).length,
          huge: diff.diffLines(lines(3000, "x"), lines(3000, "y")),
          cheap: diff.diffLines(lines(20000, "same") + "\nold", lines(20000, "same") + "\nnew"),
          names: diff.unified(diff.diffLines("a", "b").ops, { beforeName: "v1.txt", afterName: "v2.txt" }).split("\n").slice(0, 2),
        };
        """,
    )
    assert found["spaces"] == [4, 0, 2]  # only spaces at line ends are forgiven
    assert found["windows"] == 2  # \r\n and \n are the same line break
    assert found["far"] == [[1, 7, 1, 7], [23, 7, 23, 7]] and found["near"] == 1 and found["wide"] == 1
    assert found["huge"]["ok"] is False and "3000" in found["huge"]["message"]  # refused in a sentence, not a frozen tab
    cheap = found["cheap"]  # a long text with one edit is cheap: the common start and end are set aside
    assert cheap["ok"] is True and (cheap["added"], cheap["removed"], cheap["unchanged"]) == (1, 1, 20000)
    assert found["names"] == ["--- v1.txt", "+++ v2.txt"]


# --- Cron --------------------------------------------------------------------------------------------------

MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
DAYS = "SUN MON TUE WED THU FRI SAT".split()


def cron_field(source, low, high, names=()):
    """The values of one field, read independently of the JavaScript: (set, starts with a star)."""
    values = set()
    for part in source.split(","):
        span, _, step = part.partition("/")
        if span == "*":
            first, last = low, high
        else:
            ends = [names.index(end.upper()) + low if end.upper() in names else int(end) for end in span.split("-")]
            first, last = ends[0], ends[-1] if len(ends) == 2 or not step else high
        values.update(range(first, last + 1, int(step or 1)))
    return values, source.startswith("*")


def cron_clock(expression, start, count):
    """The next run times found the slow and obvious way: every minute of every day that fits is tried."""
    minute, hour, date, month, weekday = expression.split()
    minutes, _ = cron_field(minute, 0, 59)
    hours, _ = cron_field(hour, 0, 23)
    dates, any_date = cron_field(date, 1, 31)
    months, _ = cron_field(month, 1, 12, MONTHS)
    weekdays, any_weekday = cron_field(weekday, 0, 7, DAYS)
    weekdays = {day % 7 for day in weekdays}
    runs = []
    midnight = start.replace(hour=0, minute=0, second=0, microsecond=0)
    for ahead in range(366 * 8):
        day = midnight + datetime.timedelta(days=ahead)
        date_fits, weekday_fits = day.day in dates, day.isoweekday() % 7 in weekdays
        day_fits = (date_fits and weekday_fits) if (any_date or any_weekday) else (date_fits or weekday_fits)
        if day.month not in months or not day_fits:
            continue
        for minute_of_day in range(24 * 60):
            moment = day + datetime.timedelta(minutes=minute_of_day)
            if moment > start and moment.hour in hours and moment.minute in minutes:
                runs.append(int(moment.timestamp() * 1000))
                if len(runs) == count:
                    return runs
    return runs


def test_cron_next_runs_agree_with_a_clock_that_tries_every_minute(tmp_path):
    expressions = [
        "* * * * *",
        "*/15 * * * *",
        "0 9 * * 1-5",
        "30 9,17 * * *",
        "0 0 1 * *",
        "0 0 13 * 5",  # two restricted day fields: either one is enough
        "0 0 */2 * 1",  # a day field that starts with a star: both must fit
        "*/20 8-18/2 * * MON-FRI",
        "5/20 * * * *",
        "15 3 * JAN,JUL SUN",
        "0 0 * * 7",  # 7 is Sunday too
        "0 12 29 2 *",  # the next leap day
        "59 23 31 12 *",
        "0 0 1-5,10 */3 *",
    ]
    found = run_js(
        tmp_path,
        r"""
        return data.expressions.map((expression) => {
          const parsed = cron.parseCron(expression);
          return { ok: parsed.ok, runs: parsed.ok ? cron.nextRuns(parsed, data.now * 1000, 5, { utc: true }) : [], sentence: parsed.ok ? cron.describeCron(parsed) : "",
                   fields: parsed.ok ? cron.explainFields(parsed) : [] };
        });
        """,
        data={"expressions": expressions, "now": NOW},
    )
    start = datetime.datetime.fromtimestamp(NOW, datetime.timezone.utc)
    sentences = set()
    for expression, result in zip(expressions, found):
        assert result["ok"] is True, expression
        assert result["runs"] == cron_clock(expression, start, 5), expression
        assert result["sentence"].endswith(".") and len(result["fields"]) == 5
        sentences.add(result["sentence"])
    assert len(sentences) == len(expressions)  # every schedule reads differently
    assert found[2]["sentence"] == "At 09:00, on Monday through Friday."
    assert " or " in found[5]["sentence"] and " or " not in found[6]["sentence"]  # the rule nobody remembers is spelled out


def test_cron_reads_names_and_macros_and_refuses_what_is_not_cron(tmp_path):
    found = run_js(
        tmp_path,
        r"""
        const values = (expression) => { const parsed = cron.parseCron(expression); return parsed.ok ? parsed.fields.map((field) => field.values) : parsed.message; };
        const local = cron.nextRuns(cron.parseCron("30 6 * * *"), data.now * 1000, 3);
        return {
          names: [values("0 0 * jan,Mar,DEC mon-fri"), values("0 0 * 1,3,12 1-5")],
          sunday: [values("0 0 * * 0")[4], values("0 0 * * 7")[4], values("0 0 * * 5-7")[4], values("0 0 * * SUN")[4]],
          macros: ["@hourly", "@daily", "@midnight", "@weekly", "@monthly", "@yearly", "@annually"].map((macro) => cron.parseCron(macro).expression),
          spaces: cron.parseCron("  */5   *  * * *  ").expression,
          never: cron.nextRuns(cron.parseCron("0 0 30 2 *"), data.now * 1000, 5, { utc: true }),
          local: local.map((ms) => { const at = new Date(ms); return [at.getHours(), at.getMinutes(), ms > data.now * 1000]; }),
          bad: ["", "* * * *", "0 0 * * * *", "0 0 0 * * * 2027", "60 * * * *", "* 24 * * *", "* * 0 * *", "* * * 13 *", "* * * * 8", "5-1 * * * *", "*/0 * * * *",
                "H/15 * * * *", "H * * * *", "0 0 L * *", "0 0 15W * *", "0 0 * * MON#2", "0 0 * JANUARY *", "a b c d e", "1,,2 * * * *", "@reboot", "@fortnightly"].map(values),
        };
        """,
        data={"now": NOW},
    )
    assert found["names"][0] == found["names"][1] == [[0], [0], list(range(1, 32)), [1, 3, 12], [1, 2, 3, 4, 5]]
    assert found["sunday"] == [[0], [0], [0, 5, 6], [0]]
    assert found["macros"] == ["0 * * * *", "0 0 * * *", "0 0 * * *", "0 0 * * 0", "0 0 1 * *", "0 0 1 1 *", "0 0 1 1 *"]
    assert found["spaces"] == "*/5 * * * *"
    assert found["never"] == []  # the 30th of February: said on the page, not searched for ever
    assert found["local"] == [[6, 30, True]] * 3  # without utc the times are the visitor's wall clock
    for answer in found["bad"]:
        assert isinstance(answer, str) and answer, answer  # a sentence, never an exception
    jenkins = found["bad"][11]
    assert "Jenkins" in jenkins  # the dialect most members will paste from
