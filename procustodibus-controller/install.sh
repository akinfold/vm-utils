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
cat initdb/init2.sql | sed "s/{{ PROCUSTODIBUS_USER_PASSWORD }}/$PROCUSTODIBUS_DB_USER_PASSWORD/" |  docker exec -i postgres psql -h localhost -U postgres -f-
PROCUSTODIBUS_DB_USER_PASSWORD_FILE="$SECRETS_PATH/PROCUSTODIBUS_DB_USER_PASSWORD"
# Save password to secrets.
echo "Write postgres new password \"$PROCUSTODIBUS_DB_USER_PASSWORD\" to $PROCUSTODIBUS_DB_USER_PASSWORD_FILE."
echo "$PROCUSTODIBUS_DB_USER_PASSWORD" | sudo tee $PROCUSTODIBUS_DB_USER_PASSWORD_FILE
sudo chown root:root $PROCUSTODIBUS_DB_USER_PASSWORD_FILE
# Write down path to password file to main docker environment. So we can use it in docker-compose.yml
sudo sed -i "/^PROCUSTODIBUS_DB_USER_PASSWORD_FILE=.*/d" $DOCKER_ENV_FILE
echo "PROCUSTODIBUS_DB_USER_PASSWORD_FILE=\"$PROCUSTODIBUS_DB_USER_PASSWORD_FILE\"" | sudo tee -a $DOCKER_ENV_FILE

#
# Generate application-level encryption key 1
#
PROCUSTODIBUS_DB_ALEK_1="value:$( openssl rand -base64 32 )"
PROCUSTODIBUS_DB_ALEK_1_FILE="$SECRETS_PATH/PROCUSTODIBUS_DB_ALEK_1"
# Save password to secrets.
echo "Write new application-level encryption key 1 \"$PROCUSTODIBUS_DB_ALEK_1\" to $PROCUSTODIBUS_DB_ALEK_1_FILE."
echo "$PROCUSTODIBUS_DB_ALEK_1" | sudo tee $PROCUSTODIBUS_DB_ALEK_1_FILE
sudo chown root:root $PROCUSTODIBUS_DB_ALEK_1_FILE
# Write down path to password file to main docker environment. So we can use it in docker-compose.yml
sudo sed -i "/^PROCUSTODIBUS_DB_ALEK_1_FILE=.*/d" $DOCKER_ENV_FILE
echo "PROCUSTODIBUS_DB_ALEK_1_FILE=\"$PROCUSTODIBUS_DB_ALEK_1_FILE\"" | sudo tee -a $DOCKER_ENV_FILE

#
# Generate signup key required to create new organization at $APP_URL/signup.
#
PROCUSTODIBUS_SIGNUP_KEY=$( openssl rand -base64 12 )
PROCUSTODIBUS_SIGNUP_KEY_FILE="$SECRETS_PATH/PROCUSTODIBUS_SIGNUP_KEY"
# Save password to secrets.
echo "Write new signup key \"$PROCUSTODIBUS_SIGNUP_KEY\" to $PROCUSTODIBUS_SIGNUP_KEY_FILE."
echo "$PROCUSTODIBUS_SIGNUP_KEY" | sudo tee $PROCUSTODIBUS_SIGNUP_KEY_FILE
sudo chown root:root $PROCUSTODIBUS_SIGNUP_KEY_FILE
# Write down path to password file to main docker environment. So we can use it in docker-compose.yml
sudo sed -i "/^PROCUSTODIBUS_SIGNUP_KEY_FILE=.*/d" $DOCKER_ENV_FILE
echo "PROCUSTODIBUS_SIGNUP_KEY_FILE=\"$PROCUSTODIBUS_SIGNUP_KEY_FILE\"" | sudo tee -a $DOCKER_ENV_FILE

# 
# Create folder structure for procustodibus and copy files.
# 
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_COMPOSE_PATH/procustodibus-controller"
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/procustodibus-controller"
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/procustodibus-controller/acme-challenge"
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/procustodibus-controller/letsencrypt"
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/procustodibus-controller/nginx"
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/procustodibus-controller/work"

sudo -u $PROJECT_USER_NAME cp "./api.env" "$DOCKER_COMPOSE_PATH/procustodibus-controller/api.env"
sudo -u $PROJECT_USER_NAME cp "./app.env" "$DOCKER_COMPOSE_PATH/procustodibus-controller/app.env"

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
echo "Open https://$TRAEFIK_HOSTNAME in browser to access Pro Custodibus Comunity Edition."
