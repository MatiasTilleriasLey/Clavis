#!/usr/bin/env bash
# Genera cert TLS autofirmado para dev (threat model §6.20: Secure cookies exigen TLS).
# No committear certs/ (está en .gitignore).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p certs

# Incluir la IP LAN y el hostname como SAN para poder acceder desde otros dispositivos de la red
# sin que el navegador se queje del nombre (sigue siendo autofirmado -> igual hay que aceptar el
# aviso una vez por dispositivo). Podés pasar IPs/DNS extra como argumentos: gen_cert.sh IP:10.0.0.5
SAN="DNS:localhost,IP:127.0.0.1"
HOSTN="$(hostname 2>/dev/null || true)"; [ -n "$HOSTN" ] && SAN="$SAN,DNS:$HOSTN"
LAN_IP="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^(192\.168|10)\.' | head -1 || true)"
[ -n "$LAN_IP" ] && SAN="$SAN,IP:$LAN_IP"
for extra in "$@"; do SAN="$SAN,$extra"; done

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout certs/key.pem -out certs/cert.pem -days 365 \
  -subj "/CN=${LAN_IP:-localhost}" \
  -addext "subjectAltName=$SAN"
chmod 600 certs/key.pem
echo "Cert generado en certs/ (válido 365 días, autofirmado). SAN: $SAN"
