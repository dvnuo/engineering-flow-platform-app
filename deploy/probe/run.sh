#!/bin/sh
# Checklist item M0-1: which isolation can the app host use on this cluster?
#
#     deploy/probe/run.sh [IMAGE]
#
# Needs kubectl pointing at the cluster, the namespace efp-apps and the
# volume claim efp-apps-store (deploy/k8s/namespace.yaml, storage.yaml), and
# the app host image in a registry the cluster can pull from.
#
# Starts a throwaway pod (pod.yaml), prints what each of its containers found
# and ends with the variant to deploy and the two settings to use. The pod
# and what it wrote to the volume are removed again.
set -eu

IMAGE="${1:-ghcr.io/dvnuo/engineering-flow-platform-app:latest}"
NAMESPACE="efp-apps"
POD="efp-isolation-probe"
HERE="$(cd "$(dirname "$0")" && pwd)"
LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT

echo "Probing with image $IMAGE in namespace $NAMESPACE ..."
kubectl -n "$NAMESPACE" delete pod "$POD" --ignore-not-found --wait=true >/dev/null
kubectl -n "$NAMESPACE" create configmap "$POD" --from-file=probe.sh="$HERE/probe.sh" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
sed "s|IMAGE_PLACEHOLDER|$IMAGE|g" "$HERE/pod.yaml" | kubectl apply -f - >/dev/null

phase=""
tries=0
while [ "$tries" -lt 150 ]; do
    phase="$(kubectl -n "$NAMESPACE" get pod "$POD" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
    case "$phase" in
        Succeeded | Failed) break ;;
    esac
    tries=$((tries + 1))
    sleep 2
done
if [ "$phase" != "Succeeded" ] && [ "$phase" != "Failed" ]; then
    echo "The probe pod did not finish (phase: ${phase:-unknown}). What Kubernetes says:"
    kubectl -n "$NAMESPACE" describe pod "$POD" | tail -n 40
    echo
    echo "A pod that is refused at admission (a policy that forbids SYS_ADMIN or"
    echo "an Unconfined seccomp profile) is an answer too: remove the container"
    echo "it names from pod.yaml and run again."
    exit 2
fi

for container in bwrap-default-seccomp bwrap userns plain; do
    kubectl -n "$NAMESPACE" logs "$POD" -c "$container" 2>&1 | tee -a "$LOG"
    echo
done

worked() {
    grep -q "^EFP_PROBE_RESULT name=$1 mode=$2 backend=$3 ok=true" "$LOG"
}

echo "=========================== RESULT ==========================="
if worked bwrap-default-seccomp sandbox bwrap; then
    echo "Deploy deploy/k8s/deployment-sandbox.yaml; its seccompProfile may be RuntimeDefault."
    echo "  EFP_HOST_ISOLATION_MODE=sandbox"
    echo "  EFP_HOST_SANDBOX_BACKEND=bwrap"
elif worked bwrap sandbox bwrap; then
    echo "Deploy deploy/k8s/deployment-sandbox.yaml as it is (SYS_ADMIN, seccomp Unconfined)."
    echo "  EFP_HOST_ISOLATION_MODE=sandbox"
    echo "  EFP_HOST_SANDBOX_BACKEND=bwrap"
elif worked userns sandbox bwrap-userns; then
    echo "Deploy deploy/k8s/deployment-sandbox.yaml without SYS_ADMIN and SYS_CHROOT (keep seccomp Unconfined)."
    echo "  EFP_HOST_ISOLATION_MODE=sandbox"
    echo "  EFP_HOST_SANDBOX_BACKEND=bwrap-userns"
elif worked plain sandbox landlock; then
    echo "Deploy deploy/k8s/deployment-hidden.yaml with these two settings (its securityContext is enough for Landlock)."
    echo "  EFP_HOST_ISOLATION_MODE=sandbox"
    echo "  EFP_HOST_SANDBOX_BACKEND=landlock"
elif worked userns sandbox landlock; then
    echo "Deploy deploy/k8s/deployment-hidden.yaml with seccompProfile Unconfined and these two settings."
    echo "  EFP_HOST_ISOLATION_MODE=sandbox"
    echo "  EFP_HOST_SANDBOX_BACKEND=landlock"
elif worked plain hidden plain; then
    echo "No sandbox works on this cluster. Deploy deploy/k8s/deployment-hidden.yaml."
    echo "  EFP_HOST_ISOLATION_MODE=hidden"
else
    echo "Neither mode works: apps cannot be kept apart on this cluster as it is."
    echo "Read the output above: each container says which feature it missed."
    status=1
fi
echo "=============================================================="

echo "Removing the probe pod ..."
kubectl -n "$NAMESPACE" delete pod "$POD" --wait=true >/dev/null
kubectl -n "$NAMESPACE" delete configmap "$POD" >/dev/null
cat <<EOF
The probe left four empty directories (probe-*) at the root of the volume;
they are harmless, the host ignores them. Remove them from any pod that
mounts the volume if you want it tidy.
EOF
exit "${status:-0}"
