#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: scripts/update-odoo-modules.sh <database> [module1,module2,...]" >&2
  exit 2
fi

ROOT_DIR="$(env -u CDPATH bash -c "cd -- \"\$1\" && pwd" _ "$(dirname -- "$0")/..")"
MODULE_FILE="$ROOT_DIR/odoo-modules.txt"

DB="$1"

if [ "${2:-}" ]; then
  MODULES="$2"
else
  if [ ! -f "$MODULE_FILE" ]; then
    echo "Missing module list: $MODULE_FILE" >&2
    exit 2
  fi
  MODULES="$(grep -v '^[[:space:]]*$' "$MODULE_FILE" | paste -sd, -)"
fi

echo "Database: $DB"
echo "Modules:  $MODULES"
echo "This will run Odoo module updates. Ensure backups exist before production use."

docker compose run --rm odoo \
  odoo \
  --stop-after-init \
  --no-http \
  -d "$DB" \
  -u "$MODULES"
