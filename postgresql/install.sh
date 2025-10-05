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

POSTGRES_PASSWORD=$(pwgen -1BC 16 1)
POSTGRES_PASSWORD_FILE="$SECRETS_PATH/POSTGRES_PASSWORD"
# Save postgres user password to secrets.
echo "Write postgres new password \"$POSTGRES_PASSWORD\" to $POSTGRES_PASSWORD_FILE."
echo "$POSTGRES_PASSWORD" | sudo tee $POSTGRES_PASSWORD_FILE
sudo chown root:root $POSTGRES_PASSWORD_FILE
# Write down path to postgres password file to main docker environment. So we can use it in docker-compose.yml
sudo -u $PROJECT_USER_NAME sed -i "/^POSTGRES_PASSWORD_FILE=.*/d" $DOCKER_ENV_FILE
echo "POSTGRES_PASSWORD_FILE=\"$POSTGRES_PASSWORD_FILE\"" | sudo -u $PROJECT_USER_NAME tee -a $DOCKER_ENV_FILE

# Copy postgresql docker compose to vmutils compose folder.
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_COMPOSE_PATH/postgresql"
sudo -u $PROJECT_USER_NAME cp "./docker-compose.yml" "$DOCKER_COMPOSE_PATH/postgresql/docker-compose.yml"

# Add postgresql to main docker-compose.yml
sudo -u $PROJECT_USER_NAME cat $DOCKER_COMPOSE_MASTER_FILE | yq 'del(.include[] | select(. == "compose/postgresql/docker-compose.yml")) | .include += compose/postgresql/docker-compose.yml' | sudo -u $PROJECT_USER_NAME sponge $DOCKER_COMPOSE_MASTER_FILE

# Reload vmutils docker compose project file to apply changes.
sudo docker compose -f $DOCKER_COMPOSE_MASTER_FILE -p vmutils up -d --remove-orphans