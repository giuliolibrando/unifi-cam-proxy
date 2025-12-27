#!/bin/sh

# Generate client.pem if it doesn't exist
if [ ! -f /client.pem ]; then
  echo "⚠️  client.pem not found, generating certificate..."
  TMP_DIR=$(mktemp -d)
  openssl ecparam -out "$TMP_DIR/private.key" -name prime256v1 -genkey -noout
  openssl req -new -sha256 -key "$TMP_DIR/private.key" -out "$TMP_DIR/server.csr" \
    -subj "/C=TW/L=Taipei/O=Ubiquiti Networks Inc./OU=devint/CN=camera.ubnt.dev/emailAddress=support@ubnt.com"
  openssl x509 -req -sha256 -days 36500 -in "$TMP_DIR/server.csr" \
    -signkey "$TMP_DIR/private.key" -out "$TMP_DIR/public.key"
  cat "$TMP_DIR/private.key" "$TMP_DIR/public.key" > /client.pem
  rm -rf "$TMP_DIR"
  echo "✅ Certificate generated: /client.pem"
fi

if [ ! -z "${RTSP_URL:-}" ] && [ ! -z "${HOST}" ] && [ ! -z "${TOKEN}" ]; then
  echo "Using RTSP stream from $RTSP_URL"
  exec unifi-cam-proxy --host "$HOST" --name "${NAME:-unifi-cam-proxy}" --mac "${MAC:-'AA:BB:CC:00:11:22'}" --cert /client.pem --token "$TOKEN" rtsp -s "$RTSP_URL"
fi

exec "$@"
