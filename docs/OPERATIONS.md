# Operating the app host

The app host is one container that runs every EFP app. This document is for
whoever deploys and runs it. The wire formats are in
[CONTRACTS.md](CONTRACTS.md); how to write an app is in
[DEVELOPING_APPS.md](DEVELOPING_APPS.md).

## 1. What runs in the container

```
tini
`-- supervisord (root)
    |-- nginx            the gateway, port 8080: routes /{slug}/..., asks the manager about every request
    |-- manager          Python, port 9000: the API for the Portal, access checks, deployments, watchdogs
    `-- app-<id> ...     one program per app, started on the first visit, each as its own OS user
```

- Port **8080** is what the Ingress routes to. Port **9000** is for the Portal
  only, from inside the cluster; it must never get a public route.
- The host is **one replica**. Two hosts on the same volume would corrupt it;
  the Deployment uses `strategy: Recreate` for that reason.
- The container starts as **root**: the manager creates one OS user per app
  and starts each app as that user, with `no_new_privs`, without capabilities
  and with process and file limits. No app ever runs as root.

## 2. Deploying

All files are under `deploy/`.

1. **Image.** `docker build -f host/Dockerfile -t <registry>/engineering-flow-platform-app:<tag> .`
   from the repository root (the build needs `host/`, `sdk/` and
   `templates/`). `--build-arg PIP_INDEX_URL=...` points the build at a
   private index. Do not pass credentials that way: build arguments stay in
   the image history.
2. **Volume.** Create the EFS access point (`deploy/terraform/efs_access_point.tf`),
   put its handle into `deploy/k8s/storage.yaml`, then
   `kubectl apply -f deploy/k8s/namespace.yaml -f deploy/k8s/storage.yaml`.
3. **Find out which isolation the cluster allows** (section 4):
   `deploy/probe/run.sh <image>`. It ends with the Deployment file to use and
   the two settings to put into it.
4. **Secret.** Copy `deploy/k8s/secret.example.yaml`, fill it in, apply it.
   The Portal needs the same token as `APPS_HOST_API_TOKEN`.
5. **Deployment.** Edit the one the probe named (`deployment-sandbox.yaml` or
   `deployment-hidden.yaml`): image, the three URLs, the package index. Apply
   it together with `service.yaml`.
6. **Ingress.** Edit host name and TLS secret in `ingress.yaml`, apply it.
   The apps host name must be a sibling of the Portal's
   (`efp.example.com` / `app.example.com`).
7. **Portal.** `APPS_HOST_INTERNAL_URL=http://efp-app-host.efp-apps.svc.cluster.local:9000`,
   `APPS_PUBLIC_BASE_URL=https://app.example.com`.

`python deploy/check_manifests.py` (needs PyYAML) checks the manifests; CI
runs it.

### Not through the Ingress

The Ingress routes only to 8080. Keep it that way, and keep the Portal's
`/api/app-host/` and `/api/app-sdk/` rules as its own documentation says.
`deploy/k8s/networkpolicy.example.yaml` restricts 9000 to the Portal on
clusters that enforce NetworkPolicy.

## 3. Settings

Environment variables of the container (the full contract is CONTRACTS.md,
section 3).

| Variable | Default | Meaning |
|---|---|---|
| `EFP_HOST_API_TOKEN` | required | The secret shared with the Portal. Both directions use it as a bearer token. |
| `EFP_PORTAL_INTERNAL_URL` | required | The Portal as reached from the cluster. Apps get it as `EFP_PORTAL_URL`. |
| `EFP_PORTAL_PUBLIC_URL` | required | The Portal as reached by browsers (sign-in redirects). |
| `EFP_APPS_PUBLIC_URL` | required | This host as reached by browsers. |
| `EFP_HOST_ISOLATION_MODE` | `sandbox` | `sandbox` or `hidden`. Section 4. |
| `EFP_HOST_SANDBOX_BACKEND` | `auto` | `auto`, `bwrap`, `bwrap-userns`, `landlock`. `auto` takes the first that works, in that order. Name one to forbid falling back to another. |
| `EFP_HOST_STORE_DIR` | `/var/lib/efp-host/store` | Where the volume is mounted. Must have a parent directory inside the container (hidden mode closes it). |
| `EFP_HOST_RUNTIME_DIR` | `/srv/efp-apps` | The container's own disk: private tmp of every app, build scratch space; in hidden mode also code and virtual environments. |
| `EFP_HOST_RUN_DIR` | `/run/efp-host` | Sockets, the gateway secret, launch files (they hold app tokens: tmpfs is right). |
| `EFP_HOST_ADMIN_PORT` | `9000` | Manager API. |
| `EFP_HOST_GATEWAY_PORT` | `8080` | Gateway. |
| `EFP_HOST_PORT_RANGE_START` | `20000` | First loopback port given to an app. One per app, never reused while the app exists. |
| `EFP_HOST_UID_RANGE_START` | `20000` | First OS uid given to an app (the gid is the same number). |
| `EFP_HOST_IDLE_STOP_MINUTES` | `30` | An app nobody used for this long is stopped; the next visit starts it. `0` keeps apps running. An app with an open connection (a WebSocket, a long download) is in use. |
| `EFP_HOST_APP_MEMORY_MB` | `1024` | Resident memory ceiling per app (all its processes together). Over it the app is stopped, state `killed`, reason `memory`. The Portal can set another value per app. |
| `EFP_HOST_APP_DATA_MB` | `1024` | Quota of an app's data. Soft in sandbox mode (reported, not enforced); enforced by the storage service in hidden mode. |
| `EFP_HOST_APP_NPROC` | `256` | Processes and threads per app. |
| `EFP_HOST_SESSION_TTL_HOURS` | `168` | Gateway session lifetime (sliding). |
| `EFP_HOST_ACCESS_CACHE_SECONDS` | `30` | How long an access decision of the Portal is reused. |
| `EFP_HOST_ANON_RATE_PER_MINUTE` | `120` | Requests per source address to apps open to anyone. |
| `EFP_HOST_METRICS_PUSH_SECONDS` | `300` | How often traffic and status go to the Portal. |
| `EFP_HOST_INSTALL_CONCURRENCY` | `1` | Deployments that run at once; the others wait as `queued`. |
| `EFP_HOST_DEPLOY_TIMEOUT_SECONDS` | `900` | One deployment, from unpack to switch. |
| `EFP_HOST_PIP_ALLOW_SDIST` | `false` | Allow packages that have no wheel. A source distribution runs its build code at deployment time (as the app's user, inside its isolation). |
| `EFP_HOST_RESOURCE_DIR` | `/opt/efp` (set in the image) | Where the gateway's nginx template and pages are. Leave it. |
| `PIP_INDEX_URL`, `PIP_EXTRA_INDEX_URL`, `PIP_TRUSTED_HOST`, `PIP_CERT`, ... | | Read by pip itself. Every `PIP_*` variable is passed to deployments and **never** to apps. |
| `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` | | Passed to deployments and to apps. The manager's own calls to the Portal never use a proxy. Put the cluster's service domain into `NO_PROXY`, or apps will send their SDK calls to the proxy. |

A certificate file named by `PIP_CERT`, `SSL_CERT_FILE` or
`REQUESTS_CA_BUNDLE` must exist in the container (mount it, for example below
`/etc/ssl/certs`). In sandbox mode the deployment is shown that one file in
addition to the system directories.

Fixed limits (constants in the code): a bundle is at most 200 MiB and unpacks
to at most 1 GiB and 50 000 entries; one stored object is at most 64 MiB; a
deployment is refused when the volume or the local disk has less than 256 MiB
free.

## 4. Choosing the isolation mode

The volume is EFS behind an access point. An access point performs every
file operation as one user, whatever user the process is. So although each
app has its own OS user, **on the volume all apps are the same user**: file
ownership there separates nothing. The host has two ways to keep apps apart
anyway.

| | `sandbox` | `hidden` |
|---|---|---|
| What an app sees of the volume | only its own directory: code and virtual environment read-only, `data/` read-write | nothing |
| Where code and virtual environment are | on the volume | on the pod's own disk; rebuilt from the volume after a restart, without the network |
| How an app keeps data | files below `EFP_APP_DATA_DIR` (SQLite works), or the SDK's `storage` | the SDK's `storage` only (objects, no files, no SQLite) |
| What the pod needs | one of the three backends below | nothing beyond a root container |
| Cost | virtual environments on EFS: installs and cold starts are slower | the first visit after a pod restart rebuilds the app's environment; more local disk |

Sandbox backends, in the order `auto` tries them:

| Backend | How | Needs |
|---|---|---|
| `bwrap` | bubblewrap as root builds a mount namespace that holds the system directories and the app's own directory; inside it `setpriv` drops to the app's user | capability `SYS_ADMIN` (plus `SYS_CHROOT`); no seccomp profile that forbids `mount`; on nodes with AppArmor the profile `deploy/apparmor/efp-app-host` (below) |
| `bwrap-userns` | drop to the app's user first, then bubblewrap in a user namespace | unprivileged user namespaces on the node; seccomp `Unconfined` |
| `landlock` | drop to the app's user, then give up filesystem access with Landlock | a kernel with Landlock (5.13+, `lsm=landlock`) |

With bubblewrap the rest of the volume does not exist for the app, and
(backend `bwrap`) neither do the other processes of the container. With
Landlock the rest of the volume exists but cannot be opened or listed (file
*names* can still be probed with `stat`), `/tmp` is not available (the app's
`TMPDIR` is), and the app sees the container's other processes in `/proc`,
though not their environment. Hidden mode is like Landlock in that last
respect.

In a bubblewrap view the app can write its data directory, its private tmp
(also mounted as `/tmp`) and `/dev/shm` (64 MiB), and nothing else: the root
of the view, the directories that lead to the app's own and `/dev` are
read-only.

#### Nodes with AppArmor

AppArmor matters for the `bwrap` backend only. Two things get in its way:

- The container runtime's default profile (`docker-default`,
  `cri-containerd.apparmor.d`) forbids `mount`. bubblewrap cannot build a
  view under it.
- Running the container `Unconfined` is not the answer on Ubuntu 24.04 and
  later. The node's own AppArmor policy has a profile for `/usr/bin/bwrap`
  that attaches by the program's path, also to a process of an unconfined
  container, and what bubblewrap starts under it may use no capability at
  all. bubblewrap run by root otherwise keeps root's capabilities for what it
  starts, and `setpriv` inside the view needs one of them (`CAP_SETUID`) to
  become the app's user: it fails with `setpriv: setresuid failed: Operation
  not permitted`. With `EFP_HOST_SANDBOX_BACKEND=auto` the host then uses
  `bwrap-userns` (which that same node profile permits) and says so in its
  log; with `bwrap` named, no app is started.

The way out is a profile of the container's own: `deploy/apparmor/efp-app-host`
is the runtime's default profile plus `mount` and `pivot_root`. A container
that runs under it keeps it across every exec, so the node's bwrap profile
never applies. Load it on every node that may run the app host
(`sudo apparmor_parser --replace deploy/apparmor/efp-app-host`, and copy it
to `/etc/apparmor.d/` to survive a reboot), then set
`securityContext.appArmorProfile: {type: Localhost, localhostProfile: efp-app-host}`
in `deployment-sandbox.yaml`. CI starts the image this way.

Nodes without AppArmor (Amazon Linux, Bottlerocket) need none of this.
`bwrap-userns`, `landlock` and hidden mode run under the runtime's default
profile; `bwrap-userns` additionally needs the node to allow unprivileged
user namespaces to the container.

**Run the probe; do not guess.** Whether a backend works depends on the
node's kernel, the container runtime's seccomp and LSM profiles and the
admission policy of the cluster. `deploy/probe/run.sh <image>` starts a
throwaway pod with four containers, one per set of privileges, on the real
volume, runs the same probe the manager runs at every boot, and prints for
example:

```
Deploy deploy/k8s/deployment-sandbox.yaml as it is (SYS_ADMIN, seccomp Unconfined).
  EFP_HOST_ISOLATION_MODE=sandbox
  EFP_HOST_SANDBOX_BACKEND=bwrap
```

Put the backend it names into the Deployment instead of `auto`: the host then
never silently falls back to a weaker one after a node change.

**Fail closed.** At every start the manager probes the configured isolation
with a real process: it must run as an unprivileged user, read its own
directory and be unable to read or list another one. When the probe fails,
no app is started and nothing is built; `GET /v1/host` carries
`isolation_error`, every app with a process reports state `failed`, reason
`isolation_unavailable`, with the reason in `last_log_lines`, and the
manager's log says the same. Static apps keep working (they run nothing).

Switching the mode later: apps deployed in one mode must be deployed again
in the other (sandbox keeps a virtual environment on the volume, hidden a
wheel cache). Data written as files in sandbox mode is not visible through
the storage service of hidden mode, except what the SDK's `storage` wrote
(both keep it under `data/objects/`).

### What the modes need from the pod

| | `deployment-sandbox.yaml` | `deployment-hidden.yaml` |
|---|---|---|
| Capabilities | `SYS_ADMIN`, `SYS_CHROOT`, `SETUID`, `SETGID`, `SETPCAP`, `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `KILL` | `SETUID`, `SETGID`, `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `KILL` |
| `allowPrivilegeEscalation` | `true` (Kubernetes insists, with `SYS_ADMIN`) | `false` |
| seccomp | `Unconfined` (what CI tests; the probe tells whether `RuntimeDefault` is enough) | `RuntimeDefault` |
| Service-account token | none | none |

`SYS_ADMIN` makes the *container* more powerful, not the apps: every app
process has `no_new_privs`, no capability, and the image holds no setuid
program. It does mean that a kernel bug that lets an app become root in the
container is worse than in hidden mode.

## 5. The volume

```
registry.json                     what the host knows about its apps (no secrets)
keys/session.key                  signs gateway sessions
metrics/state.json                traffic counters not yet sent
jobs/<job id>.json, .log          deployments (kept 30 days; the newest of each app always)
apps/<app id>/env.json            the app's environment, with its token (mode 0600)
apps/<app id>/releases/<sha>/     code/, manifest.json, requirements.lock,
                                  venv/ (sandbox) or wheels/ (hidden)
apps/<app id>/data/               the app's data (hidden mode: data/objects/)
apps/<app id>/logs/app.log        stdout and stderr, 10 MiB x 3
```

The host keeps the current and the previous release of every app; older
ones are removed at the next deployment.

### Backup

- **Must be backed up:** `apps/*/data/`. It is the only copy of what apps
  stored. Use AWS Backup on the file system (see the note in
  `deploy/terraform/efs_access_point.tf`).
- **Good to have:** `registry.json`, `apps/*/env.json`, `keys/`. Without them
  the Portal can register every app again (it keeps slug, token and
  environment) and visitors sign in once more.
- **Can be rebuilt:** `apps/*/releases/` (deploy again from the Portal),
  `jobs/`, `metrics/`, logs.

Restore: bring the files back, start the host. It reads `registry.json`,
reconciles with the Portal and starts apps on their first visit. If only the
data survived, register and deploy the apps again under the same ids: the
data directory is found by app id.

Removing an app with `purge_data=false` keeps `apps/<id>/data/`; a later
`DELETE /v1/apps/<id>?purge_data=true` removes it, also when the app is long
gone.

## 6. Sizing

- **Memory.** The pod's limit must cover the manager and nginx (about
  300 MiB) plus the apps that run at the same time. Each app is capped at
  `EFP_HOST_APP_MEMORY_MB`, but the sum is not: twenty apps of 400 MiB need
  8 GiB. Idle stop is what keeps the number of running apps low. When the pod
  reaches its own limit the kernel kills something, possibly the manager; set
  the limit so that this does not happen in normal use and watch
  `memory.used_bytes` in `GET /v1/host`.
- **CPU.** Apps are not limited individually. One busy app can use every
  core of the pod.
- **Volume.** A virtual environment is 100-500 MiB per release, two releases
  are kept (sandbox mode). A wheel cache is about a third of that (hidden
  mode). Plus the apps' data.
- **Local disk** (`/srv/efp-apps`, an `emptyDir`). Sandbox mode: tmp files
  only. Hidden mode: code and virtual environment of every app that ran
  since the pod started; size the `emptyDir` for all of them.
- **Deployments.** A deployment in sandbox mode writes thousands of small
  files to EFS; expect a minute or more for a large dependency set.
  `EFP_HOST_INSTALL_CONCURRENCY=2` lets two apps build at once.

## 7. Day to day

- **Logs.** `kubectl logs` shows the entrypoint, supervisord, nginx and the
  manager. An app's own output is in `apps/<id>/logs/app.log`, readable
  through the Portal (`GET /v1/apps/<id>/logs`). Lines that start with
  `[efp-host` are the host's: why it stopped or did not start the app.
- **Restarting the pod.** Nothing is lost. Apps are not started at boot; the
  first visit starts each one. In hidden mode that first visit also rebuilds
  the app's environment from the volume, which takes as long as a `pip
  install` from local files.
- **Restarting only the manager** (it crashed and supervisord restarted it):
  apps keep running and are adopted. A deployment that was running is marked
  failed ("the host restarted during the deployment"); deploy again.
- **Upgrading the image.** Roll the Deployment. Because of `Recreate` all
  apps are down until the new pod is up.
- **Rotating the host token.** Change the Secret and the Portal's setting
  together, restart both.
- **An app that keeps crashing** is restarted after 2, 10 and 30 seconds;
  after a fourth crash within ten minutes it stays down until somebody opens
  it or starts it from the Portal. The Portal gets a `crashed` event with
  the last log lines each time.
- **Apps the Portal no longer lists** are disabled at the next boot, not
  deleted: their files stay until the Portal deletes them.

## 8. What the host cannot do

- **No hard disk quota.** In sandbox mode an app can fill the volume; the
  host measures every five minutes, reports `quota_exceeded` to the Portal
  once per crossing and shows `data_bytes` in the status, but does not stop
  the app. In both modes an app can fill its private tmp on the pod's disk.
- **No per-app CPU limit, no hard memory limit.** Memory is checked every
  five seconds; an app that allocates faster than that can push the pod to
  its limit first.
- **One replica, one node.** A node failure or an image upgrade takes every
  app down until the pod is back.
- **One network for all apps.** Apps listen on loopback ports of the same
  pod. An app's *code* can therefore connect to another app's port directly,
  without the gateway's access check. What protects a private app from the
  others is the signed `X-EFP-Visitor` header, which only the gateway can
  produce: an app that must not serve other apps has to require a valid
  visitor (the SDK's helpers do). The same holds for the manager's ports:
  they are reachable from apps and protected by the host token and the
  gateway secret, not by the network.
- **Apps share an origin.** Every app is served from the same host name.
  The gateway scopes cookies to `/{slug}/` and removes service-worker
  scope headers, but scripts of one app can call another app as the visitor.
- **No system packages, no second language runtime.** An app has Python 3.11
  and what pip installs from the index, as wheels.
- **Hidden mode: no files that persist.** Only the SDK's `storage`.
- **Landlock backend:** see section 4 for what it does not hide.

## 9. When something is wrong

| What you see | Where to look |
|---|---|
| The log says `isolation: bwrap does not work here: setpriv: setresuid failed` | The node's AppArmor policy: section 4, "Nodes with AppArmor". The host fell back to the next backend (or starts nothing when `bwrap` was named). |
| Every app with a process is `failed` / `isolation_unavailable` | `isolation_error` in `GET /v1/host`. Run `deploy/probe/run.sh`; a node pool or runtime change is the usual cause. |
| A deployment fails at `dependencies` | The job log has pip's own output. No wheel for a package: build one for the index, or set `EFP_HOST_PIP_ALLOW_SDIST`. Cannot reach the index: `PIP_INDEX_URL`, proxy, `PIP_CERT`. |
| A deployment fails at `start` or `health` | The job log holds the candidate's output. The app must listen on `127.0.0.1:$PORT` and answer `health_path` with a status below 500 within `startup_timeout`. |
| An app is `killed` / `memory` | It went over its ceiling; raise the app's limit in the Portal or `EFP_HOST_APP_MEMORY_MB`. |
| Static apps answer 403/404 from nginx in sandbox mode | nginx's worker (`www-data`) must reach the files: the directory above `EFP_HOST_STORE_DIR` must be `0755` (the entrypoint sets it; a custom `securityContext.fsGroup` or mount option can undo it). |
| The pod does not start: "the app host must start as root" | `runAsUser: 0` is required (section 1). |
| nginx answers 502 right after a start | The manager is still probing the isolation; a few seconds. |

## 10. CI

`.github/workflows/ci.yml` runs the unit tests, builds the image (the build
itself renders the gateway configuration and runs `nginx -t` on it), runs the
Linux-only tests as root inside the image, and then starts the real
container next to a stub Portal once per isolation variant
(`host/tests/integration/ci.sh`): it deploys the starter templates and a
probe app, checks them through the gateway, checks that one app cannot read
another's files, environment or stored objects, and that redeploy, rollback
and a new container on the same volume work. On a Linux machine with Docker
the same runs with `sh host/tests/integration/ci.sh all hidden` (or
`sandbox`).
