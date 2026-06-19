# Radical Odoo deployment

An opinionated Odoo deployment for small organisations in the Netherlands.

Immutable Docker Compose deployment for **Odoo 19 Community** with a small set
of packaged custom and OCA addons.

The repository is intended for local testing and deployment through platforms
such as **Coolify**.

## Design

- `Dockerfile` builds a custom image from `odoo:19.0`.
- OCA addons are installed as pinned Python packages from the OCA wheelhouse.
- Vendored project addons are installed through `hatch-odoo` into the
  `odoo.addons` namespace during the Docker build.
- Odoo and PostgreSQL runtime data live in Docker volumes.
- Production does not mount `/mnt/extra-addons`; addon code is image content.
- Installing addon code in the image and updating installed modules in the
  database are separate operations.

The source of truth is:

```text
Runtime/services: docker-compose.yml
Image/addons:     Dockerfile, pyproject.toml, addons/, odoo-modules.txt
Python packages:  requirements.txt
Local env sample: .env.example
VAT workflow:     docs/nl-eenmanszaak-vat-workflow.md
```

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

This deletes the local Odoo and PostgreSQL volumes.

Install developer checks, or run them through `uvx` without a persistent install:

```bash
uv tool install pre-commit
pre-commit install
uvx pre-commit run --all-files
```

## Deployment

In Coolify, create a Docker Compose application from this repository.

Set real values for required secrets in the Coolify environment variables UI:

```env
SERVICE_USER_POSTGRES=odoo
SERVICE_PASSWORD_POSTGRES=use-a-real-password
```

Optional local setting:

```env
ODOO_PORT=8069
```

Do not commit production environment files.

## First Odoo setup

After the first login:

1. Install Invoicing.
2. Go to Apps -> Update Apps List.
3. Install or update the modules listed in odoo-modules.txt.
4. Confirm the company fiscal localisation is set to Netherlands.
5. Enable the accounting user rights described in
   [`docs/nl-eenmanszaak-vat-workflow.md`](docs/nl-eenmanszaak-vat-workflow.md):
   especially Analytic Accounting and Show Full Accounting Features.
6. Validate that the official Odoo 19 Dutch reports are present before relying
   on the database for VAT filing:

```bash
scripts/check-nl-tax-readiness.sh <database>
```

## Common commands

```bash
docker compose build --pull
docker compose up -d
docker compose logs -f odoo
scripts/update-odoo-modules.sh <database>
scripts/check-nl-tax-readiness.sh <database>
```

With just:

```bash
just build
just up
just logs
just lint
just check
```

Install just with your system package manager, for example:

```bash
brew install just
```

Run all repository checks:

```bash
just lint
```

`just lint` uses `uvx pre-commit run --all-files`, so pre-commit does not need to be installed persistently.

## Updating modules

Installing addon code in the image does not install or upgrade modules in an
Odoo database.

Use the helper script against a staging copy first:

```bash
scripts/update-odoo-modules.sh <database>
```

By default, the script updates the modules listed in:

```text
odoo-modules.txt
```

To update a specific comma-separated module list:

```bash
scripts/update-odoo-modules.sh <database> module_a,module_b
```

Back up PostgreSQL and the Odoo filestore before production module updates.

## Updating Python and OCA dependencies

`requirements.in` is the human-edited dependency input.

`requirements.txt` is the pinned install file used by the Docker image.

Because OCA addon wheels depend on the Odoo runtime provided by the base image,
regenerate and verify the pinned file inside an Odoo image. Do not hand-edit it
casually.

Edit:

```text
requirements.in
```

Then resolve from inside the Odoo base image:

```bash
docker run --rm -v "$PWD:/work" -w /work odoo:19.0 \
  python3 -m pip install --break-system-packages -r requirements.in
```

If the resolver can satisfy the Odoo runtime constraint, compile the lock file
with one chosen tool:

```bash
uv pip compile requirements.in -o requirements.txt
```

or:

```bash
pip-compile -o requirements.txt requirements.in
```

Commit both files and rebuild the image:

```bash
docker compose build --pull
```

Test against a staging database before production deployment when addon packages
or Odoo-related dependencies change.

## Backups

Backups must preserve both:

- PostgreSQL database data.
- Odoo filestore data in /var/lib/odoo.

Before production module updates, back up both the database and the filestore.
Test restores in staging when the accounting database becomes important.

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

See [`docs/nl-eenmanszaak-vat-workflow.md`](docs/nl-eenmanszaak-vat-workflow.md)
for the full workflow and transaction-based acceptance test.

Do not store API keys, database dumps, filestores, or production passwords in
this repository.
