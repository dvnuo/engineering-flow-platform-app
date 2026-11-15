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
#     ci.sh image-tests             the Linux-only unit tests, as root inside the image
#     ci.sh clean                   remove containers, network and volume
#
# Variants (isolation mode / privileges):
#     hidden            hidden mode, Docker's defaults
#     sandbox           sandbox mode, bubblewrap as root: SYS_ADMIN, no seccomp or AppArmor profile
#     hidden-k8s        hidden mode with exactly the capabilities of deploy/k8s/deployment-hidden.yaml
#     sandbox-k8s       sandbox mode with exactly the capabilities of deploy/k8s/deployment-sandbox.yaml
#     sandbox-landlock  sandbox mode, Landlock backend, the capabilities of deployment-hidden.yaml
#     sandbox-userns    sandbox mode, bubblewrap in a user namespace, no SYS_ADMIN
#
# The two "-k8s" variants and the last two need PyYAML on this machine (pip install pyyaml).
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
IMAGE="${IMAGE:-efp-app-host:ci}"
TOKEN="${EFP_HOST_API_TOKEN:-ci-token}"
NETWORK="efp-smoke"
VOLUME="efp-smoke-store"
STATE="${SMOKE_STATE:-$ROOT/smoke-state.json}"
PYTHON="${PYTHON:-python3}"

k8s_flags() {
    "$PYTHON" "$ROOT/deploy/check_manifests.py" docker-flags "$ROOT/deploy/k8s/$1"
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
            FLAGS="--cap-add SYS_ADMIN --security-opt seccomp=unconfined --security-opt apparmor=unconfined"
            ;;
        hidden-k8s)
            MODE=hidden
            FLAGS="$(k8s_flags deployment-hidden.yaml)"
            ;;
        sandbox-k8s)
            MODE=sandbox
            BACKEND=bwrap
            FLAGS="$(k8s_flags deployment-sandbox.yaml) --security-opt apparmor=unconfined"
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

# Landlock and user namespaces depend on the kernel and its settings: a
# machine without them cannot run those two variants, which is not a defect
# of the host. Everything else must work everywhere.
available() {
    variant "$1"
    case "$1" in
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
    docker run -d --name efp-portal --network "$NETWORK" --network-alias portal.test -p 18000:8000 \
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
    "$PYTHON" "$HERE/smoke.py" --phase "$1" --mode "$MODE" --expect-backend "$BACKEND" \
        --token "$TOKEN" --templates "$ROOT/templates" --state "$STATE"
}

group() {
    # Folds the output in the GitHub Actions log; plain text elsewhere.
    echo "::group::$1"
}

diagnostics() {
    group "containers"
    docker ps -a || true
    echo "::endgroup::"
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
    docker run --rm --init \
        --cap-add SYS_ADMIN --security-opt seccomp=unconfined --security-opt apparmor=unconfined \
        -e EFP_TEST_REQUIRE_BWRAP=1 -e PYTHONDONTWRITEBYTECODE=1 \
        -v "$ROOT/host:/src:ro" -w /src --entrypoint sh "$IMAGE" -c \
        'pip install --no-cache-dir --quiet --root-user-action=ignore pytest && python3 -m pytest -rs -p no:cacheprovider tests/test_launcher_linux.py tests/test_controller.py tests/test_bundle.py tests/test_boot.py tests/test_deploy.py tests/test_runtime.py tests/test_isolation.py tests/test_small_parts.py'
}

clean() {
    docker rm -f efp-host efp-portal >/dev/null 2>&1 || true
    docker volume rm "$VOLUME" >/dev/null 2>&1 || true
    docker network rm "$NETWORK" >/dev/null 2>&1 || true
    rm -f "$STATE"
}

command="${1:-}"
case "$command" in
    available) available "$2" ;;
    start-portal) start_portal ;;
    start-host) start_host "$2" ;;
    recreate-host)
        echo "removing the host container; its own disk is lost, the volume stays"
        start_host "$2"
        ;;
    smoke) smoke "$2" "$3" ;;
    diagnostics) diagnostics ;;
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
        sed -n '2,24p' "$0"
        exit 2
        ;;
esac
