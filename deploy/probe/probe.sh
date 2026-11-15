#!/bin/sh
# Runs inside one container of the probe pod (deploy/probe/pod.yaml):
#
#     probe.sh NAME
#
# Each container of that pod has other privileges. This script first tries
# the bare kernel features by hand (informative: it shows which one is
# missing) and then runs the host's own probe, the one the manager runs at
# boot, on the mounted volume (authoritative).
name="${1:-container}"
store="${EFP_HOST_STORE_DIR:-/var/lib/efp-host/store}"

echo "================ $name ================"
echo "kernel:        $(uname -r)"
echo "user:          uid $(id -u)"
echo "capabilities:  $(grep '^CapEff' /proc/self/status | tr -s '\t ' ' ')"
echo "seccomp:       $(grep '^Seccomp:' /proc/self/status | tr -s '\t ' ' ') (0 = off, 2 = filter)"
echo "no_new_privs:  $(grep '^NoNewPrivs' /proc/self/status | tr -s '\t ' ' ')"
echo "LSMs:          $(cat /sys/kernel/security/lsm 2>/dev/null || echo 'not readable')"
echo "volume mount:  $(grep " $store " /proc/mounts || echo "NOT A MOUNT POINT: $store")"
echo

echo "--- kernel features, tried by hand ---"
scratch="$(mktemp -d)"

# What bubblewrap needs when it runs as root: a mount namespace and bind mounts.
if unshare -m sh -c "mkdir -p $scratch/bind && mount --bind '$store' $scratch/bind && ls $scratch/bind >/dev/null" 2>"$scratch/err"; then
    echo "mount namespace + bind mount of the volume (needs SYS_ADMIN): ok"
else
    echo "mount namespace + bind mount of the volume (needs SYS_ADMIN): FAILED: $(tr '\n' ' ' <"$scratch/err")"
fi

# What bubblewrap needs when it runs as the app's user: a user namespace.
if setpriv --reuid 19999 --regid 19999 --clear-groups unshare -U -r true 2>"$scratch/err"; then
    echo "user namespace as an unprivileged user: ok"
else
    echo "user namespace as an unprivileged user: FAILED: $(tr '\n' ' ' <"$scratch/err")"
fi

if bwrap --version >/dev/null 2>&1; then
    if bwrap --ro-bind / / true 2>"$scratch/err"; then
        echo "bubblewrap as root: ok"
    else
        echo "bubblewrap as root: FAILED: $(tr '\n' ' ' <"$scratch/err")"
    fi
    if setpriv --reuid 19999 --regid 19999 --clear-groups bwrap --unshare-user --ro-bind / / true 2>"$scratch/err"; then
        echo "bubblewrap in a user namespace: ok"
    else
        echo "bubblewrap in a user namespace: FAILED: $(tr '\n' ' ' <"$scratch/err")"
    fi
else
    echo "bubblewrap: not installed in this image"
fi

echo "Landlock ABI: $(python3 -c 'from efp_app_host import landlock; print(landlock.abi_version() or "not available")' 2>&1)"
rm -rf "$scratch"
echo

echo "--- the host's own probe on $store ---"
python3 -m efp_app_host.probe --name "$name" --store "$store"
code=$?
echo "================ end of $name (exit $code) ================"
exit 0
