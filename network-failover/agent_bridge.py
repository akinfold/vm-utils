#!/usr/bin/env python3
"""Report local configuration without executing queued controller changes.
Runs with the existing agent's Python interpreter and enrollment credentials.
"""
import json
import re
import sys


def main():
    from procustodibus_agent import __version__
    from procustodibus_agent.agent import interrogate
    from procustodibus_agent.api import ping_api
    from procustodibus_agent.cnf import Cnf

    version = tuple(int(n) for n in re.findall(r"\d+", __version__)[:3])
    if version < (1, 10, 2) or version >= (2, 0, 0):
        raise RuntimeError("Migration supports Pro Custodibus agent 1.10.2 through 1.x")
    cnf = Cnf()
    interfaces = json.loads(sys.argv[1])
    if cnf.read_only or set(interfaces).intersection(cnf.unmanaged_interfaces):
        raise RuntimeError("Migration requires managed interfaces and a writable agent")
    response = ping_api(cnf, interrogate(cnf))
    if response.get("data"):
        raise RuntimeError("Pro Custodibus has queued changes; apply or cancel them before migration")
    print("Local configuration reported; controller change queue is empty")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception:
        # Authentication material and response bodies must never reach migration logs.
        print("Agent configuration/report failed; inspect the agent locally", file=sys.stderr)
        sys.exit(1)
