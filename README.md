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

## 5. Pro Custodibus agent

Before setup agent get files procustodibus.conf and procustodibus-setup.conf from controller. Follow instructions on https://docs.procustodibus.com/guide/hosts/setup/. After that run setup.

```
cd ../procustodibus-agent && bash install.sh
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