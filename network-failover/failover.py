#!/usr/bin/env python3
"""WireGuard exit discovery, health checks and routing in the agent's network namespace."""
import argparse
import concurrent.futures
import fcntl
import ipaddress
import json
import logging
import math
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import sqlite3
import ssl
import statistics
import subprocess
import sys
import time

LOG = logging.getLogger("vmutils-failover")
PROTOCOL = "242"
MARK_BASE = 0x6F000000
MAX_EXITS = 128
NAME = re.compile(r"^[a-zA-Z0-9_.-]{1,15}$")
DEFAULT_CONFIG = "/etc/vmutils/network-failover/config.json"
DEFAULT_STATE = "/var/lib/vmutils-network-failover"


def run(*args, check=True, input=None):
    return subprocess.run(args, text=True, input=input, capture_output=True,
                          timeout=15, check=check)


def ip_json(*args):
    return json.loads(run("ip", "-N", "-j", "-4", *args).stdout)


def load_config(path):
    c = json.loads(Path(path).read_text())
    if c["role"] not in ("hub", "exit"):
        raise ValueError("role must be hub or exit")
    if not NAME.fullmatch(c["client_interface"]) or not c["exit_prefix"]:
        raise ValueError("Invalid interface or exit prefix")
    if any(not NAME.fullmatch(n) for n in c["exit_interfaces"]):
        raise ValueError("Invalid exit interface")
    if c["score_mode"] not in ("weighted", "median7d"):
        raise ValueError("score_mode must be weighted or median7d")
    tables = [c["candidate_table"], c["active_table"]]
    probes = range(c["probe_table_base"] + 1, c["probe_table_base"] + MAX_EXITS + 1)
    if (len(set(tables)) != 2 or any(t in (0, 253, 254, 255) or t < 1 for t in tables)
            or any(t in probes for t in tables)
            or c["probe_table_base"] < 1000
            or c["probe_table_base"] + MAX_EXITS >= c["policy_priority"] - 1):
        raise ValueError("Routing table/priority ranges overlap or are reserved")
    for key in ("interval_seconds", "failure_threshold", "recovery_threshold",
                "retire_after_seconds", "retired_probe_seconds", "history_seconds",
                "sample_seconds", "half_life_seconds", "minimum_samples",
                "probe_timeout_seconds", "switch_hold_seconds", "minimum_dwell_seconds"):
        if c[key] <= 0:
            raise ValueError(f"{key} must be positive")
    if not 0 <= c["recent_loss_limit"] <= 1:
        raise ValueError("recent_loss_limit must be between zero and one")
    if c["switch_margin_ms"] < 0 or c["switch_margin_fraction"] < 0:
        raise ValueError("Switch margins cannot be negative")
    if not 1 <= c["internet_quorum"] <= len(c["internet_targets"]):
        raise ValueError("Invalid internet quorum")
    for target in c["internet_targets"]:
        if ipaddress.ip_address(target["ip"]).version != 4:
            raise ValueError("This release probes IPv4 exits only")
        if not 1 <= target["port"] <= 65535 or not target["server_name"]:
            raise ValueError("Invalid TLS target")
    return c


class Store:
    def __init__(self, directory):
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(Path(directory) / "history.sqlite3"))
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS nodes (
            name TEXT PRIMARY KEY, slot INTEGER UNIQUE, identity TEXT, gateway TEXT,
            created REAL, inactive_since REAL, retired INTEGER DEFAULT 0,
            good INTEGER DEFAULT 0, bad INTEGER DEFAULT 0, healthy INTEGER DEFAULT 0,
            last_probe REAL DEFAULT 0, last_sample REAL DEFAULT 0,
            last_success REAL, present INTEGER DEFAULT 1);
          CREATE TABLE IF NOT EXISTS samples (
            name TEXT, ts REAL, rtt REAL, ok INTEGER);
          CREATE INDEX IF NOT EXISTS sample_window ON samples(name, ts);
          CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        """)

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def nodes(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM nodes ORDER BY slot")]

    def discover(self, discovered, now):
        self.db.execute("UPDATE nodes SET present=0")
        for node in discovered:
            old = self.db.execute("SELECT * FROM nodes WHERE name=?", (node["name"],)).fetchone()
            if old and old["identity"] == node["identity"]:
                self.db.execute("UPDATE nodes SET present=? WHERE name=?", (int(node.get("usable", True)), node["name"]))
                continue
            if old:
                slot = old["slot"]
                self.db.execute("DELETE FROM samples WHERE name=?", (node["name"],))
                self.db.execute("DELETE FROM nodes WHERE name=?", (node["name"],))
            else:
                used = {n["slot"] for n in self.nodes()}
                slot = next((i for i in range(1, MAX_EXITS + 1) if i not in used), None)
                if slot is None:
                    raise ValueError("Exit registry is full; archive obsolete nodes before adding more")
            self.db.execute("""INSERT INTO nodes
                (name,slot,identity,gateway,created,inactive_since,present)
                VALUES (?,?,?,?,?,?,?)""",
                (node["name"], slot, node["identity"], node["gateway"], now, now, int(node.get("usable", True))))
        self.db.commit()

    def observe(self, name, ok, rtt, now, c):
        node = dict(self.db.execute("SELECT * FROM nodes WHERE name=?", (name,)).fetchone())
        if ok:
            good, bad = node["good"] + 1, 0
            healthy = node["healthy"] or good >= c["recovery_threshold"]
            retired = 0 if healthy else node["retired"]
            inactive = None
            last_success = now
        else:
            good, bad = 0, node["bad"] + 1
            healthy = node["healthy"] and bad < c["failure_threshold"]
            inactive = node["inactive_since"] if node["inactive_since"] is not None else now
            retired = node["retired"] or now - inactive >= c["retire_after_seconds"]
            last_success = node["last_success"]
        self.db.execute("""UPDATE nodes SET good=?,bad=?,healthy=?,inactive_since=?,
            retired=?,last_probe=?,last_success=? WHERE name=?""",
            (good, bad, int(healthy), inactive, int(retired), now, last_success, name))
        if now - node["last_sample"] >= c["sample_seconds"]:
            self.db.execute("INSERT INTO samples VALUES (?,?,?,?)", (name, now, rtt, int(ok)))
            self.db.execute("UPDATE nodes SET last_sample=? WHERE name=?", (now, name))
        self.db.execute("DELETE FROM samples WHERE ts < ?", (now - c["history_seconds"],))
        self.db.commit()

    def scores(self, now, c):
        results = {}
        for node in self.nodes():
            rows = list(self.db.execute("SELECT ts,rtt,ok FROM samples WHERE name=? AND ts>=?",
                                       (node["name"], now - c["history_seconds"])))
            good = [(r["rtt"], 2 ** (-(now-r["ts"])/c["half_life_seconds"]))
                    for r in rows if r["ok"] and r["rtt"] is not None]
            recent = [r for r in rows if r["ts"] >= now - 300]
            loss = 1 - sum(r["ok"] for r in recent) / len(recent) if recent else 1
            median = statistics.median([v for v, _ in good]) if good else None
            if good:
                p50, p95 = percentile(good, .5), percentile(good, .95)
                score = median if c["score_mode"] == "median7d" else p50 + .25*(p95-p50)
            else:
                score = None
            results[node["name"]] = {"score_ms": score, "median_7d_ms": median,
                "recent_loss": loss, "samples": len(good)}
        return results


def percentile(values, quantile):
    ordered = sorted(values)
    target = sum(w for _, w in ordered) * quantile
    total = 0
    for value, weight in ordered:
        total += weight
        if total >= target:
            return value
    return ordered[-1][0]


def select(nodes, scores, current, now, c, pending=None, switched=0):
    """Health failover is immediate after thresholds; quality changes use hysteresis."""
    eligible = [n for n in nodes if n["present"] and n["healthy"] and not n["retired"]]
    if not eligible:
        return None, None
    current_node = next((n for n in eligible if n["name"] == current), None)
    def order(n):
        s = scores[n["name"]]
        # Prefer low-loss paths, then known latency, then a stable deterministic tie-break.
        return (s["recent_loss"] > c["recent_loss_limit"],
                s["score_ms"] if s["score_ms"] is not None else math.inf, n["name"])
    best = min(eligible, key=order)
    if not current_node:
        return best["name"], None
    if best["name"] == current:
        return current, None
    old, new = scores[current], scores[best["name"]]
    enough = new["samples"] >= c["minimum_samples"]
    less_loss = old["recent_loss"] > c["recent_loss_limit"] >= new["recent_loss"]
    faster = (new["score_ms"] is not None and (old["score_ms"] is None or
              old["score_ms"] - new["score_ms"] >= max(c["switch_margin_ms"],
                  old["score_ms"]*c["switch_margin_fraction"])))
    if not enough or not (less_loss or (faster and new["recent_loss"] <= c["recent_loss_limit"])):
        return current, None
    if not pending or pending["name"] != best["name"]:
        pending = {"name": best["name"], "since": now}
    if now - pending["since"] >= c["switch_hold_seconds"] and now-switched >= c["minimum_dwell_seconds"]:
        return best["name"], None
    return current, pending


def wg_peers(interface):
    peers = []
    for line in run("wg", "show", interface, "allowed-ips").stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            networks = [ipaddress.ip_network(x) for x in re.split(r"[,\s]+", " ".join(parts[1:]))
                        if x and x != "(none)"]
            peers.append((parts[0], networks))
    return peers


def discover(c):
    result = []
    interfaces = run("wg", "show", "interfaces").stdout.split()
    for name in interfaces:
        if not name.startswith(c["exit_prefix"]):
            continue
        peers = wg_peers(name)
        if len(peers) != 1 or ipaddress.ip_network("0.0.0.0/0") not in peers[0][1]:
            LOG.warning("Ignoring %s: requires exactly one peer with IPv4 default AllowedIPs", name)
            continue
        addresses = ip_json("address", "show", "dev", name)
        links = [ipaddress.ip_interface(f"{a['local']}/{a['prefixlen']}")
                 for link in addresses for a in link["addr_info"] if a["family"] == "inet"]
        if len(links) != 1 or links[0].network.prefixlen != 30 or links[0].ip not in list(links[0].network.hosts()):
            LOG.warning("Ignoring %s: requires one usable IPv4 /30 transit address", name)
            continue
        gateway = str(next(ip for ip in links[0].network.hosts() if ip != links[0].ip))
        public = run("wg", "show", name, "public-key").stdout.strip()
        result.append({"name": name, "gateway": gateway, "usable": "UP" in addresses[0].get("flags", []),
                       "identity": f"{public}:{peers[0][0]}:{links[0]}"})
    return result


def routes(table):
    result = run("ip", "-N", "-j", "-4", "route", "show", "table", str(table), check=False)
    if result.returncode:
        if "FIB table does not exist" in result.stderr:
            return []
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout)


def owned_table(table):
    rows = routes(table)
    if any(str(r.get("protocol")) != PROTOCOL for r in rows):
        raise ValueError(f"Table {table} contains routes not owned by vmutils (protocol {PROTOCOL})")
    return rows


def default_route(table, dev, gateway=None, metric=None):
    existing = owned_table(table)
    match = next((r for r in existing if r.get("dst") == "default" and
                  r.get("metric", 0) == (metric or 0)), None)
    if match and match.get("dev") == dev and match.get("gateway") == gateway:
        return
    args = ["ip", "-4", "route", "replace", "default", "table", str(table), "proto", PROTOCOL]
    if gateway:
        args.extend(["via", gateway])
    args.extend(["dev", dev])
    if metric is not None:
        args.extend(["metric", str(metric)])
    run(*args)


def remove_default(table, metric=None):
    for r in owned_table(table):
        if r.get("dst") == "default" and (metric is None or r.get("metric", 0) == metric):
            args = ["ip", "-4", "route", "del", "default", "table", str(table), "proto", PROTOCOL]
            if r.get("metric") is not None:
                args.extend(["metric", str(r["metric"])])
            run(*args)


def ensure_rule(priority, arguments):
    entries = [r for r in ip_json("rule", "show") if r.get("priority") == priority]
    expected_args = dict(zip(arguments[::2], arguments[1::2]))
    expected = {"priority": priority, "src": "all", "table": int(expected_args["table"])}
    for key in ("iif", "fwmark"):
        if key in expected_args:
            expected[key] = expected_args[key]
    if "suppress_prefixlength" in expected_args:
        expected["suppress_prefixlen"] = int(expected_args["suppress_prefixlength"])
    if entries:
        row = dict(entries[0])
        if "table" in row:
            row["table"] = int(row["table"])
        if "fwmark" in row:
            row["fwmark"] = hex(int(str(row["fwmark"]), 0))
        if len(entries) != 1 or row != expected:
            raise ValueError(f"Rule priority {priority} already occupied by another policy")
        return
    run("ip", "-4", "rule", "add", "priority", str(priority), *arguments)


def firewall(chain, table, hook, rules):
    created = run("iptables", "-w", "5", "-t", table, "-N", chain, check=False)
    if created.returncode and "Chain already exists" not in created.stderr:
        raise RuntimeError(created.stderr.strip())
    existing = run("iptables", "-w", "5", "-t", table, "-S", chain).stdout.splitlines()
    wanted = ["-A " + chain + " " + r for r in rules]
    if [shlex.split(r) for r in existing if r.startswith("-A ")] != [shlex.split(r) for r in wanted]:
        text = f"*{table}\n-F {chain}\n" + "\n".join(wanted) + "\nCOMMIT\n"
        run("iptables-restore", "--wait", "5", "--noflush", input=text)
    if run("iptables", "-w", "5", "-t", table, "-C", hook, "-j", chain, check=False).returncode:
        run("iptables", "-w", "5", "-t", table, "-I", hook, "1", "-j", chain)


def network_setup(c, interfaces, wan):
    forward, nat, inputs = [], [], []
    reverse = set()
    for interface in interfaces:
        peers = wg_peers(interface)
        sources = sorted({str(net) for _, networks in peers for net in networks if net.version == 4})
        if "0.0.0.0/0" in sources:
            raise ValueError(f"{interface}: remove default AllowedIPs from the client/exit-facing peer first")
        port = int(run("wg", "show", interface, "listen-port").stdout.strip())
        if port:
            inputs.append(f"-p udp -m udp --dport {port} -j ACCEPT")
        inputs.append(f"-i {interface} -p icmp -m icmp --icmp-type 8 -j ACCEPT")
        for source in sources:
            if c["role"] == "exit":
                reverse.add((interface, source))
                # Reconcile reverse routes; reject conflicting routes instead of taking them over.
                current = [r for r in routes("main") if r.get("dst") != "default" and str(ipaddress.ip_network(r.get("dst", "0.0.0.0/0"))) == source]
                if any(r.get("dev") != interface for r in current):
                    raise ValueError(f"Conflicting reverse route for {source}")
                if not current:
                    run("ip", "-4", "route", "add", source, "dev", interface, "proto", PROTOCOL)
                forward.append(f"-i {interface} -o {wan} -s {source} -j ACCEPT")
            else:
                forward.append(f"-i {interface} -s {source} -j ACCEPT")
            nat.append(f"-s {source} -o {wan} -j MASQUERADE")
        forward.append(f"-o {interface} -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT")
    if c["role"] == "exit":
        for route in routes("main"):
            if (str(route.get("protocol")) == PROTOCOL
                    and route.get("dev") in c["exit_interfaces"]
                    and route.get("dst") != "default"):
                network = str(ipaddress.ip_network(route["dst"]))
                if (route["dev"], network) not in reverse:
                    run("ip", "-4", "route", "del", network, "dev", route["dev"], "proto", PROTOCOL)
    if c["role"] == "hub":
        for node in discover(c):
            port = int(run("wg", "show", node["name"], "listen-port").stdout.strip())
            if port:
                inputs.append(f"-p udp -m udp --dport {port} -j ACCEPT")
    run("sysctl", "-q", "-w", "net.ipv4.ip_forward=1")
    # Strict reverse-path filtering rejects replies coming through a non-default exit.
    run("sysctl", "-q", "-w", "net.ipv4.conf.all.rp_filter=2")
    for interface in interfaces:
        run("sysctl", "-q", "-w", f"net.ipv4.conf.{interface}.rp_filter=2")
    if c["role"] == "hub":
        for node in discover(c):
            run("sysctl", "-q", "-w", f"net.ipv4.conf.{node['name']}.rp_filter=2")
    firewall("VMUTILS_FWD", "filter", "FORWARD", sorted(set(forward)))
    firewall("VMUTILS_INPUT", "filter", "INPUT", sorted(set(inputs)))
    firewall("VMUTILS_NAT", "nat", "POSTROUTING", sorted(set(nat)))


def peer_ping(node, mark, timeout):
    result = run("ping", "-n", "-c", "1", "-W", str(timeout), "-I", node["name"],
                 "-m", str(mark), node["gateway"], check=False)
    match = re.search(r"time[=<]([0-9.]+)", result.stdout)
    return float(match[1]) if result.returncode == 0 and match else None


def tls_probe(node, mark, target, timeout):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, node["name"].encode()+b"\0")
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, mark)
            sock.connect((target["ip"], target["port"]))
            with ssl.create_default_context().wrap_socket(sock, server_hostname=target["server_name"]) as tls:
                # A verified TLS handshake proves forwarding to an independent Internet service.
                return bool(tls.version())
    except (OSError, ssl.SSLError):
        return False


def probe(node, c):
    mark = MARK_BASE + node["slot"]
    timeout = c["probe_timeout_seconds"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(c["internet_targets"])+1) as pool:
        rtt_future = pool.submit(peer_ping, node, mark, timeout)
        checks = [pool.submit(tls_probe, node, mark, t, timeout) for t in c["internet_targets"]]
        ok = sum(f.result() for f in checks) >= c["internet_quorum"]
        rtt = rtt_future.result()
    # ICMP filtering prevents latency ranking, but must not misclassify a working Internet exit.
    return ok, rtt


def cycle(c, store, now):
    interfaces = run("wg", "show", "interfaces").stdout.split()
    defaults = [r for r in routes("main") if r.get("dst") == "default" and r.get("dev") not in interfaces]
    if not defaults:
        raise ValueError("No native IPv4 default route in the agent namespace")
    wan = min(defaults, key=lambda r: r.get("metric", 0))
    if c["role"] == "exit":
        monitored = [n for n in c["exit_interfaces"] if n in interfaces]
        network_setup(c, monitored, wan["dev"])
        store.put("status", {"role": "exit", "updated": now, "interfaces": monitored, "wan": wan})
        store.db.commit()
        return
    if c["client_interface"] not in interfaces:
        raise ValueError("Waiting for the hub client interface")
    # Do not activate on legacy single-interface /0 configurations.
    network_setup(c, [c["client_interface"]], wan["dev"])
    owned_table(c["candidate_table"])
    owned_table(c["active_table"])
    store.discover(discover(c), now)
    namespace_id = os.stat("/proc/self/ns/net").st_ino
    if (store.get("namespace_id") != namespace_id
            or store.get("last_cycle", 0) < now - max(60, c["interval_seconds"]*3)):
        # A restart/gap requires new health evidence; do not trust stale persisted success.
        store.db.execute("UPDATE nodes SET healthy=0,good=0,bad=0")
        store.put("pending", None)
    previous = store.get("selected")
    nodes = store.nodes()
    known_slots = {n["slot"] for n in nodes}
    for r in owned_table(c["candidate_table"]):
        if r.get("metric") not in known_slots:
            remove_default(c["candidate_table"], r.get("metric", 0))
    due = []
    for node in nodes:
        table = c["probe_table_base"] + node["slot"]
        if node["present"]:
            default_route(table, node["name"], node["gateway"])
            ensure_rule(table, ["fwmark", hex(MARK_BASE+node["slot"]), "table", str(table)])
        # Retired exits are probed less often, then rapidly until recovery is confirmed.
        interval = c["retired_probe_seconds"] if node["retired"] and not node["good"] else c["interval_seconds"]
        if now-node["last_probe"] >= interval:
            due.append(node)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        jobs = {n["name"]: pool.submit(probe, n, c) for n in due if n["present"]}
        for node in due:
            ok, rtt = jobs[node["name"]].result() if node["name"] in jobs else (False, None)
            store.observe(node["name"], ok, rtt, now, c)
    nodes = store.nodes()
    for node in nodes:
        if node["present"] and not node["retired"]:
            default_route(c["candidate_table"], node["name"], node["gateway"], node["slot"])
        else:
            remove_default(c["candidate_table"], node["slot"])
    scores = store.scores(now, c)
    selected, pending = select(nodes, scores, previous, now, c, store.get("pending"), store.get("switched", 0))
    chosen = next((n for n in nodes if n["name"] == selected), None)
    default_route(c["active_table"], chosen["name"] if chosen else wan["dev"],
                  chosen["gateway"] if chosen else wan.get("gateway"))
    # Specific main-table routes preserve private traffic; only its default is suppressed.
    ensure_rule(c["policy_priority"]-1, ["iif", c["client_interface"], "table", "254", "suppress_prefixlength", "0"])
    ensure_rule(c["policy_priority"], ["iif", c["client_interface"], "table", str(c["active_table"])])
    if selected != previous:
        LOG.warning("Exit changed: %s -> %s", previous or "hub WAN", selected or "hub WAN")
        store.put("switched", now)
    store.put("selected", selected)
    store.put("pending", pending)
    store.put("last_cycle", now)
    store.put("namespace_id", namespace_id)
    store.put("routing_ready", True)
    store.put("status", {"role": "hub", "updated": now, "selected": selected or "hub WAN",
        "pending": pending, "nodes": [{**n, **scores[n["name"]]} for n in nodes]})
    store.db.commit()


def worker(config, directory):
    c = load_config(config)
    Path(directory).mkdir(parents=True, exist_ok=True)
    with open(Path(directory)/"worker.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store = Store(directory)
        try:
            cycle(c, store, time.time())
        except Exception:
            # Once this service owns the policy, a failed reconciliation must not strand
            # clients on an exit that can no longer be checked. Preserve foreign tables.
            if c["role"] == "hub" and store.get("routing_ready", False):
                try:
                    wireguard = run("wg", "show", "interfaces").stdout.split()
                    defaults = [r for r in routes("main") if r.get("dst") == "default"
                                and r.get("dev") not in wireguard]
                    wan = min(defaults, key=lambda r: r.get("metric", 0))
                    default_route(c["active_table"], wan["dev"], wan.get("gateway"))
                    store.put("selected", None)
                    store.put("pending", None)
                    store.db.execute("UPDATE nodes SET healthy=0,good=0,bad=0")
                    store.put("status", {"role": "hub", "updated": time.time(),
                              "selected": "hub WAN", "error": "Reconciliation failed; see journal"})
                    store.db.commit()
                    LOG.error("Reconciliation failed; selected the native WAN as a fallback")
                except Exception:
                    LOG.exception("Unable to install emergency WAN fallback")
            raise
        finally:
            store.db.close()


def supervise(config, directory):
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    last_error = None
    while running:
        started = time.monotonic()
        c = load_config(config)
        try:
            result = run("docker", "inspect", "--format", "{{.State.Pid}}", c["container"], check=False)
            if result.returncode or not result.stdout.strip().isdigit() or int(result.stdout.strip()) <= 0:
                raise RuntimeError("Waiting for the Pro Custodibus agent container")
            # Re-enter the current namespace every cycle, including after container recreation.
            proc = subprocess.run(["nsenter", "--target", result.stdout.strip(), "--net", "--",
                sys.executable, str(Path(__file__).resolve()), "--config", config,
                "--state-dir", directory, "once"], text=True, capture_output=True,
                timeout=max(60, MAX_EXITS*c["probe_timeout_seconds"]//8+30))
            if proc.returncode:
                raise RuntimeError(proc.stderr.strip())
            if proc.stderr:
                LOG.info("%s", proc.stderr.strip())
            last_error = None
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            if str(exc) != last_error:
                LOG.error("%s", exc)
                last_error = str(exc)
        # Interruptible sleep keeps shutdown responsive.
        while running and time.monotonic()-started < c["interval_seconds"]:
            time.sleep(.2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--state-dir", default=DEFAULT_STATE)
    parser.add_argument("command", choices=("run", "once", "status"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.command == "status":
        store = Store(args.state_dir)
        print(json.dumps(store.get("status", {"status": "waiting for first successful cycle"}), indent=2))
        return
    try:
        if args.command == "once":
            worker(args.config, args.state_dir)
        else:
            supervise(args.config, args.state_dir)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        LOG.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
