#!/usr/bin/env python3
"""Real WireGuard, TLS, NAT and policy routing tests in disposable Linux namespaces.
Run only in the isolated test container documented in README.md.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from failover import run

NAMES = ["hub", "client", "exit-a", "exit-b", "internet"]


def ns(name, *args, **kwargs):
    return run("ip", "netns", "exec", name, *args, **kwargs)


def veth(left, right, subnet, idx):
    a, b = f"v{idx}a", f"v{idx}b"
    run("ip", "link", "add", a, "type", "veth", "peer", "name", b)
    for interface, namespace, address in ((a, left, f"172.31.{subnet}.1/24"), (b, right, f"172.31.{subnet}.254/24")):
        run("ip", "link", "set", interface, "netns", namespace)
        ns(namespace, "ip", "addr", "add", address, "dev", interface)
        ns(namespace, "ip", "link", "set", interface, "up")
    ns(left, "ip", "route", "add", "default", "via", f"172.31.{subnet}.254")


def keys(directory, label):
    private = run("wg", "genkey").stdout.strip()
    public = run("wg", "pubkey", input=private).stdout.strip()
    key = directory/f"{label}.key"
    key.write_text(private)
    key.chmod(0o600)
    return str(key), public


def wireguard(directory, left_ns, left_if, left_ip, left_port, left_wan,
              right_ns, right_if, right_ip, right_port, right_wan, left_allowed, right_allowed):
    left_key, left_public = keys(directory, left_ns+left_if)
    right_key, right_public = keys(directory, right_ns+right_if)
    for n, iface, ip, port, key, peer, endpoint, allowed in (
        (left_ns, left_if, left_ip, left_port, left_key, right_public, f"{right_wan}:{right_port}", left_allowed),
        (right_ns, right_if, right_ip, right_port, right_key, left_public, f"{left_wan}:{left_port}", right_allowed)):
        ns(n, "ip", "link", "add", iface, "type", "wireguard")
        ns(n, "ip", "addr", "add", ip, "dev", iface)
        ns(n, "wg", "set", iface, "private-key", key, "listen-port", str(port),
           "peer", peer, "endpoint", endpoint, "allowed-ips", allowed, "persistent-keepalive", "25")
        ns(n, "ip", "link", "set", iface, "up")


def main():
    processes = []
    with tempfile.TemporaryDirectory() as d:
        directory = Path(d)
        for name in NAMES:
            run("ip", "netns", "add", name)
            ns(name, "ip", "link", "set", "lo", "up")
            ns(name, "sysctl", "-qw", "net.ipv4.ip_forward=1")
        try:
            for idx, name in enumerate(["hub", "exit-a", "exit-b", "client"]):
                veth(name, "internet", idx, idx)
            for ip in ("198.18.0.1", "198.18.0.2"):
                ns("internet", "ip", "addr", "add", ip+"/32", "dev", "lo")
            cert, key = directory/"cert.pem", directory/"server.key"
            run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                "-subj", "/CN=health.test", "-addext", "subjectAltName=DNS:health.test",
                "-keyout", str(key), "-out", str(cert))
            os.environ["SSL_CERT_FILE"] = str(cert)
            server = directory/"server.py"
            server.write_text('''import socket,ssl,threading
ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain("CERT", "KEY")
def handle(raw,addr):
 try:
  raw.settimeout(2)
  with ctx.wrap_socket(raw,server_side=True) as s:
   if s.recv(1000): s.sendall(addr[0].encode())
 except (OSError,ssl.SSLError): raw.close()
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
s.bind(("0.0.0.0",443)); s.listen()
while True:
 raw,addr=s.accept(); threading.Thread(target=handle,args=(raw,addr),daemon=True).start()
'''.replace("CERT", str(cert)).replace("KEY", str(key)))
            processes.append(subprocess.Popen(["ip", "netns", "exec", "internet", "python3", str(server)]))
            wireguard(directory, "hub", "wg0", "10.0.0.1/24", 51820, "172.31.0.1",
                      "client", "wg0", "10.0.0.5/32", 51820, "172.31.3.1", "10.0.0.5/32", "0.0.0.0/0")
            ns("client", "ip", "route", "add", "172.31.0.1/32", "via", "172.31.3.254")
            ns("client", "ip", "route", "replace", "default", "dev", "wg0")
            config = json.loads((ROOT/"config.json").read_text())
            config["internet_targets"] = [{"ip": ip, "port": 443, "server_name": "health.test"}
                                         for ip in ("198.18.0.1", "198.18.0.2")]
            config["probe_timeout_seconds"] = 1
            hub_config = directory/"hub.json"
            hub_config.write_text(json.dumps(config))
            tick_script = directory/"tick.py"
            tick_script.write_text('''import sys,time
sys.path.insert(0,"ROOT")
from failover import Store,load_config,cycle
store=Store(sys.argv[2]); cycle(load_config(sys.argv[1]),store,float(sys.argv[3]))
'''.replace("ROOT", str(ROOT)))
            hub_state = directory/"hub-state"
            clock = time.time()
            def tick(name="hub", cfg=hub_config, state=hub_state):
                nonlocal clock
                clock += 5
                ns(name, "python3", str(tick_script), str(cfg), str(state), str(clock))
            def active():
                return json.loads(ns("hub", "ip", "-j", "route", "show", "table", "201").stdout)[0]["dev"]
            request = '''import socket,ssl
with socket.create_connection(("198.18.0.1",443),timeout=3) as raw:
 with ssl.create_default_context().wrap_socket(raw,server_hostname="health.test") as s:
  s.sendall(b"test"); print(s.recv(100).decode())
'''
            def assert_source(expected):
                source = ns("client", "python3", "-c", request).stdout.strip()
                assert source == expected, (source, expected)
            def add_exit(letter, subnet, transit, hub_port):
                node = f"exit-{letter}"
                wireguard(directory, "hub", f"wg-exit-{letter}", f"10.254.{transit}.1/30", hub_port, "172.31.0.1",
                          node, "wg0", f"10.254.{transit}.2/30", 51820, f"172.31.{subnet}.1",
                          "0.0.0.0/0", f"10.0.0.0/24,10.254.{transit}.1/32")
                cfg = directory/f"{node}.json"
                cfg.write_text(json.dumps({**config, "role": "exit"}))
                tick(node, cfg, directory/f"{node}-state")
                tick(node, cfg, directory/f"{node}-state")  # Idempotent reverse routes and firewall.
            # No candidates: fresh client connections leave through the hub's native WAN.
            tick()
            assert active() == "v0a", active()
            assert_source("172.31.0.1")
            print("PASS zero-exit WAN fallback and automatic hub NAT", flush=True)
            add_exit("a", 1, 1, 51821)
            for _ in range(3): tick()
            assert active() == "wg-exit-a", active()
            assert_source("172.31.1.1")
            print("PASS automatic discovery, marked TLS probes and exit NAT", flush=True)
            add_exit("b", 2, 2, 51822)
            for _ in range(3): tick()
            assert len(json.loads(ns("hub", "ip", "-j", "route", "show", "table", "200").stdout)) == 2
            assert active() == "wg-exit-a"
            # The tunnel still handshakes and pings, but the Internet behind A is unavailable.
            ns("exit-a", "iptables", "-I", "FORWARD", "1", "-j", "DROP")
            for _ in range(3): tick()
            assert active() == "wg-exit-b", active()
            assert_source("172.31.2.1")
            print("PASS live addition and Internet failure with a live WireGuard tunnel", flush=True)
            ns("exit-b", "iptables", "-I", "FORWARD", "1", "-j", "DROP")
            for _ in range(3): tick()
            assert active() == "v0a", active()
            assert_source("172.31.0.1")
            print("PASS all-exit failure returns client traffic to hub WAN", flush=True)
            # Age the continuous failure without waiting seven real days.
            ns("hub", "python3", "-c", f'''import sqlite3
c=sqlite3.connect("{hub_state}/history.sqlite3")
c.execute("UPDATE nodes SET inactive_since=?", ({clock-604800},)); c.commit()
''')
            tick()
            assert json.loads(ns("hub", "ip", "-j", "route", "show", "table", "200").stdout) == []
            assert len(json.loads(ns("hub", "ip", "-j", "route", "show", "table", "10001").stdout)) == 1
            print("PASS seven-day retirement keeps independent recovery probe routes", flush=True)
            ns("exit-a", "iptables", "-D", "FORWARD", "-j", "DROP")
            clock += 300
            for _ in range(3): tick()
            assert active() == "wg-exit-a", active()
            assert_source("172.31.1.1")
            print("PASS retired exit recovers and is re-registered automatically", flush=True)
            # Invalid reloads must send traffic through the existing WAN policy, not strand it.
            for invalid in ('{invalid', json.dumps({**config, "active_table": 300})):
                hub_config.write_text(invalid)
                result = ns("hub", "python3", str(ROOT/"failover.py"), "--config", str(hub_config),
                            "--state-dir", str(hub_state), "once", check=False)
                assert result.returncode != 0
                assert active() == "v0a"
                assert_source("172.31.0.1")
                hub_config.write_text(json.dumps(config))
                for _ in range(3): tick()
                assert active() == "wg-exit-a"
            print("PASS invalid configuration and layout edits preserve native WAN fallback", flush=True)
            # Recreated namespace starts with empty owned tables; persisted history is kept.
            ns("hub", "ip", "route", "flush", "table", "201")
            tick()
            assert active() == "wg-exit-a"
            # Check the fallback is unaffected by peer-interface disappearance.
            ns("hub", "ip", "link", "del", "wg-exit-a")
            tick()
            assert active() == "v0a"
            assert_source("172.31.0.1")
            print("PASS route reconciliation and interface disappearance", flush=True)
        finally:
            for p in processes:
                p.terminate()
                p.wait(timeout=5)
            for name in reversed(NAMES):
                run("ip", "netns", "del", name, check=False)


if __name__ == "__main__":
    main()
