#!/usr/bin/env python3
"""Prepare, apply and roll back upgrades of existing vmutils WireGuard nodes."""
import argparse
from datetime import datetime, timezone
import base64
import hashlib
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import sys
import signal
import sqlite3
import time
import uuid

from failover import (NAME, PROTOCOL, Store, load_config, inspect_container,
                      container_identity, container_namespace, agent_firewall_backend)
from provision import atomic_write

ROOT = Path(__file__).resolve().parent
CONFIG = Path('/etc/vmutils/network-failover/config.json')
STATE = Path('/var/lib/vmutils-network-failover')
LIB = Path('/usr/local/lib/vmutils/network-failover')
UNIT_FILE = Path('/etc/systemd/system/vmutils-network-failover.service')
TABLES = Path('/etc/iproute2/rt_tables')
SERVICE = 'vmutils-network-failover'
BACKUPS = Path('/var/lib/vmutils-network-migrations')
SYSTEMD = Path('/etc/systemd/system')


def command(*args, input=None, check=True, timeout=60, pass_fds=()):
    result = subprocess.run([str(a) for a in args], input=input, text=True,
                            capture_output=True, timeout=timeout, pass_fds=pass_fds)
    if check and result.returncode:
        # wg-quick can echo PrivateKey and hook commands: never include stderr here.
        raise RuntimeError(f'{Path(str(args[0])).name} operation failed (exit {result.returncode})')
    return result


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest() if text is not None else None


def public_key(key):
    try:
        if len(base64.b64decode(key, validate=True)) != 32:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('Invalid WireGuard public key') from None
    return key


def parse_config(text):
    sections = []
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        if line in ('[Interface]', '[Peer]'):
            sections.append((line[1:-1], {}))
        elif sections and '=' in line:
            key, value = (s.strip() for s in line.split('=', 1))
            sections[-1][1].setdefault(key, []).append(value)
        else:
            raise ValueError('Unsupported WireGuard configuration syntax')
    if not sections or sections[0][0] != 'Interface' or any(s[0] != 'Peer' for s in sections[1:]):
        raise ValueError('Expected one Interface followed by Peer sections')
    interface, peers = sections[0][1], [s[1] for s in sections[1:]]
    supported = {'PrivateKey', 'ListenPort', 'Address', 'MTU', 'Table', 'FwMark',
                 'SaveConfig', 'DNS', 'PreUp', 'PostUp', 'PreDown', 'PostDown'}
    if set(interface)-supported:
        raise ValueError('Unsupported interface settings; review before migration')
    for peer in peers:
        if set(peer)-{'PublicKey', 'PresharedKey', 'AllowedIPs', 'Endpoint', 'PersistentKeepalive'}:
            raise ValueError('Unsupported peer settings; review before migration')
        public_key(one(peer, 'PublicKey'))
    if one(interface, 'SaveConfig', 'false').lower() != 'false' or interface.get('DNS'):
        raise ValueError('Migration requires SaveConfig=false and no wg-quick DNS setting')
    for key in ('PreUp', 'PostUp', 'PreDown', 'PostDown'):
        for hook in interface.get(key, []):
            if not legacy_hook(hook):
                raise ValueError('Unrecognized firewall hook; review and remove it before migration')
    public_key(one(interface, 'PrivateKey'))
    if one(interface, 'Table', 'auto') not in ('auto', 'off'):
        raise ValueError('Custom routing tables require an explicit migration plan')
    if any(n.version != 4 for n in networks(interface, 'Address')):
        raise ValueError('Automatic migration supports IPv4-only interfaces')
    return interface, peers


def one(section, key, default=None):
    values = section.get(key, [])
    if len(values) > 1 or (not values and default is None):
        raise ValueError(f'Expected one {key} value')
    return values[0] if values else default


def networks(section, key):
    return [ipaddress.ip_network(v.strip(), strict=False)
            for line in section.get(key, []) for v in line.split(',') if v.strip()]


def legacy_hook(hook):
    """Only automatically remove known legacy vmutils firewall hooks."""
    patterns = [
        r'iptables -t nat -[AD] POSTROUTING -s [0-9./]+ -o [\w.-]+ -j MASQUERADE',
        r'iptables -t mangle -[AD] PREROUTING -i [\w.-]+ -j MARK --set-mark 0x30',
        r'iptables -t nat -[AD] POSTROUTING ! -o [\w.-]+ -m mark --mark 0x30 -j MASQUERADE',
        r'iptables -[AD] INPUT -p udp -m udp --dport [0-9]+ -j ACCEPT',
        r'iptables -[AD] FORWARD -[io] [\w.-]+ -j ACCEPT',
    ]
    return any(re.fullmatch(p, hook.strip()) for p in patterns)


def render(interface, peers):
    lines = []
    for name, values in [('Interface', interface)]+[('Peer', p) for p in peers]:
        lines.append(f'[{name}]')
        lines.extend(f'{k} = {v}' for k, vs in values.items() for v in vs)
        lines.append('')
    return '\n'.join(lines)


def clean_interface(old, address=None, table='off'):
    new = {k: v for k, v in old.items() if k in ('PrivateKey', 'ListenPort', 'MTU', 'Address')}
    new['Table'] = [table]
    if address:
        new['Address'] = [address]
    return new


def build_hub(topology, current, keypair):
    client = topology.get('client_interface', 'wg0')
    if not NAME.fullmatch(client) or client.startswith('wg-exit-'):
        raise ValueError('Invalid client interface')
    endpoint = topology['hub_endpoint']
    if not re.fullmatch(r'[A-Za-z0-9.-]+', endpoint):
        raise ValueError('Use an IPv4 address or DNS hostname for hub_endpoint')
    iface, peers = parse_config(current[client])
    if not 1024 <= int(one(iface, 'ListenPort', '0')) <= 65535:
        raise ValueError('Hub migration requires a fixed non-privileged ListenPort')
    exits = topology['exits']
    keys = [public_key(e['public_key']) for e in exits]
    if len(keys) != len(set(keys)) or not exits:
        raise ValueError('Specify at least one exit, with distinct public keys')
    for peer in peers:
        if one(peer, 'PublicKey') in keys and ipaddress.ip_network('0.0.0.0/0') not in networks(peer, 'AllowedIPs'):
            raise ValueError('An exit identity matches a client peer; refusing to remove it')
    clients = [p for p in peers if one(p, 'PublicKey') not in keys]
    sources = sorted({str(n) for p in clients for n in networks(p, 'AllowedIPs')})
    if not sources or any(ipaddress.ip_network(n).version != 4 or ipaddress.ip_network(n).prefixlen == 0 for n in sources):
        raise ValueError('Every IPv4 default exit must be listed; client networks must be non-default IPv4')
    # Cover future clients in the hub's attached VPN subnet, plus routed client ranges.
    sources = [str(n) for n in ipaddress.collapse_addresses(
        [ipaddress.ip_network(n) for n in sources]+networks(iface, 'Address'))]
    if any(ipaddress.ip_network(n).prefixlen == 0 for n in sources):
        raise ValueError('Client networks aggregate to a default route')
    occupied = [ipaddress.ip_network(n) for n in sources]
    for name, content in current.items():
        if name != client:
            other, _ = parse_config(content)
            occupied += networks(other, 'Address')
    desired = {client: render(clean_interface(iface, table='auto'), clients)}
    bundles, ports = {}, {int(one(iface, 'ListenPort', '51820'))}
    for e in exits:
        name = 'wg-exit-'+e['name']
        if not NAME.fullmatch(name) or name in desired:
            raise ValueError('Exit names must be unique and fit in a 15-character interface name')
        net = ipaddress.ip_network(e['transit'])
        if net.version != 4 or net.prefixlen != 30 or any(net.overlaps(n) for n in occupied if n.version == 4):
            raise ValueError('Transit networks must be distinct IPv4 /30s, outside existing networks')
        occupied.append(net)
        port = int(e['hub_port'])
        if not 1024 <= port <= 65535 or port in ports:
            raise ValueError('Hub UDP ports must be distinct, non-privileged ports')
        ports.add(port)
        if name in current:
            raise ValueError('Target exit interface already exists; use its saved plan or a new name')
        private, public = keypair()
        hub, exit_address = (str(a) for a in net.hosts())
        existing_peer = next((p for p in peers if one(p, 'PublicKey') == e['public_key']), {})
        peer = {'PublicKey': [e['public_key']], 'AllowedIPs': ['0.0.0.0/0']}
        # A pre-shared key remains local and is already present on the deployed exit.
        if existing_peer.get('PresharedKey'):
            peer['PresharedKey'] = existing_peer['PresharedKey']
        desired[name] = render({'PrivateKey': [private], 'Address': [hub+'/30'],
                               'ListenPort': [str(port)], 'Table': ['off']}, [peer])
        bundles[e['name']] = {'version': 1, 'exit_public_key': e['public_key'],
            'hub_public_key': public, 'hub_endpoint': endpoint, 'hub_port': port,
            'hub_address': hub, 'exit_address': exit_address+'/30',
            'client_sources': sources, 'uses_preshared_key': bool(peer.get('PresharedKey')),
            'preshared_key_digest': digest(one(peer, 'PresharedKey', '')) if peer.get('PresharedKey') else None}
    return desired, bundles


def build_exit(bundle, current, get_public):
    iface, peers = parse_config(current)
    if get_public(one(iface, 'PrivateKey')) != public_key(bundle['exit_public_key']):
        raise ValueError('This bundle belongs to another exit identity')
    if bundle.get('version') != 1:
        raise ValueError('Unsupported exit bundle version')
    if len(peers) != 1:
        raise ValueError('Exit migration expects exactly one existing hub peer')
    if any(n.version != 4 or n.prefixlen == 0 for n in networks(peers[0], 'AllowedIPs')):
        raise ValueError('Exit must have a non-default IPv4 upstream peer')
    address = ipaddress.ip_interface(bundle['exit_address'])
    hub = ipaddress.ip_address(bundle['hub_address'])
    sources = [ipaddress.ip_network(n) for n in bundle['client_sources']]
    if (address.version != 4 or address.network.prefixlen != 30
            or hub not in address.network.hosts() or hub == address.ip
            or address.ip not in address.network.hosts()
            or any(n.version != 4 or n.prefixlen == 0 or n.overlaps(address.network) for n in sources)):
        raise ValueError('Invalid exit transit or client networks')
    if not re.fullmatch(r'[A-Za-z0-9.-]+', bundle['hub_endpoint']) or not 1024 <= int(bundle['hub_port']) <= 65535:
        raise ValueError('Invalid hub endpoint')
    peer = {'PublicKey': [public_key(bundle['hub_public_key'])],
            'AllowedIPs': [', '.join([str(n) for n in sources]+[str(hub)+'/32'])],
            'Endpoint': [f"{bundle['hub_endpoint']}:{bundle['hub_port']}"],
            'PersistentKeepalive': ['25']}
    if bundle['uses_preshared_key']:
        psk = one(peers[0], 'PresharedKey')
        if bundle.get('preshared_key_digest') != digest(psk):
            raise ValueError('Hub and exit pre-shared keys differ; prepare matching configurations')
        peer['PresharedKey'] = [psk]
    return render(clean_interface(iface, bundle['exit_address']), [peer])


class Host:
    def __init__(self, container):
        self.container = container
        info = inspect_container(container)
        self.identity = container_identity(info)
        self.backend = agent_firewall_backend(container)
        self.pid = info['State']['Pid']
        if not self.pid or not info['State']['Running']:
            raise ValueError('The existing Pro Custodibus agent must be running')
        mounts = [m for m in info['Mounts'] if m['Destination'] == '/etc/wireguard' and m['Type'] == 'bind' and m['RW']]
        if len(mounts) != 1:
            raise ValueError('Expected a writable /etc/wireguard bind mount')
        self.directory = Path(mounts[0]['Source'])
        self.info = info

    def docker(self, *args, **kw):
        if container_identity(inspect_container(self.container)) != self.identity:
            raise RuntimeError('Agent container changed during migration')
        return command('docker', 'exec', '-i', self.container, *args, **kw)

    def net(self, *args, **kw):
        with container_namespace(self.container, self.identity) as namespace:
            if args[0] in ('iptables', 'iptables-save', 'iptables-restore'):
                args = ('iptables-'+self.backend+str(args[0])[len('iptables'):], *args[1:])
            return command('nsenter', f'--net=/proc/self/fd/{namespace}', '--',
                           'env', 'VMUTILS_IPTABLES_BACKEND='+self.backend, *args,
                           pass_fds=(namespace,), **kw)

    def configs(self):
        return {p.stem: p.read_text() for p in self.directory.glob('*.conf')
                if re.search(r'^\s*\[Interface\]', p.read_text(), re.M)}

    def keypair(self):
        private = self.docker('wg', 'genkey').stdout.strip()
        return private, self.get_public(private)

    def get_public(self, private):
        return self.docker('wg', 'pubkey', input=private+'\n').stdout.strip()

    def report(self, interfaces, script=ROOT/'agent_bridge.py'):
        result = self.docker('/opt/venvs/procustodibus-agent/bin/python3', '-', json.dumps(interfaces),
                             input=Path(script).read_text(), check=False)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or 'Agent report failed')

    def quick(self, action, name):
        return self.docker('env', 'WG_ENDPOINT_RESOLUTION_RETRIES=0', 'wg-quick', action, '/etc/wireguard/'+name+'.conf')

    def validate_networks(self, desired):
        routes = json.loads(self.net('ip', '-N', '-j', '-4', 'route', 'show', 'table', 'main').stdout)
        for name, text in desired.items():
            iface, _ = parse_config(text)
            for network in networks(iface, 'Address'):
                for route in routes:
                    if route.get('dst', 'default') == 'default' or route.get('dev') == name:
                        continue
                    if network.overlaps(ipaddress.ip_network(route['dst'], strict=False)):
                        raise ValueError('Transit or VPN address overlaps an existing route on another interface')

    def validate_ports(self, desired):
        published = self.info.get('NetworkSettings', {}).get('Ports', {})
        for name, content in desired.items():
            if name.startswith('wg-exit-'):
                iface, _ = parse_config(content)
                port = one(iface, 'ListenPort')
                bindings = published.get(port+'/udp') or []
                if not any(b['HostPort'] == port and b.get('HostIp') not in ('127.0.0.1', '::1') for b in bindings):
                    raise ValueError(f'Publish UDP port {port} on the existing agent before applying')
                for other, text in self.configs().items():
                    if other != name and one(parse_config(text)[0], 'ListenPort', '0') == port:
                        raise ValueError(f'UDP port {port} is already assigned')


def save_plan(host, role, desired, output, bundles=None, client='wg0'):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Plan output already exists; reuse it or choose a new directory')
    output.mkdir(mode=0o700, parents=True)
    os.chmod(output, 0o700)
    current = host.configs()
    plan = {'version': 1, 'role': role, 'container': host.container,
            'machine': Path('/etc/machine-id').read_text().strip(),
            'wireguard_directory': str(host.directory), 'client_interface': client,
            'before': {n: digest(current.get(n)) for n in desired}, 'desired': desired}
    atomic_write(output/'plan.json', json.dumps(plan, indent=2)+'\n', 0o600)
    for name, bundle in (bundles or {}).items():
        atomic_write(output/(name+'.exit.json'), json.dumps(bundle, indent=2)+'\n', 0o600)
    print(f'Prepared {role} plan: {output}/plan.json')
    print('Interfaces: '+', '.join(desired))
    print('Plan contains private keys; exit bundles contain only public connection settings.')


def check_plan(plan, host):
    if plan['machine'] != Path('/etc/machine-id').read_text().strip() or plan['wireguard_directory'] != str(host.directory):
        raise ValueError('Plan belongs to another machine or agent volume')
    if plan.get('version') != 1 or plan.get('role') not in ('hub', 'exit') or not plan.get('desired'):
        raise ValueError('Invalid or unsupported migration plan')
    current = host.configs()
    if all(current.get(n) == text for n, text in plan['desired'].items()):
        return False
    for name, text in plan['desired'].items():
        if not NAME.fullmatch(name):
            raise ValueError('Invalid interface name in plan')
        parse_config(text)
        path = host.directory/(name+'.conf')
        if path.is_symlink():
            raise ValueError('Symlinked interface files require manual review')
        if digest(current.get(name)) != plan['before'][name]:
            raise ValueError('Configuration changed since preparation; prepare a new plan')
    if plan['role'] == 'hub':
        host.validate_ports(plan['desired'])
    host.validate_networks(plan['desired'])
    return True


def service_state(action):
    return command('systemctl', action, SERVICE, check=False).returncode == 0


def apply(plan_path, timeout):
    if timeout < 120:
        raise ValueError('Allow at least 120 seconds for connectivity verification')
    plan = json.loads(Path(plan_path).read_text())
    host = Host(plan['container'])
    if not check_plan(plan, host):
        print('WireGuard files already match this plan. Use install.sh to update only the service.')
        return
    if any(BACKUPS.glob('*/pending')):
        raise ValueError('An earlier migration is pending; confirm or roll it back first')
    host.report(list(plan['desired']))
    if CONFIG.exists() and load_config(CONFIG)['role'] != plan['role']:
        raise ValueError('Existing watchdog role differs from the migration role')
    # Resolve dependencies before scheduling the short cutover rollback deadline.
    command('apt-get', 'update', timeout=600)
    command('apt-get', 'install', '-y', 'python3', 'iproute2', 'iptables', 'iputils-ping',
            'wireguard-tools', 'util-linux', 'ca-certificates', timeout=600)
    backup = BACKUPS/str(uuid.uuid4())
    backup.mkdir(parents=True, mode=0o700)
    os.chmod(BACKUPS, 0o700)
    metadata = {'plan': plan, 'active': service_state('is-active'),
                'enabled': service_state('is-enabled'), 'unit': 'vmutils-rollback-'+backup.name,
                'paths': {}, 'up': host.net('wg', 'show', 'interfaces').stdout.split(),
                'mutated': False, 'agent_active': host.docker('rc-service', 'procustodibus-agent', 'status', check=False).returncode == 0}
    policy = json.loads(CONFIG.read_text() if CONFIG.exists() else (ROOT/'config.json').read_text())
    policy['role'] = plan['role']
    policy['client_interface'] = plan['client_interface']
    metadata['policy'] = policy
    validate_policy_space(host, policy)
    for name in plan['desired']:
        save_path(backup, metadata, host.directory/(name+'.conf'))
    for path in (CONFIG, LIB, UNIT_FILE, TABLES, STATE):
        save_path(backup, metadata, path)
    sysctls = ['net.ipv4.ip_forward', 'net.ipv4.conf.all.rp_filter']
    sysctls += [f'net.ipv4.conf.{n}.rp_filter' for n in metadata['up']]
    metadata['sysctls'] = {key: host.net('sysctl', '-n', key).stdout.strip() for key in sysctls}
    metadata['routes'] = [r for r in json.loads(host.net('ip', '-N', '-j', '-4', 'route', 'show', 'table', 'all').stdout)
                          if str(r.get('protocol')) == PROTOCOL]
    metadata['rule_lines'] = host.net('ip', '-N', '-4', 'rule', 'show').stdout.splitlines()
    atomic_write(backup/'iptables.save', host.net('iptables-save').stdout, 0o600)
    for source in ('upgrade.py', 'failover.py', 'provision.py', 'agent_bridge.py'):
        shutil.copy2(ROOT/source, backup/source)
    metadata['deadline'] = int(time.time()) + timeout
    atomic_write(backup/'metadata.json', json.dumps(metadata), 0o600)
    atomic_write(backup/'pending', 'pending\n', 0o600)
    armed = False
    try:
        # Recovery must already be scheduled before stopping either live service.
        arm_rollback(backup, metadata)
        armed = True
        command('systemctl', 'stop', SERVICE, check=metadata['active'])
        host.docker('rc-service', 'procustodibus-agent', 'stop')
        host.report(list(plan['desired']))
        check_deadline(metadata)
        if not check_plan(plan, host):
            raise ValueError('Configuration changed during preparation')
        metadata['mutated'] = True
        atomic_write(backup/'metadata.json', json.dumps(metadata), 0o600)
        # Stop old interfaces using their original hooks and wg-quick policy cleanup.
        for name in plan['desired']:
            check_deadline(metadata)
            if name in metadata['up']:
                host.quick('down', name)
        for name, text in plan['desired'].items():
            atomic_write(host.directory/(name+'.conf'), text, 0o600)
        command('env', 'VMUTILS_SKIP_PACKAGES=1', 'bash', ROOT/'install.sh', plan['role'], '--no-start')
        config = json.loads(CONFIG.read_text())
        config['container'] = plan['container']
        config['client_interface'] = plan['client_interface']
        if plan['role'] == 'exit':
            previous = config['exit_interfaces'] if metadata['paths'].get(str(CONFIG)) is not None else []
            config['exit_interfaces'] = list(dict.fromkeys([*previous, *plan['desired']]))
        atomic_write(CONFIG, json.dumps(config, indent=2)+'\n', 0o640)
        for name in plan['desired']:
            check_deadline(metadata)
            host.quick('up', name)
        store = Store(STATE)
        store.put('routing_config', config)
        store.db.execute('UPDATE nodes SET healthy=0,good=0,bad=0,last_probe=0')
        store.db.commit()
        store.db.close()
        host.net('/usr/bin/python3', LIB/'failover.py', 'once')
        host.report(list(plan['desired']))
        host.docker('rc-service', 'procustodibus-agent', 'start')
        command('systemctl', 'restart', SERVICE)
        check_deadline(metadata)
    except Exception:
        if armed:
            rollback(backup)
        else:
            (backup/'pending').unlink(missing_ok=True)
            disarm_rollback(metadata)
        raise
    print(f'Applied. Automatic rollback remains armed for {timeout} seconds from cutover start.')
    print('Check client Internet access and Pro Custodibus, then confirm:')
    print(f'sudo python3 {backup}/upgrade.py confirm {backup}')


def check_deadline(metadata):
    if time.time() >= metadata['deadline']:
        raise RuntimeError('Migration exceeded its rollback deadline')


def arm_rollback(backup, metadata):
    unit = metadata['unit']
    due = datetime.fromtimestamp(metadata['deadline'], timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    atomic_write(SYSTEMD/(unit+'.service'), f"""[Unit]
Description=Restore an unconfirmed vmutils network migration
After=docker.service network-online.target
Wants=docker.service network-online.target
StartLimitIntervalSec=0
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 {backup}/upgrade.py rollback {backup}
Restart=on-failure
RestartSec=10s
TimeoutStartSec=300s
UMask=0077
""")
    atomic_write(SYSTEMD/(unit+'.timer'), f"""[Unit]
Description=Rollback deadline for a vmutils network migration
[Timer]
OnCalendar={due}
Persistent=true
AccuracySec=1s
Unit={unit}.service
[Install]
WantedBy=timers.target
""")
    command('systemctl', 'daemon-reload')
    command('systemctl', 'enable', '--now', unit+'.timer')


def disarm_rollback(metadata):
    unit = metadata['unit']
    command('systemctl', 'disable', '--now', unit+'.timer', check=False)
    for suffix in ('.timer', '.service'):
        (SYSTEMD/(unit+suffix)).unlink(missing_ok=True)
    command('systemctl', 'daemon-reload')


def save_path(backup, metadata, path):
    key = str(path)
    if path.exists():
        name = 'saved-'+str(len(metadata['paths']))
        destination = backup/name
        if path.is_dir():
            shutil.copytree(path, destination, ignore=shutil.ignore_patterns('history.sqlite3*', 'worker.lock'))
            database = path/'history.sqlite3'
            if database.exists():
                # Copy a transactionally consistent database while the watchdog runs.
                with sqlite3.connect(str(database)) as source_db, sqlite3.connect(str(destination/'history.sqlite3')) as target_db:
                    source_db.backup(target_db)
        else:
            shutil.copy2(path, destination)
        metadata['paths'][key] = name
    else:
        metadata['paths'][key] = None


def policy_priorities(config):
    if config['role'] != 'hub':
        return set()
    return {config['policy_priority']-1, config['policy_priority']} | set(range(
        config['probe_table_base']+1, config['probe_table_base']+129))


def normalize_rule(rule):
    row = dict(rule)
    if 'table' in row:
        row['table'] = int(row['table'])
    if 'fwmark' in row:
        row['fwmark'] = hex(int(str(row['fwmark']), 0))
    return row


def expected_rule(config, priority):
    if priority == config['policy_priority']-1:
        return {'priority': priority, 'src': 'all', 'iif': config['client_interface'],
                'table': 254, 'suppress_prefixlen': 0}
    if priority == config['policy_priority']:
        return {'priority': priority, 'src': 'all', 'iif': config['client_interface'],
                'table': config['active_table']}
    return {'priority': priority, 'src': 'all', 'table': priority,
            'fwmark': hex(0x6F000000+priority-config['probe_table_base'])}


def validate_policy_space(host, config):
    rules = json.loads(host.net('ip', '-N', '-j', '-4', 'rule', 'show').stdout)
    seen = set()
    for rule in rules:
        priority = rule.get('priority')
        if priority not in policy_priorities(config):
            continue
        if priority in seen or normalize_rule(rule) != expected_rule(config, priority):
            raise ValueError(f'Foreign or duplicate policy rule occupies priority {priority}')
        seen.add(priority)
    all_routes = json.loads(host.net('ip', '-N', '-j', '-4', 'route', 'show', 'table', 'all').stdout)
    reserved = {config['candidate_table'], config['active_table']} | set(range(
        config['probe_table_base']+1, config['probe_table_base']+129))
    if config['role'] == 'hub' and any(int(r.get('table', 254)) in reserved
            and str(r.get('protocol')) != PROTOCOL for r in all_routes):
        raise ValueError('Foreign routes occupy watchdog tables')


def clean_policy(host, config):
    """Remove owned routes and exact policy selectors, preserving unrelated rules."""
    if config['role'] == 'hub':
        for table in [config['candidate_table'], config['active_table']]+list(range(
                config['probe_table_base']+1, config['probe_table_base']+129)):
            host.net('ip', '-4', 'route', 'flush', 'table', str(table), 'proto', PROTOCOL, check=False)
        rules = json.loads(host.net('ip', '-N', '-j', '-4', 'rule', 'show').stdout)
        for rule in rules:
            priority = rule.get('priority')
            if priority not in policy_priorities(config) or normalize_rule(rule) != expected_rule(config, priority):
                continue
            args = ['ip', '-4', 'rule', 'del', 'priority', str(priority), 'from', 'all']
            if priority == config['policy_priority']-1:
                args += ['iif', config['client_interface'], 'table', '254', 'suppress_prefixlength', '0']
            elif priority == config['policy_priority']:
                args += ['iif', config['client_interface'], 'table', str(config['active_table'])]
            else:
                args += ['fwmark', hex(0x6F000000+priority-config['probe_table_base']), 'table', str(priority)]
            host.net(*args, check=False)
    if config['role'] == 'exit':
        for interface in config['exit_interfaces']:
            host.net('ip', '-4', 'route', 'flush', 'table', 'main', 'proto', PROTOCOL, 'dev', interface, check=False)


def restore_policy(host, metadata):
    # A previously stopped watchdog can still have live routes: restore those too.
    for route in metadata.get('routes', []):
        table = str(route.get('table', 254))
        existing = host.net('ip', '-N', '-j', '-4', 'route', 'show', 'table', table, check=False)
        if existing.returncode == 0 and any(r.get('dst') == route['dst']
                and r.get('metric', 0) == route.get('metric', 0)
                and str(r.get('protocol')) != PROTOCOL for r in json.loads(existing.stdout)):
            raise ValueError('Cannot restore a saved route over a foreign route')
        args = ['ip', '-4', 'route', 'replace', route['dst'], 'table', str(route.get('table', 254)),
                'proto', PROTOCOL]
        if route.get('gateway'):
            args += ['via', route['gateway']]
        args += ['dev', route['dev']]
        if 'metric' in route:
            args += ['metric', str(route['metric'])]
        host.net(*args)
    current = set(host.net('ip', '-N', '-4', 'rule', 'show').stdout.splitlines())
    for line in metadata.get('rule_lines', []):
        if line not in current:
            change_rule(host, 'add', line)
            current.add(line)


def change_rule(host, action, line):
    priority, selectors = line.split(':', 1)
    # ip's numeric text is accepted by ip rule again; no shell is involved.
    host.net('ip', '-4', 'rule', action, 'priority', str(int(priority)), *shlex.split(selectors))


def rollback(directory):
    backup = Path(directory).resolve()
    if (backup/'rolled-back').exists() or (backup/'confirmed').exists():
        (backup/'pending').unlink(missing_ok=True)
        return
    if not (backup/'pending').exists():
        raise ValueError('This migration is not pending; no automatic rollback is permitted')
    meta = json.loads((backup/'metadata.json').read_text())
    plan = meta['plan']
    host = Host(plan['container'])
    if plan['machine'] != Path('/etc/machine-id').read_text().strip() or plan['wireguard_directory'] != str(host.directory):
        raise ValueError('Rollback belongs to another host or agent volume')
    command('systemctl', 'stop', SERVICE, check=False)
    host.docker('rc-service', 'procustodibus-agent', 'stop', check=False)
    if not meta.get('mutated', True):
        if meta['active']:
            command('systemctl', 'start', SERVICE)
        if meta.get('agent_active', True):
            host.docker('rc-service', 'procustodibus-agent', 'start')
        atomic_write(backup/'rolled-back', 'No interface changes; original services resumed\n', 0o600)
        (backup/'pending').unlink()
        disarm_rollback(meta)
        return
    clean_policy(host, meta['policy'])
    up = host.net('wg', 'show', 'interfaces').stdout.split()
    for name in plan['desired']:
        if name in up:
            host.quick('down', name)
    for path_text, saved in meta['paths'].items():
        path = Path(path_text)
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        if saved:
            if (backup/saved).is_dir():
                shutil.copytree(backup/saved, path)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup/saved, path)
    for name in plan['desired']:
        if name in meta['up']:
            before_rules = set(host.net('ip', '-N', '-4', 'rule', 'show').stdout.splitlines())
            host.quick('up', name)
            after_rules = set(host.net('ip', '-N', '-4', 'rule', 'show').stdout.splitlines())
            # wg-quick chooses priorities from the remaining rules. Recreate the exact
            # saved policy order instead of accepting a different automatic allocation.
            for line in after_rules-before_rules:
                change_rule(host, 'del', line)
    restore_policy(host, meta)
    host.net('iptables-restore', input=(backup/'iptables.save').read_text())
    for key, value in meta.get('sysctls', {}).items():
        host.net('sysctl', '-qw', f'{key}={value}')
    command('systemctl', 'daemon-reload')
    command('systemctl', 'enable' if meta['enabled'] else 'disable', SERVICE, check=False)
    if meta['active']:
        command('systemctl', 'start', SERVICE)
    # Reporting must not execute a stale controller queue during rollback either.
    atomic_write(backup/'rolled-back', 'rolled back\n', 0o600)
    (backup/'pending').unlink()
    disarm_rollback(meta)
    print(f'Restored configuration from {backup}')
    try:
        host.report(list(plan['desired']), backup/'agent_bridge.py')
        if meta.get('agent_active', True):
            host.docker('rc-service', 'procustodibus-agent', 'start')
    except RuntimeError:
        print('Local routing restored; agent daemon remains stopped until its controller report succeeds', file=sys.stderr)


def confirm(directory):
    backup = Path(directory).resolve()
    if not (backup/'pending').exists():
        raise ValueError('This migration is no longer pending')
    meta = json.loads((backup/'metadata.json').read_text())
    if time.time() >= meta['deadline']:
        rollback(backup)
        raise ValueError('Confirmation arrived after the deadline; configuration was rolled back')
    host = Host(meta['plan']['container'])
    if check_plan(meta['plan'], host) or not service_state('is-active'):
        raise ValueError('Configuration or service is not ready; rollback remains armed')
    # Do not stop the timer before all checks: a rejected confirmation must leave recovery armed.
    disarm_rollback(meta)
    if time.time() >= meta['deadline']:
        rollback(backup)
        raise ValueError('Confirmation arrived after the deadline; configuration was rolled back')
    atomic_write(backup/'confirmed', 'confirmed\n', 0o600)
    (backup/'pending').unlink()
    print(f'Migration confirmed. Protected backup retained at {backup}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for role in ('hub', 'exit'):
        p = sub.add_parser(role, help='Prepare a local migration plan without changing live routing')
        p.add_argument('--container', default='procustodibus-agent')
        p.add_argument('--output', required=True)
        if role == 'hub':
            p.add_argument('--topology', required=True)
        else:
            p.add_argument('--bundle', required=True)
            p.add_argument('--interface', default='wg0')
    p = sub.add_parser('apply')
    p.add_argument('plan')
    p.add_argument('--rollback-after', type=int, default=300)
    for op in ('rollback', 'confirm'):
        sub.add_parser(op).add_argument('backup')
    args = parser.parse_args()
    if args.command in ('hub', 'exit'):
        host = Host(args.container)
        current = host.configs()
        if args.command == 'hub':
            topology = json.loads(Path(args.topology).read_text())
            client = topology.get('client_interface', 'wg0')
            desired, bundles = build_hub(topology, current, host.keypair)
            host.validate_ports(desired)
            host.validate_networks(desired)
            save_plan(host, 'hub', desired, args.output, bundles, client)
        else:
            if not NAME.fullmatch(args.interface):
                raise ValueError('Invalid exit interface')
            bundle = json.loads(Path(args.bundle).read_text())
            desired = {args.interface: build_exit(bundle, current[args.interface], host.get_public)}
            host.validate_networks(desired)
            save_plan(host, 'exit', desired, args.output)
    elif args.command == 'apply':
        def interrupted(signum, _frame):
            raise RuntimeError(f'Migration interrupted by signal {signum}')
        for signum in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, interrupted)
        apply(args.plan, args.rollback_after)
    elif args.command == 'rollback':
        rollback(args.backup)
    else:
        confirm(args.backup)


if __name__ == '__main__':
    try:
        with open('/run/lock/vmutils-network-migration.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            main()
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'Migration stopped: {exc}', file=sys.stderr)
        sys.exit(1)
