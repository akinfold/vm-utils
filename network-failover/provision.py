#!/usr/bin/env python3
"""Idempotent configuration and routing-table name registration."""
import argparse
import json
import os
from pathlib import Path
import tempfile


def atomic_write(path, content, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def register_tables(path, candidate, active):
    path = Path(path)
    text = path.read_text() if path.exists() else ""
    requested = {candidate: "vpn_candidates", active: "vpn_active"}
    found = set()
    for line in text.splitlines():
        fields = line.split("#", 1)[0].split()
        if len(fields) != 2:
            continue
        try:
            number = int(fields[0], 0)
        except ValueError:
            continue
        name = fields[1]
        if number in requested and requested[number] != name:
            raise ValueError(f"Table ID {number} is already registered as {name}")
        if name in requested.values() and requested.get(number) != name:
            raise ValueError(f"Table name {name} is already registered with ID {number}")
        if requested.get(number) == name:
            found.add(number)
    additions = [f"{number} {name}" for number, name in requested.items() if number not in found]
    if additions:
        atomic_write(path, text.rstrip()+"\n"+"\n".join(additions)+"\n")


def provision(template, config, tables, role):
    from failover import load_config
    target = Path(config)
    defaults = json.loads(Path(template).read_text())
    current = json.loads(target.read_text()) if target.exists() else {}
    if current.get("role", role) != role:
        raise ValueError("Existing network role differs; migrate the node explicitly before reinstalling")
    merged = {**defaults, **current, "role": role}
    # Validate before modifying the installed configuration or table registry.
    with tempfile.TemporaryDirectory() as directory:
        check = Path(directory)/"config.json"
        check.write_text(json.dumps(merged))
        load_config(check)
    register_tables(tables, merged["candidate_table"], merged["active_table"])
    atomic_write(target, json.dumps(merged, indent=2)+"\n", 0o640)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", required=True)
    parser.add_argument("--role", choices=("hub", "exit"), required=True)
    parser.add_argument("--config", default="/etc/vmutils/network-failover/config.json")
    parser.add_argument("--tables", default="/etc/iproute2/rt_tables")
    args = parser.parse_args()
    provision(args.template, args.config, args.tables, args.role)
