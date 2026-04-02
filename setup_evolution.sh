#!/usr/bin/env bash
# Executa UMA vez após "docker compose up" para:
#   1. Criar a instância WhatsApp
#   2. Configurar o webhook apontando para o app
#   3. Exibir o QR code para conectar o celular
#
# Uso: bash setup_evolution.sh

set -euo pipefail

source .env

EVOLUTION_URL="http://localhost:8080"
APP_WEBHOOK_URL="http://app:8000/webhook"   # URL interna Docker

echo "==> 1. Criando instância '${EVOLUTION_INSTANCE_NAME}'..."
curl -s -X POST "${EVOLUTION_URL}/instance/create" \
  -H "apikey: ${EVOLUTION_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "instanceName": "'"${EVOLUTION_INSTANCE_NAME}"'",
    "qrcode": true,
    "integration": "WHATSAPP-BAILEYS"
  }' | python3 -m json.tool

echo ""
echo "==> 2. Configurando webhook..."
curl -s -X POST "${EVOLUTION_URL}/webhook/set/${EVOLUTION_INSTANCE_NAME}" \
  -H "apikey: ${EVOLUTION_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "'"${APP_WEBHOOK_URL}"'",
    "webhook_by_events": false,
    "webhook_base64": false,
    "events": ["MESSAGES_UPSERT"]
  }' | python3 -m json.tool

echo ""
echo "==> 3. Buscando QR code (pode levar alguns segundos)..."
sleep 3
curl -s "${EVOLUTION_URL}/instance/connect/${EVOLUTION_INSTANCE_NAME}" \
  -H "apikey: ${EVOLUTION_API_KEY}" | python3 -m json.tool

echo ""
echo "Abra o WhatsApp no celular > Aparelhos conectados > Conectar aparelho e escaneie o QR code acima."
