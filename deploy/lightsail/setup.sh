#!/bin/bash
set -euo pipefail

# Setup script for AWS Lightsail Ubuntu instance
echo "=== Polymarket Bot Server Setup ==="

# Update system
sudo apt-get update && sudo apt-get upgrade -y

# Install Docker via official APT repository (no curl-pipe-sh)
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker $USER

# Setup swap (1GB) for 1GB RAM instance
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Create app directory
mkdir -p ~/polybot
cd ~/polybot

# Install AWS CLI for S3 backups
sudo apt-get install -y awscli

# Setup cron for daily backup
(crontab -l 2>/dev/null; echo "0 2 * * * /home/$USER/polybot/deploy/scripts/backup.sh >> /var/log/polybot-backup.log 2>&1") | crontab -

# Setup health check cron (every 5 minutes)
(crontab -l 2>/dev/null; echo "*/5 * * * * /home/$USER/polybot/deploy/scripts/health-check.sh >> /var/log/polybot-health.log 2>&1") | crontab -

echo "=== Setup Complete ==="
echo "Next: copy .env file and run 'docker compose up -d'"
