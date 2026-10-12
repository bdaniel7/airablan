#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <hostname> [more hostnames...]" >&2
  exit 1
fi

mkdir -p ssl

CA_KEY=ssl/ca.key
CA_CRT=ssl/ca.crt

for host in "$@"; do
  key="ssl/$host.key"
  crt="ssl/$host.crt"

  if [[ -f "$CA_KEY" && -f "$CA_CRT" ]]; then
    openssl req -new -newkey rsa:4096 -nodes \
      -keyout "$key" -out "ssl/$host.csr" -subj "/CN=$host"
    openssl x509 -req -in "ssl/$host.csr" \
      -CA "$CA_CRT" -CAkey "$CA_KEY" -CAcreateserial \
      -out "$crt" -days 3650 -sha256 \
      -extfile <(printf "subjectAltName=DNS:%s\nextendedKeyUsage=serverAuth" "$host")
    rm -f "ssl/$host.csr"
  else
    openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
      -keyout "$key" -out "$crt" \
      -subj "/CN=$host" \
      -addext "subjectAltName=DNS:$host"
  fi

  echo "wrote $crt and $key"
done