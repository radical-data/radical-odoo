#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: scripts/update-modules.sh <database> [module1,module2,...]" >&2
  exit 2
fi

DB="$1"
DEFAULT_MODULES="$(
  printf '%s' \
  'l10n_nl,account_invoice_digitize_ai,date_range,account_financial_report'
)"

MODULES="${2:-$DEFAULT_MODULES}"

echo "Database: $DB"
echo "Modules:  $MODULES"
echo "This will run Odoo module updates. Ensure backups exist before production use."

docker compose run --rm odoo \
  odoo \
  --stop-after-init \
  --no-http \
  -d "$DB" \
  -u "$MODULES"
