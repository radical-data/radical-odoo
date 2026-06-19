#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: scripts/check-nl-tax-readiness.sh <database>" >&2
  exit 2
fi

DB="$1"

docker compose run --rm odoo \
  odoo shell \
  --no-http \
  -d "$DB" << 'PY'
required_modules = [
    "account",
    "l10n_nl",
    "date_range",
    "account_financial_report",
]

optional_modules = [
    "account_invoice_digitize_ai",
]

failed = False
warnings = []


def module_state(name):
    mod = env["ir.module.module"].search([("name", "=", name)], limit=1)
    return mod.state if mod else "missing"


print("Module states")
print("=============")

for name in required_modules:
    state = module_state(name)
    print(f"{name}: {state}")
    if state != "installed":
        failed = True

for name in optional_modules:
    print(f"{name}: {module_state(name)}")


def model_exists(name):
    return name in env.registry

company = env.company
print()
print("Company")
print("=======")
print(f"Name: {company.name}")
print(f"Country: {company.country_id.code or '-'}")

chart_hint = None
if hasattr(company, "chart_template_id"):
    chart_hint = company.chart_template_id.name
elif hasattr(company, "chart_template"):
    chart_hint = company.chart_template

print(f"Chart template: {chart_hint or '-'}")

if company.country_id.code != "NL":
    print("ERROR: Active company country is not NL.")
    failed = True

if chart_hint and "nl" not in chart_hint.lower():
    warnings.append("Chart template does not look Dutch. Review chart and tax mappings.")

print()
print("Dutch tax tags")
print("==============")

tag_names = [
    "1a",
    "1b",
    "1c",
    "1d",
    "2a",
    "3a",
    "3b",
    "4a",
    "4b",
    "5b",
]

for needle in tag_names:
    tags = env["account.account.tag"].search([("name", "ilike", needle)])
    status = "ok" if tags else "missing"
    print(f"{needle}: {status}")
    if not tags:
        failed = True

tags_5a = env["account.account.tag"].search([("name", "ilike", "5a")])
if tags_5a:
    print("5a tag: ok")
else:
    print("5a tag: not found as tag; checking report lines instead")

print()
print("Dutch accounting reports")
print("========================")

report_line_hits = {}

if model_exists("account.report"):
    reports = env["account.report"].search([
        "|", "|", "|", "|",
        ("name", "ilike", "tax"),
        ("name", "ilike", "btw"),
        ("name", "ilike", "omzetbelasting"),
        ("name", "ilike", "icp"),
        ("name", "ilike", "intrastat"),
    ])

    if reports:
        for report in reports:
            print(f"report: {report.name}")
    else:
        warnings.append("No obvious Dutch tax/BTW/ICP account.report found by name.")

    if model_exists("account.report.line"):
        line_model = env["account.report.line"]
        line_fields = line_model._fields

        for needle in ["1a", "3b", "4a", "4b", "5a", "5b"]:
            if "code" in line_fields:
                domain = [
                    "|",
                    ("code", "ilike", needle),
                    ("name", "ilike", needle),
                ]
            else:
                domain = [("name", "ilike", needle)]

            lines = line_model.search(domain)
            report_line_hits[needle] = bool(lines)
            print(f"report line {needle}: {'ok' if lines else 'not found by simple search'}")
else:
    warnings.append("Model account.report is not available. Verify reporting manually.")

if not tags_5a and not report_line_hits.get("5a"):
    warnings.append(
        "5a was not found as a tax tag or by simple report-line search. "
        "This may still be fine if Odoo computes it as a total, but verify the "
        "posted transaction matrix manually."
    )

print()
print("Menu/action hints")
print("=================")

report_actions = env["ir.actions.actions"].search([
    "|", "|", "|", "|",
    ("name", "ilike", "Tax"),
    ("name", "ilike", "BTW"),
    ("name", "ilike", "Aangifte"),
    ("name", "ilike", "ICP"),
    ("name", "ilike", "Intrastat"),
])

if report_actions:
    for action in report_actions[:30]:
        print(f"{action.name} ({action.type})")
else:
    warnings.append(
        "No obvious Tax/BTW/Aangifte/ICP/Intrastat actions found by simple "
        "name search. This may be fine if Odoo exposes them through generic "
        "Accounting report menus."
    )

print()
print("Acceptance test still required")
print("==============================")
print("Post the six staging invoices/bills and verify:")
print("- NL 21% sale appears in domestic VAT, normally 1a.")
print("- EU B2B service sale appears in 3b and ICP.")
print("- Non-EU service sale does not appear in ICP.")
print("- EU supplier reverse charge appears in 4b and 5b.")
print("- Non-EU supplier reverse charge appears in 4a and 5b.")
print("- Dutch supplier input VAT appears in 5b.")
print("- XAF export works from the General Ledger.")

if warnings:
    print()
    print("Warnings")
    print("========")
    for warning in warnings:
        print(f"WARNING: {warning}")

if failed:
    raise SystemExit("Dutch tax readiness check failed.")

print()
print("Dutch tax readiness check passed.")
PY
