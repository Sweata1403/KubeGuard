"""Single-workload recovery controller. Uses only the Python standard library."""
import json
import os
import sqlite3
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TOKEN_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
PREFIX = "kubeguard.example/"

def load_settings(environ=None):
    """Read positive integer settings once at startup."""
    environ = os.environ if environ is None else environ

    defaults = {
        "FAILURE_THRESHOLD": 3,
        "POLL_INTERVAL_SECONDS": 5,
        "RECOVERY_TIMEOUT_SECONDS": 120,
        "RECOVERY_SUCCESS_THRESHOLD": 5,
        "RECOVERY_CHECK_INTERVAL_SECONDS": 2,
    }

    settings = {}

    for name, default in defaults.items():
        raw = environ.get(name, str(default))

        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ValueError(
                f"{name} must be a positive integer; got {raw!r}"
            ) from None

        if value <= 0:
            raise ValueError(
                f"{name} must be greater than zero; got {raw!r}"
            )

        settings[name] = value

    return settings

def decide(deployment, failures, threshold=3):
    """Policy decision, isolated from network calls so it is easy to test."""
    annotations = deployment["metadata"].get("annotations", {})
    containers = deployment["spec"]["template"]["spec"]["containers"]
    # The starter supports one explicitly named, image-only demo container.
    if len(containers) != 1 or containers[0]["name"] != "demo":
        return None
    approved = annotations.get(PREFIX + "approved-image")
    if annotations.get(PREFIX + "enabled") != "true" or not approved:
        return None
    if approved == containers[0]["image"] or failures < threshold:
        return None
    return approved


def settled(deployment):
    status = deployment.get("status", {})
    desired = deployment["spec"].get("replicas", 1)
    return desired > 0 and all((
        status.get("observedGeneration", 0) >= deployment["metadata"]["generation"],
        status.get("updatedReplicas", 0) == desired,
        status.get("readyReplicas", 0) == desired,
        status.get("availableReplicas", 0) == desired,
        status.get("replicas", 0) == desired,
    ))


class Kubernetes:
    def __init__(self):
        namespace = os.getenv("NAMESPACE", "kubeguard-demo")
        deployment = os.getenv("DEPLOYMENT", "demo")
        if namespace != "kubeguard-demo" or deployment != "demo":
            raise ValueError("Starter is restricted to kubeguard-demo/demo")
        self.url = "https://kubernetes.default.svc/apis/apps/v1/namespaces/" + namespace + "/deployments/" + deployment
        self.context = ssl.create_default_context(cafile=TOKEN_DIR + "/ca.crt")

    def request(self, method="GET", body=None):
        # Re-read projected token on every call to support token rotation.
        with open(TOKEN_DIR + "/token") as token_file:
            token = token_file.read().strip()
        headers = {"Authorization": "Bearer " + token}
        if body is not None:
            headers["Content-Type"] = "application/json-patch+json"
        request = Request(self.url, data=None if body is None else json.dumps(body).encode(), headers=headers, method=method)
        with urlopen(request, context=self.context, timeout=10) as response:
            return json.load(response)

    def rollback(self, deployment, approved):
        # Atomic tests reject a patch if another actor changed the Deployment.
        metadata = deployment["metadata"]
        return self.request("PATCH", [
            {"op": "test", "path": "/metadata/uid", "value": metadata["uid"]},
            {"op": "test", "path": "/metadata/resourceVersion", "value": metadata["resourceVersion"]},
            {"op": "test", "path": "/spec/template/spec/containers/0/image", "value": deployment["spec"]["template"]["spec"]["containers"][0]["image"]},
            {"op": "replace", "path": "/spec/template/spec/containers/0/image", "value": approved},
        ])


def healthy(url, expected_version=None):
    try:
        with urlopen(url, timeout=3) as response:
            body = json.load(response)
            return response.status == 200 and (expected_version is None or body.get("version") == expected_version)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return False


class Journal:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS incidents (key TEXT PRIMARY KEY, status TEXT, detail TEXT, updated REAL)")
        self.db.commit()

    def attempted(self, key):
        return self.db.execute("SELECT 1 FROM incidents WHERE key = ?", (key,)).fetchone() is not None

    def record(self, key, status, detail):
        with self.db:
            self.db.execute("INSERT INTO incidents VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET status=excluded.status, detail=excluded.detail, updated=excluded.updated", (key, status, detail, time.time()))
        print(json.dumps({"incident": key, "status": status, "detail": detail}), flush=True)


def verify(
    api,
    url,
    uid,
    approved,
    seconds=120,
    success_threshold=5,
    check_interval=2,
):
    deadline = time.monotonic() + seconds
    successes = 0

    while time.monotonic() < deadline:
        deployment = api.request()
        image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]

        if deployment["metadata"]["uid"] != uid or image != approved:
            return False

        # All replicas must be replaced and business requests must succeed.
        successes = (
            successes + 1
            if settled(deployment) and healthy(url, "v1")
            else 0
        )

        if successes >= success_threshold:
            return True

        time.sleep(check_interval)

    return False


def run():
    settings = load_settings()
    print(
        json.dumps({"status": "configured", "settings": settings}),
        flush=True,
    )

    api = Kubernetes()
    journal = Journal(os.getenv("JOURNAL", "/data/incidents.db"))
    url = "http://demo.kubeguard-demo.svc.cluster.local:8080/"
    execute = os.getenv("EXECUTE", "false").lower() == "true"
    failures, observed = 0, None
    while True:
        try:
            deployment = api.request()
            meta = deployment["metadata"]
            key = meta["uid"] + ":" + str(meta["generation"])
            if key != observed:
                observed, failures = key, 0
            failures = 0 if healthy(url) else failures + 1
            approved = decide(
                deployment, 
                failures,
                threshold=settings["FAILURE_THRESHOLD"],
                )
            if approved and not journal.attempted(key):
                if not execute:
                    print(json.dumps({"status": "would_rollback", "incident": key, "image": approved}), flush=True)
                else:
                    # Persist before mutation: a restart must not repeat this action.
                    journal.record(key, "attempting", "Restoring approved image " + approved)
                    try:
                        api.rollback(deployment, approved)
                        recovered = verify(
                            api,
                            url,
                            meta["uid"],
                            approved,
                            seconds=settings["RECOVERY_TIMEOUT_SECONDS"],
                            success_threshold=settings[
                                "RECOVERY_SUCCESS_THRESHOLD"
                            ],
                            check_interval=settings[
                                "RECOVERY_CHECK_INTERVAL_SECONDS"
                            ],
                        )
                        journal.record(key, "recovered" if recovered else "needs_engineer", "Recovery verified" if recovered else "Recovery deadline exceeded or deployment changed")
                    except Exception as error:
                        journal.record(key, "needs_engineer", type(error).__name__ + ": " + str(error))
            print(json.dumps({"status": "observing", "incident": key, "consecutive_failures": failures}), flush=True)
        except Exception as error:
            print(json.dumps({"status": "controller_error", "detail": str(error)}), flush=True)
        time.sleep(settings["POLL_INTERVAL_SECONDS"])


if __name__ == "__main__":
    run()
