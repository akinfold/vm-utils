# vm-utils

## 0. Basic setup
Open terminal of your new VM under root with Ubuntu 24.04 and run:
```
/bin/bash -c "$(curl -fsSL https://github.com/akinfold/vm-utils/raw/refs/heads/main/get-vm-utils.sh)" && cd vm-utils && bash initial-setup.sh
```
Copy-paste final SSH configuration to your local ~/.ssh/config and exit.
Login to your VM with login created during initial setup.
Download to your home dir vm-utils with command:
```
/bin/bash -c "$(curl -fsSL https://github.com/akinfold/vm-utils/raw/refs/heads/main/get-vm-utils.sh)"
```
Then follow instructions below to install other systems.


## 1. Docker
```
cd vm-utils/docker && bash install.sh
```

## 2. Traefik 3

```
cd ../traefik3 && bash install.sh
```

By default traefik configured to use Let's encrypt staging environment. This allow you to get things right before issuing trusted certificates and reduce the chance of your running up against rate limits. More info about staging environment: https://letsencrypt.org/docs/staging-environment/
If you choose to continue with staging environment, you can later switch to trusted environment by running traefik3/switch-le-env.sh script.

## 3. PostgreSQL
```
cd ../postgresql && bash install.sh
```

## 4. Pro Custodibus controller

Before setup controller prepare SMTP relay for it. 
You can create SMTP relay on Yandex Cloud Postbox. Follow instructions: https://yandex.cloud/ru/docs/postbox/quickstart
Select configuration with STARTTLS support.

```
cd ../procustodibus-controller && bash install.sh
```

### 4.1 Automatic WireGuard exit failover

The controller installer also installs `vmutils-network-failover.service` on the hub.
The service registers routing-table names, persists health/latency history, and waits
for the local Pro Custodibus agent container to be enrolled. It does not need the
controller API or an API token to make routing decisions.

The topology is country-independent:

```text
clients -> hub wg0 -> wg-exit-a -> exit A -> Internet
                  -> wg-exit-b -> exit B -> Internet
                  -> native WAN when no exit is healthy
```

The agent runs WireGuard inside its container. The root systemd supervisor runs on
Ubuntu and enters that container's **current network namespace** with `nsenter` on
every cycle, pinning the namespace with an open descriptor so PID reuse cannot
redirect routing changes into another namespace. Host-network containers are
rejected. The worker selects host `iptables-nft` or `iptables-legacy` to match the
agent container, including backups and rollback. It uses host-installed tools; no custom
agent image, Docker socket mount, or scripts inside the container are necessary.

The controller's own traffic, SSH, agent management, and WireGuard's outer UDP
transport keep using the native WAN. Policy routing applies to IPv4 traffic
**forwarded into the hub's client interface** (default `wg0`). More-specific routes
in the main table retain internal connectivity. This release does not manage IPv6
routing or IPv6 NAT; do not advertise an IPv6 full tunnel as covered by this policy.

#### Install a new hub

1. Install Docker, Traefik, PostgreSQL and the controller using sections 1–4.
   The controller installer automatically runs `network-failover/install.sh hub`.
2. Create the hub host in Pro Custodibus. Its client-facing interface is `wg0`, for
   example `10.0.0.1/24`, listen port `51820`. Add clients with their actual source
   addresses/networks in `AllowedIPs`. **Do not put exit peers or `0.0.0.0/0` on this
   shared client interface.** Leave DNS empty and remove the old manual NAT hooks.
3. Download the agent enrollment files and install the agent (section 5). The
   installer detects the already-selected hub role automatically.
4. With no exit configured, client Internet traffic uses the hub's native WAN.
   The service automatically enables forwarding and configures IPv4 masquerading
   for the source networks permitted by the hub's client peers.

The table registration is idempotent and refuses conflicting IDs or names:

| Default ID | Name | Purpose |
|---|---|---|
| 200 | `vpn_candidates` | Configured exits that have not been retired |
| 201 | `vpn_active` | Selected exit, or the hub's native default route |
| 10001–10128 | numeric only | Independent, marked probe routes, including retired exits |

Names are registered in `/etc/iproute2/rt_tables` on the host where the worker's
`ip` command runs. IDs are used for actual commands. Probe rule priorities match
their table IDs; priorities 19999/20000 select private/main routes and the active
route for traffic arriving on `wg0`. Probe socket marks start at `0x6f000001`.
Routes owned by this component use protocol number 242. Reserve these IDs, priorities,
marks, and the `VMUTILS_*` iptables chains for this component. A conflicting route or
policy rule is reported instead of being overwritten. Configure different table
IDs/ranges **before first activation** if they are already in use.

#### Add an exit without hand-written routing rules

Create a separate point-to-point WireGuard link in Pro Custodibus for each exit.
Use a new peer identity for each hub interface. The following is an example; allocate
non-overlapping transit networks that are unused in your environment.

| Setting | Hub side | Exit side |
|---|---|---|
| Interface | `wg-exit-a` | `wg0` |
| Address | `10.250.1.1/30` | `10.250.1.2/30` |
| Listen Port | `51821` | `51820` |
| Remote peer | exit A's identity | this hub link's identity |
| Allowed IPs | `0.0.0.0/0` | `10.0.0.0/24, 10.250.1.1/32` |
| Endpoint hostname/port | may be empty when the exit initiates | hub public hostname, `51821` |
| Persistent Keepalive | optional | `25` |
| Routing Table | `off` | `off` |
| DNS / manual firewall scripts | empty | empty |

The exit's Allowed IPs must include **all actual forwarded client source networks**
and the hub's transit address used by probes. They serve both as destination routing
selectors and as incoming source-address authorization. A single hub `/32` does not
permit un-NATed traffic from its clients. Never set a default prefix on the exit's
peer back to the hub: this would loop its Internet traffic into the tunnel.

Install the agent on the exit using:

```sh
cd ~/vm-utils/procustodibus-agent
bash install.sh exit
```

The exit role automatically derives IPv4 reverse routes and NAT from the WireGuard
peer Allowed IPs, enables forwarding, allows ICMP probes inside the tunnel, and opens
the interface's actual UDP listen port in the container. No `Pre Up`/`Post Down`
iptables blocks need to be pasted into Pro Custodibus. Public/cloud firewalls and
Docker UDP publication must still permit the chosen ports; the agent Compose file
publishes 51820–51829. Do not run wg-easy on the same published ports.

On the hub, a new interface is recognized automatically when:

- its name starts with `wg-exit-` (WireGuard names must be at most 15 characters);
- it has exactly one peer allowing `0.0.0.0/0`;
- it has exactly one usable IPv4 `/30` address, identifying the other host as the
  transit gateway (no DNS lookup or hard-coded exit inventory is required).

The service registers its candidate route and creates isolated probe routing. An
interface must pass health checks before carrying client traffic. Repeat with
`wg-exit-b`, hub port `51822`, and another `/30` for the next exit. You do not edit
or restart the watchdog when adding exits. The candidate route metric is a stable
registry slot, **not the selection priority**: measured quality chooses the exit.
The worker owns the candidate table; do not add competing manual routes there.

On exit nodes with more than one managed WireGuard interface, list them in
`exit_interfaces` in the installed configuration. Their client networks must not
have conflicting reverse routes. The default exit interface is `wg0`.

#### Health, retirement, and native WAN fallback

Every five seconds, independently for each exit, the worker performs:

- a ping to the exit's private transit address for RTT;
- certificate-verified TLS handshakes to external numeric IP targets, with SNI.
  Probe sockets are bound to that interface and use its dedicated routing table.
  The default targets are Cloudflare DNS and Google DNS; one must succeed. Targets,
  quorum, and timeouts are configurable. Runtime probing requires no DNS resolution.

A WireGuard handshake or a ping alone is not an Internet health check. An exit can
stay up while its upstream forwarding/NAT is broken. Conversely, blocked ICMP
prevents latency ranking but does not disqualify an otherwise working TLS path.
These checks establish connectivity to the configured services, not universal
reachability or throughput of every Internet destination.

Three consecutive failed health cycles make an exit ineligible; three successful
cycles make it eligible again. With the defaults, detection normally takes about
15–25 seconds, depending on probe duration and scheduling. On failure of the last
exit, or when none exists, the active table selects the hub's native WAN and the
managed NAT rules allow new client connections to continue from the hub's public IP.
This is intentional **fail-open behavior**. Recovery to a working exit is automatic.

After seven continuously unsuccessful days, a candidate is removed from
`vpn_candidates`. Its identity, history, and probe path are retained. Retired exits
are checked every five minutes; after the first success, fast checks resume, and
three successes restore the candidate automatically. Linux removes device routes
when an interface is deleted, but the registry retains the record for rediscovery.
An interface that disappears or goes down must pass fresh recovery checks when it
returns, even with the same identity. A changed WireGuard identity or transit
address resets the old node's health/history.
Retirement does not delete Pro Custodibus hosts, peers, keys, or interfaces.

The native WAN can only provide connectivity when the hub and its own upstream
work. This does not make the single hub redundant. Changing exits changes the public
source IP: existing sessions may break, including connections with conntrack/NAT
state from the previous path. The continuity guarantee concerns **new connections**,
not preservation of existing TCP sessions. Direct hub egress may also have different
content-access restrictions from an exit in another country.

#### Latency selection: recent weighted quality over seven days

A plain seven-day median is robust against isolated spikes, but a route that gets
much worse today can remain the winner for days. It also hides tail latency and
ignores failed probes. The default `weighted` mode uses the following policy:

1. Consider only currently healthy, present, non-retired exits.
2. Prefer paths whose recent sampled health-failure rate (five minutes) is at most
   20%. If all available paths exceed it, retain the ability to use a working exit.
3. Rank by `weighted_p50 + 0.25 * (weighted_p95 - weighted_p50)` of successful
   **transit RTT** samples. Keep seven days of data and weight each sample by
   `2 ** (-age / 6 hours)`. This favors current latency while penalizing jitter.
4. A performance-driven challenger needs 20 RTT samples, an improvement of at least
   both 20% and 5 ms, sustained for five minutes, and a minimum 15-minute dwell on the
   current exit. A change from excessive recent failures to an acceptable failure
   rate also qualifies after the same sample/hold/dwell requirements.
5. Health failover and recovery from native WAN bypass performance hold/dwell timers.
   A new exit does not need a week of observations before it can provide connectivity.

Samples are persisted every 30 seconds in SQLite, independently of five-second
health checks. The recent failure rate is the fraction of these sampled **health
checks** that failed, not a packet-loss measurement of arbitrary client traffic.
Unweighted seven-day median, score, sample count, and recent failures are visible in
status. Set `score_mode` to `median7d` for literal seven-day median ranking; health
gating and switch hysteresis still apply. This is a routing policy, not a universal
measure of exit quality: throughput and destination-specific latency are not ranked.

#### Operations and upgrades

Configuration: `/etc/vmutils/network-failover/config.json`.
Persistent history: `/var/lib/vmutils-network-failover/history.sqlite3`.
Install/upgrade preserves existing tuning and refuses an implicit role change.

```sh
# Upgrade/add automation on an existing hub without rerunning controller setup.
cd ~/vm-utils/network-failover
bash install.sh hub

# On an existing exit instead:
bash install.sh exit

# Inspect state and logs.
sudo python3 /usr/local/lib/vmutils/network-failover/failover.py status
sudo journalctl -u vmutils-network-failover -f

# Reload after editing configuration.
sudo systemctl restart vmutils-network-failover

# Inspect the current agent namespace using host tools.
sudo nsenter --target "$(sudo docker inspect -f '{{.State.Pid}}' procustodibus-agent)" --net -- ip route show table vpn_candidates
sudo nsenter --target "$(sudo docker inspect -f '{{.State.Pid}}' procustodibus-agent)" --net -- ip route show table vpn_active
```

The supervisor rereads configuration each cycle. Invalid configuration or an
in-place routing-layout change triggers fallback through the previously owned WAN
policy. Change table IDs, container/role, or interface layout through an explicit
migration; restarting the supervisor does not bypass this check. After an observation gap it
requires fresh successes before trusting a stored healthy state. Stopping the
service leaves its last routes/firewall rules installed; it is not a rollback.
If abandoning the feature, first restore a reviewed routing configuration and then
remove only its owned rules, routes and chains. Back up the configuration and SQLite
history before upgrades. Avoid changing table IDs while old rules remain installed.

#### Upgrade an existing controller and deployed exits

Use the dedicated upgrade mode. **Do not rerun the initial controller installer**
without `--upgrade`: initial setup initializes database users and encryption secrets.
The upgrade mode leaves the controller, database, enrollment, client keys, and exit
private keys in place. It creates separate hub-side keys for the new exit links.

Prerequisites: the existing vmutils agent container is running, its WireGuard
configuration is a writable bind mount, and agent version is 1.10.2 through 1.x.
Use a maintenance window, keep an SSH connection through the public host address,
and avoid concurrent Pro Custodibus edits. Apply or cancel pending changes in its
queue first. The upgrade checks this automatically and refuses a nonempty queue.
The hub's existing `wg0` is restarted briefly during cutover; established client
connections can break. This is an upgrade with rollback, not a zero-downtime migration.

On the **hub**, copy `network-failover/topology.example.json` to a protected local
file such as `/root/topology.json`. Fill in:

- `hub_endpoint`: the public hub hostname or IPv4 address;
- each exit's short `name` (for example `fr` and `de`, used only as identifiers);
- its **existing public key**, as shown in Pro Custodibus or `wg show`;
- a distinct, unused transit `/30` and hub UDP port per exit.

No private keys belong in this topology file. The public keys explicitly identify
which peers to remove from the old shared interface. Include every default-route
exit, even if one is currently disabled. Client peers and their Allowed IPs are
preserved. Exit return routes cover the hub's attached VPN subnet and existing
routed client ranges, so adding a client in the same subnet needs no exit edit.
Adding an entirely new routed source range still requires updating the upstream
peer's Allowed IPs on exits; WireGuard source authorization cannot be inferred remotely. With the repository's default Docker publication use hub ports
`51821`–`51829`; publishing additional ports and opening a provider firewall, if
needed, remains an explicit infrastructure change.

```sh
cd ~/vm-utils/network-failover
# Prepare files only: does not change live routing or install software.
bash upgrade.sh hub --topology /root/topology.json --output /root/hub-upgrade

# Review the protected plan locally; it contains private keys. Do not post it.
# Apply on this hub, with a five-minute automatic rollback window.
bash upgrade.sh apply /root/hub-upgrade/plan.json --rollback-after 300
```

The same preparation entry point is available as
`procustodibus-controller/install.sh --upgrade hub ...`.

The upgrade validates configuration drift, port publication, transit overlap and
routing ownership; installs/updates the watchdog and registers its tables; removes
recognized legacy firewall hooks and shared-interface default peers; and brings up
all the new hub interfaces with `Table = off`. The client interface keeps ordinary
specific routes. Until an exit has been upgraded and passes health checks, clients
use the hub's native WAN automatically.

Before stopping either live service, it saves protected backups and enables a
persistent systemd rollback timer. It then stops only the agent daemon and watchdog. A failed apply
attempt immediately restores the saved configuration. A lost SSH session does not
cancel the timer. Verify Internet access **from an actual VPN client** and the
controller UI, then run the exact `confirm` command printed by the upgrade, before
the deadline. If verification fails, wait for rollback or run the printed backup
script with `rollback` instead of `confirm`:

```sh
sudo python3 /var/lib/vmutils-network-migrations/UUID/upgrade.py rollback /var/lib/vmutils-network-migrations/UUID
```

`UUID` is the migration ID printed on that host. Keep the backup directory until
verification is complete. Backups include the affected tunnel files, installed
watchdog files/configuration/state, table registry, owned routes/rules, sysctl values
and namespace firewall snapshot. SQLite history is backed up transactionally,
including committed WAL records. An interrupted rollback can be retried.
Confirmation verifies the applied files and running watchdog before disarming
rollback. Confirmation after the deadline performs rollback instead. Do not treat
an old confirmed backup as safe to replay after subsequent network changes. The timer and its recovery service survive a host reboot. An overdue timer runs
after startup, and recovery retries if Docker is temporarily unavailable. A reboot
can still interrupt connections; it is not part of a normal migration.
If the controller is unreachable or gains queued changes during rollback, local
routing is restored but the agent daemon remains stopped; resolve its connectivity
or queue and start it with `docker exec procustodibus-agent rc-service procustodibus-agent start`.

**Confirm the hub first. Then upgrade exits one at a time.** The hub plan directory
contains one `NAME.exit.json` per exit. These bundles contain public connection
settings only. Copy each bundle to the corresponding exit over your normal SSH
administration connection. On that exit:

```sh
cd ~/vm-utils/network-failover
bash upgrade.sh exit --bundle /root/fr.exit.json --output /root/exit-upgrade
bash upgrade.sh apply /root/exit-upgrade/plan.json --rollback-after 300
# Check the exit and VPN client connectivity, then run the printed confirm command.
```

The preparation command is also available through
`procustodibus-agent/install.sh --upgrade exit ...`. The local exit private key is
checked against the bundle's public identity. The upgrade replaces the old tunnel
address with the allocated transit address, sets the new hub peer and keepalive,
and enables automatic reverse routing, forwarding and NAT. Its old private VPN
address therefore changes; update references to that address if you used it for
administration. No routing/iptables scripts need to be pasted into the exit's UI.
The upgrade also refuses transit/native-route overlap, mismatched UDP publication,
client identities mistakenly listed as exits, mismatched pre-shared keys, IPv6
interfaces, and custom routing tables. Custom hooks, DNS changes, SaveConfig,
multiple upstream peers and unsupported
settings require review; the upgrade refuses to guess how to rewrite them.

After each apply, the existing agent reports its local files to Pro Custodibus
without executing queued changes. This uses the installed agent's own reporting
code and existing enrollment; there is no database rewrite or new API token.
The normal agent daemon then resumes. This compatibility path is version-gated and
must be rechecked before adopting agent 2.x. The API report is needed only for
migration coordination; runtime failover continues without the controller.

Plans are tied to the host, bind mount and original configuration hashes. Reapplying
a plan whose files already match is a no-op. After changing a topology, prepare a
new plan directory. To update **only the watchdog software** on an already migrated
node, use `bash install.sh hub` or `bash install.sh exit`; this preserves its tuning
and history and does not reenroll the node or change WireGuard interfaces.

#### Tests

See [review findings](network-failover/REVIEW.md) and the
[live-server test plan](network-failover/LIVE-TEST.md) before the first rollout.


```sh
python3 -m unittest discover -s network-failover/tests -v
shellcheck network-failover/install.sh network-failover/upgrade.sh
bash -n procustodibus-controller/install.sh procustodibus-agent/install.sh

# Real Linux WireGuard/TLS/NAT integration, fully isolated from external networks.
docker build -t vmutils-failover-test:local -f network-failover/tests/Dockerfile .
docker run --rm --network none --cap-add NET_ADMIN --cap-add NET_RAW \
  --cap-add SYS_ADMIN --security-opt apparmor=unconfined \
  -v "$PWD:/work:ro" vmutils-failover-test:local \
  python3 network-failover/tests/integration.py

# Migration and rollback against real Linux WireGuard/routing.
docker run --rm --network none --cap-add NET_ADMIN --cap-add NET_RAW \
  --cap-add SYS_ADMIN --security-opt apparmor=unconfined \
  -v "$PWD:/work:ro" vmutils-failover-test:local \
  python3 network-failover/tests/migration_integration.py

# Namespace pinning after the inspected process exits.
docker run --rm --network none --cap-add NET_ADMIN --cap-add SYS_ADMIN \
  --security-opt apparmor=unconfined -v "$PWD:/work:ro" vmutils-failover-test:local \
  python3 network-failover/tests/namespace_integration.py

# Run the real supervisor across namespace replacement and invalid-config restart.
docker run --rm --network none --cap-add NET_ADMIN --cap-add NET_RAW \
  --cap-add SYS_ADMIN --security-opt apparmor=unconfined -v "$PWD:/work:ro" \
  vmutils-failover-test:local python3 network-failover/tests/supervisor_integration.py

# Real systemd timers in a disposable container, with no network or Docker socket.
docker build -t vmutils-failover-systemd-test:local -f network-failover/tests/Dockerfile.systemd .
docker run -d --name vmutils-review-systemd --network none --privileged \
  --cgroupns=private --tmpfs /run --tmpfs /run/lock --tmpfs /tmp \
  -v "$PWD:/work:ro" vmutils-failover-systemd-test:local
docker exec vmutils-review-systemd python3 /work/network-failover/tests/systemd_integration.py
docker rm -f vmutils-review-systemd
```

`SYS_ADMIN` is used only by the disposable integration container to create its own
network namespaces; it is not added to the deployed agent container. The test uses
real WireGuard interfaces, private local TLS endpoints and fresh client connections;
it simulates the seven-day clock for retirement. The migration test replaces only
Docker, systemd and controller-report adapters; WireGuard, routes, NAT, file backups
and restoration are real. A live controller/systemd deployment still requires the
maintenance-window validation above. The separate systemd test exercises actual
calendar deadlines, overdue activation, and cancellation; its callback writes a
fixture marker. Its `prepare-restart` / `verify-restart` phases also verify a complete
container/systemd restart with the deadline missed while stopped. Repeat the main
integration test with `-e VMUTILS_IPTABLES_BACKEND=legacy` to exercise legacy NAT.
Tests do not contact production VPSs.

References: [Pro Custodibus containers](https://docs.procustodibus.com/guide/agents/container/),
[interface routing options](https://docs.procustodibus.com/guide/interfaces/add/),
[Linux policy routing](https://man7.org/linux/man-pages/man8/ip-rule.8.html),
[agent configuration reporting since 1.10](https://docs.procustodibus.com/guide/agents/download/),
[agent source](https://git.sr.ht/~arx10/procustodibus-agent).

## 5. Pro Custodibus agent

Before setup agent get files procustodibus.conf and procustodibus-setup.conf from controller. Follow instructions on https://docs.procustodibus.com/guide/hosts/setup/. After that run setup.

```
cd ~/vm-utils/procustodibus-agent && bash install.sh
```

On a hub, the installer detects the role recorded by controller installation. On an
exit, use `bash install.sh exit`; for an ordinary client, use `bash install.sh client`
(no routing service is installed). Without an existing role or argument, the installer
asks you to choose `exit` or `client`.

### 5.1 Update Pro Custodibus agent to latest version

```
cd /etc/vmutils/docker && sudo docker pull procustodibus/agent && sudo docker compose -f docker-compose.yml -p vmutils up -d --remove-orphans
```

## 6. WG Easy

This is an alternative WireGuard manager. Do not install it on the same ports as
the Pro Custodibus agent; it is not required by the failover topology above.

```
cd ../wg-easy && bash install.sh
```

# Common operations

Restart vmutils
```
cd /etc/vmutils/docker && sudo docker compose -f docker-compose.yml -p vmutils up -d --remove-orphans
```

Read logs
```
cd /etc/vmutils/docker && sudo docker compose logs --follow
```

Show containers
```
cd /etc/vmutils/docker && sudo docker compose ps
```

Show volumes
```
cd /etc/vmutils/docker && sudo docker volume ls
```

Remove volume
```
cd /etc/vmutils/docker && sudo docker volume rm <volume id>
```

Show services
```
cd /etc/vmutils/docker && sudo docker compose ps
```

Restart service containers
```
cd /etc/vmutils/docker && sudo docker compose restart <service name>
```

Get service 
```
cd /etc/vmutils/docker && sudo docker exec -it <container name> sh
```