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

# Check postgresql already installed
if [[ "$( sudo docker container inspect -f '{{.State.Status}}' "postgresql" 2>&1 )" != "running" ]]; then
    echo "Please setup postgresql with ../postgresql/install.sh script before run this setup."
    exit 1
fi

#
# Initialize procustodibus database.
# Source: https://docs.docker.com/guides/pre-seeding/#pre-seed-the-postgres-database-using-a-sql-script
#
PROCUSTODIBUS_DB_USER_PASSWORD=$(pwgen -1BC 16 1)
cat initdb/init.sql | sed "s/{{ PROCUSTODIBUS_USER_PASSWORD }}/$PROCUSTODIBUS_DB_USER_PASSWORD/" | sudo docker exec -i postgresql psql -h localhost -U postgres -f-
echo "New postgresql procustodibus_user password is \"$PROCUSTODIBUS_DB_USER_PASSWORD\"."

#
# Generate application-level encryption key 1
#
PROCUSTODIBUS_DB_ALEK_1="value:$( openssl rand -base64 32 )"
echo "New application-level encryption key 1 is \"$PROCUSTODIBUS_DB_ALEK_1\"."

#
# Generate signup key required to create new organization at $APP_URL/signup.
#
PROCUSTODIBUS_SIGNUP_KEY=$( openssl rand -base64 12 )
echo "New signup key is \"$PROCUSTODIBUS_SIGNUP_KEY\"."

# 
# Create folder structure for procustodibus and copy files.
# 
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_COMPOSE_PATH/procustodibus-controller"
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/procustodibus-controller"
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/procustodibus-controller/acme-challenge"
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/procustodibus-controller/letsencrypt"
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/procustodibus-controller/nginx"
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/procustodibus-controller/work"

cat "./api.env" | sed "s/{{ PROCUSTODIBUS_DB_ALEK_1 }}/$PROCUSTODIBUS_DB_ALEK_1/" | sed "s/{{ PROCUSTODIBUS_DB_USER_PASSWORD }}/$PROCUSTODIBUS_DB_USER_PASSWORD/" | sed "s/{{ PROCUSTODIBUS_SIGNUP_KEY }}/$PROCUSTODIBUS_SIGNUP_KEY/" | sudo -u $PROJECT_USER_NAME tee "$DOCKER_COMPOSE_PATH/procustodibus-controller/api.env"
sudo -u $PROJECT_USER_NAME cp "./app.env" "$DOCKER_COMPOSE_PATH/procustodibus-controller/app.env"
sudo -u $PROJECT_USER_NAME cp "./docker-compose.yml" "$DOCKER_COMPOSE_PATH/procustodibus-controller/docker-compose.yml"


TRAEFIK_HOSTNAME=$( sudo grep 'TRAEFIK_HOSTNAME' "$DOCKER_ROOT_PATH/.env" | cut -d= -f2 | sed -e 's:#.*$::g' -e 's/^"//' -e 's/"$//' )
sudo -u $PROJECT_USER_NAME cat ./nginx/procustodibus.conf | sed "s/{{ PROCUSTODIBUS_HOST }}/$TRAEFIK_HOSTNAME/" | sudo -u $PROJECT_USER_NAME tee "$DOCKER_APPDATA_PATH/procustodibus-controller/nginx/procustodibus.conf"

#
# Add procustodibus-controller to main docker-compose.yml
#
sudo -u $PROJECT_USER_NAME sed -i "/^\s*- compose\/procustodibus-controller\/docker-compose.yml/d" $DOCKER_COMPOSE_MASTER_FILE
echo "  - compose/procustodibus-controller/docker-compose.yml" | sudo -u $PROJECT_USER_NAME tee -a $DOCKER_COMPOSE_MASTER_FILE

# Reload vmutils docker compose project file to apply changes.
sudo docker compose -f $DOCKER_COMPOSE_MASTER_FILE -p vmutils up -d --remove-orphans

echo ""
echo "Open https://$TRAEFIK_HOSTNAME/signup in browser to access Pro Custodibus Comunity Edition and create new organization."
echo "Use your signup key \"$PROCUSTODIBUS_SIGNUP_KEY\" to finish setup."
