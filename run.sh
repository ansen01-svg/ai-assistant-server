   #!/usr/bin/env bash
   set -e
   source .venv/bin/activate
   docker compose up -d
   uvicorn ai_assistant_server.main:app --reload --host 0.0.0.0 --port 8000
