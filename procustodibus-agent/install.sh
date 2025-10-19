#!/bin/bash

# Exit immediately if a pipeline returns a non-zero status.
# https://www.gnu.org/savannah-checkouts/gnu/bash/manual/bash.html#The-Set-Builtin
set -e 

. "../env.sh"

# Check docker already installed.
if ! type docker > /dev/null 2>&1; then
    echo "Please setup docker with ../docker/install.sh script before run this setup."
    exit 1
fi

# Check that we have everything we need to setup Pro Custodibus agent.
read -p "Do you have procustodibus.conf and procustodibus-setup.conf received from Pro Custodibus controller? [y/n]: " READY_TO_GO
if [[ $READY_TO_GO != "y" ]]; then
    echo "Please follow instructions on https://docs.procustodibus.com/guide/hosts/setup and get procustodibus.conf and procustodibus-setup.conf files before setuo Pro Custodibus agent."
    exit 1
fi


sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/procustodibus-agent/wireguard"

READY_TO_GO=""
PROCUSTODIBUS_CONF_FILE="$DOCKER_APPDATA_PATH/procustodibus-agent/wireguard/procustodibus.conf"
while [[ $READY_TO_GO != "y" ]]; do
    echo "Copy paste content of procustodibus.conf file received from Pro Custodibus controller." 
    echo "More info about this file you can read on https://docs.procustodibus.com/guide/hosts/setup/#configuration-file"
    echo "Press ^D to continue."
    sudo -u $PROJECT_USER_NAME cat > $PROCUSTODIBUS_CONF_FILE

    read -p "Proceed with the entered data? [y/n]: " READY_TO_GO
    if [[ $READY_TO_GO != "y" ]]; then
        echo "OK. Let's try it again."
        echo ""
        continue
    fi
done

READY_TO_GO=""
PROCUSTODIBUS_SETUP_CONF_FILE="$DOCKER_APPDATA_PATH/procustodibus-agent/wireguard/procustodibus-setup.conf"
while [[ $READY_TO_GO != "y" ]]; do
    echo "Copy paste content of procustodibus-setup.conf file received from Pro Custodibus controller." 
    echo "More info about this file you can read on https://docs.procustodibus.com/guide/hosts/setup/#setup-file"
    echo "Press ^D to continue."
    sudo -u $PROJECT_USER_NAME cat > $PROCUSTODIBUS_SETUP_CONF_FILE

    read -p "Proceed with the entered data? [y/n]: " READY_TO_GO
    if [[ $READY_TO_GO != "y" ]]; then
        echo "OK. Let's try it again."
        echo ""
        continue
    fi
done


sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_COMPOSE_PATH/procustodibus-agent"
sudo -u $PROJECT_USER_NAME cp "./docker-compose.yml" "$DOCKER_COMPOSE_PATH/procustodibus-controller/docker-compose.yml"

#
# Add procustodibus-agent to main docker-compose.yml
#
sudo -u $PROJECT_USER_NAME sed -i "/^\s*- compose\/procustodibus-agent\/docker-compose.yml/d" $DOCKER_COMPOSE_MASTER_FILE
echo "  - compose/procustodibus-agent/docker-compose.yml" | sudo -u $PROJECT_USER_NAME tee -a $DOCKER_COMPOSE_MASTER_FILE

# Reload vmutils docker compose project file to apply changes.
sudo docker compose -f $DOCKER_COMPOSE_MASTER_FILE -p vmutils up -d --remove-orphans
