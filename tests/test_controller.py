import copy
import tempfile
import unittest
from unittest.mock import patch
from controller.main import Journal, Kubernetes, decide, settled, verify


def deployment():
    return {"metadata": {"uid": "a", "generation": 2, "resourceVersion": "10", "annotations": {"kubeguard.example/enabled": "true", "kubeguard.example/approved-image": "demo:v1"}}, "spec": {"replicas": 3, "template": {"spec": {"containers": [{"name": "demo", "image": "demo:v2"}]}}}, "status": {"observedGeneration": 2, "updatedReplicas": 3, "readyReplicas": 3, "availableReplicas": 3, "replicas": 3}}


class RecoveryTests(unittest.TestCase):
    def test_missing_approval_blocks_recovery(self):
        for value in (None, ''):
            with self.subTest(value=value):
                d = deployment()
                if value is None:
                    del d['metadata']['annotations']['kubeguard.example/approved-image']
                else:
                    d['metadata']['annotations']['kubeguard.example/approved-image'] = value
                self.assertIsNone(decide(d, 10))

    def test_missing_opt_in_blocks_recovery(self):
        d = deployment()
        del d['metadata']['annotations']['kubeguard.example/enabled']
        self.assertIsNone(decide(d, 10))

    def test_unhealthy_business_endpoint_expires_verification(self):
        api = object.__new__(Kubernetes)
        d = deployment()
        d['spec']['template']['spec']['containers'][0]['image'] = 'demo:v1'
        with patch.object(api, 'request', return_value=d), \
                patch('controller.main.healthy', return_value=False) as health, \
                patch('controller.main.time.sleep'), \
                patch('controller.main.time.monotonic', side_effect=[0, 1, 2, 4]):
            self.assertFalse(verify(api, 'http://unused', 'a', 'demo:v1', seconds=3))
            self.assertEqual(health.call_count, 2)

    def test_policy_requires_opt_in_and_failure_threshold(self):
        d = deployment()
        self.assertIsNone(decide(d, 2))
        self.assertEqual(decide(d, 3), "demo:v1")
        d["metadata"]["annotations"]["kubeguard.example/enabled"] = "false"
        self.assertIsNone(decide(d, 10))

    def test_no_action_when_already_on_approved_image(self):
        d = deployment()
        d["spec"]["template"]["spec"]["containers"][0]["image"] = "demo:v1"
        self.assertIsNone(decide(d, 99))

    def test_rollout_must_finish(self):
        d = deployment()
        self.assertTrue(settled(d))
        d["status"]["updatedReplicas"] = 2
        self.assertFalse(settled(d))

    def test_attempt_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(directory + "/db")
            journal.record("a:2", "attempting", "test")
            journal.db.close()
            restored = Journal(directory + "/db")
            self.assertTrue(restored.attempted("a:2"))
            restored.db.close()

    def test_patch_has_concurrency_guards(self):
        api = object.__new__(Kubernetes)
        with patch.object(api, "request") as request:
            api.rollback(deployment(), "demo:v1")
            body = request.call_args.args[1]
            self.assertEqual(body[1], {"op": "test", "path": "/metadata/resourceVersion", "value": "10"})
            self.assertEqual(body[-1]["value"], "demo:v1")

    def test_verification_aborts_on_new_deployment(self):
        api = object.__new__(Kubernetes)
        d = copy.deepcopy(deployment())
        d["metadata"]["uid"] = "replacement"
        with patch.object(api, "request", return_value=d):
            self.assertFalse(verify(api, "http://unused", "a", "demo:v1"))

    def test_recovered_requires_repeated_business_checks(self):
        api = object.__new__(Kubernetes)
        d = deployment()
        d["spec"]["template"]["spec"]["containers"][0]["image"] = "demo:v1"
        with patch.object(api, "request", return_value=d), patch("controller.main.healthy", return_value=True) as health, patch("controller.main.time.sleep"):
            self.assertTrue(verify(api, "http://unused", "a", "demo:v1"))
            self.assertEqual(health.call_count, 5)


if __name__ == "__main__":
    unittest.main()
