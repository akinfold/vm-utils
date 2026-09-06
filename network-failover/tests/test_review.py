"""Regressions for review findings that affect production cutover and recovery."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import failover as f
import upgrade as u
from test_upgrade import HUB, EXIT, key
import test_upgrade as fixtures


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((Path(f.__file__).parent/'config.json').read_text())

    def test_rejects_invalid_types_nonfinite_numbers_and_late_policy(self):
        for name, value in [('policy_priority', 40000), ('candidate_table', True),
                            ('probe_table_base', 1.5), ('interval_seconds', float('nan')),
                            ('probe_timeout_seconds', float('inf')), ('minimum_samples', False),
                            ('client_interface', '-bad'), ('exit_prefix', 'wg0'),
                            ('container', None), ('exit_interfaces', 'wg0'), ('internet_targets', ['bad'])]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                f.validate_config({**self.config, name: value})

    def test_fast_interface_return_requires_fresh_successes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = f.Store(directory)
            node = {'name': 'wg-exit-a', 'identity': 'a', 'gateway': '10.20.0.2'}
            store.discover([node], 100)
            for now in (100, 105, 110):
                store.observe(node['name'], True, 5, now, self.config)
            self.assertTrue(store.nodes()[0]['healthy'])
            store.discover([], 111)
            store.discover([node], 112)
            self.assertFalse(store.nodes()[0]['healthy'])
            self.assertEqual(store.nodes()[0]['last_probe'], 0)
            for now in (113, 118):
                store.observe(node['name'], True, 5, now, self.config)
                self.assertFalse(store.nodes()[0]['healthy'])
            store.observe(node['name'], True, 5, 123, self.config)
            self.assertTrue(store.nodes()[0]['healthy'])
            store.db.close()

    def test_firewall_tools_match_agent_backend(self):
        for backend in ('nft', 'legacy'):
            with patch.dict(os.environ, {'VMUTILS_IPTABLES_BACKEND': backend}):
                for tool in ('iptables', 'iptables-save', 'iptables-restore'):
                    self.assertEqual(f.firewall_command((tool, '-w'))[0],
                                     'iptables-'+backend+tool[len('iptables'):])
                self.assertEqual(f.firewall_command(('wg', 'show')), ('wg', 'show'))

    def test_new_clients_in_attached_subnet_have_an_exit_return_path(self):
        topology = fixtures.MigrationTests().topology()
        _, bundles = u.build_hub(topology, {'wg0': HUB}, lambda: (key(8), key(9)))
        self.assertEqual(bundles['a']['client_sources'], ['10.0.0.0/24'])
        self.assertNotIn(key(7), json.dumps(bundles))
        with self.assertRaises(ValueError):
            u.build_exit(bundles['a'], EXIT.replace(key(7), key(12)), lambda _: key(3))

    def test_client_public_key_cannot_be_migrated_as_an_exit(self):
        topology = fixtures.MigrationTests().topology()
        topology['exits'][0]['public_key'] = key(2)
        with self.assertRaisesRegex(ValueError, 'client peer'):
            u.build_hub(topology, {'wg0': HUB}, lambda: (key(8), key(9)))

    def test_backup_captures_live_sqlite_wal_transactions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, backup = root/'state', root/'backup'
            state.mkdir(); backup.mkdir()
            connection = sqlite3.connect(state/'history.sqlite3')
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('CREATE TABLE example (value TEXT)')
            connection.execute("INSERT INTO example VALUES ('committed in WAL')")
            connection.commit()
            meta = {'paths': {}}
            u.save_path(backup, meta, state)
            with sqlite3.connect(backup/meta['paths'][str(state)]/'history.sqlite3') as copied:
                self.assertEqual(copied.execute('SELECT value FROM example').fetchone()[0], 'committed in WAL')
            connection.close()

    def test_expired_confirmation_rolls_back_instead_of_disabling_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'pending').write_text('pending')
            (root/'metadata.json').write_text(json.dumps({'deadline': time.time()-1}))
            with patch.object(u, 'rollback') as rollback, patch.object(u, 'disarm_rollback') as disarm:
                with self.assertRaisesRegex(ValueError, 'after the deadline'):
                    u.confirm(root)
                rollback.assert_called_once_with(root.resolve())
                disarm.assert_not_called()

    def test_completed_rollback_retry_is_harmless(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'rolled-back').write_text('done')
            (root/'pending').write_text('interrupted before unlink')
            with patch.object(u, 'Host') as host:
                u.rollback(root)
                host.assert_not_called()
                self.assertFalse((root/'pending').exists())

    def test_host_network_container_is_rejected_before_entering_namespace(self):
        info = {'State': {'Running': True, 'Pid': 123}, 'HostConfig': {'NetworkMode': 'host'}}
        with patch.object(f, 'run', return_value=Mock(stdout=json.dumps([info]))):
            with self.assertRaises(ValueError):
                f.inspect_container('agent')

    def test_routing_layout_change_is_rejected_before_network_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            store = f.Store(directory)
            store.put('routing_config', self.config)
            with patch.object(f, 'run') as command, self.assertRaises(ValueError):
                f.cycle({**self.config, 'active_table': 300}, store, 100)
            command.assert_not_called()
            store.db.close()

    def test_port_mapping_must_match_advertised_hub_port(self):
        host = object.__new__(u.Host)
        host.info = {'NetworkSettings': {'Ports': {'51821/udp': [{'HostPort': '60000', 'HostIp': '0.0.0.0'}]}}}
        desired, _ = u.build_hub(fixtures.MigrationTests().topology(), {'wg0': HUB}, lambda: (key(8), key(9)))
        host.configs = lambda: {'wg0': HUB}
        with self.assertRaises(ValueError):
            host.validate_ports(desired)

    def test_transit_overlap_with_native_route_is_rejected(self):
        host = object.__new__(u.Host)
        host.net = Mock(return_value=Mock(stdout=json.dumps([
            {'dst': '10.250.0.0/16', 'dev': 'eth0', 'protocol': 'kernel'}])))
        desired, _ = u.build_hub(fixtures.MigrationTests().topology(), {'wg0': HUB}, lambda: (key(8), key(9)))
        with self.assertRaisesRegex(ValueError, 'overlaps'):
            host.validate_networks(desired)

    def test_hub_with_ephemeral_port_is_not_silently_rekeyed(self):
        with self.assertRaisesRegex(ValueError, 'fixed'):
            u.build_hub(fixtures.MigrationTests().topology(),
                        {'wg0': HUB.replace('ListenPort = 51820', 'ListenPort = 0')},
                        lambda: (key(8), key(9)))


if __name__ == '__main__':
    unittest.main()
