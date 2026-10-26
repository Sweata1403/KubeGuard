# Understand each file

## app/main.py — the application we protect

`Handler.do_GET()` answers HTTP requests. `/live` reports whether the process
can answer, `/ready` reports demo readiness, and `/` represents a business
endpoint. `APP_VERSION=v1` makes `/` return 200; `v2` returns 500. Both versions
pass their probes deliberately: a live process can still have broken business
logic. Kubernetes cannot infer that logic from a simple liveness endpoint.

`ThreadingHTTPServer` listens on port 8080. It is intentionally a small learning
workload; use an appropriate application framework/server for a production API.

## app/Dockerfile — package the application

`FROM` supplies Python. `ARG APP_VERSION` is chosen at build time and copied
into `ENV` for runtime. The same source builds two test images with different
behaviors. `COPY` puts the code into the image, `USER` avoids running as root,
and `CMD` starts the server. `EXPOSE` documents the port; it does not publish it.

## controller/main.py — detection, policy, action, verification

This runs inside Kubernetes, continuously checking one demo Deployment and one
business endpoint. All networking uses Python's standard library.

| Function/class | Purpose |
|---|---|
| `decide()` | Requires explicit opt-in, an approved image, a supported container, and three failed samples |
| `settled()` | Checks that Kubernetes has observed the current generation and every desired replica has been updated and is ready |
| `Kubernetes.request()` | Calls the API over verified TLS using a rotating service-account token |
| `Kubernetes.rollback()` | Atomically checks for concurrent changes, then replaces only the image |
| `healthy()` | Tests the business endpoint and, during verification, the expected demo version |
| `Journal` | Saves action attempts and outcomes in SQLite before and after mutation |
| `verify()` | Waits up to 120 seconds for full rollout plus five consecutive successful checks |
| `run()` | Polls every five seconds, maintains failure counts, applies policy, and writes outcomes |

`EXECUTE=false` logs a proposed action. `EXECUTE=true` permits an actual image
patch. Deployment annotations are a separate opt-in requirement. Failure counts
reset when the UID/generation changes so old samples do not carry into a new
rollout. A database key combines UID and generation; the same attempt cannot
repeat merely because the controller restarts.

This journal stores the latest state per incident, not a complete immutable event
history. `attempting` after a controller crash needs manual investigation.

## controller/report.py — the working-hours handover

Reads the same SQLite database and prints incidents as Markdown, newest first.
It does not send email or schedule itself. Run it inside the controller pod using
the README command.

## controller/Dockerfile — package the recovery code

Copies the controller and report into a Python image. The process runs as a
non-root user. Kubernetes attaches a writable `/data` volume for the database;
the rest of the container filesystem is read-only.

## k8s/namespace.yaml — isolate the demo

Creates `kubeguard-demo`, the namespace containing the protected application,
controller, identity, and journal. A namespace is a grouping boundary; it is not
complete security isolation by itself.

## k8s/demo.yaml — describe desired application state

The Deployment requests three replicas. Its selector matches pod labels, and
the Service uses the same labels to route requests. Annotations explicitly enable
the runbook and name its approved image.

Requests influence scheduling; limits bound resource use. Startup, readiness,
and liveness probes have different purposes: allow startup, control traffic,
and detect a stuck process. Security settings remove unnecessary privileges.
Three replicas alone do not guarantee that pods run on different nodes.

## k8s/rbac.yaml — controller identity and permission

The ServiceAccount gives the controller an identity. The Role permits only
`get` and `patch` on the named demo Deployment; the RoleBinding attaches the
Role to that identity. Kubernetes permissions limit the resource, not the
particular fields within its patch. Field restrictions would need admission
policy in an enterprise deployment.

## k8s/controller.yaml — run the controller and preserve its journal

The PVC requests persistent storage. The Deployment mounts it at `/data`.
`replicas: 1` plus `Recreate` avoids overlapping controller instances during
ordinary upgrades. These settings are not a distributed locking mechanism.
Observation mode is the initial environment setting. The service-account token
is mounted by Kubernetes so the controller can authenticate to the API.

## tests/test_controller.py — check recovery rules

Unit tests exercise opt-in, failure thresholds, approved-image behavior, rollout
completion, persistence across restart, concurrency guards, and recovery checks.
Mocks replace network calls. Passing these tests does not prove a live cluster
recovery; the README walkthrough is the integration test you run locally.

## .github/workflows/ci.yaml — automated repository checks

On pushes and pull requests, GitHub Actions sets up Python, runs tests, and
builds the three images. It does not deploy, publish images, or modify a cluster.
Enterprise CI should also pin action versions to reviewed commit SHAs.

## .gitignore and .dockerignore — exclude generated content

`.gitignore` keeps caches, databases, and local environments out of Git.
`.dockerignore` is effective when its folder is the build context. Here builds
use `app/` and `controller/` as contexts and copy only Python files, so the root
`.dockerignore` is informational for future root-context builds.

## README.md — runbook and scope

Contains prerequisites, build/deploy commands, how to inject failure, how to
enable recovery, how to verify results, cleanup, and known limitations.

## Suggested learning order

1. Explain the status codes in `app/main.py` and run it locally.
2. Build the two images and understand build arguments versus environment values.
3. Read the Deployment/Service and understand labels, replicas, and probes.
4. Read the RBAC to understand exactly what the controller may change.
5. Trace `run()` through `decide()`, `rollback()`, `verify()`, and `Journal`.
6. Run the failed-deployment demo, then inspect the handover report.
