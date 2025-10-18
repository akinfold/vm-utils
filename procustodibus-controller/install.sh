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

TRAEFIK_HOSTNAME=$( sudo grep 'TRAEFIK_HOSTNAME' "$DOCKER_ROOT_PATH/.env" | cut -d= -f2 | sed -e 's:#.*$::g' -e 's/^"//' -e 's/"$//' )


# 
# Configure SMTP server.
#
echo ""
echo "Configure SMTP relay for Pro Custodibus to be able to receive email notifications."
echo "You can create SMTP relay on Yandex Cloud Postbox. Follow instructions: https://yandex.cloud/ru/docs/postbox/quickstart"
echo "Select configuration with STARTTLS support."
echo ""
PROCUSTODIBUS_MAIL_FROM_ADDRESS="procustodibus@$TRAEFIK_HOSTNAME"
PROCUSTODIBUS_MAIL_RELAY="postbox.cloud.yandex.net"
PROCUSTODIBUS_MAIL_RELAY_PORT="587"
PROCUSTODIBUS_MAIL_RELAY_USERNAME=""
PROCUSTODIBUS_MAIL_RELAY_PASSWORD=""    
PROCUSTODIBUS_MAIL_SETUP=""
while [[ $PROCUSTODIBUS_MAIL_SETUP != "y" ]] && [[ $PROCUSTODIBUS_MAIL_SETUP != "n" ]]; do
    echo ""
    echo -n "Setup SMTP mail relay? [y/n] "
    read PROCUSTODIBUS_MAIL_SETUP

    if [[ $PROCUSTODIBUS_MAIL_SETUP == "y" ]]; then
        echo -n "Email address from which to send emails [default: $PROCUSTODIBUS_MAIL_FROM_ADDRESS]: "
        read USER_INPUT
        if [[ -n $USER_INPUT ]]; then 
            PROCUSTODIBUS_MAIL_FROM_ADDRESS=$USER_INPUT 
        fi

        echo -n "Hostname of mail relay [default: $PROCUSTODIBUS_MAIL_RELAY]: "
        read USER_INPUT
        if [[ -n $USER_INPUT ]]; then 
            PROCUSTODIBUS_MAIL_RELAY=$USER_INPUT 
        fi

        echo -n "Port at which to connect to mail relay [default: $PROCUSTODIBUS_MAIL_RELAY_PORT]: "
        read USER_INPUT
        if [[ -n $USER_INPUT ]]; then 
            PROCUSTODIBUS_MAIL_RELAY=$USER_INPUT 
        fi

        echo -n "Username for authentication with mail relay [default: $PROCUSTODIBUS_MAIL_RELAY_USERNAME]: "
        read USER_INPUT
        if [[ -n $USER_INPUT ]]; then 
            PROCUSTODIBUS_MAIL_RELAY_USERNAME=$USER_INPUT 
        fi

        echo -n "Password for authentication with mail relay [default: $PROCUSTODIBUS_MAIL_RELAY_PASSWORD]: "
        read USER_INPUT
        if [[ -n $USER_INPUT ]]; then 
            PROCUSTODIBUS_MAIL_RELAY_PASSWORD=$USER_INPUT 
        fi
        
    elif [[ $PROCUSTODIBUS_MAIL_SETUP == "n" ]]; then
        echo "Continue without mail relay. Pro Custodibus won't be able to send any emails. Attempts to send email will simply fail with a harmless error message."
    else
        echo "Please select "y" or "n". Let's try again."
    fi
done


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

sudo -u $PROJECT_USER_NAME cp "./app.env" "$DOCKER_COMPOSE_PATH/procustodibus-controller/app.env"
sudo -u $PROJECT_USER_NAME cp "./docker-compose.yml" "$DOCKER_COMPOSE_PATH/procustodibus-controller/docker-compose.yml"
API_ENV="$DOCKER_COMPOSE_PATH/procustodibus-controller/api.env"

sudo -u $PROJECT_USER_NAME cp "./api.env" "$API_ENV"
sudo -u $PROJECT_USER_NAME sed -i "s/{{ PROCUSTODIBUS_DB_ALEK_1 }}/$( echo "$PROCUSTODIBUS_DB_ALEK_1" | sed -e 's/[\/&\]/\\&/g' )/g" "$API_ENV"
sudo -u $PROJECT_USER_NAME sed -i "s/{{ PROCUSTODIBUS_DB_USER_PASSWORD }}/$PROCUSTODIBUS_DB_USER_PASSWORD/g" "$API_ENV"
sudo -u $PROJECT_USER_NAME sed -i "s/{{ PROCUSTODIBUS_SIGNUP_KEY }}/$( echo "$PROCUSTODIBUS_SIGNUP_KEY" | sed -e 's/[\/&\]/\\&/g' )/g" "$API_ENV"
sudo -u $PROJECT_USER_NAME sed -i "s/{{ PROCUSTODIBUS_MAIL_FROM_ADDRESS }}/$PROCUSTODIBUS_MAIL_FROM_ADDRESS/g" "$API_ENV"
sudo -u $PROJECT_USER_NAME sed -i "s/{{ PROCUSTODIBUS_MAIL_RELAY }}/$PROCUSTODIBUS_MAIL_RELAY/g" "$API_ENV"
sudo -u $PROJECT_USER_NAME sed -i "s/{{ PROCUSTODIBUS_MAIL_RELAY_PORT }}/$PROCUSTODIBUS_MAIL_RELAY_PORT/g" "$API_ENV"
sudo -u $PROJECT_USER_NAME sed -i "s/{{ PROCUSTODIBUS_MAIL_RELAY_USERNAME }}/$( echo "$PROCUSTODIBUS_MAIL_RELAY_USERNAME" | sed -e 's/[\/&\]/\\&/g' )/g" "$API_ENV"
sudo -u $PROJECT_USER_NAME sed -i "s/{{ PROCUSTODIBUS_MAIL_RELAY_PASSWORD }}/$( echo "$PROCUSTODIBUS_MAIL_RELAY_PASSWORD" | sed -e 's/[\/&\]/\\&/g' )/g" "$API_ENV"


sudo -u $PROJECT_USER_NAME cat ./nginx/procustodibus.conf | sed "s/{{ PROCUSTODIBUS_HOST }}/$TRAEFIK_HOSTNAME/" | sudo -u $PROJECT_USER_NAME tee "$DOCKER_APPDATA_PATH/procustodibus-controller/nginx/procustodibus.conf"

#
# Add procustodibus-controller to main docker-compose.yml
#
sudo -u $PROJECT_USER_NAME sed -i "/^\s*- compose\/procustodibus-controller\/docker-compose.yml/d" $DOCKER_COMPOSE_MASTER_FILE
echo "  - compose/procustodibus-controller/docker-compose.yml" | sudo -u $PROJECT_USER_NAME tee -a $DOCKER_COMPOSE_MASTER_FILE

# Reload vmutils docker compose project file to apply changes.
sudo docker compose -f $DOCKER_COMPOSE_MASTER_FILE -p vmutils up -d --remove-orphans

echo ""
echo "Open https://$TRAEFIK_HOSTNAME/signup in browser to access Pro Custodibus and create new organization."
echo "Use your signup key \"$PROCUSTODIBUS_SIGNUP_KEY\" to finish setup."
