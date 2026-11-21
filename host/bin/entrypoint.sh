#!/bin/sh
# Entry point of the app host container (run under tini).
#
# 1. checks the settings and creates the directories;
# 2. closes or opens the directory above the volume, by isolation mode;
# 3. renders the gateway's nginx.conf (it holds the gateway secret: root only);
# 4. hands over to supervisord, which runs nginx, the manager and one program
#    per app.
set -eu

export EFP_HOST_RUN_DIR="${EFP_HOST_RUN_DIR:-/run/efp-host}"
export EFP_HOST_STORE_DIR="${EFP_HOST_STORE_DIR:-/var/lib/efp-host/store}"
export EFP_HOST_RUNTIME_DIR="${EFP_HOST_RUNTIME_DIR:-/srv/efp-apps}"
export EFP_HOST_ISOLATION_MODE="${EFP_HOST_ISOLATION_MODE:-sandbox}"
export EFP_HOST_RESOURCE_DIR="${EFP_HOST_RESOURCE_DIR:-/opt/efp}"

log() {
    echo "[efp-host entrypoint] $*"
}

fail() {
    echo "[efp-host entrypoint] ERROR: $*" >&2
    exit 1
}

[ "$(id -u)" = "0" ] || fail "the app host must start as root (it gives each app its own user); see docs/OPERATIONS.md"
[ -n "${EFP_HOST_API_TOKEN:-}" ] || fail "EFP_HOST_API_TOKEN is not set"
for name in EFP_PORTAL_INTERNAL_URL EFP_PORTAL_PUBLIC_URL EFP_APPS_PUBLIC_URL; do
    eval "value=\${$name:-}"
    [ -n "$value" ] || fail "$name is not set"
done
case "$EFP_HOST_ISOLATION_MODE" in
    sandbox | hidden) ;;
    *) fail "EFP_HOST_ISOLATION_MODE must be sandbox or hidden, got '$EFP_HOST_ISOLATION_MODE'" ;;
esac

# Sockets and generated files of a previous run of this container.
mkdir -p "$EFP_HOST_RUN_DIR"
rm -rf "$EFP_HOST_RUN_DIR/supervisor.d" "$EFP_HOST_RUN_DIR/launch"
rm -f "$EFP_HOST_RUN_DIR"/*.sock "$EFP_HOST_RUN_DIR"/*.pid
mkdir -p "$EFP_HOST_RUN_DIR/supervisor.d" "$EFP_HOST_RUN_DIR/launch" "$EFP_HOST_STORE_DIR" "$EFP_HOST_RUNTIME_DIR"
chmod 0755 "$EFP_HOST_RUN_DIR" "$EFP_HOST_RUN_DIR/supervisor.d" "$EFP_HOST_RUNTIME_DIR"
chmod 0700 "$EFP_HOST_RUN_DIR/launch"

# The directory above the volume decides who can walk into it.
#   hidden : root only. Apps never see the volume.
#   sandbox: open. The gateway serves static apps from the volume and the
#            sandbox shows each app its own directory at its real path.
store_parent="$(dirname "$EFP_HOST_STORE_DIR")"
if [ "$store_parent" = "/" ]; then
    [ "$EFP_HOST_ISOLATION_MODE" = "sandbox" ] || fail "EFP_HOST_STORE_DIR must not sit directly below / in hidden mode"
elif [ "$EFP_HOST_ISOLATION_MODE" = "hidden" ]; then
    chown root:root "$store_parent"
    chmod 0700 "$store_parent"
else
    chmod 0755 "$store_parent"
fi

# nginx.conf holds the gateway secret.
umask 077
python3 -m efp_app_host.gateway.render_nginx > /etc/nginx/nginx.conf.new || fail "the gateway configuration could not be rendered"
mv /etc/nginx/nginx.conf.new /etc/nginx/nginx.conf
umask 022
nginx -t -q || fail "nginx rejects the rendered configuration"

log "isolation mode: $EFP_HOST_ISOLATION_MODE (backend: ${EFP_HOST_SANDBOX_BACKEND:-auto})"
log "volume: $EFP_HOST_STORE_DIR, local disk: $EFP_HOST_RUNTIME_DIR"
exec /usr/local/bin/supervisord -n -c /etc/supervisor/supervisord.conf
