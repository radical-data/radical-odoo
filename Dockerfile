FROM odoo:19.0

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

COPY pyproject.toml README.md odoo-modules.txt ./
COPY addons ./addons
COPY scripts/validate-image-addons.py ./scripts/validate-image-addons.py

RUN python3 scripts/validate-image-addons.py --source

RUN python3 -m pip install --break-system-packages --no-cache-dir .

RUN python3 scripts/validate-image-addons.py --installed

USER odoo
