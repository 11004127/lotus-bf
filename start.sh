#!/usr/bin/env bash
# Start (or restart) LoTUS-BF on the server.
#   usage:  export DB_URL='postgresql://...'   then   bash start.sh
#   ports:  BACKEND_PORT (default 5000), FRONTEND_PORT (default 3000)
set -u
cd "$(dirname "$0")"
BACKEND_PORT="${BACKEND_PORT:-5000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

if [ -z "${DB_URL:-}" ]; then
  echo "✗ DB_URL is not set. Run:  export DB_URL='postgresql://user:password@host:5432/dbname'"
  exit 1
fi

# stop this project's previous processes (only your own)
pkill -u "$USER" -f "python3 app.py" 2>/dev/null
pkill -u "$USER" -f "vite --port $FRONTEND_PORT" 2>/dev/null
sleep 1

# refuse to start if someone else is already using the ports
for p in "$BACKEND_PORT" "$FRONTEND_PORT"; do
  if ! python3 -c "import socket,sys; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('0.0.0.0', int(sys.argv[1])))" "$p" 2>/dev/null; then
    echo "✗ Port $p is already in use (maybe by another user)."
    echo "  Pick other ports, e.g.:  BACKEND_PORT=5010 FRONTEND_PORT=3010 bash start.sh"
    exit 1
  fi
done

[ -d node_modules ] || { echo "→ npm install"; npm install --no-audit --no-fund || exit 1; }

echo "→ backend on port $BACKEND_PORT (log: backend.log)"
PORT="$BACKEND_PORT" nohup python3 app.py > backend.log 2>&1 &
sleep 4
if curl -s "http://localhost:$BACKEND_PORT/" | grep -q "Server working"; then
  echo "  ✓ backend running"
  resp=$(curl -s "http://localhost:$BACKEND_PORT/terms")
  n=$(printf '%s' "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['count'])" 2>/dev/null)
  if [ -n "$n" ]; then
    echo "  ✓ database connected: $n terms"
  else
    echo "  ✗ backend is up but the database query failed. Check DB_URL. Response:"
    printf '%s\n' "$resp" | head -c 600; echo
    pkill -u "$USER" -f "python3 app.py"; exit 1
  fi
else
  echo "  ✗ backend failed — last lines of backend.log:"; tail -n 15 backend.log; exit 1
fi

echo "→ frontend on port $FRONTEND_PORT (log: frontend.log)"
LOTUS_API="http://localhost:$BACKEND_PORT" nohup npx vite --port "$FRONTEND_PORT" --strictPort > frontend.log 2>&1 &
sleep 5
if curl -s "http://localhost:$FRONTEND_PORT/lotus-bf/" | grep -q "root"; then
  echo "  ✓ frontend running"
  echo
  echo "Your URL:  http://hpc.psy.ntu.edu.tw:$FRONTEND_PORT/lotus-bf/"
else
  echo "  ✗ frontend failed — last lines of frontend.log:"; tail -n 15 frontend.log; exit 1
fi
