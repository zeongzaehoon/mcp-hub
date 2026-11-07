#!/usr/bin/env bash
# Register (or remove) a FindTheGap MCP server in Claude Desktop's config.
#
#   scripts/claude-desktop-register.sh <mcp-url> [name]            # key from $FTG_MCP_KEY or prompted
#   scripts/claude-desktop-register.sh --remove [name]
#
# Examples
#   FTG_MCP_KEY=mcpk_xxx scripts/claude-desktop-register.sh http://localhost:19000/mcp
#   scripts/claude-desktop-register.sh https://mcp.example.com/mcp ftg-prod
#   scripts/claude-desktop-register.sh --remove ftg-prod
#
# Picks a stdio bridge automatically: npx + mcp-remote when node is installed, otherwise uvx + mcp-proxy.
# Checks the URL/key with a tools/list call before touching the config, backs the config up, and
# restricts it to 0600 (the key is stored in plaintext there). Restart Claude Desktop (Cmd+Q) afterwards.
set -euo pipefail

CONFIG="${CLAUDE_DESKTOP_CONFIG:-$HOME/Library/Application Support/Claude/claude_desktop_config.json}"
NAME="ftg"
MODE="add"
URL=""

usage() { sed -n '2,15p' "$0"; exit 2; }

case "${1:-}" in
  -h|--help|"") usage ;;
  --remove) MODE="remove"; NAME="${2:-ftg}" ;;
  *) URL="$1"; NAME="${2:-ftg}" ;;
esac

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
[[ "$NAME" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "name must be [A-Za-z0-9_-]+" >&2; exit 1; }
mkdir -p "$(dirname "$CONFIG")"
[[ -f "$CONFIG" ]] || echo '{}' > "$CONFIG"

backup() {
  local b="$CONFIG.bak-$(date +%Y%m%d%H%M%S)-$$"
  cp "$CONFIG" "$b"
  echo "backup: $b"
}

if [[ "$MODE" == "remove" ]]; then
  backup
  python3 - "$CONFIG" "$NAME" <<'EOF'
import json, sys
p, name = sys.argv[1], sys.argv[2]
cfg = json.load(open(p))
if cfg.get("mcpServers", {}).pop(name, None) is None:
    sys.exit(f"'{name}' is not registered")
json.dump(cfg, open(p, "w"), indent=2, ensure_ascii=False)
print(f"removed '{name}'. Quit and reopen Claude Desktop.")
EOF
  exit 0
fi

[[ "$URL" =~ ^https?:// ]] || { echo "mcp-url must start with http:// or https://" >&2; exit 1; }
if [[ "$URL" =~ ^http:// && ! "$URL" =~ ^http://(localhost|127\.0\.0\.1)[:/] ]]; then
  echo "refusing plain http to a non-local host (the key would travel unencrypted)" >&2; exit 1
fi

KEY="${FTG_MCP_KEY:-}"
if [[ -z "$KEY" ]]; then
  read -r -s -p "API key (mcpk_…): " KEY; echo
fi
[[ "$KEY" == mcpk_* ]] || { echo "key must start with mcpk_" >&2; exit 1; }

# ── choose a stdio bridge ──
BRIDGE=""
if NPX="$(command -v npx 2>/dev/null)"; then
  BRIDGE="mcp-remote"
elif UVX="$(command -v uvx 2>/dev/null)"; then
  BRIDGE="mcp-proxy"
else
  echo "need node (npx) or uv (uvx) for the stdio bridge: brew install node  |  brew install uv" >&2; exit 1
fi

# ── verify URL + key before writing anything ──
echo "checking $URL …"
: > /tmp/ftg-mcp-probe.$$
HTTP=$(curl -sS -o /tmp/ftg-mcp-probe.$$ -w '%{http_code}' --max-time 10 -X POST "$URL" \
  -H "Authorization: Bearer $KEY" -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' || true)
# The gateway answers JSON-RPC over SSE: auth failures come back as HTTP 200 with an "error" member.
VERDICT=$(sed -n 's/^data: //p' /tmp/ftg-mcp-probe.$$ | python3 -c 'import sys,json
try:
    d = json.loads(sys.stdin.readline() or "{}")
    print("error " + d["error"]["message"] if "error" in d else "tools %d" % len(d["result"]["tools"]))
except Exception: print("error unreadable response")' 2>/dev/null || echo "error unreadable response")
rm -f /tmp/ftg-mcp-probe.$$
case "$HTTP" in
  200) case "$VERDICT" in
         "tools 0") echo "server answered but listed 0 tools — the key has no active server access" >&2; exit 1 ;;
         tools\ *) echo "ok: ${VERDICT#tools } tools visible" ;;
         *) echo "rejected: ${VERDICT#error }" >&2; exit 1 ;;
       esac ;;
  401|403) echo "rejected ($HTTP): key invalid, suspended, or without server access" >&2; exit 1 ;;
  000) echo "cannot reach $URL" >&2; exit 1 ;;
  *) echo "unexpected HTTP $HTTP from $URL" >&2; exit 1 ;;
esac

backup
FTG_MCP_KEY_INTERNAL="$KEY" python3 - "$CONFIG" "$NAME" "$URL" "$BRIDGE" "${NPX:-}" "${UVX:-}" <<'EOF'
import json, os, sys
p, name, url, bridge, npx, uvx = sys.argv[1:7]
key = os.environ["FTG_MCP_KEY_INTERNAL"]
cfg = json.load(open(p))
if bridge == "mcp-remote":
    entry = {"command": npx, "args": ["-y", "mcp-remote", url, "--header", "Authorization:${FTG_AUTH}"],
             "env": {"FTG_AUTH": f"Bearer {key}"}}
else:
    entry = {"command": uvx, "args": ["--from", "mcp-proxy", "mcp-proxy", "--transport", "streamablehttp",
                                      "-H", "Authorization", f"Bearer {key}", url]}
cfg.setdefault("mcpServers", {})[name] = entry
json.dump(cfg, open(p, "w"), indent=2, ensure_ascii=False)
print(f"registered '{name}' via {bridge} -> {url}")
EOF
chmod 600 "$CONFIG"
echo "done. Quit Claude Desktop completely (Cmd+Q) and reopen; '$NAME' appears under the tools menu."
