#!/usr/bin/env bash
# Inicia o Choperia Bot completo
set -euo pipefail

echo "==> Subindo containers..."
docker-compose up -d

echo "==> Aguardando WAHA inicializar..."
for i in $(seq 1 30); do
    STATUS=$(curl -s "http://localhost:3002/api/sessions/default" \
        -H "X-Api-Key: choperia-waha-2024" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
    if [ "$STATUS" = "WORKING" ]; then
        echo "✅ WAHA conectado!"
        break
    elif [ "$STATUS" = "STOPPED" ] || [ "$STATUS" = "FAILED" ]; then
        echo "   Iniciando sessão WhatsApp..."
        curl -s -X POST "http://localhost:3002/api/sessions/default/start" \
            -H "X-Api-Key: choperia-waha-2024" > /dev/null
    fi
    sleep 3
done

echo "==> Status final:"
docker ps --format "table {{.Names}}\t{{.Status}}" | grep choperia
echo ""
echo "Bot pronto. Mande mensagens no self-chat do WhatsApp."
