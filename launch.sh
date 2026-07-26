#!/usr/bin/env bash
# Starts mail-mcp over HTTP, restricted to one tool surface.
# Usage: ./launch.sh <A|B|C> [port]
set -euo pipefail

surface="${1:-}"
if [[ -z "$surface" ]]; then
    echo "Usage: $0 <A|B|C> [port]" >&2
    exit 1
fi


export MAILMCP_AUTH_TOKEN=agentmail.dev.test
export MAILMCP_TRANSPORT=http
export MAILMCP_SURFACE="$surface"
export MAILMCP_BIND_PORT="${2:-8123}"

exec uv run mail-mcp
