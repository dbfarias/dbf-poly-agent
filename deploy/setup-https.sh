#!/bin/bash
# HTTPS setup with DuckDNS + Let's Encrypt
# Run this once on the server after creating a DuckDNS subdomain.
#
# Prerequisites:
#   1. Go to https://www.duckdns.org and log in with GitHub
#   2. Create a subdomain (e.g. "polybot") → polybot.duckdns.org
#   3. Set it to point to your server IP (15.207.1.92)
#   4. Copy your DuckDNS token
#
# Usage:
#   bash deploy/setup-https.sh polybot your-duckdns-token your@email.com

set -euo pipefail

DOMAIN="${1:-}"
DUCKDNS_TOKEN="${2:-}"
EMAIL="${3:-}"

if [ -z "$DOMAIN" ] || [ -z "$DUCKDNS_TOKEN" ] || [ -z "$EMAIL" ]; then
    echo "Usage: $0 <duckdns-subdomain> <duckdns-token> <email>"
    echo "Example: $0 polybot abc123-token-here you@email.com"
    exit 1
fi

FQDN="${DOMAIN}.duckdns.org"
echo "Setting up HTTPS for ${FQDN}..."

# 1. Update DuckDNS to point to this server's IP
echo "Updating DuckDNS record..."
RESULT=$(curl -s "https://www.duckdns.org/update?domains=${DOMAIN}&token=${DUCKDNS_TOKEN}&ip=")
if [ "$RESULT" != "OK" ]; then
    echo "ERROR: DuckDNS update failed. Check your token and subdomain."
    exit 1
fi
echo "DuckDNS updated: ${FQDN} → $(curl -s ifconfig.me)"

# 2. Set up DuckDNS auto-update cron (every 5 min)
CRON_CMD="*/5 * * * * curl -s 'https://www.duckdns.org/update?domains=${DOMAIN}&token=${DUCKDNS_TOKEN}&ip=' > /dev/null 2>&1"
(crontab -l 2>/dev/null | grep -v "duckdns.org"; echo "$CRON_CMD") | crontab -
echo "DuckDNS auto-update cron installed."

# 3. Make sure nginx is running on port 80 for ACME challenge
echo "Ensuring nginx is running..."
docker compose -f docker-compose.prod.yml up -d nginx

# 4. Wait for DNS propagation
echo "Waiting 10s for DNS propagation..."
sleep 10

# 5. Request initial certificate
echo "Requesting Let's Encrypt certificate..."
docker run --rm \
    -v polybot_certbot-etc:/etc/letsencrypt \
    -v polybot_certbot-www:/var/www/certbot \
    certbot/certbot certonly \
    --webroot \
    --webroot-path=/var/www/certbot \
    --email "${EMAIL}" \
    --agree-tos \
    --no-eff-email \
    --cert-name polybot \
    -d "${FQDN}"

if [ $? -ne 0 ]; then
    echo "ERROR: Certificate request failed. Check that:"
    echo "  - Port 80 is open on your server"
    echo "  - DNS resolves: dig ${FQDN}"
    echo "  - nginx is serving /.well-known/acme-challenge/"
    exit 1
fi

echo "Certificate obtained!"

# 6. Update .env with the domain
if grep -q "^DOMAIN=" .env 2>/dev/null; then
    sed -i "s/^DOMAIN=.*/DOMAIN=${FQDN}/" .env
else
    echo "DOMAIN=${FQDN}" >> .env
fi

# Update ALLOWED_ORIGINS to include HTTPS
if grep -q "^ALLOWED_ORIGINS=" .env 2>/dev/null; then
    sed -i "s|^ALLOWED_ORIGINS=.*|ALLOWED_ORIGINS=https://${FQDN},http://${FQDN}|" .env
fi

# 7. Reload nginx to pick up the new cert
echo "Restarting services with HTTPS..."
docker compose -f docker-compose.prod.yml up -d

echo ""
echo "=============================="
echo "HTTPS is ready!"
echo "Dashboard: https://${FQDN}"
echo "=============================="
echo ""
echo "Auto-renewal is handled by the certbot container."
echo "DuckDNS IP is auto-updated every 5 minutes via cron."
