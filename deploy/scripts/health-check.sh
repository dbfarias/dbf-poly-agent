#!/bin/bash
# Health check script — restarts container if unhealthy

HEALTH_URL="http://localhost:8000/api/health"
MAX_RETRIES=3

for i in $(seq 1 $MAX_RETRIES); do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || echo "000")
    if [ "$STATUS" = "200" ]; then
        exit 0
    fi
    sleep 5
done

echo "$(date): Health check failed after $MAX_RETRIES retries. Restarting..."
cd /home/$USER/polybot && docker compose restart app
