# Contributing to AI Invoice Digitization

Thank you for your interest in contributing to AI Invoice Digitization for Odoo.

## Development Setup

### Prerequisites

- Python 3.10+
- A working Odoo 16, 17, 18, or 19 installation
- Git

### Getting Started

1. Clone the repository into your Odoo addons path:
   ```bash
   git clone <repository-url> account_invoice_digitize_ai
   ```

2. Install the module in your Odoo development instance.

3. No additional Python packages are required -- the module uses only Odoo's standard dependencies.

## Code Style

- Follow [Odoo coding standards](https://www.odoo.com/documentation/18.0/contributing/development/coding_guidelines.html) and PEP 8
- Use Odoo's `logging` module: `_logger = logging.getLogger(__name__)`
- Prefix all module-specific model methods with `_ai_` (e.g., `_ai_trigger_extraction()`)
- Use `_()` for all user-facing strings (i18n)
- Keep prompt templates in constants or data files, not inline

### Linting

```bash
ruff check --select E,W,F,C901,PLR0911,PLR0915,PLR0912 --ignore E501 .
```

## Testing

### Running Tests

```bash
# Run all module tests
./odoo-bin -d test_db -i account_invoice_digitize_ai --test-enable --stop-after-init

# Run a specific test class
./odoo-bin -d test_db -i account_invoice_digitize_ai --test-tags /account_invoice_digitize_ai:TestExtraction --stop-after-init
```

### Writing Tests

- Use `odoo.tests.common.TransactionCase` for tests needing clean rollback
- Tag tests with `@tagged('post_install', '-at_install')`
- Mock all external HTTP calls -- tests must never call real APIs
- Use `unittest.mock.patch` for mocking
- Create realistic mock responses covering success, partial, low confidence, and error cases
- Target minimum 80% code coverage

## Pull Request Process

1. Create a feature branch from the target Odoo branch (`19.0` or `18.0`):
   ```bash
   git checkout -b feature/your-feature-name 19.0
   ```

2. Make your changes following the code style guidelines above.

3. Add or update tests for your changes.

4. Update documentation if needed:
   - Update `ARCHITECTURE.md` if you add, rename, or remove files
   - Update `CHANGELOG.md` under the `[Unreleased]` section
   - Update `README.md` if your change affects usage or configuration

5. Ensure all tests pass and linting is clean.

6. Submit a pull request with a clear description of the changes.

## Commit Messages

- Use clear, concise commit messages
- Start with a verb in imperative mood: "Add", "Fix", "Update", "Remove"
- Reference issues when applicable: "Fix extraction timeout (#42)"

## Reporting Issues

- Use the GitHub issue tracker
- Include: Odoo version, module version, steps to reproduce, expected vs actual behavior
- For extraction issues: include the invoice type (PDF text, scanned, Factur-X) and the AI model used
- Do not include API keys, invoice content, or other sensitive data in issue reports

## Architecture Notes

- Read `ARCHITECTURE.md` for the current project structure
- The module must work on Odoo Community and Enterprise, versions 16-19
- Zero external Python dependencies -- use only Odoo's standard libraries
- All AI failures must be graceful -- never crash the invoice workflow
