#!/bin/bash

# Exit immediately if a pipeline returns a non-zero status.
# https://www.gnu.org/savannah-checkouts/gnu/bash/manual/bash.html#The-Set-Builtin
set -e 

. "../env.sh"

TRAEFIK_DC="$DOCKER_COMPOSE_PATH/traefik3/docker-compose.yml"
LE_CA_OPT="--certificatesResolvers.letsencrypt.acme.caServer"
LE_CA_STAGING_URL="https://acme-staging-v02.api.letsencrypt.org/directory"

if [ ! -f "$TRAEFIK_DC" ]; then
    echo "Unable to find file \"$TRAEFIK_DC\"."
    echo "Traefik3 not installed. Use traefik3/install.sh to install traefik3 before use this script."
    exit 1
fi


NEED_SWITCH_LE_ENV=""
if [[ $( sudo -u $PROJECT_USER_NAME cat "$TRAEFIK_DC" | yq ".services.traefik.command.[] | select(. == \"$LE_CA_OPT=*\")" | cut -d= -f2 | grep 'acme-staging' | wc -l ) -gt 0 ]]; then 

    # Traefik configured to use Let's Encrypt staging environment.

    echo ""
    echo "Traefik configured to use Let's Encrypt staging environment."
    echo "Do you want to switch it to trusted environment? [y/n]"
    read NEED_SWITCH_LE_ENV
    if [[ $NEED_SWITCH_LE_ENV == "y" ]]; then
        echo "Removing staging CA from \"$TRAEFIK_DC\"."
        sudo -u $PROJECT_USER_NAME cat "$TRAEFIK_DC" | yq "del(.services.traefik.command.[] | select(. == \"$LE_CA_OPT=*\"))" | sudo -u $PROJECT_USER_NAME sponge "$TRAEFIK_DC"
        echo "Staging CA removed. Checking final configuration..."
        if [[ $( sudo -u $PROJECT_USER_NAME grep "$LE_CA_OPT=" | wc -l ) -gt 0 ]]; then
            echo "We failed, $LE_CA_OPT steel in place. Try agane later or remove it from \"$TRAEFIK_DC\" manually and restart docker compose."
            exit 1
        else
            # Reload vmutils docker compose project file to apply changes.
            sudo docker compose -f $DOCKER_COMPOSE_MASTER_FILE -p vmutils up -d --remove-orphans
        fi
    fi

else

    # Traefik configured to use Let's Encrypt trusted environment.
    
    echo ""
    echo "Traefik configured to use Let's Encrypt truested environment."
    echo "Do you want to switch it to staging environment? [y/n]"
    read NEED_SWITCH_LE_ENV
    if [[ $NEED_SWITCH_LE_ENV == "y" ]]; then
        echo "Adding staging CA to \"$TRAEFIK_DC\"."
        sudo -u $PROJECT_USER_NAME "$TRAEFIK_DC" | yq ".services.traefik.command += \"$LE_CA_OPT=$LE_CA_STAGING_URL\"" | sudo -u $PROJECT_USER_NAME sponge "$TRAEFIK_DC"
        echo "Staging CA added. Checking final configuration..."
        if [[ $( sudo -u $PROJECT_USER_NAME grep "$LE_CA_OPT=$LE_CA_STAGING_URL" | wc -l ) -lt 1 ]]; then
            echo "We failed, \"$LE_CA_OPT=$LE_CA_STAGING_URL\" not in place. Try agane later or add it to \"$TRAEFIK_DC\" manually and restart docker compose."
            exit 1
        else
            # Reload vmutils docker compose project file to apply changes.
            sudo docker compose -f $DOCKER_COMPOSE_MASTER_FILE -p vmutils up -d --remove-orphans
        fi
    fi
fi
