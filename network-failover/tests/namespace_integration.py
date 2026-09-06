#!/usr/bin/env python3
"""Verify namespace pinning after its process exits, without a Docker daemon."""
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import failover as f


def main():
    name = 'pin-test'
    f.run('ip', 'netns', 'add', name)
    process = subprocess.Popen(['ip', 'netns', 'exec', name, 'sleep', '60'])
    try:
        f.run('ip', 'netns', 'exec', name, 'ip', 'link', 'add', 'marker0', 'type', 'dummy')
        # Wait for ip netns exec to enter its target before inspecting /proc/PID/ns/net.
        for _ in range(100):
            result = f.run('nsenter', '--target', str(process.pid), '--net', '--',
                           'ip', 'link', 'show', 'marker0', check=False)
            if not result.returncode:
                break
        assert result.returncode == 0
        info = {'Id': 'fixture', 'State': {'Pid': process.pid, 'Running': True, 'StartedAt': 'fixture'},
                'HostConfig': {'NetworkMode': 'bridge'}}
        with patch.object(f, 'inspect_container', return_value=info):
            with f.container_namespace('fixture') as fd:
                process.terminate(); process.wait(timeout=5)
                subprocess.run(['nsenter', f'--net=/proc/self/fd/{fd}', '--',
                                'ip', 'link', 'show', 'marker0'], pass_fds=(fd,), check=True, capture_output=True)
        print('PASS pinned namespace remains correct after container PID exits', flush=True)
        with patch.object(f, 'inspect_container', return_value={**info, 'Id': 'replacement'}):
            try:
                with f.container_namespace('fixture', f.container_identity(info)):
                    raise AssertionError('Changed identity was accepted')
            except RuntimeError:
                pass
        print('PASS migration refuses a changed container identity', flush=True)
    finally:
        if process.poll() is None:
            process.terminate(); process.wait(timeout=5)
        f.run('ip', 'netns', 'del', name, check=False)


if __name__ == '__main__':
    main()
