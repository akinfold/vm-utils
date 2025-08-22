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

# Folder we will use to store all wg-easy related data
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/wg-easy" 
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/wg-easy/wireguard"


WG_PORT=$( random_unused_port )
sudo sed -i "/^WG_PORT=.*/d" $DOCKER_ENV_FILE
echo "WG_PORT=\"$WG_PORT\"" | sudo tee -a $DOCKER_ENV_FILE

# Add basic auth credentials to .env file to use them in wg-easy unattended setup.
sudo sed -i "/^BASIC_AUTH_LOGIN=.*/d" $DOCKER_ENV_FILE
sudo cat $BASIC_AUTH_CREDENTIALS_FILE | cut -d: -f1 | xargs -I{} echo "BASIC_AUTH_LOGIN={}" | sudo tee -a $DOCKER_ENV_FILE
sudo sed -i "/^BASIC_AUTH_PASSWORD_HASH=.*/d" $DOCKER_ENV_FILE
sudo cat $BASIC_AUTH_CREDENTIALS_FILE | cut -d: -f2 | xargs -I{} echo "BASIC_AUTH_PASSWORD_HASH={}" | sudo tee -a $DOCKER_ENV_FILE

# Add wg-easy to main docker-compose.yml
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_COMPOSE_PATH/wg-easy"
sudo -u $PROJECT_USER_NAME cp "./docker-compose.yml" "$DOCKER_COMPOSE_PATH/wg-easy/docker-compose.yml"
sudo -u $PROJECT_USER_NAME sed -i "/^\s*- compose\/wg-easy\/docker-compose.yml/d" $DOCKER_COMPOSE_MASTER_FILE
echo "  - compose/wg-easy/docker-compose.yml" | sudo -u $PROJECT_USER_NAME tee -a $DOCKER_COMPOSE_MASTER_FILE

# Reload vmutils docker compose project file to apply changes.
sudo docker compose -f $DOCKER_COMPOSE_MASTER_FILE -p vmutils up -d 
