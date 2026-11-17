#!/bin/sh
# The container smoke test: starts the image and a stub Portal, runs smoke.py.
#
#     ci.sh all VARIANT             everything below, in order (for a Linux machine with Docker)
#     ci.sh available VARIANT       prints available=true|false: can this machine run the variant?
#     ci.sh start-portal            the stub Portal, on a Docker network of its own
#     ci.sh start-host VARIANT      the app host with the privileges of the variant
#     ci.sh smoke PHASE VARIANT     smoke.py (PHASE: first | after-recreate)
#     ci.sh recreate-host VARIANT   a new container on the same volume (the local disk is lost)
#     ci.sh diagnostics             everything worth reading after a failure
#     ci.sh host-info               what this machine is: kernel, Docker, AppArmor and what it denied
#     ci.sh image-tests             the Linux-only unit tests, as root inside the image
#     ci.sh clean                   remove containers, network and volume
#
# Variants (isolation mode / privileges):
#     hidden            hidden mode, Docker's defaults
#     sandbox           sandbox mode, bubblewrap as root: SYS_ADMIN, no seccomp profile
#     hidden-k8s        hidden mode with exactly the capabilities of k8s/deployment-hidden.yaml
#     sandbox-k8s       sandbox mode with exactly the capabilities of k8s/deployment-sandbox.yaml
#     sandbox-landlock  sandbox mode, Landlock backend, the capabilities of deployment-hidden.yaml
#     sandbox-userns    sandbox mode, bubblewrap in a user namespace, no SYS_ADMIN
#
# The two "-k8s" variants and the last two need PyYAML on this machine (pip install pyyaml).
#
# AppArmor: the variants that run bubblewrap as root start the container under
# the profile k8s/apparmor/efp-app-host (loaded here with sudo) instead of
# "unconfined". The comment in that file says why an unconfined container
# cannot use that backend on Ubuntu 24.04 and later.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
IMAGE="${IMAGE:-efp-app-host:ci}"
TOKEN="${EFP_HOST_API_TOKEN:-ci-token}"
NETWORK="efp-smoke"
VOLUME="efp-smoke-store"
STATE="${SMOKE_STATE:-$ROOT/smoke-state.json}"
FIRST_LOG="${SMOKE_FIRST_LOG:-$ROOT/smoke-first-container.log}"
PYTHON="${PYTHON:-python3}"

k8s_flags() {
    "$PYTHON" "$ROOT/scripts/check_manifests.py" docker-flags "$ROOT/k8s/$1"
}

# The docker option for the container's AppArmor profile, for the variants
# that run bubblewrap as root. Loads k8s/apparmor/efp-app-host where
# AppArmor is in use and the profile can be loaded; "unconfined" otherwise.
apparmor_option() {
    if [ "$(cat /sys/module/apparmor/parameters/enabled 2>/dev/null)" = "Y" ] && command -v apparmor_parser >/dev/null 2>&1; then
        if sudo -n apparmor_parser --replace "$ROOT/k8s/apparmor/efp-app-host" >&2; then
            echo "--security-opt apparmor=efp-app-host"
            return
        fi
        echo "the AppArmor profile k8s/apparmor/efp-app-host could not be loaded; the container runs unconfined" >&2
    fi
    echo "--security-opt apparmor=unconfined"
}

# Can bubblewrap, run as root in a container with these flags, start
# something that drops to another user? The answer is the exit code; what
# was seen goes to stderr.
bwrap_root_works() {
    # shellcheck disable=SC2086
    docker run --rm $1 -v "$HERE:/it:ro" --entrypoint sh "$IMAGE" /it/check_bwrap_root.sh >&2
}

# Sets MODE, BACKEND (what the host must choose, empty for hidden) and FLAGS.
variant() {
    BACKEND=""
    EXTRA_ENV=""
    case "$1" in
        hidden)
            MODE=hidden
            FLAGS=""
            ;;
        sandbox)
            MODE=sandbox
            BACKEND=bwrap
            FLAGS="--cap-add SYS_ADMIN --security-opt seccomp=unconfined $(apparmor_option)"
            ;;
        hidden-k8s)
            MODE=hidden
            FLAGS="$(k8s_flags deployment-hidden.yaml)"
            ;;
        sandbox-k8s)
            MODE=sandbox
            BACKEND=bwrap
            FLAGS="$(k8s_flags deployment-sandbox.yaml) $(apparmor_option)"
            ;;
        sandbox-landlock)
            MODE=sandbox
            BACKEND=landlock
            FLAGS="$(k8s_flags deployment-hidden.yaml)"
            EXTRA_ENV="-e EFP_HOST_SANDBOX_BACKEND=landlock"
            ;;
        sandbox-userns)
            MODE=sandbox
            BACKEND=bwrap-userns
            FLAGS="$(k8s_flags deployment-hidden.yaml) --security-opt seccomp=unconfined --security-opt apparmor=unconfined"
            EXTRA_ENV="-e EFP_HOST_SANDBOX_BACKEND=bwrap-userns"
            ;;
        *)
            echo "unknown variant '$1'" >&2
            exit 2
            ;;
    esac
}

# Landlock, user namespaces and bubblewrap as root depend on the kernel and
# on the machine's security policy: a machine that cannot do one of them
# cannot run that variant, which is not a defect of the host. Hidden mode
# must work everywhere.
available() {
    variant "$1"
    case "$1" in
        sandbox | sandbox-k8s)
            if bwrap_root_works "$FLAGS"; then
                echo "available=true"
            else
                echo "this machine does not let bubblewrap run as root in a container and then drop to another user: the variant cannot run here" >&2
                host_info >&2
                echo "available=false"
            fi
            ;;
        sandbox-landlock)
            # shellcheck disable=SC2086
            abi="$(docker run --rm $FLAGS --entrypoint python3 "$IMAGE" -c 'from efp_app_host import landlock; print(landlock.abi_version())' 2>/dev/null || echo 0)"
            if [ "${abi:-0}" -gt 0 ] 2>/dev/null; then
                echo "Landlock ABI $abi" >&2
                echo "available=true"
            else
                echo "this machine's kernel has no usable Landlock: the variant cannot run here" >&2
                echo "available=false"
            fi
            ;;
        sandbox-userns)
            # shellcheck disable=SC2086
            if docker run --rm $FLAGS --entrypoint sh "$IMAGE" -c 'setpriv --reuid 19999 --regid 19999 --clear-groups bwrap --unshare-user --ro-bind / / true' >&2; then
                echo "available=true"
            else
                echo "this machine does not allow unprivileged user namespaces in containers: the variant cannot run here" >&2
                echo "available=false"
            fi
            ;;
        *)
            echo "available=true"
            ;;
    esac
}

start_portal() {
    docker network create "$NETWORK" >/dev/null 2>&1 || true
    docker rm -f efp-portal >/dev/null 2>&1 || true
    docker run -d --name efp-portal --no-healthcheck --network "$NETWORK" --network-alias portal.test -p 18000:8000 \
        -v "$HERE:/it:ro" --entrypoint python3 "$IMAGE" \
        /it/stub_portal.py --port 8000 --token "$TOKEN" --apps /it/seed_apps.json >/dev/null
    echo "stub Portal started (http://127.0.0.1:18000, in the network: http://portal.test:8000)"
}

start_host() {
    variant "$1"
    docker rm -f efp-host >/dev/null 2>&1 || true
    echo "starting the host: mode $MODE, flags: ${FLAGS:-(Docker's defaults)} $EXTRA_ENV"
    # shellcheck disable=SC2086
    docker run -d --name efp-host --network "$NETWORK" -p 8080:8080 -p 9000:9000 \
        -e EFP_HOST_API_TOKEN="$TOKEN" \
        -e EFP_PORTAL_INTERNAL_URL=http://portal.test:8000 \
        -e EFP_PORTAL_PUBLIC_URL=http://portal.test \
        -e EFP_APPS_PUBLIC_URL=http://127.0.0.1:8080 \
        -e EFP_HOST_ISOLATION_MODE="$MODE" \
        -e EFP_HOST_ANON_RATE_PER_MINUTE=100000 \
        -e EFP_HOST_METRICS_PUSH_SECONDS=5 \
        $EXTRA_ENV \
        -v "$VOLUME:/var/lib/efp-host/store" \
        $FLAGS "$IMAGE" >/dev/null
}

smoke() {
    variant "$2"
    result=0
    "$PYTHON" "$HERE/smoke.py" --phase "$1" --mode "$MODE" --expect-backend "$BACKEND" \
        --token "$TOKEN" --templates "$ROOT/templates" --examples "$ROOT/examples" --state "$STATE" || result=1
    if [ "$1" = "first" ]; then
        # The probe app was told to write "planted" files where no app may
        # write, and reported that it could not. Seen from outside every
        # sandbox: none of them exists, neither on the volume nor in the container.
        planted="$(docker exec efp-host sh -c 'find /var/lib/efp-host/store /srv/efp-apps /etc /run /var /dev -xdev -name "planted*" 2>/dev/null; ls /planted 2>/dev/null' || true)"
        if [ -n "$planted" ]; then
            echo "  FAIL  an app wrote where it must not; found in the container:"
            echo "$planted" | sed 's/^/        /'
            result=1
        else
            echo "  ok    nothing was planted on the volume or in the container (seen from outside the apps)"
        fi
    fi
    return "$result"
}

group() {
    # Folds the output in the GitHub Actions log; plain text elsewhere.
    echo "::group::$1"
}

# What this machine is. The isolation backends depend on it.
host_info() {
    echo "kernel: $(uname -r)"
    echo "docker: $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo unknown)"
    echo "AppArmor enabled: $(cat /sys/module/apparmor/parameters/enabled 2>/dev/null || echo no)"
    echo "kernel.apparmor_restrict_unprivileged_userns: $(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null || echo 'not present')"
    echo "kernel.unprivileged_userns_clone: $(cat /proc/sys/kernel/unprivileged_userns_clone 2>/dev/null || echo 'not present')"
    echo "LSMs: $(cat /sys/kernel/security/lsm 2>/dev/null || echo unknown)"
    echo "AppArmor profiles that name bwrap or the app host:"
    (sudo -n aa-status 2>/dev/null || true) | grep -i -E "bwrap|efp-app-host" | sed 's/^/    /' || true
    ls /etc/apparmor.d 2>/dev/null | grep -i bwrap | sed 's|^|    /etc/apparmor.d/|' || true
    echo "what AppArmor denied lately:"
    (sudo -n dmesg 2>/dev/null || true) | grep 'apparmor="DENIED"' | tail -n 25 | sed 's/^/    /' || true
}

diagnostics() {
    group "this machine"
    host_info || true
    echo "::endgroup::"
    group "containers"
    docker ps -a || true
    echo "::endgroup::"
    if [ -s "$FIRST_LOG" ]; then
        group "the log of the first host container (before it was replaced)"
        tail -n 600 "$FIRST_LOG" || true
        echo "::endgroup::"
    fi
    group "the host container's log (entrypoint, supervisord, nginx, manager)"
    docker logs --tail 800 efp-host 2>&1 || true
    echo "::endgroup::"
    group "GET /v1/host"
    curl -s -m 10 -H "Authorization: Bearer $TOKEN" http://127.0.0.1:9000/v1/host || true
    echo
    echo "::endgroup::"
    group "GET /v1/apps"
    curl -s -m 10 -H "Authorization: Bearer $TOKEN" http://127.0.0.1:9000/v1/apps || true
    echo
    echo "::endgroup::"
    group "processes in the host container"
    docker top efp-host -eo user,pid,ppid,rss,args 2>&1 || true
    echo "::endgroup::"
    group "logs of the apps"
    docker exec efp-host sh -c 'for f in /var/lib/efp-host/store/apps/*/logs/app.log; do echo "----- $f"; tail -n 120 "$f"; done' 2>&1 || true
    echo "::endgroup::"
    group "logs of the deployments"
    docker exec efp-host sh -c 'for f in /var/lib/efp-host/store/jobs/*.log; do echo "----- $f"; tail -n 150 "$f"; done' 2>&1 || true
    echo "::endgroup::"
    group "the volume and the local disk"
    docker exec efp-host sh -c 'ls -la /var/lib/efp-host /var/lib/efp-host/store /var/lib/efp-host/store/apps /srv/efp-apps /run/efp-host; ls -la /srv/efp-apps/*/ 2>/dev/null | head -n 80' 2>&1 || true
    echo "::endgroup::"
    group "the stub Portal's log"
    docker logs --tail 300 efp-portal 2>&1 || true
    echo "::endgroup::"
}

image_tests() {
    # As root, with what the sandbox variant is given: dropping to another
    # user, bubblewrap, the probe of both modes, a real supervisord.
    flags="--cap-add SYS_ADMIN --security-opt seccomp=unconfined $(apparmor_option)"
    require=1
    if ! bwrap_root_works "$flags"; then
        # The tests of bubblewrap as root skip themselves then; say so loudly.
        require=0
        host_info >&2
        echo "::warning title=bubblewrap as root not tested::this machine does not let bubblewrap run as root in a container and then drop to another user; the tests of the bwrap backend were skipped"
    fi
    echo "container flags: $flags (bubblewrap as root must work: $require)"
    # shellcheck disable=SC2086
    docker run --rm --init \
        $flags \
        -e EFP_TEST_REQUIRE_BWRAP="$require" -e PYTHONDONTWRITEBYTECODE=1 \
        -v "$ROOT/host:/src:ro" -w /src --entrypoint sh "$IMAGE" -c \
        'pip install --no-cache-dir --quiet --root-user-action=ignore pytest && python3 -m pytest -rs -p no:cacheprovider tests/test_launcher_linux.py tests/test_controller.py tests/test_bundle.py tests/test_boot.py tests/test_deploy.py tests/test_runtime.py tests/test_isolation.py tests/test_small_parts.py'
}

clean() {
    docker rm -f efp-host efp-portal >/dev/null 2>&1 || true
    docker volume rm "$VOLUME" >/dev/null 2>&1 || true
    docker network rm "$NETWORK" >/dev/null 2>&1 || true
    rm -f "$STATE" "$FIRST_LOG"
}

command="${1:-}"
case "$command" in
    available) available "$2" ;;
    start-portal) start_portal ;;
    start-host) start_host "$2" ;;
    recreate-host)
        echo "removing the host container; its own disk is lost, the volume stays"
        # Its log goes with it: keep it for "diagnostics".
        docker logs efp-host >"$FIRST_LOG" 2>&1 || true
        start_host "$2"
        ;;
    smoke) smoke "$2" "$3" ;;
    diagnostics) diagnostics ;;
    host-info) host_info ;;
    image-tests) image_tests ;;
    clean) clean ;;
    all)
        clean
        status=0
        start_portal
        start_host "$2"
        smoke first "$2" || status=1
        if [ "$status" = "0" ]; then
            start_host "$2"
            smoke after-recreate "$2" || status=1
        fi
        [ "$status" = "0" ] || diagnostics
        exit "$status"
        ;;
    *)
        sed -n '2,30p' "$0"
        exit 2
        ;;
esac
