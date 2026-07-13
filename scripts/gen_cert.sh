#!/usr/bin/env bash
# Genera cert TLS autofirmado para dev (threat model §6.20: Secure cookies exigen TLS).
# No committear certs/ (está en .gitignore).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout certs/key.pem -out certs/cert.pem -days 365 \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
chmod 600 certs/key.pem
echo "Cert generado en certs/ (válido 365 días, autofirmado)."
