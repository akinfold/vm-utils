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

# Folder we will use to store all Traefik-related configurations
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/traefik3" 

sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/traefik3/acme"
# The acme.json file will store all the SSL certificates that are generated.
sudo -u $PROJECT_USER_NAME touch "$DOCKER_APPDATA_PATH/traefik3/acme/acme.json"
# Set proper permission for acme.json. Without 600 permissions on acme.json, Traefik won't start.
sudo -u $PROJECT_USER_NAME chmod 600 "$DOCKER_APPDATA_PATH/traefik3/acme/acme.json"

# Create log files
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_LOGS_PATH/traefik3"
sudo -u $PROJECT_USER_NAME touch "$DOCKER_LOGS_PATH/traefik3/traefik.log"
sudo -u $PROJECT_USER_NAME touch "$DOCKER_LOGS_PATH/traefik3/access.log"


sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/traefik3/rules"
# Create tls optoins configuration file referenced in docker-compose.yml
sudo cp "./rules/tls-opts.yml" "$DOCKER_COMPOSE_PATH/traefik3/rules/tls-opts.yml"
# Create basic authentification middleware file referenced in docker-compose.yml
sudo cp "./rules/middlewares-basic-auth.yml" "$DOCKER_COMPOSE_PATH/traefik3/rules/middlewares-basic-auth.yml"

# Add traefik3 to main docker-compose.yml
sudo mkdir -p "$DOCKER_COMPOSE_PATH/traefik3"
sudo cp "./docker-compose.yml" "$DOCKER_COMPOSE_PATH/traefik3/docker-compose.yml"
sudo echo "  - compose/traefik3/docker-compose.yml" >> $DOCKER_COMPOSE_MASTER_FILE
