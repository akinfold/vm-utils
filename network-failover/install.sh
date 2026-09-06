#!/usr/bin/env bash
set -euo pipefail

TASK_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROLE=${1:-hub}
START=${2:-start}
if [[ "$ROLE" != hub && "$ROLE" != exit ]]; then
    echo "Usage: bash install.sh [hub|exit]" >&2
    exit 2
fi

if [[ "${VMUTILS_SKIP_PACKAGES:-0}" != 1 ]]; then
    sudo apt-get update
    sudo apt-get install -y python3 iproute2 iptables iputils-ping wireguard-tools util-linux ca-certificates
fi
sudo install -d -m 755 /usr/local/lib/vmutils/network-failover /etc/vmutils/network-failover
sudo install -d -m 700 /var/lib/vmutils-network-failover
sudo install -m 755 "$TASK_DIR/failover.py" /usr/local/lib/vmutils/network-failover/failover.py
sudo install -m 644 "$TASK_DIR/provision.py" /usr/local/lib/vmutils/network-failover/provision.py
sudo python3 "$TASK_DIR/provision.py" --template "$TASK_DIR/config.json" --role "$ROLE"
sudo install -m 644 "$TASK_DIR/vmutils-network-failover.service" /etc/systemd/system/vmutils-network-failover.service
sudo systemctl daemon-reload
sudo systemctl enable vmutils-network-failover.service
if [[ "$START" != --no-start ]]; then
    sudo systemctl restart vmutils-network-failover.service
fi
printf 'Installed network automation for role %s. It waits for the agent container if necessary.\n' "$ROLE"
echo "Status: sudo python3 /usr/local/lib/vmutils/network-failover/failover.py status"
echo "Logs: sudo journalctl -u vmutils-network-failover -f"
