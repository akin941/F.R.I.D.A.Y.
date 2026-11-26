#!/usr/bin/env bash
# Generate the loopback TLS certificate for compose.local-tls.yml and print
# the SPKI pin to type into the FridayNode pairing window.
#
#   deploy/docker/gen-local-tls.sh [--force]
#
# A self-signed P-256 certificate (named curve) for 127.0.0.1 / localhost /
# local-tls, valid ten years: the node pins the KEY, so the dates only matter
# to tools that check them, and keeping the key is what keeps a paired node
# working. --force makes a new key, which means re-pairing the node.
# The private key stays in deploy/docker/local-tls/ (0700, gitignored) and
# is never added to any trust store.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
DIR="$HERE/local-tls"
FORCE=0
[[ ${1:-} == --force ]] && FORCE=1
mkdir -p "$DIR"
chmod 700 "$DIR"
if [[ -s $DIR/cert.pem && -s $DIR/key.pem && $FORCE -ne 1 ]]; then
  echo "keeping the existing certificate (--force makes a new key and requires re-pairing)"
else
  CONF=$(mktemp)
  trap 'rm -f "$CONF"' EXIT
  cat > "$CONF" <<'CNF'
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = friday-local-tls
[ext]
subjectAltName = IP:127.0.0.1, DNS:localhost, DNS:local-tls
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature
extendedKeyUsage = serverAuth
CNF
  openssl ecparam -name prime256v1 -genkey -param_enc named_curve -noout -out "$DIR/key.pem"
  openssl req -new -x509 -key "$DIR/key.pem" -out "$DIR/cert.pem" -days 3650 -config "$CONF" -sha256
  # 0644 files inside the 0700 directory: on the host only the owner can
  # reach the key (the directory gates it); inside the container Caddy runs
  # with every capability but NET_BIND_SERVICE dropped (no DAC override),
  # so the files themselves must be world-readable for it to load them.
  chmod 644 "$DIR/key.pem" "$DIR/cert.pem"
fi
PIN=$(openssl x509 -in "$DIR/cert.pem" -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | base64)
echo "certificate: $DIR/cert.pem"
echo "SPKI pin (type it into FridayNode -> Eşleştir… -> Sertifika pini): $PIN"
echo "for preflight, in deploy/docker/.env: NODE_TLS_PIN_SHA256=$PIN"
