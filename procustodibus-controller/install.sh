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

# Source: https://docs.procustodibus.com/guide/onpremises/install/#docker
# Folder we will use to store all Pro Custodibus related configurations
sudo -u $PROJECT_USER_NAME mkdir -p "$DOCKER_APPDATA_PATH/procustodibus-controller"
cd "$DOCKER_APPDATA_PATH/procustodibus-controller"
sudo -u $PROJECT_USER_NAME curl -L -c /tmp/srht.cookies -b /tmp/srht.cookies https://git.sr.ht/~arx10/procustodibus-api/blob/main/ops/install/generate-docker-compose.sh | sudo -u $PROJECT_USER_NAME bash -s ce

# Change ports because we use Traefik to terminate TLS.
