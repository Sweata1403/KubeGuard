"""Run with python tests/e2e_recovery.py; creates its own disposable kind cluster."""
import json
from pathlib import Path
import subprocess
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]


def command(*args):
    result = subprocess.run(args, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=300)
    if result.returncode:
        raise RuntimeError(f"{' '.join(args)}\n{result.stdout}")
    return result.stdout


def wait_for(description, check, seconds=180):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            print(description, flush=True)
            return
        time.sleep(2)
    raise TimeoutError(description)


def main():
    name = 'kubeguard-e2e-' + uuid.uuid4().hex[:8]
    context = 'kind-' + name
    artifacts = ROOT / 'e2e-artifacts'
    artifacts.mkdir(exist_ok=True)
    config = artifacts / 'kubeconfig'

    def kubectl(*args):
        return command('kubectl', '--kubeconfig', str(config), '--context', context,
                       '-n', 'kubeguard-demo', *args)

    def deployment():
        return json.loads(kubectl('get', 'deployment', 'demo', '-o', 'json'))

    def image():
        return deployment()['spec']['template']['spec']['containers'][0]['image']

    try:
        for version in ('v1', 'v2'):
            command('docker', 'build', '--build-arg', f'APP_VERSION={version}',
                    '-t', f'kubeguard-demo:{version}', 'app')
        command('docker', 'build', '-t', 'kubeguard-controller:v1', 'controller')
        # A private kubeconfig avoids changing the operator's current context.
        command('kind', 'create', 'cluster', '--name', name, '--wait', '180s',
                '--kubeconfig', str(config))
        command('kind', 'load', 'docker-image', 'kubeguard-demo:v1',
                'kubeguard-demo:v2', 'kubeguard-controller:v1', '--name', name)
        for manifest in ('namespace', 'rbac', 'demo', 'controller'):
            kubectl('apply', '-f', f'k8s/{manifest}.yaml')
        for target in ('demo', 'kubeguard'):
            kubectl('rollout', 'status', f'deployment/{target}', '--timeout=180s')

        kubectl('set', 'image', 'deployment/demo', 'demo=kubeguard-demo:v2')
        kubectl('rollout', 'status', 'deployment/demo', '--timeout=120s')
        broken = deployment()['metadata']
        incident = broken['uid'] + ':' + str(broken['generation'])

        def observed():
            entries = kubectl('logs', 'deployment/kubeguard').splitlines()
            return any(json.loads(line).get('status') == 'would_rollback'
                       and json.loads(line).get('incident') == incident
                       for line in entries if line.startswith('{'))

        wait_for('Observation mode detected the broken release', observed)
        if image() != 'kubeguard-demo:v2':
            raise AssertionError('Observation mode changed the application image')
        kubectl('set', 'env', 'deployment/kubeguard', 'EXECUTE=true')
        kubectl('rollout', 'status', 'deployment/kubeguard', '--timeout=180s')

        # Check the persisted row for this exact incident, not an older log entry.
        query = ("import json,sqlite3; c=sqlite3.connect('/data/incidents.db'); "
                 "print(json.dumps(c.execute('SELECT status FROM incidents WHERE key=?', "
                 f"({incident!r},)).fetchone()))")

        def recovered():
            row = json.loads(kubectl('exec', 'deployment/kubeguard', '--',
                                     'python', '-c', query))
            if row and row[0] == 'needs_engineer':
                raise AssertionError('Controller recorded needs_engineer')
            return row == ['recovered']

        wait_for('Persisted recovery verified', recovered)
        if image() != 'kubeguard-demo:v1':
            raise AssertionError('Approved image was not restored')
        kubectl('rollout', 'status', 'deployment/demo', '--timeout=120s')
        check = ("import json,urllib.request; "
                 "r=urllib.request.urlopen('http://demo:8080/',timeout=5); "
                 "assert r.status==200; assert json.load(r)['version']=='v1'")
        kubectl('exec', 'deployment/kubeguard', '--', 'python', '-c', check)
        report = kubectl('exec', 'deployment/kubeguard', '--', 'python', 'report.py')
        (artifacts / 'report.md').write_text(report, encoding='utf-8')
        print('PASS: observation, rollback, journal, rollout, and HTTP response', flush=True)
    finally:
        for filename, args in (
            ('pods.txt', ('get', 'pods', '-o', 'wide')),
            ('events.txt', ('get', 'events', '--sort-by=.metadata.creationTimestamp')),
            ('controller.log', ('logs', 'deployment/kubeguard')),
            ('deployments.json', ('get', 'deployments', '-o', 'json')),
        ):
            try:
                output = kubectl(*args)
            except Exception as error:
                output = str(error)
            (artifacts / filename).write_text(output, encoding='utf-8')
        # Never delete a user cluster: only the unique name created by this run.
        command('kind', 'delete', 'cluster', '--name', name)
        if (artifacts / 'kubeconfig').exists():
            (artifacts / 'kubeconfig').unlink()


if __name__ == '__main__':
    main()
