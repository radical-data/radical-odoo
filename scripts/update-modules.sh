#!/usr/bin/env bash
set -euo pipefail

exec "$(dirname "$0")/update-odoo-modules.sh" "$@"
