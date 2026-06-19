FROM odoo:19

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    ca-certificates \
    libzbar0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt

RUN python3 -m pip install --break-system-packages --no-cache-dir \
    -r /tmp/requirements.txt

WORKDIR /opt/radical-odoo-deployment

COPY pyproject.toml README.md ./
COPY addons ./addons

RUN set -eux; \
    test -f "addons/account_invoice_digitize_ai/__manifest__.py"; \
    python3 - <<'PY'
import ast
from pathlib import Path
manifest_path = Path("addons/account_invoice_digitize_ai/__manifest__.py")
manifest = ast.literal_eval(manifest_path.read_text())
assert manifest.get("installable") is True, "account_invoice_digitize_ai is not installable"
assert "account" in manifest.get("depends", []), "account_invoice_digitize_ai does not depend on account"
print("Loaded addon:", manifest.get("name"), manifest.get("version"))
PY

RUN python3 -m pip install --break-system-packages --no-cache-dir .

RUN set -eux; \
    python3 - <<'PY'
import importlib.util
for module in (
    "odoo.addons.account_invoice_digitize_ai",
    "odoo.addons.account_financial_report",
    "odoo.addons.date_range",
    "odoo.addons.report_xlsx",
):
    assert importlib.util.find_spec(module), f"Missing packaged addon: {module}"
    print("Loaded packaged addon:", module)
PY

USER odoo
