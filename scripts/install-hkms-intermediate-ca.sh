#!/usr/bin/env bash
# Install the intermediate CA that some HKMS download servers leave out of
# their TLS chain.
#
# open.hkmapservice.gov.hk currently sends the leaf certificate and the
# Hongkong Post root, but not:
#
#   Hongkong Post e-Cert SSL CA 3 - 17
#
# Windows fills in that intermediate automatically. Linux/OpenSSL does not, so
# Qt and curl fail with "unable to get local issuer certificate". Run this
# script once, then restart QGIS. It is not included in the plugin zip.
#
# The intermediate is valid until 2032. If the host changes its certificate
# authority sooner, update CERT_URL and CERT_SUBJECT below.
#
# To undo the change on Debian or Ubuntu:
#   sudo rm /usr/local/share/ca-certificates/hongkong-post-ecert-ssl-ca-3-17.crt
#   sudo update-ca-certificates --fresh

set -euo pipefail

CERT_URL="http://www1.eCert.gov.hk/root/ecert_ssl_ca_3-17.crt"
CERT_NAME="hongkong-post-ecert-ssl-ca-3-17"
CERT_SUBJECT="Hongkong Post e-Cert SSL CA 3 - 17"
HOST="open.hkmapservice.gov.hk"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script with sudo: sudo $0" >&2
    exit 1
fi

for tool in curl openssl; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "error: $tool is required." >&2
        exit 1
    fi
done

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

echo "Downloading the intermediate certificate from $CERT_URL"
curl -fsS --max-time 30 -o "$tmp_dir/chain.crt" "$CERT_URL"

if ! openssl x509 -in "$tmp_dir/chain.crt" -out "$tmp_dir/chain.pem" 2>/dev/null; then
    openssl x509 -inform DER -in "$tmp_dir/chain.crt" -out "$tmp_dir/chain.pem"
fi

subject="$(openssl x509 -in "$tmp_dir/chain.pem" -noout -subject)"
case "$subject" in
    *"$CERT_SUBJECT"*) ;;
    *)
        echo "error: the downloaded certificate is not $CERT_SUBJECT." >&2
        echo "       subject: $subject" >&2
        exit 1
        ;;
esac

# The file is fetched over plain HTTP, so check that the existing system root
# really signed it before trusting it.
if [ -d /etc/ssl/certs ]; then
    ca_path=/etc/ssl/certs
else
    ca_path=/etc/pki/tls/certs
fi
if ! openssl verify -CApath "$ca_path" "$tmp_dir/chain.pem" >/dev/null 2>&1; then
    echo "error: the certificate is not signed by the trusted Hongkong Post root." >&2
    echo "       Check the root CA in $ca_path, or ask the data provider for the full chain." >&2
    exit 1
fi

if command -v update-ca-certificates >/dev/null 2>&1; then
    mkdir -p /usr/local/share/ca-certificates
    install -m 0644 "$tmp_dir/chain.pem" "/usr/local/share/ca-certificates/$CERT_NAME.crt"
    update-ca-certificates
elif command -v update-ca-trust >/dev/null 2>&1; then
    if [ -d /etc/pki/ca-trust/source/anchors ]; then
        install -m 0644 "$tmp_dir/chain.pem" "/etc/pki/ca-trust/source/anchors/$CERT_NAME.pem"
    elif [ -d /etc/ca-certificates/trust-source/anchors ]; then
        install -m 0644 "$tmp_dir/chain.pem" "/etc/ca-certificates/trust-source/anchors/$CERT_NAME.pem"
    else
        echo "error: could not find the system trust anchor directory." >&2
        exit 1
    fi
    update-ca-trust extract 2>/dev/null || update-ca-trust
else
    echo "error: this script supports update-ca-certificates or update-ca-trust." >&2
    exit 1
fi

echo "Checking $HOST ..."
if curl -sS -o /dev/null --max-time 30 "https://$HOST/"; then
    echo "TLS verification succeeded. Restart QGIS before downloading again."
else
    echo "warning: the download host still does not verify." >&2
    echo "         Restart QGIS, then run: curl -I https://$HOST/" >&2
    exit 1
fi
