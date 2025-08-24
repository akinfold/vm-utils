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

# Folder we will use to store all wgdashboard related data
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/wgdashboard" 
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/wgdashboard/wireguard"
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/wgdashboard/scripts"
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/wgdashboard/app_conf"
# Create log files
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_LOGS_PATH/wgdashboard"

WG_PORT=$( random_unused_port )
WG_PORT_RANGE="$WG_PORT-$(( WG_PORT+10 ))"
sudo sed -i "/^WG_PORT_RANGE=.*/d" $DOCKER_ENV_FILE
echo "WG_PORT_RANGE=\"$WG_PORT_RANGE\"" | sudo tee -a $DOCKER_ENV_FILE
echo ""
echo "WireGuard port mapping is set to $WG_PORT_RANGE:51820-51830/udp please save this information."
echo ""

# Add wgdashboard to main docker-compose.yml
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_COMPOSE_PATH/wgdashboard"
sudo -u $PROJECT_USER_NAME cp "./docker-compose.yml" "$DOCKER_COMPOSE_PATH/wgdashboard/docker-compose.yml"
sudo -u $PROJECT_USER_NAME sed -i "/^\s*- compose\/wgdashboard\/docker-compose.yml/d" $DOCKER_COMPOSE_MASTER_FILE
echo "  - compose/wgdashboard/docker-compose.yml" | sudo -u $PROJECT_USER_NAME tee -a $DOCKER_COMPOSE_MASTER_FILE

# Reload vmutils docker compose project file to apply changes.
sudo docker compose -f $DOCKER_COMPOSE_MASTER_FILE -p vmutils up -d 

TRAEFIK_HOSTNAME=$( sudo grep 'TRAEFIK_HOSTNAME' /etc/vmutils/docker/.env | cut -d= -f2 | sed -e 's:#.*$::g' -e 's/^"//' -e 's/"$//' )
echo ""
echo "Open https://$TRAEFIK_HOSTNAME/wg in browser to access Wgdashboard." 
echo "Default wgdashboard login and password is \"admin:admin\". Use them to configure wgdeshboard and change admin credentials."
echo "Wgdashboard protected by basic authentication provided by Traefik so you will need basic auth credentials set earlier in initial_setup.sh."