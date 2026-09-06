# Failover implementation review

Review baseline: `6ef42aa`, before the first live-server rollout. The findings below
were corrected and covered by regression tests. Production VPSs were not modified.

| Finding | Consequence | Correction and evidence |
|---|---|---|
| Services stopped before rollback was armed; only a transient timer existed | A disconnected upgrade or reboot could leave automation stopped without recovery | Backups and persistent units are installed before service stops. Pre-cutover failures resume original services without rewriting tunnels. Real systemd tests cover deadlines, cancellation, and a complete restart after the deadline. |
| Late confirmation disabled recovery before validating the deadline | A rejected confirmation could leave an unconfirmed migration installed indefinitely | Expired confirmation rolls back; readiness checks happen before disarming recovery. Completion markers make retry safe. |
| Network operations used an inspected PID directly | PID reuse could enter the wrong namespace; a restart could mix commands between containers | Namespace descriptors remain pinned for each operation; identity changes abort migration; host-network mode is rejected. Tested after the inspected process exits. |
| Host and agent could use different iptables backends | New ACCEPT/NAT rules might not govern the agent's existing firewall path | Runtime, migration and rollback select the agent's nft/legacy backend. Real WireGuard/TLS/NAT tests pass with both backends. |
| An interface retained healthy state across a quick disappearance/return | Traffic could enter a recovered but unverified exit before the configured success threshold | Loss of the interface invalidates health; return forces fresh probes and consecutive successes. |
| Exit bundles enumerated only existing client addresses | A subsequently added client in the same VPN subnet lacked exit authorization/return routing | Bundles include the attached client subnet plus routed client ranges. New ranges still need explicit WireGuard authorization. |
| Routing-layout edits and malformed configuration could strand existing policy | Wrong table IDs or stopped checks could retain a dead selected exit | Validate types, finite numbers and priority order; persist layout ownership; use the prior owned WAN policy on reload failure. Real client requests verify fallback. |
| Backups copied live SQLite files and omitted installed owned routes/rules | WAL transactions could be missed; rollback of a stopped watchdog could lose its still-active policy | Use SQLite's backup API, capture owned policy and sysctl state, and restore it independently of service active state. Tests include committed WAL data and a stopped prior policy. |
| wg-quick automatically reallocated rule priorities on restoration | Restored tunnel files could route traffic in a different order than before cutover | Capture numeric policy rules and replay their original priorities after bringing old interfaces up. Kernel regression compares the complete original/restored ruleset. |
| Rollback could delete unrelated rules sharing a reserved priority | Concurrent or preexisting foreign policy could be removed | Preflight rejects collisions; cleanup compares complete selectors and removes only matching owned rules. Kernel tests preserve a foreign rule at the same priority. |
| Migration accepted ambiguous or conflicting transport settings | A wrong peer identity, PSK, remapped UDP port or overlapping subnet could cause an avoidable outage | Reject client identities used as exits, PSK mismatches, mismatched published ports, native-route overlaps, ephemeral hub ports and unsupported IPv6/custom-table migrations before cutover. |

## Validation boundaries

The runtime integration suite uses real WireGuard, private TLS endpoints, iptables,
policy routing and fresh client connections. Failure cases include broken Internet
forwarding with a live tunnel, complete exit failure, retirement/recovery, interface
loss, invalid configuration and table-layout drift.

The migration suite uses real tunnel files, WireGuard, kernel routes, firewall,
backups and restoration. Docker/systemd/controller calls in that suite are adapters.
A separate isolated systemd container tests the actual timer mechanism, including
boot recovery. The namespace suite tests the Linux descriptor mechanism directly. The supervisor
suite runs the real supervisor and workers through namespace replacement and a
restart with invalid configuration; only Docker discovery is a fixture.

These tests do not establish compatibility with a specific live Pro Custodibus
controller database, provider firewall, public MTU, DNS path, or Docker installation.
Agent reporting still uses the installed agent's internal 1.x API. Validate that
coordination in the maintenance window before confirming the first upgrade.

A single hub remains a single point of failure. Exit changes do not preserve
existing NAT/TCP sessions. IPv6 failover, automatic propagation of entirely new
client source ranges, throughput ranking, and geographically dependent policy are
outside this implementation. See [LIVE-TEST.md](LIVE-TEST.md) for staged acceptance.
