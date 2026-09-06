import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from failover import Store, load_config, select
from provision import provision, register_tables

CONFIG = Path(__file__).resolve().parents[1] / "config.json"


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.c = load_config(CONFIG)
        self.now = 1_000_000
        self.store.discover([
            {"name": "wg-exit-a", "gateway": "10.20.0.2", "identity": "a"},
            {"name": "wg-exit-b", "gateway": "10.20.1.2", "identity": "b"}], self.now)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def observe(self, name, ok, at, rtt=20):
        self.store.observe(name, ok, rtt if ok else None, at, self.c)

    def healthy(self, name):
        for i in range(3):
            self.observe(name, True, self.now+i*5)

    def test_requires_three_successes_and_three_failures(self):
        name = "wg-exit-a"
        self.observe(name, True, self.now)
        self.assertFalse(self.store.nodes()[0]["healthy"])
        self.healthy(name)
        self.assertTrue(self.store.nodes()[0]["healthy"])
        for i in range(2):
            self.observe(name, False, self.now+20+i*5)
            self.assertTrue(self.store.nodes()[0]["healthy"])
        self.observe(name, False, self.now+30)
        self.assertFalse(self.store.nodes()[0]["healthy"])

    def test_retirement_and_recovery_survive_database_reopen(self):
        self.observe("wg-exit-a", False, self.now)
        self.observe("wg-exit-a", False, self.now+604799)
        self.assertFalse(self.store.nodes()[0]["retired"])
        self.observe("wg-exit-a", False, self.now+604800)
        self.assertTrue(self.store.nodes()[0]["retired"])
        self.store.db.close()
        self.store = Store(self.tmp.name)
        self.assertTrue(self.store.nodes()[0]["retired"])
        for i in range(2):
            self.observe("wg-exit-a", True, self.now+604810+i*5)
            self.assertTrue(self.store.nodes()[0]["retired"])
        self.observe("wg-exit-a", True, self.now+604820)
        self.assertFalse(self.store.nodes()[0]["retired"])
        self.assertTrue(self.store.nodes()[0]["healthy"])

    def test_success_interrupts_seven_day_inactivity(self):
        self.observe("wg-exit-a", False, self.now)
        self.observe("wg-exit-a", True, self.now+604700)
        self.observe("wg-exit-a", False, self.now+604800)
        self.assertFalse(self.store.nodes()[0]["retired"])

    def test_never_active_node_is_retired(self):
        self.observe("wg-exit-a", False, self.now+604800)
        self.assertTrue(self.store.nodes()[0]["retired"])

    def test_removed_interface_does_not_erase_recovery_record(self):
        self.healthy("wg-exit-a")
        self.store.discover([], self.now+20)
        self.assertFalse(self.store.nodes()[0]["present"])
        self.assertEqual(len(self.store.nodes()), 2)
        self.store.discover([{"name": "wg-exit-a", "identity": "a", "gateway": "10.20.0.2"}], self.now+30)
        self.assertTrue(self.store.nodes()[0]["present"])

    def test_new_identity_resets_latency_and_health(self):
        self.healthy("wg-exit-a")
        self.store.discover([{"name": "wg-exit-a", "identity": "new", "gateway": "10.20.0.2"}], self.now+20)
        self.assertFalse(self.store.nodes()[0]["healthy"])
        self.assertEqual(self.store.scores(self.now+20, self.c)["wg-exit-a"]["samples"], 0)

    def test_history_pruning_and_weighted_recent_latency(self):
        # Most historical samples are fast, but the current route has deteriorated.
        for i in range(30):
            self.store.db.execute("INSERT INTO samples VALUES (?,?,?,?)", ("wg-exit-a", self.now-500000+i*30, 10, 1))
        for i in range(21):
            self.store.db.execute("INSERT INTO samples VALUES (?,?,?,?)", ("wg-exit-a", self.now-600+i*30, 100, 1))
        score = self.store.scores(self.now, self.c)["wg-exit-a"]
        self.assertEqual(score["median_7d_ms"], 10)
        self.assertEqual(score["score_ms"], 100)
        self.c["score_mode"] = "median7d"
        self.assertEqual(self.store.scores(self.now, self.c)["wg-exit-a"]["score_ms"], 10)
        self.observe("wg-exit-a", False, self.now+604801)
        self.assertEqual(self.store.scores(self.now+604801, self.c)["wg-exit-a"]["samples"], 0)

    def policy_fixture(self):
        self.healthy("wg-exit-a")
        self.healthy("wg-exit-b")
        scores = {"wg-exit-a": {"score_ms": 80, "recent_loss": 0, "samples": 30},
                  "wg-exit-b": {"score_ms": 20, "recent_loss": 0, "samples": 30}}
        return self.store.nodes(), scores

    def test_quality_switch_waits_for_hold_and_dwell(self):
        nodes, scores = self.policy_fixture()
        chosen, pending = select(nodes, scores, "wg-exit-a", self.now, self.c, switched=self.now-100)
        self.assertEqual(chosen, "wg-exit-a")
        chosen, pending = select(nodes, scores, chosen, self.now+301, self.c, pending, self.now-100)
        self.assertEqual(chosen, "wg-exit-a")
        chosen, _ = select(nodes, scores, chosen, self.now+901, self.c, pending, self.now-100)
        self.assertEqual(chosen, "wg-exit-b")

    def test_health_failure_bypasses_latency_hold(self):
        nodes, scores = self.policy_fixture()
        nodes[0]["healthy"] = False
        chosen, _ = select(nodes, scores, "wg-exit-a", self.now, self.c, switched=self.now)
        self.assertEqual(chosen, "wg-exit-b")
        nodes[1]["healthy"] = False
        chosen, _ = select(nodes, scores, "wg-exit-a", self.now, self.c)
        self.assertIsNone(chosen)  # The native hub WAN is selected.

    def test_cold_start_can_use_new_exit_without_week_of_history(self):
        nodes, scores = self.policy_fixture()
        scores["wg-exit-b"]["samples"] = 1
        chosen, _ = select(nodes, scores, None, self.now, self.c)
        self.assertEqual(chosen, "wg-exit-b")
        chosen, _ = select(nodes, scores, "wg-exit-a", self.now, self.c)
        self.assertEqual(chosen, "wg-exit-a")

    def test_lossy_low_latency_exit_is_not_preferred(self):
        nodes, scores = self.policy_fixture()
        scores["wg-exit-b"]["recent_loss"] = .5
        chosen, _ = select(nodes, scores, None, self.now, self.c)
        self.assertEqual(chosen, "wg-exit-a")

    def test_icmp_block_does_not_preclude_last_healthy_exit(self):
        nodes, scores = self.policy_fixture()
        nodes[0]["healthy"] = False
        scores["wg-exit-b"]["score_ms"] = None
        chosen, _ = select(nodes, scores, None, self.now, self.c)
        self.assertEqual(chosen, "wg-exit-b")

    def test_missing_or_retired_exit_is_never_selected(self):
        nodes, scores = self.policy_fixture()
        nodes[0]["retired"] = True
        nodes[1]["present"] = False
        chosen, _ = select(nodes, scores, None, self.now, self.c)
        self.assertIsNone(chosen)


class InstallTests(unittest.TestCase):
    def test_register_once_preserving_existing_table_names(self):
        with tempfile.TemporaryDirectory() as d:
            tables = Path(d)/"rt_tables"
            tables.write_text("255 local\n254 main\n77 custom\n")
            register_tables(tables, 200, 201)
            original = tables.read_text()
            register_tables(tables, 200, 201)
            self.assertEqual(tables.read_text(), original)
            self.assertIn("77 custom", original)

    def test_table_collision_fails_without_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            tables = Path(d)/"rt_tables"
            for original in ("200 other\n", "77 vpn_candidates\n"):
                tables.write_text(original)
                with self.assertRaises(ValueError):
                    register_tables(tables, 200, 201)
                self.assertEqual(tables.read_text(), original)

    def test_upgrade_preserves_tuning_and_refuses_role_change(self):
        with tempfile.TemporaryDirectory() as d:
            config, tables = Path(d)/"config.json", Path(d)/"rt_tables"
            provision(CONFIG, config, tables, "hub")
            data = json.loads(config.read_text())
            data["minimum_dwell_seconds"] = 1234
            config.write_text(json.dumps(data))
            provision(CONFIG, config, tables, "hub")
            self.assertEqual(json.loads(config.read_text())["minimum_dwell_seconds"], 1234)
            with self.assertRaises(ValueError):
                provision(CONFIG, config, tables, "exit")

    def test_bad_configuration_does_not_register_tables(self):
        with tempfile.TemporaryDirectory() as d:
            config, tables = Path(d)/"config.json", Path(d)/"rt_tables"
            config.write_text('{"candidate_table": 254}')
            with self.assertRaises(ValueError):
                provision(CONFIG, config, tables, "hub")
            self.assertFalse(tables.exists())


if __name__ == "__main__":
    unittest.main()
