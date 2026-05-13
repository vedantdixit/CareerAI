#!/bin/bash
set -e

echo ""
echo "  🚀 CareerAI — Starting up"
echo ""

cd "$(dirname "$0")/backend"

# Setup .env
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "  ⚠️  Created backend/.env"
  echo "  ➜  Add your Anthropic API key: https://console.anthropic.com/"
  echo "  ➜  Edit: backend/.env  →  ANTHROPIC_API_KEY=sk-ant-..."
  echo ""
fi

# Python venv
if [ ! -d "venv" ]; then
  echo "  📦 Setting up Python environment..."
  python3 -m venv venv
  source venv/bin/activate
  pip install -r requirements.txt -q
  echo "  ✅ Dependencies installed"
else
  source venv/bin/activate
fi

echo "  🌐 Starting server on http://localhost:8000"
echo "  ────────────────────────────────────────────"
echo "  Open: http://localhost:8000"
echo "  Press Ctrl+C to stop"
echo ""

uvicorn main:app --reload --port 8000 --host 0.0.0.0
