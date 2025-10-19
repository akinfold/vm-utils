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
sudo -u $PROJECT_USER_NAME cp -a "./rules/." "$DOCKER_APPDATA_PATH/traefik3/rules/"

# Add default host FQDN for traefik Let's Encrypt certificate.
TRAEFIK_HOSTNAME="$( hostname )"
TRAEFIK_NEED_SETUP_HOSTNAME=""
TRAEFIK_NEW_HOSTNAME=""
echo "Setup valid hostname with DNS A-record to use it as default traefik hostname for let's encrypt certificate."
while [[ $TRAEFIK_NEED_SETUP_HOSTNAME != "y" ]] && [[ $TRAEFIK_NEED_SETUP_HOSTNAME != "n" ]]; do
    echo ""
    echo "By default traefik will use hostname \"$TRAEFIK_HOSTNAME\"."
    echo ""
    echo -n "Setup new hostname? [y/n] "
    read TRAEFIK_NEED_SETUP_HOSTNAME
    if [[ $TRAEFIK_NEED_SETUP_HOSTNAME == "y" ]]; then
        
        TRAEFIK_NEED_SETUP_HOSTNAME=""

        echo -n "Enter new hostname: "
        read TRAEFIK_NEW_HOSTNAME

        if [[ -z $TRAEFIK_NEW_HOSTNAME ]]; then
            echo "Hostname can't be empty. Let's try again."
            continue
        fi    

        if [[ -z $( dig $TRAEFIK_NEW_HOSTNAME | awk '/^;; ANSWER SECTION:$/ { getline ; print $5 }' ) ]]; then 
            echo "Hostname can't be resolved in DNS using dig command. Let's try again."
            continue
        fi 

        $TRAEFIK_HOSTNAME = $TRAEFIK_NEW_HOSTNAME
    fi

    sudo sed -i "/^TRAEFIK_HOSTNAME=.*/d" $DOCKER_ENV_FILE
    echo "TRAEFIK_HOSTNAME=\"$TRAEFIK_HOSTNAME\"" | sudo tee -a $DOCKER_ENV_FILE
    break
done

# Copy traefik3 docker compose to 
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_COMPOSE_PATH/traefik3"
sudo -u $PROJECT_USER_NAME cp "./docker-compose.yml" "$DOCKER_COMPOSE_PATH/traefik3/docker-compose.yml"

# Choose Let's encrypt certificate type.
echo ""
echo "By default traefik configured to use Let's encrypt staging environment."
echo "This allow you to get things right before issuing trusted certificates and reduce the chance of your running up against rate limits."
echo "More info about staging environment: https://letsencrypt.org/docs/staging-environment/"
echo "If you choose to continue with staging environment, you can later switch to trusted environment by running traefik3/switch-le-env.sh script."
TRAEFIK_LE_ENVIRONMENT_SELECTION=""
while [[ $TRAEFIK_LE_ENVIRONMENT_SELECTION != "y" ]] && [[ $TRAEFIK_LE_ENVIRONMENT_SELECTION != "n" ]]; do
    echo ""
    echo -n "Continue with staging environment? [y/n] "
    read TRAEFIK_LE_ENVIRONMENT_SELECTION

    if [[ $TRAEFIK_LE_ENVIRONMENT_SELECTION == "n" ]]; then
        echo "Switching to Let's encrypt trusted enviroment."
        sudo -u $PROJECT_USER_NAME sed -i "/^.*--certificatesResolvers.letsencrypt.acme.caServer=.\+$/d" "$DOCKER_COMPOSE_PATH/traefik3/docker-compose.yml"
    elif [[ $TRAEFIK_LE_ENVIRONMENT_SELECTION == "y" ]]; then
        echo "Continue with Let's encrypt staging enviroment."
    else
        echo "Please select "y" or "n". Let's try again."
    fi
done

#
# Allow 80 and 443 ports in UFW.
# IMPORTANT! 
# Without this, all http and https connections from other docker containers using public hostname and IP 
# on the same host will be broken. For example procustodibus-agent won't be able to connect to procustodibus-controller.
sudo ufw allow 80
sudo ufw allow 443

# Add traefik3 to main docker-compose.yml
sudo -u $PROJECT_USER_NAME sed -i "/^\s*- compose\/traefik3\/docker-compose.yml/d" $DOCKER_COMPOSE_MASTER_FILE
echo "  - compose/traefik3/docker-compose.yml" | sudo -u $PROJECT_USER_NAME tee -a $DOCKER_COMPOSE_MASTER_FILE

# Reload vmutils docker compose project file to apply changes.
sudo docker compose -f $DOCKER_COMPOSE_MASTER_FILE -p vmutils up -d --remove-orphans

echo ""
echo "Open https://$TRAEFIK_HOSTNAME/traefik in browser to access Traefik dashboard."
