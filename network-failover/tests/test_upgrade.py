"""Migration planning and rollback safety tests; keys are synthetic fixtures."""
import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import upgrade as u


def key(n):
    return base64.b64encode(bytes([n])*32).decode()


HUB = f'''[Interface]
PrivateKey = {key(1)}
Address = 10.0.0.1/24
ListenPort = 51820
Table = auto
PreUp = iptables -A FORWARD -i wg0 -j ACCEPT
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT
[Peer]
PublicKey = {key(2)}
AllowedIPs = 10.0.0.5/32
[Peer]
PublicKey = {key(3)}
AllowedIPs = 0.0.0.0/0
PresharedKey = {key(7)}
'''
EXIT = f'''[Interface]
PrivateKey = {key(4)}
Address = 10.0.0.246/24
ListenPort = 51820
[Peer]
PublicKey = {key(5)}
AllowedIPs = 10.0.0.0/24
PresharedKey = {key(7)}
'''


class MigrationTests(unittest.TestCase):
    def topology(self):
        return {'hub_endpoint': 'hub.example.com', 'exits': [
            {'name': 'a', 'public_key': key(3), 'transit': '10.250.1.0/30', 'hub_port': 51821}]}

    def test_preserves_clients_and_exit_identity_and_removes_legacy_hooks(self):
        desired, bundles = u.build_hub(self.topology(), {'wg0': HUB}, lambda: (key(8), key(9)))
        iface, peers = u.parse_config(desired['wg0'])
        self.assertEqual(u.one(iface, 'PrivateKey'), key(1))
        self.assertEqual(len(peers), 1)
        self.assertEqual(u.one(peers[0], 'PublicKey'), key(2))
        self.assertNotIn('PreUp', iface)
        self.assertEqual(u.one(u.parse_config(desired['wg-exit-a'])[0], 'Table'), 'off')
        exit_config = u.build_exit(bundles['a'], EXIT, lambda private: key(3))
        exit_iface, exit_peers = u.parse_config(exit_config)
        self.assertEqual(u.one(exit_iface, 'PrivateKey'), key(4))
        self.assertEqual(u.one(exit_iface, 'Address'), '10.250.1.2/30')
        self.assertEqual(u.one(exit_peers[0], 'PublicKey'), key(9))
        self.assertEqual(u.one(exit_peers[0], 'PresharedKey'), key(7))
        self.assertIn('10.0.0.0/24', u.one(exit_peers[0], 'AllowedIPs'))
        self.assertNotIn(key(8), json.dumps(bundles))
        self.assertNotIn(key(7), json.dumps(bundles))

    def test_disabled_or_new_exit_can_be_added_by_public_identity(self):
        topology = self.topology()
        topology['exits'].append({'name': 'b', 'public_key': key(10),
                                 'transit': '10.250.2.0/30', 'hub_port': 51822})
        desired, bundles = u.build_hub(topology, {'wg0': HUB}, lambda: (key(8), key(9)))
        self.assertIn('wg-exit-b', desired)
        self.assertFalse(bundles['b']['uses_preshared_key'])

    def test_rejects_overlaps_ports_unlisted_default_and_arbitrary_hooks(self):
        for field, value in [('transit', '10.0.0.0/30'), ('hub_port', 51820),
                             ('name', '../../escape'), ('public_key', 'bad')]:
            topology = self.topology()
            topology['exits'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                u.build_hub(topology, {'wg0': HUB}, lambda: (key(8), key(9)))
        topology = self.topology()
        topology['exits'][0]['public_key'] = key(10)
        with self.assertRaises(ValueError):
            u.build_hub(topology, {'wg0': HUB}, lambda: (key(8), key(9)))
        with self.assertRaises(ValueError):
            u.parse_config(HUB.replace('iptables -A FORWARD -i wg0 -j ACCEPT', 'touch /tmp/arbitrary'))

    def test_exit_bundle_cannot_overwrite_another_identity(self):
        _, bundles = u.build_hub(self.topology(), {'wg0': HUB}, lambda: (key(8), key(9)))
        with self.assertRaises(ValueError):
            u.build_exit(bundles['a'], EXIT, lambda private: key(12))

    def test_drift_is_rejected_and_repeated_plan_is_noop(self):
        host = Mock()
        host.directory = Path('/example/wireguard')
        host.configs.return_value = {'wg0': HUB}
        plan = {'version': 1, 'machine': 'fixture', 'wireguard_directory': str(host.directory), 'role': 'hub',
                'before': {'wg0': u.digest(HUB)}, 'desired': {'wg0': HUB+'\n'}}
        with patch.object(Path, 'read_text', return_value='fixture'):
            self.assertTrue(u.check_plan(plan, host))
            host.configs.return_value = {'wg0': HUB+'\n'}
            self.assertFalse(u.check_plan(plan, host))
            host.configs.return_value = {'wg0': HUB+'# changed\n'}
            with self.assertRaises(ValueError):
                u.check_plan(plan, host)

    def test_foreign_policy_is_not_deleted_on_rollback(self):
        config = json.loads((Path(u.__file__).parent/'config.json').read_text())
        host = Mock()
        host.net.return_value.stdout = json.dumps([{'priority': 20000, 'src': 'all', 'table': 42}])
        with self.assertRaises(ValueError):
            u.validate_policy_space(host, config)
        self.assertEqual(host.net.call_count, 1)

    def test_backup_preserves_file_mode_and_missing_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root/'key.conf'
            original.write_text('fixture')
            original.chmod(0o600)
            backup = root/'backup'
            backup.mkdir()
            meta = {'paths': {}}
            u.save_path(backup, meta, original)
            u.save_path(backup, meta, root/'missing')
            saved = backup/meta['paths'][str(original)]
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
            self.assertIsNone(meta['paths'][str(root/'missing')])


if __name__ == '__main__':
    unittest.main()
