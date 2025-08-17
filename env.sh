#!/bin/bash

#
# Base project env variables
#

PROJECT_NAME="vmutils"

PROJECT_USER_NAME=$PROJECT_NAME

LOGS_PATH="/var/log/$PROJECT_NAME"

ROOT_PATH="/etc/$PROJECT_NAME"

# To store credentials used by apps securely. Notice that the folder is owned by root and permissions are set to 600
SECRETS_PATH="$ROOT_PATH/secrets"
BASIC_AUTH_CREDENTIALS_FILE="$SECRETS_PATH/basic_auth_credentials.htpasswd"


#
# Docker folder structure paths
# Source https://www.simplehomelab.com/docker-media-server-2024/#Setting_Up_the_Docker_Environment
#

# Root Docker data folder.
DOCKER_ROOT_PATH="$ROOT_PATH/docker"

# To centralize all relevant logs. We use this to store my script logs, traefik logs, etc. 
# Although you can customize your apps (e.g. Nginx Proxy Manager) to store logs in this folder.
DOCKER_LOGS_PATH=$LOGS_PATH

# This folder will store the data for all our apps and services.
DOCKER_APPDATA_PATH="$DOCKER_ROOT_PATH/appdata"

# This folder will have a subfolder for each host, inside which all the individual Docker Compose files will be stored.
DOCKER_COMPOSE_PATH="$DOCKER_ROOT_PATH/compose"

# To store credentials used by apps securely.
DOCKER_SECRETS_PATH=$SECRETS_PATH

# To store shared information. We can save a lot of things in this folder that we share between docker hosts (e.g. SSH config, .bash_aliases, etc.).
DOCKER_SHARED_PATH="$DOCKER_ROOT_PATH/shared"

# This is our template or configuration file for all our services.
# This way we won't have to use real values in docker-compose.yml (for security). 
# And, we can use the variable names in many places. Notice that the file is owned by root and permissions are set to 600.
DOCKER_ENV_FILE="$DOCKER_ROOT_PATH/.env"

# This is our template or configuration file for all our services. We will call this file the Docker Compose Master File.
DOCKER_COMPOSE_MASTER_FILE="$DOCKER_ROOT_PATH/docker-compose.yml"


# Generate random port which is not in use.
function random_unused_port {
    while true; do
        # Generate peseudo random new port number 
        RANDOM_UNUSED_NEW_PORT=$(( $RANDOM + $RANDOM ))

        # Check port number in range [1024, 65535].
        if [[ $RANDOM_UNUSED_NEW_PORT -lt 1024 || $RANDOM_UNUSED_NEW_PORT -gt 65535 ]]; then 
            continue 
        fi

        # Check port unused.
        if $( nc -z 127.0.0.1 $RANDOM_UNUSED_NEW_PORT ); then 
            continue
        fi 

        # If all checks passed return generated port number.
        echo $RANDOM_UNUSED_NEW_PORT
        break
    done
}
