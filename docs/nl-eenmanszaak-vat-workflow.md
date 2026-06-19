# Dutch eenmanszaak VAT workflow

This note documents the intended accounting setup for an NL eenmanszaak doing
international services, funded projects, installations, workshops, artistic work,
technical production, and consultancy.

It is operational documentation for using Odoo as the source of figures for:

- Dutch VAT return / aangifte omzetbelasting
- ICP declaration
- Annual income-tax preparation
- Accountant and audit evidence

It is not tax advice. Confirm edge cases with a Dutch accountant, especially
grants, subsidies, mixed public funding, cultural exemptions, workshops/events,
and unusual cross-border services.

## Position on Odoo 19 and OCA

For Odoo 19, the canonical Dutch tax path is:

```text
l10n_nl
```

That module is part of the Odoo 19 base image and provides the Dutch chart of
accounts, Dutch VAT schema, Dutch tax report, ICP report, and XAF support.

The OCA repository OCA/l10n-netherlands contains useful Dutch modules, but
they must not be added to this deployment unless matching Odoo 19 versions are
available and validated in staging.

Candidate OCA modules to revisit later:

```text
l10n_nl_tax_statement
l10n_nl_tax_statement_icp
l10n_nl_xaf_auditfile_export
```

Until then, use the official Odoo 19 Dutch reports as the filing source and use
OCA `account_financial_report` only for supporting schedules.

## Readiness checks

Run the automated check after installing or upgrading modules:

```bash
scripts/check-nl-tax-readiness.sh <database>
```

This is a guard, not a certification. It verifies the installed modules, active
company, Dutch chart hint, core Dutch tax tags, and likely accounting report
records. Some VAT return totals, such as `5a`, may be implemented as report
lines or computed totals rather than raw tax tags. The transaction matrix below
is the real acceptance test.

## Baseline modules

Use Odoo 19 Community with:

```text
l10n_nl
account_invoice_digitize_ai
date_range
account_financial_report
```

## Required user rights and settings

Some accounting capabilities are hidden by default in Odoo. Before testing or
using the Dutch VAT workflow, enable the relevant rights for the user who will
manage bookkeeping and tax reports.

### Enable developer mode

In Odoo:

```text
Settings -> General Settings -> Activate the developer mode
```

Developer mode makes the deeper user-rights and technical accounting settings
visible.

### Enable accounting powers for the bookkeeping user

In Odoo:

```text
Settings -> General Settings -> Manage Users
```

Then:

```text
Click the user who should manage accounting
Extra Rights
Tick Analytic Accounting
Tick Show Full Accounting Features
Save
```

For the main administrator/bookkeeping user, these should be enabled:

```text
Analytic Accounting
Show Full Accounting Features
```

`Analytic Accounting` is needed to track revenue and costs by project, grant,
workshop, installation, client, or collaboration.

`Show Full Accounting Features` is needed because this deployment is intended
for real bookkeeping, VAT checks, journals, reconciliation, and accountant-grade
reports rather than only simple invoicing.

### Optional but usually useful rights

For the main administrator/bookkeeping user, also consider enabling:

```text
Access Rights
Technical Features
Show Inalterability Features
Multi Currencies, if invoices or bills can be non-EUR
Partial Purchase Deductibility, if mixed/private/non-deductible costs exist
```

Keep these restricted to trusted admin/bookkeeping users. Do not grant broad
technical or accounting rights to portal users, public users, or collaborators
who only need to submit information.

### Fiscal localisation reload

If the Dutch localisation was installed after the database was created, or if
the Dutch reports/taxes do not appear after installing `l10n_nl`, reload the
fiscal localisation from:

```text
Settings -> Invoicing -> Fiscal Localisation -> Reload
```

Treat this as a refresh/recovery step. Do it in staging first if the database
already contains real accounting data.

## Service items

Even when the business does not sell physical products, Odoo should still use
service products for invoice and bill defaults.

Recommended service products:

```text
Workshop / training
Installation / exhibition work
Project fee
Research / consultancy
Artist fee
Technical production
Travel reimbursement
Grant / subsidy income
```

Each service product should have appropriate income or expense accounts and
default taxes. The default tax should be safe for the common case, but every
cross-border invoice or vendor bill must still be reviewed before posting.

## Sales VAT cases

### Dutch client

Typical treatment:

```text
Charge Dutch VAT, usually 21% unless a specific reduced, exempt, or out-of-scope
rule applies.
```

Expected Dutch VAT report area:

```text
1a, or another domestic VAT box depending on the tax used.
```

### EU business client

Typical treatment for B2B services:

```text
No Dutch VAT charged.
VAT reverse-charged to the customer.
Customer VAT number must be present and valid.
```

Expected Dutch VAT report area:

```text
3b
```

Expected additional report:

```text
ICP
```

Reconciliation rule:

```text
Dutch VAT report 3b total = ICP total for the same period
```

Do not use a generic 0% sales tax for EU B2B services unless it feeds the Dutch
VAT report and ICP correctly.

### EU client without a valid VAT number

Do not automatically use reverse charge.

Review whether the client is a taxable person/business and whether the specific
service has a special place-of-supply rule. Workshops, cultural events,
education, admission-like services, and physically located work may require
extra care.

### Non-EU organisation client

Typical treatment for B2B services is often:

```text
No Dutch VAT charged.
Not ICP.
```

The exact Odoo tax and VAT report treatment depends on the service and the tax
mapping. Keep non-EU service income separate from EU reverse-charge service
income.

### Grants and funded projects

Treat these carefully.

Separate:

```text
True grant or subsidy with no direct service supplied to the funder
```

from:

```text
Payment for workshops, research, installation, documentation, consultancy,
artwork, production, or other services
```

Do not create one generic "grant income 0% VAT" tax and use it everywhere.

## Purchase VAT cases

### Dutch supplier

Typical treatment:

```text
Supplier charges Dutch VAT.
Input VAT is reclaimed where deductible.
```

Expected Dutch VAT report area:

```text
5b
```

### EU supplier or collaborator

Typical treatment for B2B services:

```text
Supplier invoices without local VAT.
Reverse-charge Dutch VAT on the vendor bill.
Declare output VAT and input VAT.
```

Expected Dutch VAT report areas:

```text
4b and 5b
```

### Non-EU supplier or collaborator

Typical treatment for B2B services:

```text
Reverse-charge Dutch VAT on the vendor bill.
Declare output VAT and input VAT.
```

Expected Dutch VAT report areas:

```text
4a and 5b
```

## Period-end workflow

For each VAT period:

1. Post all customer invoices.
2. Post all vendor bills and collaborator invoices.
3. Import and reconcile bank statements.
4. Post payroll, depreciation, corrections, and private-use journals if relevant.
5. Review all EU and non-EU sales invoices for correct tax.
6. Review all EU and non-EU vendor bills for correct reverse-charge tax.
7. Generate the Dutch VAT report.
8. Generate the ICP report if there are EU B2B services/supplies.
9. Export supporting reports from `account_financial_report`.

Minimum evidence pack:

```text
Dutch VAT report
ICP report, if applicable
Trial Balance
General Ledger
Open Items
Journal Ledger
Profit & Loss
Balance Sheet
XAF export, when requested
```

Reconcile:

```text
EU B2B services sales = VAT report 3b = ICP total
EU service purchases reverse charge = 4b and matching 5b
Non-EU service purchases reverse charge = 4a and matching 5b
VAT payable/refundable = VAT control account balance after period close
```

## Troubleshooting missing accounting menus

If accounting menus, analytic accounts, tax reports, or journal-level options
are missing, check these first:

```text
Developer mode is active.
The current user has Analytic Accounting enabled.
The current user has Show Full Accounting Features enabled.
The company country is NL.
The fiscal localisation is Netherlands / Netherlands - Accounting.
The fiscal localisation has been reloaded after installing l10n_nl, if needed.
```
