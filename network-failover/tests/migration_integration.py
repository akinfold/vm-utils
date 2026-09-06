#!/usr/bin/env python3
"""Real kernel migration/rollback test, with Docker/systemd/API adapters replaced.
Run in the same isolated container as integration.py; never on a production host.
"""
import json
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import upgrade as u
from failover import run


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        node = 'migration'
        run('ip', 'netns', 'add', node)
        try:
            def net(*args, **kw):
                return run('ip', 'netns', 'exec', node, *map(str, args), **kw)
            net('ip', 'link', 'set', 'lo', 'up')
            net('ip', 'link', 'add', 'wan0', 'type', 'dummy')
            net('ip', 'address', 'add', '192.0.2.1/24', 'dev', 'wan0')
            net('ip', 'link', 'set', 'wan0', 'up')
            net('ip', 'route', 'add', 'default', 'via', '192.0.2.254')
            keys = []
            for _ in range(3):
                private = run('wg', 'genkey').stdout.strip()
                keys.append((private, run('wg', 'pubkey', input=private).stdout.strip()))
            volume = root/'wireguard'
            volume.mkdir()
            old = f'''[Interface]
PrivateKey = {keys[0][0]}
ListenPort = 51820
Address = 10.0.0.1/24
PreUp = iptables -t nat -A POSTROUTING -s 10.0.0.0/24 -o wan0 -j MASQUERADE
PostDown = iptables -t nat -D POSTROUTING -s 10.0.0.0/24 -o wan0 -j MASQUERADE
[Peer]
PublicKey = {keys[1][1]}
AllowedIPs = 10.0.0.5/32
[Peer]
PublicKey = {keys[2][1]}
AllowedIPs = 0.0.0.0/0
'''
            (volume/'wg0.conf').write_text(old)
            (volume/'wg0.conf').chmod(0o600)
            net('wg-quick', 'up', volume/'wg0.conf')
            original_rules = net('ip', '-N', '-j', '-4', 'rule', 'show').stdout
            assert any(str(r.get('table')) == '51820' for r in json.loads(original_rules))
            config_path, state, library = root/'config.json', root/'state', root/'lib'
            source_root = u.ROOT
            service = {'active': False, 'enabled': False}
            calls = []
            fail_report = [False]

            class Host:
                def __init__(self, container):
                    self.container = container
                    self.directory = volume
                def configs(self):
                    return {p.stem: p.read_text() for p in volume.glob('*.conf')}
                def keypair(self):
                    private = run('wg', 'genkey').stdout.strip()
                    return private, run('wg', 'pubkey', input=private).stdout.strip()
                def validate_ports(self, desired):
                    pass
                def report(self, interfaces, script=None):
                    calls.append('report')
                    if fail_report[0] and config_path.exists():
                        fail_report[0] = False
                        raise RuntimeError('Injected controller report failure')
                def docker(self, *args, **kw):
                    calls.append(args)
                    return type('Result', (), {'returncode': 0, 'stdout': ''})()
                def quick(self, action, name):
                    return net('wg-quick', action, volume/(name+'.conf'))
                def net(self, *args, **kw):
                    if str(args[0]) == '/usr/bin/python3':
                        args = (args[0], args[1], '--config', config_path,
                                '--state-dir', state, 'once')
                    return net(*args, **kw)

            original_command = u.command
            def command(*args, **kw):
                calls.append(args)
                if args[0] == 'apt-get' or args[0] == 'systemd-run':
                    return type('Result', (), {'returncode': 0})()
                if args[0] == 'systemctl':
                    op = args[1]
                    if op == 'is-active':
                        ok = service['active'] if args[2] == u.SERVICE else False
                    elif op == 'is-enabled':
                        ok = service['enabled']
                    else:
                        ok = True
                        if op in ('start', 'restart', 'stop') and args[2] == u.SERVICE:
                            service['active'] = op != 'stop'
                        if op in ('enable', 'disable'):
                            service['enabled'] = op == 'enable'
                    return type('Result', (), {'returncode': 0 if ok else 1})()
                if args[0] == 'env':
                    library.mkdir(exist_ok=True)
                    shutil.copy2(source_root/'failover.py', library/'failover.py')
                    cfg = json.loads((source_root/'config.json').read_text())
                    cfg['probe_timeout_seconds'] = 1
                    cfg['internet_targets'] = [{'ip': '198.18.0.1', 'port': 443, 'server_name': 'health.test'}]
                    config_path.write_text(json.dumps(cfg))
                    u.UNIT_FILE.write_text('fixture unit')
                    u.TABLES.write_text('200 vpn_candidates\n201 vpn_active\n')
                    service['enabled'] = True
                    return type('Result', (), {'returncode': 0})()
                return original_command(*args, **kw)

            replacements = dict(Host=Host, command=command, CONFIG=config_path, STATE=state,
                LIB=library, UNIT_FILE=root/'unit', TABLES=root/'tables', BACKUPS=root/'backups')
            with patch.multiple(u, **replacements):
                host = Host('fixture')
                topology = {'hub_endpoint': '192.0.2.1', 'exits': [
                    {'name': 'a', 'public_key': keys[2][1], 'transit': '10.250.1.0/30', 'hub_port': 51821}]}
                desired, bundles = u.build_hub(topology, host.configs(), host.keypair)
                plan_dir = root/'plan'
                u.save_plan(host, 'hub', desired, plan_dir, bundles)
                u.apply(plan_dir/'plan.json', 300)
                assert set(net('wg', 'show', 'interfaces').stdout.split()) == {'wg0', 'wg-exit-a'}
                assert net('wg', 'show', 'wg0', 'peers').stdout.strip() == keys[1][1]
                policy = json.loads(config_path.read_text())
                u.validate_policy_space(host, policy)
                rules = json.loads(net('ip', '-N', '-j', '-4', 'rule', 'show').stdout)
                assert not any(str(r.get('table')) == '51820' for r in rules)
                route = json.loads(net('ip', '-j', '-4', 'route', 'show', 'table', '201').stdout)
                assert route[0]['dev'] == 'wan0'
                backup = next(u.BACKUPS.iterdir())
                assert (backup/'pending').exists()
                assert any(c[0] == 'systemd-run' for c in calls if isinstance(c, tuple))
                u.rollback(backup)
                assert (volume/'wg0.conf').read_text() == old
                assert set(net('wg', 'show', 'interfaces').stdout.split()) == {'wg0'}
                assert net('ip', '-N', '-j', '-4', 'rule', 'show').stdout == original_rules
                assert not config_path.exists()
                assert not service['active']
                assert (backup/'rolled-back').exists()
                print('PASS legacy shared-interface upgrade, automatic WAN fallback, and rollback', flush=True)
                # A post-cutover controller failure must restore the live network automatically.
                fail_report[0] = True
                try:
                    u.apply(plan_dir/'plan.json', 300)
                    raise AssertionError('Injected failure did not stop the apply')
                except RuntimeError as exc:
                    assert 'Injected' in str(exc)
                assert (volume/'wg0.conf').read_text() == old
                assert net('ip', '-N', '-j', '-4', 'rule', 'show').stdout == original_rules
                assert not list(u.BACKUPS.glob('*/pending'))
                print('PASS automatic rollback after a post-cutover controller-report failure', flush=True)
                # Repeat the same plan after rollback and exercise explicit confirmation.
                u.apply(plan_dir/'plan.json', 300)
                pending = next(u.BACKUPS.glob('*/pending')).parent
                u.confirm(pending)
                assert (pending/'confirmed').exists()
                assert not (pending/'pending').exists()
                print('PASS repeatable upgrade and explicit rollback-timer confirmation', flush=True)
                # Apply the generated public bundle to a deployed exit, preserving its key.
                exit_ns = 'migration-exit'
                run('ip', 'netns', 'add', exit_ns)
                try:
                    def exit_net(*args, **kw):
                        return run('ip', 'netns', 'exec', exit_ns, *map(str, args), **kw)
                    exit_net('ip', 'link', 'set', 'lo', 'up')
                    exit_net('ip', 'link', 'add', 'wan0', 'type', 'dummy')
                    exit_net('ip', 'address', 'add', '192.0.2.2/24', 'dev', 'wan0')
                    exit_net('ip', 'link', 'set', 'wan0', 'up')
                    exit_net('ip', 'route', 'add', 'default', 'via', '192.0.2.254')
                    old_exit = u.render({'PrivateKey': [keys[2][0]], 'ListenPort': ['51820'],
                        'Address': ['10.0.0.246/24']}, [{'PublicKey': [keys[0][1]],
                        'AllowedIPs': ['10.0.0.0/24']}])
                    exit_dir = root/'exit'
                    exit_dir.mkdir()
                    exit_file = exit_dir/'wg0.conf'
                    exit_file.write_text(old_exit)
                    exit_file.chmod(0o600)
                    exit_net('wg-quick', 'up', exit_file)
                    new_exit = u.build_exit(bundles['a'], old_exit,
                        lambda private: run('wg', 'pubkey', input=private).stdout.strip())
                    exit_net('wg-quick', 'down', exit_file)
                    exit_file.write_text(new_exit)
                    exit_net('wg-quick', 'up', exit_file)
                    exit_config = {**json.loads((source_root/'config.json').read_text()), 'role': 'exit'}
                    (exit_dir/'config.json').write_text(json.dumps(exit_config))
                    exit_net('python3', source_root/'failover.py', '--config', exit_dir/'config.json',
                             '--state-dir', exit_dir/'state', 'once')
                    assert exit_net('wg', 'show', 'wg0', 'public-key').stdout.strip() == keys[2][1]
                    reverse = json.loads(exit_net('ip', '-j', '-4', 'route', 'get', '10.0.0.5').stdout)
                    assert reverse[0]['dev'] == 'wg0'
                    assert 'VMUTILS_NAT' in exit_net('iptables-save').stdout
                    print('PASS deployed exit key preservation and automatic reverse routes/NAT', flush=True)
                finally:
                    run('ip', 'netns', 'del', exit_ns, check=False)

        finally:
            run('ip', 'netns', 'del', node, check=False)


if __name__ == '__main__':
    main()
