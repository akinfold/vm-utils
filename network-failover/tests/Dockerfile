FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends python3 iproute2 iptables iputils-ping wireguard-tools util-linux ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /work
