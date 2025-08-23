#!/bin/bash
# Source https://github.com/NOXCIS/Wiregate

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

# Folder we will use to store all wiregate related data
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/wiregate" 
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/wiregate/iptable-rules"
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/wiregate/db"
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/wiregate/tor"
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/wiregate/master-key"
# Create log files
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_LOGS_PATH/wiregate"

WGD_PORT_RANGE_STARTPORT="$( random_unused_port )"
sudo sed -i "/^WGD_PORT_RANGE_STARTPORT=.*/d" $DOCKER_ENV_FILE
echo "WGD_PORT_RANGE_STARTPORT=\"$WGD_PORT_RANGE_STARTPORT\"" | sudo tee -a $DOCKER_ENV_FILE

WGD_PORT_RANGE="$WGD_PORT_RANGE_STARTPORT-$(( WGD_PORT_RANGE_STARTPORT+3 ))"
sudo sed -i "/^WGD_PORT_RANGE=.*/d" $DOCKER_ENV_FILE
echo "WGD_PORT_RANGE=\"$WGD_PORT_RANGE\"" | sudo tee -a $DOCKER_ENV_FILE

# Add wiregate to main docker-compose.yml
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_COMPOSE_PATH/wiregate"
sudo -u $PROJECT_USER_NAME cp "./docker-compose.yml" "$DOCKER_COMPOSE_PATH/wiregate/docker-compose.yml"
sudo -u $PROJECT_USER_NAME sed -i "/^\s*- compose\/wiregate\/docker-compose.yml/d" $DOCKER_COMPOSE_MASTER_FILE
echo "  - compose/wiregate/docker-compose.yml" | sudo -u $PROJECT_USER_NAME tee -a $DOCKER_COMPOSE_MASTER_FILE

# Reload vmutils docker compose project file to apply changes.
sudo docker compose -f $DOCKER_COMPOSE_MASTER_FILE -p vmutils up -d 
