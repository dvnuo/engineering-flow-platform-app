"""The nginx configuration: what the renderer writes, and what can be checked
about the result without an nginx at hand (``nginx -t`` runs when the image
is built)."""

from __future__ import annotations

import dataclasses
import os
import re
import sys
from pathlib import Path

import pytest

from efp_app_host.gateway import render_nginx
from efp_app_host.gateway.render_nginx import STATIC_HOST, RenderError, render
from efp_app_host.gateway.resources import nginx_dir
from efp_app_host.gateway.secret import access_log_key

from gateway_support import make_gateway

# Variables nginx defines itself and the template uses.
BUILT_IN = {
    "remote_addr", "scheme", "host", "request_uri", "request_method", "is_args", "args", "msec", "status",
    "request_time", "upstream_header_time", "bytes_sent", "proxy_add_x_forwarded_for",
}  # fmt: skip
BUILT_IN_PREFIXES = ("http_", "upstream_http_")


def strip_comments(text: str) -> str:
    """The configuration without comments; quoted strings are left alone."""

    out: list[str] = []
    quote = ""
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            out.append(char)
            if char == "\\" and index + 1 < len(text):
                out.append(text[index + 1])
                index += 1
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
            out.append(char)
        elif char == "#":
            while index < len(text) and text[index] != "\n":
                index += 1
            continue
        else:
            out.append(char)
        index += 1
    assert not quote, "a quoted string is not closed"
    return "".join(out)


def blocks(text: str) -> list[tuple[str, str]]:
    """(header, body) of every block, nested ones included. Also checks that
    braces pair up and that nothing but a finished directive precedes a brace."""

    found: list[tuple[str, str]] = []
    stack: list[tuple[str, int]] = []
    statement: list[str] = []
    quote = ""
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            statement.append(char)
            if char == "\\":
                statement.append(text[index + 1])
                index += 1
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
            statement.append(char)
        elif char == ";":
            assert "".join(statement).strip(), "an empty directive"
            statement = []
        elif char == "{":
            header = " ".join("".join(statement).split())
            assert header, "a block without a name"
            stack.append((header, index + 1))
            statement = []
        elif char == "}":
            assert not "".join(statement).strip(), f"no semicolon after: {''.join(statement).strip()!r}"
            assert stack, "a closing brace too many"
            header, start = stack.pop()
            found.append((header, text[start:index]))
            statement = []
        else:
            statement.append(char)
        index += 1
    assert not stack, f"not closed: {[header for header, _ in stack]}"
    assert not "".join(statement).strip(), "text after the last block"
    return found


def block(found: list[tuple[str, str]], header: str) -> str:
    bodies = [body for name, body in found if name == header]
    assert len(bodies) == 1, f"{header!r}: {len(bodies)} blocks"
    return bodies[0]


@pytest.fixture
def conf(settings) -> str:
    return render(settings)


@pytest.fixture
def parsed(conf) -> list[tuple[str, str]]:
    return blocks(strip_comments(conf))


@pytest.fixture
def gateway_server(parsed) -> list[tuple[str, str]]:
    """The blocks inside the server browsers reach (both servers have a "location /")."""

    return blocks(next(body for header, body in parsed if header == "server" and "default_server" in body))


def directives(body: str) -> list[str]:
    """The directives of a block body, one string each, inner blocks left out."""

    depth = 0
    out: list[str] = []
    current: list[str] = []
    for char in body:
        if char == "{":
            depth += 1
            current = []
        elif char == "}":
            depth -= 1
        elif depth == 0:
            if char == ";":
                out.append(" ".join("".join(current).split()))
                current = []
            else:
                current.append(char)
    return out


# ---- the renderer ------------------------------------------------------------------
def test_the_output_carries_the_settings_and_the_secret(settings, registry, fake_runtime, fake_portal):
    custom = dataclasses.replace(settings, gateway_port=8123, admin_port=9123)
    conf = render(custom)
    secret = (custom.run_dir / "gateway.secret").read_text(encoding="utf-8").strip()

    assert "listen 8123 default_server;" in conf
    assert "listen 8123;" in conf
    assert "proxy_pass http://127.0.0.1:9123/_efp/auth;" in conf
    # The defaults appear nowhere once other ports are set.
    assert "listen 8080" not in conf and "127.0.0.1:9000" not in conf
    assert set(re.findall(r"\blisten (\d+)", conf)) == {"8123"}
    assert set(re.findall(r"http://127\.0\.0\.1:(\d+)", conf)) == {"9123"}
    assert len(secret) >= 43 and f'proxy_set_header X-EFP-Gateway-Secret "{secret}";' in conf
    assert f'"k":"{access_log_key(secret)}"' in conf
    assert f"syslog:server=unix:{(custom.run_dir / 'access-log.sock').as_posix()}," in conf
    assert f"pid {(custom.run_dir / 'nginx.pid').as_posix()};" in conf
    script = re.search(r"js_import efp from (\S+);", conf).group(1)
    assert Path(script).is_file() and Path(script).name == "efp_gateway.js"

    # The manager reads the same secret, so nginx's requests are accepted.
    gateway = make_gateway(custom, registry, fake_runtime, fake_portal)
    assert gateway.secret == secret
    assert gateway.access_log_key == access_log_key(secret)


def test_no_placeholder_is_left(conf, settings):
    assert "{{" not in conf and "}}" not in conf
    for name in render_nginx.values_for(settings):
        assert name not in conf, name


def test_the_secret_is_made_once(settings):
    first = render(settings)
    path = settings.run_dir / "gateway.secret"
    if sys.platform != "win32":
        assert (os.stat(path).st_mode & 0o777) == 0o600
    assert render(settings) == first


def test_values_that_would_break_the_configuration_are_refused(settings, tmp_path):
    with pytest.raises(RenderError, match="access-log socket"):
        render(dataclasses.replace(settings, run_dir=tmp_path / "run dir"))
    with pytest.raises(RenderError):
        render(dataclasses.replace(settings, run_dir=tmp_path / "run;evil"))


def test_a_template_with_an_unknown_placeholder_is_refused(settings, tmp_path):
    (tmp_path / "efp_gateway.js").write_text("export default {};\n", encoding="utf-8")
    (tmp_path / "nginx.conf.tmpl").write_text("listen {{GATEWAY_PORT}};\nroot {{NOT_A_VALUE}};\n", encoding="utf-8")
    with pytest.raises(RenderError, match="NOT_A_VALUE"):
        render(settings, resources=tmp_path)
    (tmp_path / "nginx.conf.tmpl").write_text("listen {{GATEWAY_PORT}};\nroot {{ lower }};\n", encoding="utf-8")
    with pytest.raises(RenderError, match="line 2"):
        render(settings, resources=tmp_path)
    with pytest.raises(RenderError, match="nginx template"):
        render(settings, resources=tmp_path / "missing")
    (tmp_path / "efp_gateway.js").unlink()
    with pytest.raises(RenderError, match="njs script"):
        render(settings, resources=tmp_path)


def test_the_command_prints_the_configuration(settings, monkeypatch, capsys):
    monkeypatch.setenv("EFP_HOST_API_TOKEN", "build-time")
    monkeypatch.setenv("EFP_HOST_RUN_DIR", str(settings.run_dir))
    monkeypatch.setenv("EFP_HOST_STORE_DIR", str(settings.store_dir))
    monkeypatch.setenv("EFP_HOST_GATEWAY_PORT", "8456")
    # The addresses of the Portal are not needed to write the file.
    for name in ("EFP_PORTAL_INTERNAL_URL", "EFP_PORTAL_PUBLIC_URL", "EFP_APPS_PUBLIC_URL"):
        monkeypatch.delenv(name, raising=False)
    assert render_nginx.main() == 0
    out = capsys.readouterr()
    assert "listen 8456 default_server;" in out.out and out.err == ""
    assert "build-time" not in out.out

    monkeypatch.delenv("EFP_HOST_API_TOKEN")
    assert render_nginx.main() == 1
    out = capsys.readouterr()
    assert out.out == "" and "EFP_HOST_API_TOKEN" in out.err

    monkeypatch.setenv("EFP_HOST_API_TOKEN", "build-time")
    monkeypatch.setenv("EFP_HOST_RESOURCE_DIR", str(settings.run_dir / "nowhere"))
    assert render_nginx.main() == 1
    assert capsys.readouterr().out == ""


# ---- the shape of the configuration ---------------------------------------------------
def test_blocks_and_semicolons_pair_up(parsed):
    headers = [header for header, _ in parsed]
    assert headers.count("http") == 1 and headers.count("events") == 1
    assert headers.count("server") == 2


def test_every_variable_is_defined(conf):
    text = strip_comments(conf)
    defined = set(re.findall(r"\b(?:set|auth_request_set|js_set)\s+\$([a-z0-9_]+)", text))
    defined |= set(re.findall(r"\bmap\s+\S+\s+\$([a-z0-9_]+)\s*\{", text))
    defined |= set(re.findall(r"\(\?<([a-z0-9_]+)>", text))
    used = set(re.findall(r"\$([a-z][a-z0-9_]*)", text))
    unknown = {name for name in used - defined - BUILT_IN if not name.startswith(BUILT_IN_PREFIXES)}
    assert unknown == set()
    # ... and nothing is defined for no use.
    assert {name for name in defined if name not in used} == set()


def test_the_njs_functions_the_configuration_names_exist(conf):
    with open(nginx_dir() / "efp_gateway.js", "r", encoding="utf-8") as handle:
        script = handle.read()
    assert script.isascii()
    exported = re.search(r"export default \{(.*?)\};", script, re.S).group(1)
    names = set(re.findall(r"\befp\.([A-Za-z]+)", strip_comments(conf)))
    assert names == {"reservedRequestHeader", "filterResponseHeaders"}
    for name in names:
        assert re.search(rf"\b{name}: {name}\b", exported)
        assert re.search(rf"^function {name}\(r\) \{{", script, re.M)
    # njs 0.7: nothing newer than plain functions and "var".
    assert not re.search(r"\b(let|const|class|async|await)\b|=>|`", re.sub(r"//.*", "", script))


def test_the_gateway_server_routes_as_the_contract_says(conf, parsed, settings):
    manager = f"127.0.0.1:{settings.admin_port}"
    server = next(body for header, body in parsed if header == "server" and "default_server" in body)
    own = directives(server)
    assert f"listen {settings.gateway_port} default_server" in own
    assert "client_max_body_size 100m" in own
    assert any(item.startswith("access_log syslog:server=unix:") and item.endswith("efp_access if=$efp_app") for item in own)

    health = directives(block(parsed, "location = /_efp/health"))
    assert 'return 200 "ok"' in health and "access_log off" in health

    for path in ("/_efp/callback", "/_efp/logout"):
        assert f"proxy_pass http://{manager}" in directives(block(parsed, f"location = {path}"))
    assert directives(block(parsed, "location ^~ /_efp/")) == ["return 404"]

    redirect = directives(block(parsed, "location ~ ^/(?<efp_slug>[a-z0-9][a-z0-9-]*[a-z0-9])$"))
    assert redirect == ["return 301 /$efp_slug/$is_args$args"]

    auth = directives(block(parsed, "location = /_efp_internal/auth"))
    assert "internal" in auth
    assert f"proxy_pass http://{manager}/_efp/auth" in auth
    for expected in (
        "proxy_pass_request_body off",
        "proxy_pass_request_headers off",
        'proxy_set_header Content-Length ""',
        "proxy_set_header X-EFP-Original-URI $request_uri",
        "proxy_set_header X-EFP-Method $request_method",
        "proxy_set_header X-EFP-Source $efp_client_addr",
        "proxy_set_header Cookie $http_cookie",
        "proxy_set_header Accept $http_accept",
        "proxy_set_header Sec-Fetch-Mode $http_sec_fetch_mode",
    ):
        assert expected in auth, expected


def test_the_app_location_does_what_section_6_2_asks(parsed):
    app = directives(block(parsed, "location ~ ^/(?<efp_slug>[a-z0-9][a-z0-9-]*[a-z0-9])/"))
    for expected in (
        "auth_request /_efp_internal/auth",
        "error_page 401 403 500 502 504 = @efp_deny",
        "proxy_pass http://$efp_upstream$efp_uri",
        "proxy_buffering off",
        "proxy_read_timeout 3600s",
        "proxy_send_timeout 3600s",
        "proxy_set_header Upgrade $http_upgrade",
        "proxy_set_header Connection $efp_connection",
        "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for",
        "proxy_set_header X-Forwarded-Proto $efp_forwarded_proto",
        "proxy_set_header X-Forwarded-Host $efp_public_host",
        "proxy_set_header X-Forwarded-Prefix /$efp_app_slug",
        "proxy_set_header Cookie $efp_cookie",
        "proxy_set_header X-EFP-Visitor $efp_visitor",
        "js_header_filter efp.filterResponseHeaders",
        # No header of an app's answer redirects the request inside nginx.
        "proxy_ignore_headers X-Accel-Redirect X-Accel-Expires X-Accel-Limit-Rate X-Accel-Buffering X-Accel-Charset",
        "proxy_hide_header NEL",
        "proxy_hide_header Report-To",
        "proxy_hide_header Reporting-Endpoints",
        "proxy_hide_header Clear-Site-Data",
        "proxy_hide_header Service-Worker-Allowed",
        "add_header X-Content-Type-Options nosniff always",
        "add_header X-Robots-Tag noindex always",
        "add_header Referrer-Policy same-origin always",
        'add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), display-capture=()" always',
    ):
        assert expected in app, expected

    # What the access check answers is what the location reads.
    answered = {item.split()[2] for item in app if item.startswith("auth_request_set ")}
    assert answered == {
        "$upstream_http_x_efp_app",
        "$upstream_http_x_efp_slug",
        "$upstream_http_x_efp_upstream",
        "$upstream_http_x_efp_upstream_uri",
        "$upstream_http_x_efp_cookie",
        "$upstream_http_x_efp_visitor",
        "$upstream_http_x_efp_set_cookie",
        "$upstream_http_x_efp_user",
        "$upstream_http_x_efp_static_root",
        "$upstream_http_x_efp_reason",
        "$upstream_http_x_efp_detail",
    }


def test_the_refusal_route_is_given_the_cookies(parsed):
    # It starts the sign-in, and uses the state of one another tab has already started.
    assert "proxy_set_header Cookie $http_cookie" in directives(block(parsed, "location @efp_deny"))


def test_the_headers_nginx_reads_are_the_ones_the_manager_sends(conf, parsed, gateway_server, settings):
    from efp_app_host.gateway import routes

    app = directives(block(parsed, "location ~ ^/(?<efp_slug>[a-z0-9][a-z0-9-]*[a-z0-9])/"))
    read = {item.split()[2] for item in app if item.startswith("auth_request_set ")}
    sent = [
        routes.APP_HEADER, routes.SLUG_HEADER, routes.UPSTREAM_HEADER, routes.UPSTREAM_URI_HEADER, routes.COOKIE_HEADER,
        routes.VISITOR_HEADER, routes.SET_COOKIE_HEADER, routes.USER_HEADER, routes.STATIC_ROOT_HEADER,
        routes.REASON_HEADER, routes.DETAIL_HEADER,
    ]  # fmt: skip
    assert read == {"$upstream_http_" + name.lower().replace("-", "_") for name in sent}

    deny = directives(block(parsed, "location @efp_deny"))
    for name, value in (
        (routes.REASON_HEADER, "$efp_reason"),
        (routes.DETAIL_HEADER, "$efp_detail"),
        (routes.ORIGINAL_URI_HEADER, "$request_uri"),
        ("Accept", "$http_accept"),
        ("Sec-Fetch-Mode", "$http_sec_fetch_mode"),
        ("Accept-Language", "$http_accept_language"),
    ):
        assert f"proxy_set_header {name} {value}" in deny
    assert "proxy_method GET" in deny and "proxy_pass_request_body off" in deny and "proxy_pass_request_headers off" in deny
    # The root is the app center: the manager's own route, with the visitor's cookies and no reason.
    home = directives(block(gateway_server, "location = /"))
    assert "proxy_pass http://127.0.0.1:%d$efp_home_uri" % settings.admin_port in home
    assert "set $efp_home_uri /_efp/home;" in conf
    assert "proxy_set_header Cookie $http_cookie" in home and "proxy_pass_request_headers off" in home
    assert f"proxy_set_header {routes.ORIGINAL_URI_HEADER} $request_uri" in home
    assert not [item for item in home if routes.REASON_HEADER in item]
    # Its icons: the manager again, which gets the cookies and nothing else of the request.
    icons = directives(block(gateway_server, "location ^~ /_efp/icon/"))
    assert "proxy_pass http://127.0.0.1:%d" % settings.admin_port in icons
    assert "proxy_set_header Cookie $http_cookie" in icons and "proxy_pass_request_headers off" in icons
    assert f"proxy_set_header {routes.REASON_HEADER} app_not_found" in directives(block(gateway_server, "location /"))


def test_the_secret_only_goes_to_the_manager_and_the_static_server(conf, parsed, settings):
    secret = (settings.run_dir / "gateway.secret").read_text(encoding="utf-8").strip()
    manager = f"proxy_pass http://127.0.0.1:{settings.admin_port}"
    holders = []
    for header, body in parsed:
        own = directives(body)
        if secret in header or any(secret in item for item in own):
            holders.append(header)
        if header.startswith("location") and any(secret in item for item in own):
            assert any(item.startswith(manager) for item in own), header
            assert f'proxy_set_header X-EFP-Gateway-Secret "{secret}"' in own
    assert sorted(holders) == sorted(
        [
            "location = /_efp_internal/auth",
            "location ^~ /_efp/icon/",
            "location @efp_deny",
            "location = /",
            "location /",
            "map $efp_static_root $efp_static_secret",
            f'if ($http_x_efp_gateway_secret != "{secret}")',  # the static server compares it
        ]
    )
    assert conf.count(secret) == len(holders)

    # An app's own process never gets it: in the app location the header is
    # a variable that is empty unless the access check named a static directory.
    app = directives(block(parsed, "location ~ ^/(?<efp_slug>[a-z0-9][a-z0-9-]*[a-z0-9])/"))
    assert "proxy_set_header X-EFP-Gateway-Secret $efp_static_secret" in app
    assert "proxy_set_header X-EFP-Static-Root $efp_static_root" in app
    assert "proxy_set_header Host $efp_upstream_host" in app
    secret_map = directives(block(parsed, "map $efp_static_root $efp_static_secret"))
    assert secret_map == ["volatile", f'default "{secret}"', '"" ""']
    host_map = directives(block(parsed, "map $efp_static_root $efp_upstream_host"))
    assert host_map == ["volatile", f"default {STATIC_HOST}", '"" $efp_public_host']

    # The callback and logout locations pass a browser's request on; they
    # must not add it either.
    for path in ("/_efp/callback", "/_efp/logout"):
        assert not any("Gateway-Secret" in item for item in directives(block(parsed, f"location = {path}")))


def test_the_static_server_is_locked_and_cannot_leave_its_directory(parsed, settings):
    secret = (settings.run_dir / "gateway.secret").read_text(encoding="utf-8").strip()
    server = next(body for header, body in parsed if header == "server" and "default_server" not in body)
    own = directives(server)
    assert f"listen {settings.gateway_port}" in own
    assert f"server_name {STATIC_HOST}" in own
    assert "access_log off" in own
    assert own.index("allow 127.0.0.1") < own.index("deny all")
    assert "disable_symlinks on from=$http_x_efp_static_root" in own
    assert directives(block(parsed, f'if ($http_x_efp_gateway_secret != "{secret}")')) == ["return 404"]
    assert directives(block(parsed, 'if ($http_x_efp_static_root = "")')) == ["return 404"]

    files = directives(block(parsed, "location ~ ^/[^/]+(?<efp_static_path>/.*)$"))
    assert "alias $http_x_efp_static_root$efp_static_path" in files
    # The decoded path never reaches a header or a redirect of ours.
    assert not any("$uri" in item for _, body in parsed for item in directives(body))
    assert directives(block(parsed, "location ~ /\\.")) == ["return 404"]


def test_client_headers_named_x_efp_are_refused(parsed):
    assert directives(block(parsed, "if ($efp_reserved_header)")) == ["return 400"]
    http = directives(block(parsed, "http"))
    assert "js_set $efp_reserved_header efp.reservedRequestHeader" in http
    assert "proxy_http_version 1.1" in http
    assert "absolute_redirect off" in http


def test_the_access_log_line_is_what_the_manager_parses(conf, settings):
    """Fill nginx's variables by hand and feed the line to the collector."""

    import json

    from efp_app_host.gateway.metrics import TrafficStore

    parts = re.findall(r"^\s+'(.*)'(?:;)?$", conf[conf.index("log_format efp_access") :].split(";")[0] + ";", re.M)
    template = "".join(parts)
    assert template.startswith("{") and template.endswith("}")
    values = {
        "msec": "1763195400.123",
        "efp_app": "app-1",
        "efp_app_slug": "mindmap",
        "status": "200",
        "request_time": "0.012",
        "upstream_header_time": "0.010",
        "efp_user": "12",
        "bytes_sent": "5120",
    }
    line = re.sub(r"\$([a-z_]+)", lambda match: values[match.group(1)], template)
    entry = json.loads(line)
    assert set(entry) == {"k", "t", "app", "slug", "status", "rt", "uht", "user", "bytes"}
    assert "request_uri" not in template and "cookie" not in template and "http_" not in template

    secret = (settings.run_dir / "gateway.secret").read_text(encoding="utf-8").strip()
    traffic = TrafficStore(None, log_key=access_log_key(secret), clock=lambda: 1763195400.0)
    assert traffic.ingest(f"<190>Nov 15 08:30:00 efp_gateway: {line}") is True
    row = traffic.traffic()[0]
    assert (row["app_id"], row["requests"], row["visitors"], row["p95_ms"]) == ("app-1", 1, 1, 10)

    # A request the gateway refused has neither app nor user: nginx leaves it out.
    refused = dict(values, efp_app="", efp_user="", upstream_header_time="")
    assert traffic.ingest(re.sub(r"\$([a-z_]+)", lambda match: refused[match.group(1)], template)) is False
