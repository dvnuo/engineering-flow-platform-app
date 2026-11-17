# Kubernetes manifests of the app host

What each file in this directory is, and the order to apply them in. The
full guide (settings, isolation modes, storage, upgrades, troubleshooting)
is [docs/OPERATIONS.md](../docs/OPERATIONS.md). Run every command below from
the **repository root**, with `kubectl` pointing at the intended cluster.

Everything lives in the namespace `efp-apps`. The Deployment, Service,
Ingress and Secret are all named `efp-app-host`; the volume and its claim
are `efp-apps-store`.

| File | What it creates | Edit before applying |
|---|---|---|
| `namespace.yaml` | the namespace `efp-apps` | nothing |
| `storage.yaml` | the PersistentVolume and claim `efp-apps-store`, on EFS through an access point | the volume handle: `terraform output efp_apps_volume_handle` (see [`terraform/`](../terraform/)) |
| `secret.example.yaml` | the Secret `efp-app-host`: the token the host and the Portal share | copy it and fill in the copy; never commit a filled-in copy |
| `deployment-sandbox.yaml` | the app host in isolation mode `sandbox` (preferred) | the image, the three URLs, the package index, and what the probe said |
| `deployment-hidden.yaml` | the app host in isolation mode `hidden` (the fallback) | the same |
| `service.yaml` | the Service: 8080 is the gateway, 9000 the manager API for the Portal | nothing |
| `ingress.yaml` | the apps host name, routed to 8080 only | host name, TLS secret, ingress class |
| `networkpolicy.example.yaml` | optional: 9000 reachable from the Portal only | the two selectors |
| [`probe/`](probe/) | a throwaway pod that tries every isolation on the real volume and names the Deployment file to use | nothing; `run.sh` takes the image as its argument |
| [`apparmor/efp-app-host`](apparmor/efp-app-host) | the AppArmor profile the sandbox backend `bwrap` needs on nodes with Ubuntu 24.04 or later | nothing; it is loaded on the nodes, not applied with `kubectl` |

The two Deployment files define the same Deployment: apply **one** of them.

## Order

```bash
docker build -t <registry>/engineering-flow-platform-app:<tag> .
docker push <registry>/engineering-flow-platform-app:<tag>

kubectl apply -f k8s/namespace.yaml -f k8s/storage.yaml
sh k8s/probe/run.sh <registry>/engineering-flow-platform-app:<tag>
kubectl apply -f <your copy of secret.example.yaml>
kubectl apply -f k8s/deployment-sandbox.yaml -f k8s/service.yaml
kubectl apply -f k8s/ingress.yaml
kubectl -n efp-apps rollout status deployment/efp-app-host
```

The probe ends with the Deployment file to use (`deployment-sandbox.yaml` or
`deployment-hidden.yaml`) and two settings to put into it; take that file in
the fifth command.

Then tell the Portal where the host is: `APPS_ENABLED=true`,
`APPS_HOST_INTERNAL_URL=http://efp-app-host.efp-apps.svc.cluster.local:9000`,
`APPS_PUBLIC_BASE_URL=https://<the host name in ingress.yaml>` and
`APPS_HOST_API_TOKEN` with the value in the Secret.

## Checking the files

```bash
python -m pip install pyyaml
python scripts/check_manifests.py
```

It checks what must never be changed by accident (one replica, strategy
`Recreate`, no service-account token, the Ingress routing to the gateway
only, the probe trying exactly the privileges the Deployments ask for). CI
runs it, and starts the image with exactly the capabilities each Deployment
file grants (`integration/ci.sh`, the variants ending in `-k8s`).
