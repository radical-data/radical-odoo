# Radical Odoo deployment

Minimal Docker Compose deployment for **Odoo 19 Community** with selected
custom and OCA addons.

It is intended for local testing and deployment through platforms such as **Coolify**.

## Design

- `Dockerfile` builds a custom image from `odoo:19`.
- OCA addons are installed as pinned Python packages from the OCA wheelhouse.
- This repository is also an installable Odoo project package using `hatch-odoo`.
- The `account_invoice_digitize_ai` addon is vendored under
  `addons/account_invoice_digitize_ai` and installed into the `odoo.addons`
  namespace during the Docker build.
- Odoo and PostgreSQL runtime data live in Docker volumes.
- Do not install Python packages or addons manually inside a running container.
- Installing addon code and installing/upgrading modules in the database are
  separate operations.

The source of truth is:

```text
Runtime/services: docker-compose.yml
Image/addons:     Dockerfile, pyproject.toml, addons/
Python packages:  requirements.txt
Local env sample: .env.example
```

`requirements.in` is the human-edited dependency input. `requirements.txt` is
the pinned install file used by the Docker image. Because OCA addon wheels
depend on the Odoo runtime provided by the base image, regenerate and verify
the pinned file inside an Odoo image. Do not hand-edit it casually.

## Local usage

Create a local environment file:

```bash
cp .env.example .env
```

Edit `.env`, then run:

```bash
docker compose up --build
```

Open:

```text
http://localhost:8069
```

For a clean local reset:

```bash
docker compose down -v
```

This deletes the local Odoo/PostgreSQL volumes.

## Deployment

In Coolify, create a Docker Compose application from this repository.

Set real values for required secrets in the Coolify environment variables UI:

```env
SERVICE_USER_POSTGRES=odoo
SERVICE_PASSWORD_POSTGRES=use-a-real-password
```

## First Odoo setup

After the first login:

1. Install **Invoicing**.
2. Go to **Apps → Update Apps List**.
3. Install the custom/OCA modules you need, especially:
   - `l10n_nl`
   - `account_invoice_digitize_ai`
   - `date_range`
   - `account_financial_report`
4. Confirm the company fiscal localisation is set to **Netherlands** and that
   the chart of accounts is **Netherlands - Accounting**.
5. Enable the accounting user rights described in
   [`docs/nl-eenmanszaak-vat-workflow.md`](docs/nl-eenmanszaak-vat-workflow.md):
   especially **Analytic Accounting** and **Show Full Accounting Features**.
6. Validate that the official Odoo 19 Dutch reports are present before relying
   on the database for VAT filing:

```bash
scripts/check-nl-tax-readiness.sh <database>
```

## Useful checks

```bash
docker compose exec odoo python3 -c 'import odoo.addons.account_invoice_digitize_ai'
docker compose exec odoo python3 -c 'import odoo.addons.account_financial_report, odoo.addons.date_range, odoo.addons.report_xlsx'
docker compose logs -f odoo
```

## Dutch tax filing approach

This deployment targets an NL eenmanszaak doing services, installations,
workshops, funded projects, and international collaborations.

For Odoo 19, the primary Dutch tax filing path is Odoo's built-in Dutch
localisation:

```text
l10n_nl
```

Do not depend on OCA l10n-netherlands modules for filing until matching Odoo
19 versions exist and have been validated in staging.

Expected official Odoo 19 Dutch outputs:

- Tax Report / Aangifte omzetbelasting
- Intrastat Report / ICP
- Profit & Loss
- XAF export from the General Ledger

Use `account_financial_report` for supporting accountant-grade reports:

- Trial Balance
- General Ledger
- Journal Ledger
- Aged Partner Balance
- Open Items

Operational rule:

> Every invoice and vendor bill line must use the correct Dutch tax object.
> Do not use a generic 0% tax for EU B2B services if it does not feed the
> Dutch VAT report and ICP correctly.

For this business model, review these cases carefully:

- NL clients: Dutch VAT, usually 21% unless a specific exception applies.
- EU B2B clients: reverse charge / intra-EU services, normally VAT return 3b
  and ICP, with a valid customer VAT number.
- Non-EU organisation clients: usually no Dutch VAT for B2B services, but check
  the exact service type and place-of-supply rule.
- NL suppliers: reclaimable input VAT where deductible.
- EU suppliers/collaborators: reverse-charge purchase VAT, normally 4b and 5b.
- Non-EU suppliers/collaborators: reverse-charge purchase VAT, normally 4a and 5b.
- Grants/funded projects: classify case by case. A true subsidy is not the same
  as payment for a service.

See [`docs/nl-eenmanszaak-vat-workflow.md`](docs/nl-eenmanszaak-vat-workflow.md) for the
full workflow.

Required manual UI setup:

1. Enable developer mode: Settings → General Settings → Activate the developer mode.
2. Open the relevant user: Settings → General Settings → Manage Users.
3. In Extra Rights, enable Analytic Accounting and
   **Show Full Accounting Features** for the user who will manage bookkeeping,
   tax reports, analytic accounts, and reconciliation.

`scripts/check-nl-tax-readiness.sh` is intentionally conservative. Odoo internals
can move between versions, so treat this as a useful guard and not a perfect
certification. It checks that the Dutch localisation is installed, that the
company looks Dutch, that the core Dutch tax tags are present, and that Odoo has
accounting report records that look like Dutch tax/ICP reporting.

The real acceptance test for staging is transaction-based:

1. NL client invoice with 21% VAT.
2. EU B2B service invoice with VAT reverse-charged.
3. Non-EU B2B service invoice.
4. NL supplier bill with VAT.
5. EU supplier/collaborator bill reverse-charged.
6. Non-EU supplier/collaborator bill reverse-charged.

Then verify:

```text
EU B2B sales appear in 3b and ICP.
EU reverse-charge purchases appear in 4b and 5b.
Non-EU reverse-charge purchases appear in 4a and 5b.
Domestic sales appear in 1a.
Input VAT appears in 5b.
XAF export works from General Ledger.
```

## Updating Python and OCA dependencies

Edit `requirements.in`, then compile `requirements.txt` with your chosen locking
tool. Because the OCA addon wheels depend on odoo==19.0.*, compile or verify
the environment from inside the Odoo base image:

```bash
docker run --rm -v "$PWD:/work" -w /work odoo:19 \
  python3 -m pip install --break-system-packages -r requirements.in
```

Then record the resolved versions in `requirements.txt` and rebuild the image.
If your resolver can satisfy the Odoo runtime constraint, you may use:

```bash
uv pip compile requirements.in -o requirements.txt
pip-compile -o requirements.txt requirements.in
```

Commit both files. The Docker image installs only `requirements.txt`.

## Updating the vendored invoice digitisation addon

The `account_invoice_digitize_ai` source is vendored in:

```text
addons/account_invoice_digitize_ai
```

To update it, copy in the new upstream source, review the diff, run the build,
and test against a staging database before deploying.

Record the imported upstream commit in `addons/account_invoice_digitize_ai/UPSTREAM.md`.

## Database upgrades

Installing code in the image does not install or upgrade modules in an Odoo
database. Use the helper script against a staging copy first:

```bash
scripts/update-modules.sh <database>
```

Back up PostgreSQL and the Odoo filestore before production upgrades.

## Notes

- OCA addons should normally be added as pinned Python packages, not cloned as
  whole repositories.
- Vendored project addons are installed through the local hatch-odoo project package.
- Local `.env` files are ignored by Git.
- Do not store API keys or production passwords in this repository.
