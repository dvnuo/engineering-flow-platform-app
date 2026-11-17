"""Checks of the Kubernetes manifests, and their privileges as ``docker run`` flags.

    python scripts/check_manifests.py                      check every manifest
    python scripts/check_manifests.py docker-flags FILE    print the flags of a Deployment

CI uses the second form to start the image with exactly the capabilities a
Deployment file grants, so that what is shipped is what was tested.

Needs PyYAML (``pip install pyyaml``); it is not a dependency of the host.
"""

from __future__ import annotations

import sys
from pathlib import Path

K8S = Path(__file__).resolve().parents[1] / "k8s"
NAMESPACE = "efp-apps"


def load(path: Path) -> list[dict]:
    import yaml

    with open(path, "r", encoding="utf-8") as handle:
        return [document for document in yaml.safe_load_all(handle) if document]


def container_of(deployment: dict) -> dict:
    containers = deployment["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 1, "the app host is one container"
    return containers[0]


def docker_flags(path: Path) -> list[str]:
    """The security settings of a Deployment as ``docker run`` flags."""

    (deployment,) = [document for document in load(path) if document.get("kind") == "Deployment"]
    security = container_of(deployment)["securityContext"]
    capabilities = security.get("capabilities") or {}
    flags: list[str] = []
    for name in capabilities.get("drop") or []:
        flags += ["--cap-drop", name]
    for name in capabilities.get("add") or []:
        flags += ["--cap-add", name]
    if security.get("allowPrivilegeEscalation") is False:
        flags += ["--security-opt", "no-new-privileges"]
    if (security.get("seccompProfile") or {}).get("type") == "Unconfined":
        flags += ["--security-opt", "seccomp=unconfined"]
    return flags


def check_deployment(path: Path, *, mode: str) -> None:
    (deployment,) = [document for document in load(path) if document.get("kind") == "Deployment"]
    assert deployment["metadata"]["namespace"] == NAMESPACE, path
    spec = deployment["spec"]
    assert spec["replicas"] == 1, f"{path}: the host is a single replica"
    assert spec["strategy"]["type"] == "Recreate", f"{path}: two hosts must never share the volume"
    pod = spec["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False, f"{path}: member code must not get a service-account token"
    assert "serviceAccountName" not in pod, path
    container = container_of(deployment)
    env = {item["name"]: item for item in container["env"]}
    assert env["EFP_HOST_ISOLATION_MODE"]["value"] == mode, path
    for name in ("EFP_HOST_API_TOKEN", "EFP_PORTAL_INTERNAL_URL", "EFP_PORTAL_PUBLIC_URL", "EFP_APPS_PUBLIC_URL"):
        assert name in env, f"{path}: {name} is required"
    assert "valueFrom" in env["EFP_HOST_API_TOKEN"], f"{path}: the token comes from a Secret"
    assert {port["containerPort"] for port in container["ports"]} == {8080, 9000}, path
    limits = container["resources"]["limits"]
    assert "memory" in limits and "cpu" in limits, f"{path}: resource limits"
    for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
        assert probe in container, f"{path}: {probe}"
    security = container["securityContext"]
    assert security["runAsUser"] == 0, path
    assert security["capabilities"]["drop"] == ["ALL"], f"{path}: start from no capability"
    added = set(security["capabilities"]["add"])
    assert {"SETUID", "SETGID", "KILL"} <= added, f"{path}: the host switches users and stops their processes"
    if mode == "hidden":
        assert "SYS_ADMIN" not in added, f"{path}: hidden mode needs no SYS_ADMIN"
        assert security["allowPrivilegeEscalation"] is False, path
    else:
        assert "SYS_ADMIN" in added, f"{path}: the bwrap backend needs SYS_ADMIN"
    mounts = {mount["mountPath"] for mount in container["volumeMounts"]}
    assert {"/var/lib/efp-host/store", "/srv/efp-apps", "/run/efp-host"} <= mounts, path
    claims = [volume["persistentVolumeClaim"]["claimName"] for volume in pod["volumes"] if "persistentVolumeClaim" in volume]
    assert claims == ["efp-apps-store"], path


def check_all() -> None:
    k8s = K8S
    manifests = sorted(K8S.rglob("*.yaml"))
    assert manifests, f"no manifest under {K8S}"
    for path in manifests:
        documents = load(path)
        assert documents, f"{path} is empty"
        for document in documents:
            assert document.get("apiVersion") and document.get("kind") and document.get("metadata", {}).get("name"), path
    check_deployment(k8s / "deployment-sandbox.yaml", mode="sandbox")
    check_deployment(k8s / "deployment-hidden.yaml", mode="hidden")

    (service,) = load(k8s / "service.yaml")
    assert {port["port"] for port in service["spec"]["ports"]} == {8080, 9000}
    assert service["spec"]["type"] == "ClusterIP"

    (ingress,) = load(k8s / "ingress.yaml")
    annotations = ingress["metadata"]["annotations"]
    assert annotations["nginx.ingress.kubernetes.io/proxy-read-timeout"] == "3600"
    assert annotations["nginx.ingress.kubernetes.io/proxy-send-timeout"] == "3600"
    assert annotations["nginx.ingress.kubernetes.io/proxy-body-size"]
    assert ingress["spec"]["tls"], "the apps hostname is served over TLS"
    backends = [path["backend"]["service"] for rule in ingress["spec"]["rules"] for path in rule["http"]["paths"]]
    assert backends and all(backend["port"]["number"] == 8080 for backend in backends), "the Ingress routes to the gateway only, never to the manager API"

    volume, claim = load(k8s / "storage.yaml")
    assert volume["kind"] == "PersistentVolume" and volume["spec"]["csi"]["driver"] == "efs.csi.aws.com"
    assert claim["kind"] == "PersistentVolumeClaim" and claim["metadata"]["namespace"] == NAMESPACE

    (secret,) = load(k8s / "secret.example.yaml")
    assert "EFP_HOST_API_TOKEN" in secret["stringData"]

    (pod,) = load(K8S / "probe" / "pod.yaml")
    assert pod["spec"]["restartPolicy"] == "Never" and pod["spec"]["automountServiceAccountToken"] is False
    names = [container["name"] for container in pod["spec"]["containers"]]
    assert names == ["bwrap-default-seccomp", "bwrap", "userns", "plain"]
    # The probe tries exactly the privileges the two Deployments ask for.
    by_name = {container["name"]: container["securityContext"]["capabilities"]["add"] for container in pod["spec"]["containers"]}
    sandbox = container_of(load(k8s / "deployment-sandbox.yaml")[0])["securityContext"]["capabilities"]["add"]
    hidden = container_of(load(k8s / "deployment-hidden.yaml")[0])["securityContext"]["capabilities"]["add"]
    assert by_name["bwrap"] == sandbox and by_name["bwrap-default-seccomp"] == sandbox
    assert by_name["plain"] == hidden and by_name["userns"] == hidden
    sub_paths = [mount["subPath"] for container in pod["spec"]["containers"] for mount in container["volumeMounts"] if mount["name"] == "store"]
    assert len(set(sub_paths)) == 4, "each probe container works in its own directory of the volume"
    print(f"manifests ok: {len(manifests)} files")


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "docker-flags":
        print(" ".join(docker_flags(Path(argv[1]))))
        return 0
    if argv:
        print(__doc__)
        return 2
    check_all()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
