#!/usr/bin/env python3
"""Run the real supervisor across replacement namespaces; only Docker discovery is a fixture."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from failover import Store, run

ROOT = Path(__file__).resolve().parents[1]


def main():
    holders, supervisor = [], None
    names = ['supervisor-old', 'supervisor-new']
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        try:
            for name in names:
                run('ip', 'netns', 'add', name)
                def ns(*args):
                    return run('ip', 'netns', 'exec', name, *args)
                ns('ip', 'link', 'set', 'lo', 'up')
                ns('ip', 'link', 'add', 'wan0', 'type', 'dummy')
                ns('ip', 'address', 'add', '192.0.2.1/24', 'dev', 'wan0')
                ns('ip', 'link', 'set', 'wan0', 'up')
                ns('ip', 'route', 'add', 'default', 'via', '192.0.2.254')
                key = root/(name+'.key')
                key.write_text(run('wg', 'genkey').stdout)
                key.chmod(0o600)
                public = run('wg', 'pubkey', input=run('wg', 'genkey').stdout).stdout.strip()
                ns('ip', 'link', 'add', 'wg0', 'type', 'wireguard')
                ns('ip', 'address', 'add', '10.0.0.1/24', 'dev', 'wg0')
                ns('wg', 'set', 'wg0', 'private-key', str(key), 'listen-port', '51820',
                   'peer', public, 'allowed-ips', '10.0.0.5/32')
                ns('ip', 'link', 'set', 'wg0', 'up')
                holders.append(subprocess.Popen(['ip', 'netns', 'exec', name, 'sleep', '120']))
            # A tiny Docker CLI fixture advertises the currently active namespace process.
            info = root/'inspect.json'
            docker = root/'docker'
            docker.write_text('''#!/usr/bin/python3
import os,sys,subprocess
if sys.argv[1] == 'inspect':
 print(open(os.environ['TEST_DOCKER_INFO']).read())
elif sys.argv[1] == 'exec':
 subprocess.run(['iptables','--version'],check=True)
else:
 sys.exit(2)
''')
            docker.chmod(0o755)
            config = json.loads((ROOT/'config.json').read_text())
            config['interval_seconds'] = .2
            config['container'] = 'fixture'
            config_path, state = root/'config.json', root/'state'
            config_path.write_text(json.dumps(config))
            def advertise(index):
                temporary = root/'next.json'
                temporary.write_text(json.dumps([{'Id': 'fixture', 'State': {'Running': True,
                    'Pid': holders[index].pid, 'StartedAt': str(index)}, 'HostConfig': {'NetworkMode': 'bridge'}}]))
                temporary.replace(info)
            def wait_route(name):
                deadline = time.monotonic()+15
                while time.monotonic() < deadline:
                    result = run('ip', 'netns', 'exec', name, 'ip', '-j', '-4', 'route',
                                 'show', 'table', '201', check=False)
                    if not result.returncode and json.loads(result.stdout):
                        return
                    if supervisor.poll() is not None:
                        raise AssertionError('Supervisor exited unexpectedly')
                    time.sleep(.1)
                raise AssertionError('Supervisor failed to rebuild routing')
            advertise(0)
            log = open(root/'supervisor.log', 'w+')
            env = {**os.environ, 'PATH': str(root)+':'+os.environ['PATH'], 'TEST_DOCKER_INFO': str(info)}
            supervisor = subprocess.Popen(['python3', str(ROOT/'failover.py'), '--config', str(config_path),
                '--state-dir', str(state), 'run'], env=env, stdout=log, stderr=log)
            wait_route(names[0])
            advertise(1)
            holders[0].terminate(); holders[0].wait(timeout=5)
            run('ip', 'netns', 'del', names[0])
            wait_route(names[1])
            print('PASS real supervisor rebuilds policy in the replacement namespace', flush=True)
            config_path.write_text('{invalid')
            deadline = time.monotonic()+10
            while time.monotonic() < deadline:
                store = Store(state)
                status = store.get('status', {})
                store.db.close()
                if status.get('error'):
                    break
                time.sleep(.1)
            assert status.get('selected') == 'hub WAN' and status.get('error')
            assert supervisor.poll() is None
            # Restarting with bad JSON must still find the last known namespace from persisted state.
            supervisor.terminate(); supervisor.wait(timeout=5)
            supervisor = subprocess.Popen(['python3', str(ROOT/'failover.py'), '--config', str(config_path),
                '--state-dir', str(state), 'run'], env=env, stdout=log, stderr=log)
            previous = status['updated']
            deadline = time.monotonic()+10
            while time.monotonic() < deadline:
                store = Store(state)
                status = store.get('status', {})
                store.db.close()
                if status.get('updated', 0) > previous:
                    break
                time.sleep(.1)
            assert status.get('updated', 0) > previous and supervisor.poll() is None
            print('PASS bad configuration reload/restart retains the owned WAN fallback', flush=True)
            log.close()
        finally:
            if supervisor and supervisor.poll() is None:
                supervisor.terminate(); supervisor.wait(timeout=10)
            for holder in holders:
                if holder.poll() is None:
                    holder.terminate(); holder.wait(timeout=5)
            for name in names:
                run('ip', 'netns', 'del', name, check=False)


if __name__ == '__main__':
    main()
