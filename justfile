set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

default:
	@just --list

build:
	docker compose build --pull

up:
	docker compose up -d

down:
	docker compose down

reset:
	docker compose down -v

logs:
	docker compose logs -f odoo

check:
	uvx pre-commit run ruff-check --all-files
	uvx pre-commit run shellcheck --all-files
	uvx pre-commit run hadolint-docker --all-files
	uvx pre-commit run check-json --all-files
	uvx pre-commit run check-toml --all-files
	uvx pre-commit run check-yaml --all-files
	uvx pre-commit run check-merge-conflict --all-files
	uvx pre-commit run check-case-conflict --all-files
	uvx pre-commit run check-added-large-files --all-files

# Auto-fix what tools can fix. Diagnostic hooks may still fail.
fix:
	uvx pre-commit run --all-files

lint: check

format: fix

ci: check build

update-modules database modules="":
	if [ -n "{{modules}}" ]; then \
		scripts/update-odoo-modules.sh "{{database}}" "{{modules}}"; \
	else \
		scripts/update-odoo-modules.sh "{{database}}"; \
	fi
