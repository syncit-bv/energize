#!/bin/bash
# Render.com startup script voor Energize EMS
# Maakt .streamlit/secrets.toml aan vanuit environment variables,
# daarna start Streamlit op de door Render toegewezen PORT.

set -e

mkdir -p .streamlit
cat > .streamlit/secrets.toml << EOF
entsoe_key = "${ENTSOE_KEY:-}"
em_key = "${EM_KEY:-}"
EOF

echo "✅ Secrets ingeladen"
echo "🚀 Streamlit starten op poort ${PORT:-8501}..."

exec streamlit run streamlit_dashboard.py \
  --server.port "${PORT:-8501}" \
  --server.address "0.0.0.0" \
  --server.headless true \
  --server.enableCORS false \
  --server.enableXsrfProtection false
