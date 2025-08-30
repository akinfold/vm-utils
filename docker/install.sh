#!/bin/bash

# Exit immediately if a pipeline returns a non-zero status.
# https://www.gnu.org/savannah-checkouts/gnu/bash/manual/bash.html#The-Set-Builtin
set -e 

. "../env.sh"

#
# Install docker if not installed
#

# Source: https://stackoverflow.com/questions/7522712/how-can-i-check-if-a-command-exists-in-a-shell-script
if ! type docker > /dev/null 2>&1; then

  # Source https://docs.docker.com/engine/install/ubuntu/

  # Add Docker's official GPG key:
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc

  # Add the repository to Apt sources:
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
  sudo apt-get update

  # Install latest version:
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

  # Test docker installation:
  sudo docker run hello-world

  echo "Congratulations, my lord! You have now successfully installed and started Docker Engine."
  echo "Beware! Docker and ufw use iptables in ways that make them incompatible with each other. Read more on https://docs.docker.com/engine/network/packet-filtering-firewalls/#docker-and-ufw"

else
  
  echo "Docker already installed."

fi

#
# Create basic folder structure for docker
# Source: https://www.simplehomelab.com/udms-14-docker-media-server
#
echo ""
echo "Create docker compose basic folder and file structure:"
echo ""

sudo -u $PROJECT_USER_NAME mkdir -p $DOCKER_ROOT_PATH
sudo chmod 775 $DOCKER_ROOT_PATH
sudo setfacl -Rdm u:$PROJECT_USER_NAME:rwx $DOCKER_ROOT_PATH
sudo setfacl -Rm u:$PROJECT_USER_NAME:rwx $DOCKER_ROOT_PATH
sudo setfacl -Rdm g:docker:rwx $DOCKER_ROOT_PATH
sudo setfacl -Rm g:docker:rwx $DOCKER_ROOT_PATH
echo "$( ls -la $DOCKER_ROOT_PATH )"

sudo -u $PROJECT_USER_NAME mkdir -p $DOCKER_LOGS_PATH
echo "$( ls -la $DOCKER_LOGS_PATH)"

sudo -u $PROJECT_USER_NAME mkdir -p $DOCKER_APPDATA_PATH
echo "$( ls -la $DOCKER_APPDATA_PATH )"

sudo -u $PROJECT_USER_NAME mkdir -p $DOCKER_COMPOSE_PATH
echo "$( ls -la $DOCKER_COMPOSE_PATH )"

sudo mkdir -p $DOCKER_SECRETS_PATH
echo "$( sudo ls -la $DOCKER_SECRETS_PATH )"

sudo -u $PROJECT_USER_NAME mkdir -p $DOCKER_SHARED_PATH
echo "$( ls -la $DOCKER_SHARED_PATH )"

if [[ ! -f $DOCKER_ENV_FILE ]]; then
  sudo touch "$DOCKER_ENV_FILE"

  echo "PUID=$( id -u $PROJECT_USER_NAME )" | sudo tee -a $DOCKER_ENV_FILE
  echo "PGID=$( id -g $PROJECT_USER_NAME )" | sudo tee -a $DOCKER_ENV_FILE
  echo "TZ=\"Europe/Moscow\"" | sudo tee -a $DOCKER_ENV_FILE
  echo "USERDIR=\"/home/$PROJECT_USER_NAME\"" | sudo tee -a $DOCKER_ENV_FILE
  echo "DOCKERDIR=\"$DOCKER_ROOT_PATH\"" | sudo tee -a $DOCKER_ENV_FILE
  echo "DOCKER_ROOT_PATH=\"$DOCKER_ROOT_PATH\"" | sudo tee -a $DOCKER_ENV_FILE
  echo "DOCKER_APPDATA_PATH=\"$DOCKER_APPDATA_PATH\"" | sudo tee -a $DOCKER_ENV_FILE
  echo "DOCKER_LOGS_PATH=\"$DOCKER_LOGS_PATH\"" | sudo tee -a $DOCKER_ENV_FILE
  echo "DOCKER_SECRETS_PATH=\"$DOCKER_SECRETS_PATH\"" | sudo tee -a $DOCKER_ENV_FILE
  echo "BASIC_AUTH_CREDENTIALS_FILE=\"$BASIC_AUTH_CREDENTIALS_FILE\"" | sudo tee -a $DOCKER_ENV_FILE

  sudo chown root:root "$DOCKER_ENV_FILE"
  sudo chmod 600 "$DOCKER_ENV_FILE"
  echo "$( sudo ls -la $DOCKER_ENV_FILE )"
fi

if [[ ! -f $DOCKER_COMPOSE_MASTER_FILE ]]; then
  sudo -u $PROJECT_USER_NAME cp "./docker-compose.yml" $DOCKER_COMPOSE_MASTER_FILE
  echo "$( ls -la $DOCKER_COMPOSE_MASTER_FILE )"
fi
