#!/bin/bash
# Initialization script for the Oracle VM (Create instance > Show advanced options > Management >
# Initialization script > Paste cloud-init script). Runs once as root on first boot, Ubuntu 24.04 (aarch64 or x86).
# Progress: ssh in, then `sudo tail -f /var/log/cloud-init-output.log`; done when /var/log/go-scan-init.done exists.
set -euxo pipefail

# Oracle's Ubuntu image ends INPUT with a REJECT rule: allow 80/443 before it and keep it across reboots
reject=$(iptables -L INPUT --line-numbers -n | awk '$2 == "REJECT" {print $1; exit}')
iptables -I INPUT "${reject:-1}" -p tcp -m state --state NEW -m multiport --dports 80,443 -j ACCEPT
netfilter-persistent save

# 4 GB swap: the first image build (torch) is memory hungry
if [ ! -f /swapfile ]; then
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# Docker + compose plugin, usable by ubuntu without sudo
curl -fsSL https://get.docker.com | sh
usermod -aG docker ubuntu
systemctl enable --now docker

apt-get install -y git rsync
mkdir -p /srv/go-scan && chown ubuntu:ubuntu /srv/go-scan

touch /var/log/go-scan-init.done
