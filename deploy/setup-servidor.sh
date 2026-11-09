#!/usr/bin/env bash
# Preparação inicial de uma VPS Ubuntu 24.04 (DigitalOcean ou similar).
# Rode UMA vez, como root:   bash setup-servidor.sh
#
# O que faz:
#   1. Atualiza o sistema e liga as atualizações de segurança automáticas
#   2. Ativa o firewall (só a porta do SSH fica aberta)
#   3. Instala o fail2ban (bloqueia quem fica tentando adivinhar a senha)
#   4. Instala o Docker, se ainda não existir
#   5. Desativa login por senha no SSH, SOMENTE se já existir uma chave SSH cadastrada
#      (assim você não se tranca para fora do servidor)
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Rode como root (ou com sudo)." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

echo "==> 1/5 Atualizando o sistema"
apt-get update -y
apt-get upgrade -y
apt-get install -y unattended-upgrades ufw fail2ban curl ca-certificates
dpkg-reconfigure -f noninteractive unattended-upgrades || true

echo "==> 2/5 Firewall"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable

echo "==> 3/5 fail2ban"
systemctl enable --now fail2ban

echo "==> 4/5 Docker"
if command -v docker >/dev/null 2>&1; then
  echo "Docker já instalado: $(docker --version)"
else
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

echo "==> 5/5 SSH"
if [ -s /root/.ssh/authorized_keys ]; then
  sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
  sed -i 's/^#\?PermitRootLogin .*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
  systemctl reload ssh || systemctl reload sshd || true
  echo "Login por senha desativado (há chave SSH cadastrada)."
else
  echo "ATENÇÃO: não há chave SSH em /root/.ssh/authorized_keys."
  echo "         Login por senha NÃO foi desativado. Cadastre uma chave e rode o script de novo."
fi

echo
echo "Pronto. Próximo passo: copie o projeto para /opt/docflow e siga o GUIA-VPS.md (passo 4)."
