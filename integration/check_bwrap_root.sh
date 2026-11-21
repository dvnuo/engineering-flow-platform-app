#!/bin/sh
# Runs inside a container: can bubblewrap, started by root, run something
# that then switches to another user? That is what the "bwrap" sandbox
# backend does for every app.
#
# bubblewrap run by root keeps root's capabilities for what it starts. Where
# the switch fails all the same, the lines below show why: the capability set
# inside bubblewrap, and the AppArmor label of the process there. On Ubuntu
# 24.04 and later the machine's own AppArmor policy puts whatever an
# unconfined /usr/bin/bwrap starts under a profile that may use no capability.
label() {
    cat /proc/self/attr/current 2>/dev/null || echo "(none)"
}

echo "in the container: AppArmor label $(label); $(grep '^CapEff' /proc/self/status | tr -s '\t ' ' ')"
bwrap --ro-bind / / --proc /proc --dev /dev sh -c '
    echo "inside bubblewrap: AppArmor label $(cat /proc/self/attr/current 2>/dev/null || echo "(none)"); $(grep "^CapEff" /proc/self/status | tr -s "\t " " ")"
' || echo "inside bubblewrap: bubblewrap itself could not build a view"

if bwrap --ro-bind / / --proc /proc --dev /dev setpriv --reuid 19999 --regid 19999 --clear-groups --no-new-privs true; then
    echo "bubblewrap as root, then setpriv to another user: ok"
    exit 0
fi
echo "bubblewrap as root, then setpriv to another user: FAILED"
exit 1
