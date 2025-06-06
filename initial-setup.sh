#!/bin/bash

# 
# Update packages
# 
apt-get update


# 
# Change root password
# 
apt-get install pwgen

NEW_ROOT_PASSWORD=$(pwgen -1BC 16 1)

echo "Changing root password to $NEW_ROOT_PASSWORD"
echo "root:$NEW_ROOT_PASSWORD" | sudo chpasswd
echo "Root password changed."


# 
# Change SSH port
# Sources:
#   * https://serverfault.com/questions/1159599/how-to-change-the-ssh-server-port-on-ubuntu
#   * https://raw.githubusercontent.com/fcoulloudon/ssh_custom_port/refs/heads/main/script.sh
# 
NEW_SSH_PORT=$((1024 + $RANDOM))

echo "Opening new SSH port $NEW_SSH_PORT in UFW."
ufw allow $NEW_SSH_PORT/tcp
echo "Port $NEW_SSH_PORT has opened in UFW."

echo "Changing SSH port to $NEW_SSH_PORT"

# Define the override directory and file
OVERRIDE_DIR="/etc/systemd/system/ssh.socket.d"
OVERRIDE_FILE="$OVERRIDE_DIR/override.conf"

# Create the override directory if it doesn't exist
if [ ! -d "$OVERRIDE_DIR" ]; then
    echo "Creating directory: $OVERRIDE_DIR"
    mkdir -p "$OVERRIDE_DIR"
fi

# Write the override configuration
cat <<EOF > "$OVERRIDE_FILE"
[Socket]
ListenStream=
ListenStream=$NEW_SSH_PORT
EOF

echo "Configuration written to $OVERRIDE_FILE"

# Modify the SSH server configuration file
SSHD_CONFIG="/etc/ssh/sshd_config"
if grep -q "^Port " "$SSHD_CONFIG"; then
    sed -i "s/^Port .*/Port $NEW_SSH_PORT/" "$SSHD_CONFIG"
else
    echo "Port $NEW_SSH_PORT" >> "$SSHD_CONFIG"
fi

echo "SSH server configuration updated in $SSHD_CONFIG"

# Reload systemd to apply changes
systemctl daemon-reload
# Restart the SSH socket
systemctl restart ssh.socket

# Restart the SSH service
systemctl restart ssh.service

echo "SSH port has been changed to $NEW_SSH_PORT. Verify by running: systemctl status ssh.socket and systemctl status ssh.service"

echo "New root password: $NEW_ROOT_PASSWORD"
echo "New SSH port: $NEW_SSH_PORT"

