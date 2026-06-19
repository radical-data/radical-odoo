# Architecture - account_invoice_digitize_ai

## Project Structure

```
account_invoice_digitize_ai/
├── __manifest__.py                    # Odoo module manifest (version, dependencies, data files)
├── __init__.py                        # Python package init (imports models)
├── ARCHITECTURE.md                    # This file - project structure reference
├── CHANGELOG.md                       # Version history and release notes
├── CONTRIBUTING.md                    # Contribution guidelines
├── LICENSE                            # GNU Affero General Public License v3.0 (AGPL-3)
├── README.md                          # Project documentation
├── .gitignore                         # Git ignore rules
├── ruff.toml                          # Ruff linter/formatter configuration
├── .github/
│   └── workflows/
│       └── ci.yml                     # CI: ruff lint + Odoo 19 test suite (push/PR)
│
├── models/
│   ├── __init__.py                    # Models package init
│   ├── res_config_settings.py         # Settings: AI service, API key, model, extraction mode, recognition service, cost estimate, confidence, auto-apply, rounding correction, learning toggle, accounting detection, optional dependency detection
│   ├── account_move.py                # ORM extension: fields, computed fields, email integration, button actions, rate limiting — account.move
│   ├── ai_auto_apply.py               # Auto-apply policy: vendor reliability, confidence thresholds, eligibility checks — account.move extension
│   ├── ai_extraction_engine.py        # Extraction pipeline: config, orchestration, Factur-X shortcut, API call, vision retry — account.move extension
│   ├── ai_cron_processor.py           # Background extraction cron: queue processing, single extraction — account.move extension
│   ├── ai_field_mapper.py             # Field mapping: apply extraction, header/currency/PO matching, safety checks — account.move extension
│   ├── ai_line_builder.py             # Line building: invoice lines, account resolution, correction tracking — account.move extension
│   ├── ai_rounding_fixer.py           # TTC rounding correction: adjust or add line strategy — account.move extension
│   ├── ai_document_builder.py         # Document preparation: text/metadata/QR/tables, prompt building, extraction log — account.move extension
│   ├── ai_preprocess_pipeline.py      # Pre-processing pipeline (Azure DI, AWS Textract) — account.move extension
│   ├── ai_extraction_log.py           # Debug extraction logs (prompt, response, tokens, cost, error message, dashboard metrics)
│   ├── ai_document.py                 # Document processing utilities (PDF text, Factur-X detection, VAT, table extraction, language detection)
│   ├── ai_facturx_parser.py           # Factur-X / ZUGFeRD CII XML parser (all profiles, zero AI cost)
│   ├── ai_qr_decoder.py              # QR code extraction + SPC/EPC parsing + reference validation + prompt context
│   ├── ai_matcher.py                  # Matching facade + VendorMatchCache + payment term matching
│   ├── ai_matcher_partner.py          # Partner matching: VAT, name, email, token-based fuzzy
│   ├── ai_matcher_tax.py              # Tax matching: vendor history, exact/approximate rate
│   ├── ai_matcher_account.py          # Account matching: 5-tier strategy, category→prefix mapping
│   ├── ai_matcher_po.py               # Purchase order matching (3-tier: exact/fuzzy/amount) + product matching (supplierinfo/default_code)
│   ├── ai_validator.py                # Mathematical cross-validation of extracted amounts
│   ├── ai_provider.py                 # Abstract base class + factory for AI providers (shared get_available_models/estimate_cost)
│   ├── ai_provider_anthropic.py       # Claude/Anthropic provider (tool_use structured output, default)
│   ├── ai_provider_openai.py          # OpenAI/GPT provider (function calling + strict mode, vision)
│   ├── ai_provider_google.py          # Google/Gemini provider (functionDeclarations, inlineData vision)
│   ├── ai_provider_xai.py            # xAI/Grok provider (inherits OpenAI — OpenAI-compatible API)
│   ├── ai_provider_deepseek.py       # DeepSeek provider (inherits OpenAI — OpenAI-compatible API, no vision)
│   ├── ai_provider_mistral.py        # Mistral AI provider (inherits OpenAI — OpenAI-compatible API)
│   ├── ai_provider_local.py          # Local/self-hosted provider placeholder (Ollama, vLLM, LM Studio)
│   ├── ai_prompt.py                   # Prompt templates, JSON schema, multilingual label constants
│   ├── ai_preprocessor.py             # Abstract base class + factory for document pre-processors
│   ├── ai_preprocessor_normalize.py   # Shared normalization builders for extraction results (vendor, buyer, lines…)
│   ├── ai_preprocessor_azure.py       # Azure Document Intelligence pre-processor (prebuilt-invoice, async poll)
│   ├── ai_preprocessor_aws.py         # AWS Textract pre-processor (AnalyzeExpense API, field normalization)
│   ├── ai_aws_sigv4.py                # AWS Signature Version 4 signing (pure Python, stdlib hmac/hashlib)
│   ├── ai_fiscal_context.py           # Fiscal context builder with daily cache (accounts, taxes, vendor history)
│   ├── ai_fiscal_cache_invalidator.py # Invalidates fiscal cache on account/tax create/write
│   ├── ai_document_qualifier.py       # Document qualification heuristics (invoice/proforma/paid keyword scan)
│   ├── ai_duplicate_detector.py       # Duplicate invoice detection (partner + ref + date + amount, company-scoped)
│   ├── ai_anomaly_detector.py         # Amount anomaly detection (vendor history comparison, company-scoped)
│   ├── ai_vendor_memory.py            # Per-vendor per-company learning memory (corrections, auto-apply, line-level account overrides)
│   └── ai_vendor_score.py            # Per-vendor per-company extraction reliability scoring
│
├── wizards/
│   ├── __init__.py                    # Wizards package init
│   ├── ai_batch_extract_wizard.py    # Batch AI extraction for multiple invoices (progressive commit)
│   ├── ai_memory_wizard.py           # Export/import vendor memory (JSON, TransientModel)
│   ├── ai_preview_line.py           # Preview line items (read-only TransientModel child of preview wizard)
│   ├── ai_preview_wizard.py          # Extraction preview before applying to invoice
│   ├── ai_test_wizard.py            # Test extraction: router, full pipeline mode, shared helpers (settings button)
│   ├── ai_test_text_extraction.py   # Test wizard — text extraction mode (no API call)
│   ├── ai_test_preprocessing.py     # Test wizard — document recognition mode (Azure DI / AWS Textract)
│   └── ai_test_prompt_preview.py    # Test wizard — prompt preview mode (zero cost)
│
├── views/
│   ├── res_config_settings_views.xml  # Settings UI: document recognition, invoice analysis, estimated cost, post-processing, advanced options
│   ├── account_move_views.xml         # Invoice form: button, status, confidence, warnings, extraction summary
│   ├── ai_extraction_log_views.xml    # Extraction log list/form/search views + root menu "AI Digitization"
│   ├── ai_vendor_memory_views.xml     # Vendor memory list/form/search views + menu
│   ├── ai_vendor_score_views.xml     # Vendor reliability score list/graph/form/search views + menu
│   ├── ai_dashboard_views.xml        # Graph/pivot view definitions for extraction logs and vendor scores (used by view switcher)
│   ├── ai_memory_wizard_views.xml   # Export/import vendor memory wizard forms + actions
│   ├── ai_batch_extract_views.xml  # Batch extraction wizard form + server action binding
│   ├── ai_preview_wizard_views.xml # Extraction preview wizard form
│   └── ai_test_wizard_views.xml   # Test extraction wizard form + action
│
├── security/
│   ├── ir.model.access.csv            # Model access rights (per group)
│   └── security.xml                   # Security groups and record rules
│
├── data/
│   └── data.xml                       # Default config values, mail.alias for email integration, AI extraction cron
│
├── i18n/
│   └── fr.po                        # French translation
│
├── static/
│   ├── description/
│   │   ├── icon.svg                 # Module icon (SVG placeholder, convert to PNG for store)
│   │   ├── index.html               # Module description page for Odoo Apps Store
│   │   └── pipeline.svg             # Extraction pipeline flowchart (used in README)
│   └── src/
│       ├── js/
│       │   ├── confidence_widget.js   # OWL field widget: colored confidence badge
│       │   └── extraction_status_widget.js  # OWL widget: polls extraction status every 5s, auto-opens wizard on completion
│       ├── xml/
│       │   ├── confidence_widget.xml  # QWeb template for confidence widget
│       │   └── extraction_status_widget.xml  # QWeb template for extraction status polling widget
│       └── css/                     * # Custom styles (if needed)
│
└── tests/
    ├── __init__.py                    # Tests package init
    ├── test_extraction.py             # Extraction pipeline, partner/tax matching, cross-validation, providers
    ├── test_config.py                 # Settings fields, defaults, cost estimate
    ├── test_correction_tracking.py    # Correction tracking: header/line corrections, snapshot lifecycle, account resolution
    ├── test_field_mapping.py          # Field mapping: map_invoice_fields, currency, partner overrides, pre-identify vendor, PO matching
    ├── test_vendor_memory.py          # Vendor memory: corrections, auto-apply, context, detection
    ├── test_vendor_score.py           # Vendor score: reliability rate, degradation, constraints
    ├── test_duplicate_detection.py    # Duplicate invoice detection: exact, partial, edge cases
    ├── test_anomaly_detection.py      # Amount anomaly detection: high, low, insufficient history
    ├── test_document_qualification.py # Document qualification: invoice/proforma/paid/empty/multilingual
    ├── test_iban_validation.py        # IBAN checksum validation: valid/invalid formats, mod-97
    ├── test_buyer_verification.py     # Buyer verification: VAT/name match against Odoo company
    ├── test_number_format.py          # Number format detection: comma/dot decimal, ambiguous, edge cases
    ├── test_pdf_metadata.py           # PDF metadata extraction: valid, empty, errors, special chars
    ├── test_table_extraction.py       # Table extraction: pdfplumber, validation, multi-page merge, markdown
    ├── test_email_integration.py      # Email integration: message_new, partner matching, auto-extract
    ├── test_multi_company.py          # Multi-company isolation: memory, score, detectors
    ├── test_memory_wizard.py          # Export/import wizard: JSON export, import, vendor matching
    ├── test_payment_terms.py          # Payment terms matching: exact, partial, day-count, vendor history
    ├── test_account_matching.py       # Account matching: vendor history, category mapping, fallback, category vs vendor default priority, VendorMatchCache
    ├── test_product_matching.py       # Product matching: supplier info, default_code, vendor-specific
    ├── test_facturx.py                # Factur-X XML parser: valid, credit note, invalid, lines
    ├── test_facturx_pipeline.py       # Factur-X pipeline: shortcut skips AI, wrapper dict, non-PDF, unavailable, apply, preview
    ├── test_batch_extraction.py       # Batch extraction wizard: counts, skip, processing, no API key
    ├── test_robustness.py             # Edge cases: falsy values, Factur-X failure, thread safety, race conditions, rounding
    ├── test_pipeline_guards.py        # Pipeline guards: vision retry, line validation, rate limiting, page limit
    ├── test_po_matching.py            # Purchase order matching: normalization, exact/fuzzy/amount, line matching, Factur-X
    ├── test_preprocessing.py          # Pre-processing: Azure/AWS normalization, pipeline modes, SigV4
    ├── test_providers.py              # Multi-provider: OpenAI, Google Gemini, xAI, DeepSeek, Mistral extract/validate/convert/factory
    ├── test_structured_outputs.py     # Structured outputs: tool_use, network retry, prompt estimation, test wizard
    ├── test_extraction_modes.py       # Extraction modes: guided/simplified/free context, matching, warnings, learning
    ├── test_customer_invoices.py      # Customer invoice extraction: button visibility, free mode enforcement, credit note, skip learning
    ├── test_lightweight_log.py        # Lightweight extraction log: create log without debug, vendor/confidence/duration
    ├── test_token_matching.py         # Token-based fuzzy partner matching: normalize, score, abbreviated/reordered names
    ├── test_extraction_cache.py       # Extraction result cache: populate, reuse, invalidate on attachment change, re-extract
    ├── test_async_extraction.py       # Async extraction via ir.cron: queue, cron process, failure, sync fallback, stale marking, batch size, re-extract
    ├── test_fiscal_context.py         # Fiscal context cache: build, hit, selective/full invalidation, stale eviction, tax-only mode
    ├── test_language_detection.py     # Language detection: keyword heuristic, 7 languages, prompt injection
    ├── test_account_learning.py       # Account learning: line corrections, vendor memory override, tier 0 resolution
    ├── test_auto_apply.py             # Auto-apply: confidence threshold, vendor reliability, warnings, doc type
    ├── test_vendor_resolution.py      # Vendor resolution: preview wizard partner selection/creation, force partner
    ├── test_polling.py                # Polling widget: status transitions, auto-open wizard, interval
    ├── test_edge_cases.py              # Edge cases: credit note types, validator penalties (arithmetic/sum), reverse charge warnings
    ├── test_prompt_schema.py           # Prompt schema: JSON schema integrity, template formatting, category enum consistency, keyword lists
    ├── test_qr_extraction.py          # QR code extraction: SPC/EPC parsing, reference validation, cross-validation, context, PDF pipeline
    └── test_vision_retry.py           # Vision retry edge cases: retry decision logic, failure count, already vision, image input, data replacement
```

## Key Architecture Decisions

### AI Provider Abstraction

All AI providers implement a common `AIProvider` interface. The extraction pipeline calls the active provider without knowing HTTP specifics. Adding a new provider requires only a new `ai_provider_xxx.py` file.

### Text-First Strategy

PDFs are always processed text-first (cheaper). Vision mode (image) is used only as fallback for scanned documents or when text extraction produces garbled results.

### Factur-X Shortcut

Structured invoices (Factur-X / ZUGFeRD) bypass AI entirely — data is extracted from embedded CII XML, parsed into the same format as Claude's response, and fed through the standard extraction pipeline at zero cost.

### Vision Retry Fallback

When text-mode extraction fails cross-validation (≥2 failures), the pipeline automatically retries in vision mode (PDF pages as images). This catches garbled text extraction from poorly structured PDFs.

### Extraction Preview

Single invoice extraction shows a preview dialog before applying data. Users see vendor, reference, date, total, line items, and warnings — then choose to apply or discard.

### Fiscal Context Cache

Company-level fiscal data (expense accounts, purchase taxes) is cached in a module-level dict with daily TTL. Vendor-specific history remains uncached (varies per vendor).

### Batch Processing

Users can select multiple invoices in list view and trigger batch extraction via a server action bound to `account.move`. Each extraction is committed individually to save progress.

### Product Matching

Extracted vendor product codes are matched against `product.supplierinfo` (vendor-specific, then any vendor) and `product.product` (internal reference) for automatic product assignment.

### Document Recognition Abstraction

Optional external document recognition providers (Azure Document Intelligence, AWS Textract) implement a common `DocumentPreprocessor` interface. Three modes: Full recognition (AI as backup), Combined (recognition + AI cross-check), Text extraction only (AI analyzes the result). User-configurable in settings.

### Structured Outputs (tool_use)

The Anthropic provider uses the tool_use API with a JSON Schema (`EXTRACTION_TOOL_SCHEMA`) to guarantee valid JSON output. Claude is forced to return data matching the schema exactly, eliminating all JSON parsing failures. A legacy text-based fallback is preserved for backward compatibility.

### Resilient Network Handling

All HTTP calls to AI providers retry on transient errors (timeout, connection reset, 5xx server errors) with exponential backoff (max 3 attempts). Rate-limit (429) retries were already present; v0.0.12 extends this to all transient failures.

### Purchase Order Matching (Optional)

When the `purchase` module is installed, extracted PO references are matched against `purchase.order` records (3-tier: exact, fuzzy, amount/date). Invoice lines are linked to PO lines via `purchase_line_id`. When `purchase` is not installed, PO references are still extracted and displayed but not matched. Runtime detection via `'purchase.order' in env` — no hard dependency added.

### Zero External Dependencies

The module uses only Odoo's standard Python dependencies (`requests` for HTTP). No `pip install` required. AWS SigV4 signing is implemented in pure Python (stdlib `hmac`, `hashlib`).

### Server Mode Only (v1)

API calls are made from the Python backend. Odoo Online (SaaS) support is deferred to v2.

### Multi-Company Isolation

Vendor memory, vendor scores, anomaly detection, and duplicate detection are all scoped per company. Each company maintains its own correction history and reliability scores. API settings (key, model) are shared globally.

### Always-On Lightweight Log

`ai.extraction.log` is created for every extraction (not just debug mode). Lightweight fields (vendor name, confidence, duration, provider, mode) are always populated. Full prompt/response content is only stored when debug mode is enabled.

### Token-Based Fuzzy Partner Matching

When exact and ilike name matching fail, a token-based fuzzy matcher (tier 2b) normalizes company names by removing legal suffixes (SA, GmbH, Ltd, etc.) and scores candidates using asymmetric Jaccard similarity. Handles abbreviated and reordered names.

### Extraction Result Cache

Last extraction result is cached on `account.move` (`ai_last_extraction_data` + `ai_last_extraction_attachment_id`). If the user clicks "Discard" then "Digitize" again with the same attachment, the cached result is reused without calling the API. Cache is invalidated on attachment change. "Re-extract" button forces a fresh API call.

### Async Extraction (ir.cron)

Optional background extraction mode: clicking "Digitize" queues the invoice (`ai_extraction_queued_at`), and a cron job (every 30s) processes the queue. No `bus.bus` dependency — user refreshes the page to see results. Falls back to synchronous mode when disabled.

### Language Detection

`detect_language()` in `ai_document.py` uses keyword-based heuristics to identify the document language among 7 candidates (fr, de, en, es, it, nl, pt). The detected language is injected into the prompt context alongside number format, helping the AI interpret ambiguous terms and date formats correctly.

### Account Learning via Vendor Memory

`ai.vendor.memory` stores line-level account corrections (`line_description` field). When a user changes an account on an invoice line, the `write()` override detects the correction and records it via `record_line_correction()`. On subsequent extractions, `_ai_resolve_account()` checks for matching overrides via `get_account_override()` — a tier 0 priority that takes precedence over all other account matching strategies in `_ai_build_line_vals()`.

### Auto-Apply High Confidence

When enabled (`ai_auto_apply_enabled` + `ai_auto_apply_min_confidence` in settings), extractions that meet strict criteria are applied automatically without user preview. `_ai_can_auto_apply()` checks: vendor matched and reliable (≥3 extractions, ≥70% success rate via `_ai_is_vendor_reliable()`), all field confidences above threshold, no warnings, and valid document type. Applied in both synchronous and async extraction paths.

### Client-Side Polling Widget

OWL component `AiExtractionStatusWidget` (`extraction_status_widget.js`) replaces the static statusbar for extraction status in `account_move_views.xml`. When status is `processing`, the widget polls the server every 5 seconds. On completion, it automatically opens the preview wizard, providing a seamless async extraction experience without requiring manual page refresh.

### QR Code Extraction (Swiss QR-bill + EPC QR)

`ai_qr_decoder.py` is a pure utility module (not an Odoo model) that extracts QR codes from PDF images using `pyzbar` (optional dependency, same pattern as `pdfplumber`). Supports two payment QR formats: Swiss QR-bill (SPC/0200) and EPC/BCD (SCT). QR data (IBAN, amount, currency, reference) is injected into the AI prompt as high-confidence context and cross-validated against the AI extraction result. IBAN and currency are overridden by QR when they conflict; amount mismatch is penalized but not overridden. Configurable in settings (enabled by default).

### TTC Rounding Correction

When invoices display TTC prices (common on receipts), converting each line to HT (`price / (1 + rate)`) introduces rounding at each line. Over N lines, this can accumulate into a small gap between the extracted total and Odoo's computed total. `_ai_fix_rounding_gap()` in `ai_rounding_fixer.py` detects this after line creation: if the gap is within the configured tolerance (default 0.01), it adjusts the highest-priced line's `price_unit` to close the gap, then verifies Odoo's recomputed total improved. If not, the adjustment is rolled back. Configurable in settings (`ai_rounding_correction` + `ai_rounding_tolerance`), enabled by default.

### Single Codebase, Multi-Version

One codebase supports Odoo 16, 17, 18, and 19 with version-conditional logic abstracted behind helper methods. Odoo 19 breaking changes (company_id→company_ids, deprecated→active, journal_id NOT NULL, read-only model attributes, message_new API) are handled via runtime field detection.
