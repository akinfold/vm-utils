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

while true; do
    echo "To configure wg-easy own authentication we need to set new user name and password."
    
    echo -n "User name: "
    read WG_INIT_USERNAME
    if [[ -z $WG_INIT_USERNAME ]]; then
        echo "Wg-easy user name can't be empty. Let's try again."
        continue
    fi

    echo -n "Password (min 10 symbols): "
    read WG_INIT_PASSWORD
    if [[ ${#WG_INIT_PASSWORD} -lt 10 ]]; then
        echo "Wg-easy password must be at least 10 symbols long. Let's try again."
        continue
    fi

    break
done 

# Folder we will use to store all wg-easy related data
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/wg-easy" 
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/wg-easy/wireguard"


WG_PORT=$( random_unused_port )
sudo sed -i "/^WG_PORT=.*/d" $DOCKER_ENV_FILE
echo "WG_PORT=\"$WG_PORT\"" | sudo tee -a $DOCKER_ENV_FILE

# Copy docker-compose.yml to vmutils working directory.
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_COMPOSE_PATH/wg-easy"
sudo -u $PROJECT_USER_NAME cp "./docker-compose.yml" "$DOCKER_COMPOSE_PATH/wg-easy/docker-compose.yml"

# Inject to wg-easy docker-compose.yml authentification credentials for unattended setup.
sudo -u $PROJECT_USER_NAME sed -i "s/{{ INIT_USERNAME }}/$WG_INIT_USERNAME/" "$DOCKER_COMPOSE_PATH/wg-easy/docker-compose.yml"
sudo -u $PROJECT_USER_NAME sed -i "s/{{ INIT_PASSWORD }}/$WG_INIT_PASSWORD/" "$DOCKER_COMPOSE_PATH/wg-easy/docker-compose.yml"

# Add wg-easy to main docker-compose.yml
sudo -u $PROJECT_USER_NAME sed -i "/^\s*- compose\/wg-easy\/docker-compose.yml/d" $DOCKER_COMPOSE_MASTER_FILE
echo "  - compose/wg-easy/docker-compose.yml" | sudo -u $PROJECT_USER_NAME tee -a $DOCKER_COMPOSE_MASTER_FILE

# Reload vmutils docker compose project file to apply changes.
sudo docker compose -f $DOCKER_COMPOSE_MASTER_FILE -p vmutils up -d 

# Remove from wg-easy docker-compose.yml authentication credentials injected there for unattended setup and all other initial env vars.
sudo -u $PROJECT_USER_NAME sed -i "/^\s*\(#\s*\|\s*\)- INIT_\w\+=.\+$/d" "$DOCKER_COMPOSE_PATH/wg-easy/docker-compose.yml"

# Reload vmutils docker compose project file to apply changes.
sudo docker compose -f $DOCKER_COMPOSE_MASTER_FILE -p vmutils up -d --remove-orphans