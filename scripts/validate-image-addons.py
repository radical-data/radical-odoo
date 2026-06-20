#!/usr/bin/env python3
"""Validate source and packaged Odoo addons for the deployment image."""

from __future__ import annotations

import argparse
import ast
import importlib.util
from pathlib import Path

SOURCE_ADDONS = {
    "account_invoice_digitize_ai": {
        "required_depends": {"account"},
    },
    "money": {
        "required_depends": {"account", "account_financial_report"},
    },
}

PACKAGED_MODULES = (
    "odoo.addons.account_invoice_digitize_ai",
    "odoo.addons.money",
    "odoo.addons.account_financial_report",
    "odoo.addons.date_range",
    "odoo.addons.report_xlsx",
)


def read_manifest(path: Path) -> dict:
    try:
        value = ast.literal_eval(path.read_text())
    except FileNotFoundError as exc:
        raise SystemExit(f"Missing addon manifest: {path}") from exc
    except (SyntaxError, ValueError) as exc:
        raise SystemExit(f"Invalid addon manifest: {path}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"Manifest is not a dictionary: {path}")
    return value


def validate_source() -> None:
    for addon, rules in SOURCE_ADDONS.items():
        manifest_path = Path("addons") / addon / "__manifest__.py"
        manifest = read_manifest(manifest_path)
        if manifest.get("installable") is not True:
            raise SystemExit(f"{addon} is not installable")
        depends = set(manifest.get("depends", []))
        missing_depends = rules["required_depends"] - depends
        if missing_depends:
            missing = ", ".join(sorted(missing_depends))
            raise SystemExit(f"{addon} is missing required dependencies: {missing}")
        print(
            "Validated source addon:",
            addon,
            manifest.get("name", "-"),
            manifest.get("version", "-"),
        )


def validate_installed() -> None:
    for module in PACKAGED_MODULES:
        if importlib.util.find_spec(module) is None:
            raise SystemExit(f"Missing packaged addon: {module}")
        print("Validated packaged addon:", module)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", action="store_true", help="validate vendored source addons")
    parser.add_argument(
        "--installed", action="store_true", help="validate installed Python modules"
    )
    args = parser.parse_args()
    if not args.source and not args.installed:
        parser.error("choose at least one of --source or --installed")
    if args.source:
        validate_source()
    if args.installed:
        validate_installed()


if __name__ == "__main__":
    main()
