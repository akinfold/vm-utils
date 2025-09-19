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

## 3. WG Easy
```
cd ../wg-easy && bash install.sh
```