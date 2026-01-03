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

### 4.1 Pro Custodibus Hub and Spoke Topology with Spoke as Internet Gateway

This topology also known as Star Topology. All Spoke hosts connect to central Hub host.
Hub is a host with public IP which all Spokes uses to connect to. We configure our Spokes to send all their traffik through Hub, and configure Hub itself to send all its traffik through Internet Gateway Spoke.

#### Guiding principle

The guiding principle and most important thing to remember when configuring the routing for WireGuard interfaces (in this article, and everywhere) is this:

> Use the ```AllowedIPs``` setting of each peer to specify the traffic you want to **send to** or **send through** the peer. 

Specifically, ```AllowedIPs``` should be the list of IP addresses and IP address ranges that are used as the destination address for all packets that should be routed to (or through) the peer.

This means that the AllowedIPs setting is usually not symmetric between two peers: For example, if you want Host A to send all its outgoing Internet traffic through Host B, you would set AllowedIPs = 0.0.0.0/0, ::/0 in Host A’s peer configuration for Host B. But on Host B, if you want to send Host A only traffic returning from the Internet that has Host A as its destination, you would set AllowedIPs = 10.0.0.1/32, fd10:0:0:1::/64 in Host B’s peer configuration.


#### Hub configuration

DNS Servers
Use https://quad9.net/
```
9.9.9.9, 149.112.112.112
```

Pre Up Script
```
iptables -t nat -A POSTROUTING -s 10.0.0.0/24 -o eth0 -j MASQUERADE
iptables -A INPUT -p udp -m udp --dport 51820 -j ACCEPT
iptables -A FORWARD -i wg0 -j ACCEPT
iptables -A FORWARD -o wg0 -j ACCEPT
```

Post Down Script
```
iptables -t nat -D POSTROUTING -s 10.0.0.0/24 -o eth0 -j MASQUERADE
iptables -D INPUT -p udp -m udp --dport 51820 -j ACCEPT
iptables -D FORWARD -i wg0 -j ACCEPT
iptables -D FORWARD -o wg0 -j ACCEPT
```

#### Spoke with Internet Gateway configuration

DNS Servers
Use https://quad9.net/
```
9.9.9.9, 149.112.112.112
```

Pre Up Script 
```
iptables -t mangle -A PREROUTING -i wg0 -j MARK --set-mark 0x30
iptables -t nat -A POSTROUTING ! -o wg0 -m mark --mark 0x30 -j MASQUERADE
iptables -A INPUT -p udp -m udp --dport 51820 -j ACCEPT
iptables -A FORWARD -i wg0 -j ACCEPT
iptables -A FORWARD -o wg0 -j ACCEPT
```

Post Down Script
```
iptables -t mangle -D PREROUTING -i wg0 -j MARK --set-mark 0x30
iptables -t nat -D POSTROUTING ! -o wg0 -m mark --mark 0x30 -j MASQUERADE
iptables -D INPUT -p udp -m udp --dport 51820 -j ACCEPT
iptables -D FORWARD -i wg0 -j ACCEPT
iptables -D FORWARD -o wg0 -j ACCEPT
```


## 5. Pro Custodibus agent

Before setup agent get files procustodibus.conf and procustodibus-setup.conf from controller. Follow instructions on https://docs.procustodibus.com/guide/hosts/setup/. After that run setup.

```
cd ../procustodibus-agent && bash install.sh
```

### 5.1 Update Pro Custodibus agent to latest version

```
cd /etc/vmutils/docker && sudo docker pull procustodibus/agent && sudo docker compose -f docker-compose.yml -p vmutils up -d --remove-orphans
```

## 6. WG Easy
```
cd ../wg-easy && bash install.sh
```

# Common operatoins

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