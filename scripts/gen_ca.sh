#!/usr/bin/env bash
set -euo pipefail

mkdir -p ssl

openssl req -x509 -newkey rsa:4096 -sha256 -days 7300 -nodes \
  -keyout ssl/ca.key -out ssl/ca.crt \
  -subj "/CN=Local Radio CA" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign"

echo "wrote ssl/ca.crt (install on your phone) and ssl/ca.key"