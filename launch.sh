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

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
policy_file="$script_dir/sort_policy.txt"
if [[ -f "$policy_file" ]]; then
    export MAILMCP_SORT_POLICY_FILE="$policy_file"
fi

exec uv run mail-mcp
