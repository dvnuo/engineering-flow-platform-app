// njs helpers of the EFP app gateway (docs/CONTRACTS.md, section 6.2).
//
// Loaded by nginx.conf.tmpl with js_import. Written for njs 0.7 (Debian
// bookworm's libnginx-mod-http-js): plain functions, "var", no newer syntax.

// Cookie names (lower case) that belong to the gateway. An app may not set them.
var RESERVED_COOKIE_PREFIXES = ['__host-efp', '__secure-efp'];

// js_set $efp_reserved_header: "1" when the client sent a header whose name
// starts with "X-EFP-". nginx cannot remove request headers by pattern, so
// the configuration refuses such a request.
function reservedRequestHeader(r) {
    var headers = r.rawHeadersIn;
    var i, name;
    if (headers) {
        for (i = 0; i < headers.length; i++) {
            if (String(headers[i][0]).toLowerCase().indexOf('x-efp-') === 0) {
                return '1';
            }
        }
        return '';
    }
    for (name in r.headersIn) {
        if (String(name).toLowerCase().indexOf('x-efp-') === 0) {
            return '1';
        }
    }
    return '';
}

// One Set-Cookie value of an app -> the value the browser gets, or null when
// the cookie is dropped.
//   - no name, or a reserved name: dropped;
//   - a Domain attribute: dropped (the cookie would reach other hosts);
//   - any Path is replaced by "/{slug}/", so the cookie stays with this app.
function rewriteSetCookie(line, slug) {
    var parts = String(line).split(';');
    var pair = parts[0].trim();
    var equals = pair.indexOf('=');
    var name, kept, i, attribute, attributeName;

    if (equals <= 0) {
        return null;
    }
    name = pair.substring(0, equals).trim().toLowerCase();
    if (name === '') {
        return null;
    }
    for (i = 0; i < RESERVED_COOKIE_PREFIXES.length; i++) {
        if (name.indexOf(RESERVED_COOKIE_PREFIXES[i]) === 0) {
            return null;
        }
    }

    kept = [pair];
    for (i = 1; i < parts.length; i++) {
        attribute = parts[i].trim();
        if (attribute === '') {
            continue;
        }
        attributeName = attribute.split('=')[0].trim().toLowerCase();
        if (attributeName === 'domain') {
            return null;
        }
        if (attributeName === 'path') {
            continue;
        }
        kept.push(attribute);
    }
    kept.push('Path=/' + slug + '/');
    return kept.join('; ');
}

// js_header_filter of the app location. Reads two variables the access check
// set: $efp_app_slug (the app that answers) and $efp_set_cookie (a renewed
// gateway session cookie, usually empty).
function filterResponseHeaders(r) {
    var result = [];
    var slug, cookies, renewed, i, rewritten;

    try {
        delete r.headersOut['Service-Worker-Allowed'];
    } catch (e) {
        // proxy_hide_header in the configuration removes it as well
    }

    try {
        slug = r.variables.efp_app_slug;
        cookies = r.headersOut['Set-Cookie'];
        if (typeof cookies === 'string') {
            cookies = [cookies];
        }
        if (cookies && slug) {
            for (i = 0; i < cookies.length; i++) {
                rewritten = rewriteSetCookie(cookies[i], slug);
                if (rewritten !== null) {
                    result.push(rewritten);
                }
            }
        }
        renewed = r.variables.efp_set_cookie;
        if (renewed) {
            result.push(renewed);
        }
        if ((cookies && cookies.length) || result.length) {
            r.headersOut['Set-Cookie'] = result;
        }
    } catch (e) {
        // Never let an app's cookie through unchecked.
        try {
            r.headersOut['Set-Cookie'] = [];
        } catch (ignored) {
            // nothing more can be done here
        }
        r.error('efp gateway: the response cookies could not be filtered: ' + e.message);
    }
}

export default {
    reservedRequestHeader: reservedRequestHeader,
    rewriteSetCookie: rewriteSetCookie,
    filterResponseHeaders: filterResponseHeaders
};
