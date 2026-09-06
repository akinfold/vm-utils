# First live-server test

Use a maintenance window and an administration connection to each server's public
address. Start with the hub, confirm native WAN connectivity, then migrate one exit
and finally the second. Keep the printed backup IDs and rollback deadlines visible.
Do not start a second node's cutover while the preceding node is unconfirmed.

## Before the window

- Pull the reviewed commit on the hub and exits; record `git rev-parse HEAD`.
- Check agent version (supported 1.10.2–1.x), empty Pro Custodibus change queues,
  host clock synchronization, and the existing bind-mounted WireGuard directory.
- Fill the topology with the existing exit public keys, unused `/30` transit
  networks, public hub endpoint and UDP ports actually published by Docker.
- Keep plans/backups on the servers with their generated restrictive permissions.
  Only the public `NAME.exit.json` bundles should be transferred between nodes.
- Record the baseline client public IPv4 address and private services that must
  remain accessible. Use a real VPN client for Internet checks, not a shell on the hub.

## Migration acceptance

1. Prepare and apply the hub plan using the README commands. Check the printed
   persistent timer with `sudo systemctl list-timers 'vmutils-rollback-*'`.
2. On a VPN client, open a new HTTPS connection. With no migrated exit yet, it should
   use the hub's public IPv4 address. Check private VPN destinations too.
3. Check controller access, reported interfaces and change queues. Read watchdog
   status and the journal. Confirm the hub before its deadline only if these pass.
4. Transfer one exit bundle, prepare and apply it there. Its old private tunnel
   address changes; use public SSH for administration. Confirm that the hub discovers
   the link, obtains three successful checks and moves fresh client connections to
   that exit. Confirm the exit before its deadline.
5. Repeat for the other exit. Confirm its independent health checks even if it is
   not selected for normal traffic. Verify that a new client inside the hub's existing
   VPN subnet can reach the Internet without modifying exit routes.

If a check fails, use the printed rollback command or let the timer expire. Do not
confirm just to stop the timer. If reporting fails during rollback, local routing
is restored and the agent daemon may remain stopped; resolve the controller/queue
issue before starting it as described in the README.

## Controlled failure matrix

Perform one fault at a time, keeping a separate public administration connection.
Restore the fault before proceeding. Save status, selected route, timestamps and
client-visible public IP for each case.

| Test | Expected result |
|---|---|
| Stop the active exit's agent container, then restart it | After failed checks, fresh client connections use the other healthy exit. Returning exits require fresh successes. |
| Stop both exit containers, then restore one | Fresh connections use the hub WAN; a restored healthy exit becomes eligible again. |
| Block forwarding on one exit while leaving its WireGuard tunnel up | Internet probes fail for that exit despite a live handshake; the hub chooses a working path. Arrange independent timed cleanup before injecting firewall faults. |
| Restart the hub's agent container | The watchdog attaches to the new namespace and rebuilds policy; private routes and native WAN fallback remain usable. |
| Briefly remove/restore an exit link | The old healthy flag is not reused; the recovery threshold applies. |
| Leave an apply unconfirmed on a test/canary node | The timer restores that node's saved configuration and service state. Confirm this behavior before relying on it during further changes. |
| Keep both exits healthy for an extended observation period | Status exposes RTT/history/failure rate; quality changes obey sample, improvement, hold and dwell requirements. |

Health failover normally needs roughly 15–25 seconds for two exits; boot/container
startup can add delay. Existing TCP sessions may need to reconnect when the public
source IP changes. A brief successful request does not prove MTU correctness: test
a larger download and the private applications actually used through this network.

Seven-day retirement is time-simulated in the isolated tests. Do not change the
production host clock or edit its history database to accelerate this test. Observe
retirement naturally or use a separate disposable installation with shorter timing.

## Diagnostics

Run on the host:

```sh
sudo python3 /usr/local/lib/vmutils/network-failover/failover.py status
sudo journalctl -u vmutils-network-failover --since '-15 minutes'
sudo systemctl list-timers 'vmutils-rollback-*'
sudo docker exec procustodibus-agent wg show
sudo docker exec procustodibus-agent ip -4 rule
sudo docker exec procustodibus-agent ip -4 route show table 200
sudo docker exec procustodibus-agent ip -4 route show table 201
sudo docker exec procustodibus-agent iptables --version
```

`wg show` omits private keys. Do not paste WireGuard config files, migration plans,
agent enrollment files or database dumps into shared logs. Preserve the logs and
observations needed to diagnose a failure before retrying a migration.
