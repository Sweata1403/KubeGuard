# KubeGuard — first working slice

A learning repository for an enterprise incident automation product. It demonstrates
one image-only recovery workflow on a local, disposable Kubernetes cluster.

**Demo:** v1 serves working requests; v2 serves HTTP 500. The controller observes
three consecutive failed requests, restores the explicitly approved v1 image,
waits for the full rollout, checks five successful business requests, and records
the outcome. Observation mode is enabled by default.

This is a starter, not the finished enterprise product. It does not measure a
real traffic error-rate window, diagnose root causes, send pages, or integrate
with GitOps. `needs_engineer` is a persisted status and log entry, not an actual
notification. Failures on the already-approved image are only observed.

## Requirements

- Python 3.12+, Docker, kubectl, and kind on your own computer.
- Docker must be running; kubectl must point to the disposable kind cluster.
- Network access to pull the base image and kind node image.
- A default StorageClass for the incident PVC (check `kubectl get storageclass`).
- Bash-compatible terminal; Windows users can use WSL2 with Docker integration.

## Run the demo

Run these commands from the extracted repository root:

```bash
python3 -m unittest discover -s tests -v
kind create cluster --name kubeguard
docker build --build-arg APP_VERSION=v1 -t kubeguard-demo:v1 app
docker build --build-arg APP_VERSION=v2 -t kubeguard-demo:v2 app
docker build -t kubeguard-controller:v1 controller
kind load docker-image kubeguard-demo:v1 kubeguard-demo:v2 kubeguard-controller:v1 --name kubeguard
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/rbac.yaml
kubectl apply -f k8s/demo.yaml
kubectl apply -f k8s/controller.yaml
kubectl -n kubeguard-demo rollout status deployment/demo --timeout=120s
kubectl -n kubeguard-demo rollout status deployment/kubeguard --timeout=120s
```

Check pods with `kubectl -n kubeguard-demo get pods`. A Pending controller usually
requires checking PVC binding: `kubectl -n kubeguard-demo get pvc`.

In another terminal, follow controller logs:

```bash
kubectl -n kubeguard-demo logs -f deployment/kubeguard
```

Introduce the broken release:

```bash
kubectl -n kubeguard-demo set image deployment/demo demo=kubeguard-demo:v2
```

Wait until logs show `would_rollback`. No change is made in observation mode.
Now enable execution for this demo:

```bash
kubectl -n kubeguard-demo set env deployment/kubeguard EXECUTE=true
kubectl -n kubeguard-demo rollout status deployment/kubeguard --timeout=120s
kubectl -n kubeguard-demo logs -f deployment/kubeguard
```

Expected incident statuses: `attempting`, then `recovered`. Detection depends on
which replica answers the synthetic request during rollout; the three failed
samples are not a statistically meaningful traffic error-rate estimate.

Confirm the image and print the handover:

```bash
kubectl -n kubeguard-demo get deployment demo -o jsonpath='{.spec.template.spec.containers[0].image}'
kubectl -n kubeguard-demo exec deployment/kubeguard -- python report.py
```

Expected image: `kubeguard-demo:v1`. Report timestamps use UTC.

Optional business endpoint check, after recovery:

```bash
kubectl -n kubeguard-demo port-forward service/demo 8080:8080
```

Open http://localhost:8080 in your browser. Port-forward binds to a selected pod;
restart the command if that pod is replaced during a rollout.

Disable automation:

```bash
kubectl -n kubeguard-demo set env deployment/kubeguard EXECUTE=false
```

Delete only this disposable demo cluster when finished:

```bash
kind delete cluster --name kubeguard
```

Deleting the cluster also deletes its incident history. Within the cluster, the
PVC keeps the journal across controller pod replacements.

## Recovery boundaries

- Only `kubeguard-demo/demo` can be read or patched under the supplied RBAC.
- Approval is explicit in Deployment annotations; v1 is trusted by the demo
  operator, not automatically proven healthy by a release pipeline.
- Only the image changes. Environment, configuration, schema migrations, and
  multi-container applications are outside this runbook.
- Each Deployment UID/generation receives at most one persisted action attempt.
  A crash during an attempt leaves `attempting`; inspect it manually. The starter
  does not automatically resume in-flight recovery.
- An atomic JSON Patch tests the UID, resourceVersion, and old image before
  mutation. Concurrent changes abort the attempt rather than being overwritten.
- Run exactly one controller replica. This version has no distributed lease.
- Do not connect a GitOps reconciler to this demo; it could reapply the bad image.
- A failed verification becomes `needs_engineer`; there is no retry loop or real
  pager connection. The next milestone must wire these outcomes to notifications.
- Image tags and base images are convenient for learning. A customer release
  needs immutable digests, scanning, and a compatible approved rollback target.
- The sample HTTP server is a demonstration workload, not a production API server.

## Next milestones

1. Replace synthetic polling with Prometheus request error-rate and latency windows.
2. Correlate incidents with release timestamps; record an approved full template.
3. Add actual escalation notifications and interrupted-action reconciliation.
4. Add controller leader election and deployment-controller/GitOps integration.
5. Add dashboards, access controls, and pilot testing before enterprise claims.

## File explanations

Read [docs/FILE_GUIDE.md](docs/FILE_GUIDE.md) for every file and the important code
functions. Start with `app/main.py`, then `k8s/demo.yaml`, then the controller.

## Validation in the authoring environment

Python unit tests and local HTTP checks were run. Docker builds and a live
Kubernetes recovery were not run because those tools were unavailable. The CI
workflow runs unit tests and builds images; it does not yet run a cluster demo.

## Reference documentation

- https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/
- https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-probes/
- https://kubernetes.io/docs/reference/kubectl/generated/kubectl_patch/
- https://kubernetes.io/docs/reference/using-api/api-concepts/
- https://docs.python.org/3.12/library/http.server.html
