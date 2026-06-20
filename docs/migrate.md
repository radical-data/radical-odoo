# Coolify Odoo migration to repo-based deployment

This procedure migrates an existing Coolify Odoo instance from a stock
`odoo:19` Compose deployment to this repo's custom image, preserving the Odoo
database and filestore via an Odoo ZIP backup/restore.

## 1. Prepare the new Coolify project

Create a new Coolify application from this Git repo using Docker Compose.

Set environment variables:

```text
SERVICE_USER_POSTGRES=odoo
SERVICE_PASSWORD_POSTGRES=<strong password>
```

Deploy once and confirm the custom image contains the expected addons:

```python
python3 - <<'PY'
import importlib.util
for module in (
    "odoo.addons.account_invoice_digitize_ai",
    "odoo.addons.account_financial_report",
    "odoo.addons.date_range",
    "odoo.addons.report_xlsx",
):
    print(module, "OK" if importlib.util.find_spec(module) else "MISSING")
PY
```

All should print `OK`.

## 2. Back up the old Odoo

In the old Odoo instance, open:

```text
/web/database/manager
```

Create a ZIP backup of the production database, including the filestore.

## 3. Stop the old instance

Stop the old Coolify service so no new invoices, uploads, emails, or settings
changes happen after the final backup.

Do not delete the old project or its volumes yet.

## 4. Restore into the new project

In the new Odoo instance, open:

```text
/web/database/manager
```

Restore the ZIP backup with:

- **Database name:** `odoo`
- **Neutralize:** unchecked
- **Database was moved:** selected

Use a strong database-manager master password and save it.

## 5. Install repo modules into the restored DB

Run inside the new Odoo container:

```bash
odoo --stop-after-init --no-http \
  -d odoo \
  --db_host=db \
  --db_port=5432 \
  --db_user="$SERVICE_USER_POSTGRES" \
  --db_password="$SERVICE_PASSWORD_POSTGRES" \
  -i account_invoice_digitize_ai,date_range,report_xlsx,account_financial_report
```

## 6. Verify

Check module state:

```bash
odoo shell --no-http \
  -d odoo \
  --db_host=db \
  --db_port=5432 \
  --db_user="$SERVICE_USER_POSTGRES" \
  --db_password="$SERVICE_PASSWORD_POSTGRES" <<'PY'
for name in (
    "l10n_nl",
    "account_invoice_digitize_ai",
    "date_range",
    "report_xlsx",
    "account_financial_report",
):
    mod = env["ir.module.module"].search([("name", "=", name)], limit=1)
    print(name, mod.state if mod else "missing")
PY
```

Expected:

```text
l10n_nl installed
account_invoice_digitize_ai installed
date_range installed
report_xlsx installed
account_financial_report installed
```

Then redeploy/restart Odoo and check:

```text
Invoicing / Accounting -> Reporting -> OCA accounting reports
```

## 7. Final checks

Before switching the production domain, confirm:

- Login works.
- Old invoices, contacts, products, journals, taxes, and attachments are present.
- Invoice PDFs and uploaded files open correctly.
- OCA General Ledger or Trial Balance generates successfully.
- Dutch VAT reports are still available.
- `web.base.url` points to the intended production URL after cutover.

Keep the old Coolify project and volumes for a few days as rollback insurance.
