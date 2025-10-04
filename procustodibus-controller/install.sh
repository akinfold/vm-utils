#!/bin/bash

# Exit immediately if a pipeline returns a non-zero status.
# https://www.gnu.org/savannah-checkouts/gnu/bash/manual/bash.html#The-Set-Builtin
set -e 

. "../env.sh"

# Check docker already installed
if ! type docker > /dev/null 2>&1; then
    echo "Please setup docker with ../docker/install.sh script before run this setup."
    exit 1
fi

# Check traefik 3 already installed
if [[ "$( sudo docker container inspect -f '{{.State.Status}}' "traefik" 2>&1 )" != "running" ]]; then
    echo "Please setup traefik 3 with ../traefik3/install.sh script before run this setup."
    exit 1
fi

sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_COMPOSE_PATH/procustodibus-controller"
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/procustodibus-controller"
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/procustodibus-controller/db"

# Source: https://docs.procustodibus.com/guide/onpremises/install/#docker
# Folder we will use to store all Pro Custodibus related configurations
cd "$DOCKER_APPDATA_PATH/procustodibus-controller"

# Copy env files to vmutils procustodibus-acontroller compose folder.
sudo -u $PROJECT_USER_NAME mv "./api.env" "$DOCKER_COMPOSE_PATH/procustodibus-controller/api.env"
sudo -u $PROJECT_USER_NAME mv "./app.env" "$DOCKER_COMPOSE_PATH/procustodibus-controller/app.env"

sudo -u $PROJECT_USER_NAME curl -L -c /tmp/srht.cookies -b /tmp/srht.cookies https://git.sr.ht/~arx10/procustodibus-api/blob/main/ops/install/generate-docker-compose.sh | sudo -u $PROJECT_USER_NAME bash -s ce

# Build new docker-compose.yml based on pro custodibus generated one.
# 1. Disable port exposure to host because traefik3 will do that.
# 2. Configure traefik3 with labels.
# 3. Connect procustodibus controller container to traefik network.
# 4. Change paths.
# 5. Remove db volume.
# 6. Rename default procustodibus apps names to procustodibus-api, procustodibus-controller and procustodibus-db.
# 7. Save resulting docker-compose.yml to compose folder.
sudo -u $PROJECT_USER_NAME cat docker-compose.yml | yq 'del(.services.app.ports[] | select(. == "*:443")) 
| .services.app.labels=["traefik.enable=true", "traefik.http.routers.procustodibus-controller.rule=Host(`$TRAEFIK_HOSTNAME`)", "traefik.http.routers.procustodibus-controller.entrypoints=websecure", "traefik.http.routers.procustodibus-controller.service=procustodibus-controller", "traefik.http.services.procustodibus-controller.loadbalancer.server.port=80", "traefik.http.routers.procustodibus-controller.middlewares=chain-basic-auth@file"]
| .services.app.networks.traefik += {}
| .services.api.volumes = ["$DOCKER_APPDATA_PATH/procustodibus-controller/config:/etc/procustodibus:Z", "$DOCKER_APPDATA_PATH/procustodibus-controller/work:/work:z"]
| .services.app.volumes = ["$DOCKER_APPDATA_PATH/procustodibus-controller/acme-challenge:/var/www/certbot:Z", "$DOCKER_APPDATA_PATH/procustodibus-controller/letsencrypt:/etc/letsencrypt:Z", "$DOCKER_APPDATA_PATH/procustodibus-controller/nginx:/etc/nginx/conf.d:Z", "$DOCKER_APPDATA_PATH/procustodibus-controller/work:/work:z"]
| .services.db.volumes = ["$DOCKER_APPDATA_PATH/procustodibus-controller/db:/var/lib/postgresql/data", "$DOCKER_APPDATA_PATH/procustodibus-controller/initdb:/docker-entrypoint-initdb.d:Z", "$DOCKER_APPDATA_PATH/procustodibus-controller/work:/work:z"]
| del(.volumes)
| .services.procustodibus-api = .services.api | del(.services.api) 
| .services.procustodibus-controller = .services.app | del(.services.app) 
| .services.procustodibus-db = .services.db | del(.services.db)' | sudo -u $PROJECT_USER_NAME sponge "$DOCKER_COMPOSE_PATH/procustodibus-controller/docker-compose.yml"

# Replace nginx config to disable SSL and change listen port to 80 instead 443.
sudo -u $PROJECT_USER_NAME curl -L -c /tmp/srht.cookies -b /tmp/srht.cookies -o "$DOCKER_APPDATA_PATH/procustodibus-controller/nginx/procustodibus.conf" https://git.sr.ht/~arx10/procustodibus-app/tree/main/item/ops/run/nginx-no-ssl.conf

# Add procustodibus-controller to main docker-compose.yml
sudo -u $PROJECT_USER_NAME sed -i "/^\s*- compose\/procustodibus-controller\/docker-compose.yml/d" $DOCKER_COMPOSE_MASTER_FILE
echo "  - compose/procustodibus-controller/docker-compose.yml" | sudo -u $PROJECT_USER_NAME tee -a $DOCKER_COMPOSE_MASTER_FILE

# Reload vmutils docker compose project file to apply changes.
sudo docker compose -f $DOCKER_COMPOSE_MASTER_FILE -p vmutils up -d --remove-orphans

echo ""
echo "Open https://$TRAEFIK_HOSTNAME in browser to access Pro Custodibus Comunity Edition."
