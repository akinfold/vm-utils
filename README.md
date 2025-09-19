# vm-utils

## 0. Basic setup
Open terminal of your new VM under root with Ubuntu 24.04 and run:
```
/bin/bash -c "$(curl -fsSL https://github.com/akinfold/vm-utils/raw/refs/heads/main/get-vm-utils.sh)" && cd vm-utils && bash initial-setup.sh
```
then write down new ssh port and exit.


## 1. Docker
```
cd vm-utils/docker && bash install.sh
```

## 2. Traefik 3
```
cd vm-utils/traefik3 && bash install.sh
```

## 3. WG Easy
```
cd vm-utils/wg-easy && bash install.sh
```