#!/usr/bin/env python3
"""Test real persistent systemd timers in an isolated systemd test container.
The timer invokes a fixture marker writer; network rollback is tested separately.
"""
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import upgrade as u


def wait_for(path, seconds=20):
    deadline = time.monotonic()+seconds
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(.1)
    assert path.exists(), f'Timer did not invoke its service: {path}'


def main():
    with tempfile.TemporaryDirectory(prefix='vmutils-timer-') as directory:
        root = Path(directory)
        # No Docker daemon or host network is involved in this timer test.
        fixture = root/'upgrade.py'
        fixture.write_text('import pathlib,sys\npathlib.Path(sys.argv[2],"fired").touch()\n')
        meta = {'unit': 'vmutils-rollback-timer-test', 'deadline': int(time.time())+3}
        try:
            u.arm_rollback(root, meta)
            u.command('systemd-analyze', 'verify', u.SYSTEMD/(meta['unit']+'.timer'),
                      u.SYSTEMD/(meta['unit']+'.service'))
            wait_for(root/'fired')
            print('PASS real systemd rollback deadline invocation', flush=True)
            u.disarm_rollback(meta)
            (root/'fired').unlink()
            meta['deadline'] = int(time.time())+3
            u.arm_rollback(root, meta)
            # Reproduce a reboot's timer downtime: persistent units are reloaded after the deadline.
            u.command('systemctl', 'stop', meta['unit']+'.timer')
            time.sleep(4)
            u.command('systemctl', 'daemon-reload')
            u.command('systemctl', 'start', meta['unit']+'.timer')
            wait_for(root/'fired')
            print('PASS overdue persistent timer fires after reload/restart', flush=True)
            u.disarm_rollback(meta)
            (root/'fired').unlink()
            meta['deadline'] = int(time.time())+3
            u.arm_rollback(root, meta)
            u.disarm_rollback(meta)
            time.sleep(4)
            assert not (root/'fired').exists()
            print('PASS confirmation disarms the real timer', flush=True)
        finally:
            u.disarm_rollback(meta)


if __name__ == '__main__':
    if len(sys.argv) == 2:
        root = Path('/var/lib/vmutils-timer-restart-test')
        if sys.argv[1] == 'prepare-restart':
            root.mkdir(exist_ok=True)
            (root/'upgrade.py').write_text('import pathlib,sys\npathlib.Path(sys.argv[2],"fired").touch()\n')
            meta = {'unit': 'vmutils-rollback-restart-test', 'deadline': int(time.time())+15}
            (root/'metadata.json').write_text(json.dumps(meta))
            u.arm_rollback(root, meta)
            print('Persistent restart test armed', flush=True)
        elif sys.argv[1] == 'verify-restart':
            meta = json.loads((root/'metadata.json').read_text())
            try:
                wait_for(root/'fired')
                print('PASS overdue timer survives a complete systemd/container restart', flush=True)
            finally:
                u.disarm_rollback(meta)
        else:
            raise SystemExit('Unknown test phase')
    else:
        main()
