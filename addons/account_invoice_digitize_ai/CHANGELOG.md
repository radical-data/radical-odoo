# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [19.0.0.3.1] - 2026-06-11

### Fixed

- **Fresh install failure** (`ValueError: External ID not found: account_invoice_digitize_ai.ai_memory_export_action`): the `ai.vendor.memory` list header references the memory export/import actions, but `views/ai_memory_wizard_views.xml` (which defines them) was loaded _after_ `views/ai_vendor_memory_views.xml` in the manifest. On a clean database the actions did not yet exist, so installation aborted. Reordered the manifest so the wizard file loads first. This long-standing bug only surfaced on first-time installs (upgrades on an existing database masked it); it is now caught by the new fresh-install CI.
- **Time-dependent anomaly test**: `test_anomaly_detection` seeded history with a hard-coded `2024-06-01` date that has now fallen outside the detector's 730-day lookback window, so the test silently broke as time passed. It now seeds with a date relative to today.

## [19.0.0.3.0] - 2026-06-11

### Added

- **Claude Opus 4.8** model option for the Anthropic provider (maximum accuracy, latest) — same pricing as Opus 4.6 ($5 / $25 per 1M tokens). The Fable/Mythos family is intentionally not offered: those models force adaptive thinking on, which rejects the forced `tool_use` this provider relies on for guaranteed-valid JSON.
- **Anthropic prompt caching**: the tool schema and system prompt (chart of accounts, taxes, vendor memory) are now sent with a cache breakpoint, cutting input-token cost by up to ~90% and reducing latency on same-vendor batches. Cache misses degrade gracefully to full price.
- **Continuous integration**: GitHub Actions workflow (`.github/workflows/ci.yml`) running `ruff check` and the module test suite on Odoo 19 for every push and pull request.

### Changed

- **License**: switched from OPL-1 (Odoo Proprietary License v1.0) to AGPL-3 (GNU Affero General Public License v3.0)
- **Optional dependencies no longer block installation**: `pdfplumber`, `facturx` and `pyzbar` were removed from the manifest's `external_dependencies`. They are guarded by graceful `try/except` imports with `*_AVAILABLE` flags, so the module now installs without them (matching the zero-mandatory-dependency design and avoiding a hard failure when `pyzbar`'s system library `libzbar0` is absent).
- **Retry backoff** now honors the provider's `Retry-After` header on HTTP 429/503 responses (clamped to 1–60s), falling back to exponential backoff when the header is absent.

### Fixed

- **Background extraction concurrency**: the cron queue now claims its batch with `FOR UPDATE SKIP LOCKED`, preventing a concurrent manual extraction or trigger from processing the same invoice twice.
- **Documentation**: corrected an inaccurate statement that API keys are "stored encrypted" — they are stored in plaintext in `ir.config_parameter`; added guidance on restricting Settings access, infrastructure-level encryption, and scoping the provider key.

## [19.0.0.2.2] - 2026-02-27

### Added

- **DeepSeek provider** (`ai_provider_deepseek.py`): DeepSeek Chat (fast, very affordable), DeepSeek Reasoner (chain-of-thought) — inherits OpenAI-compatible implementation, no vision support
- **Mistral AI provider** (`ai_provider_mistral.py`): Mistral Small 3.2, Mistral Medium 3.1, Mistral Large 3 — inherits OpenAI-compatible implementation, vision support
- **Preview line items**: extraction preview wizard now displays a read-only table of extracted line items (description, quantity, unit price, tax rate, subtotal) before applying — `ai.preview.line` TransientModel with One2many relation
- **Error message field**: `error_message` on `ai.extraction.log` — captures failure reason in extraction log list/form views
- **Reverse charge warning**: orange banner on invoice form when AI detects reverse charge / auto-liquidation — `ai_reverse_charge_warning` computed field
- **Optional dependency UX**: settings page shows installation status for optional libraries (`pyzbar`, `pdfplumber`, `facturx`) with install instructions when missing — 3 computed booleans on `res.config.settings`
- Unit tests: `test_providers.py` — 14 new tests (7 DeepSeek + 7 Mistral: provider name, endpoint, models, vision, extract, cost) + 2 factory tests
- Unit tests: `test_fiscal_context.py` — 7 tests (cache build, cache hit, selective/full invalidation, stale day eviction, tax-only mode)
- Unit tests: `test_async_extraction.py` — 3 new tests (cron stale item marking, batch size limit of 5, re-extract cache clearing)
- Unit tests: `test_facturx_pipeline.py` — 6 tests (Factur-X pipeline shortcut, wrapper dict, non-PDF, unavailable, apply, preview)
- Unit tests: `test_vision_retry.py` — 6 tests (vision retry decision logic: failure count, already vision, image input, trigger, data replacement)

### Fixed

- **XXE protection**: Factur-X XML parser now uses `resolve_entities=False, no_network=True` to prevent XML External Entity injection
- **JSON import size limit**: vendor memory import wizard rejects files larger than 5 MB and validates JSON syntax before processing
- **Provider allowlist**: `_ai_get_config()` validates provider names against a hardcoded allowlist; unknown values fall back safely
- **Config parameter validation**: confidence thresholds clamped to 0.0–1.0, rounding tolerance cannot be negative, learning threshold ≥ 1
- **Cost estimate fallback**: shows "Unable to estimate" instead of blank field when computation fails
- **Confirmation dialogs**: Re-extract and Batch Extract buttons now require user confirmation before running
- **Silent QR decoder exceptions**: added `_logger.debug()` to previously silent `except: pass` blocks in QR image extraction
- **QR IBAN injection**: use cleaned IBAN (stripped spaces/dashes) when injecting QR IBAN into extraction data, preventing mod-97 validation failures
- **Vision retry QR data**: re-extract QR codes from PDF and inject into cross-validation during vision retry (was missing, causing IBAN/amount overrides to be skipped)
- **Silent line rejection**: log warning when all extracted lines are rejected during line building (was silently empty)
- **Line sum tolerance cap**: proportional tolerance for large invoices capped at 5.00 (a 100k invoice previously had tolerance of 100.00)
- **Division by zero guard**: `_resolve_line_qty_price` now handles edge case where `tax_rate = -100%`
- **Auto-apply threshold**: `_ai_can_auto_apply` catches `ValueError` on invalid config parameter for minimum confidence
- **Attachment field ondelete**: `ai_last_extraction_attachment_id` uses `ondelete='set null'` (was default cascade)
- **Debug log truncation**: prompt/response fields in extraction log truncated at 50 KB to prevent oversized records
- **Confidence widget title**: now translatable via `_t()` instead of hardcoded English string
- **Memory wizard result**: `result_message` uses `self.env._()` for translatable feedback
- **Cron proforma status**: cron no longer overwrites `'done'` status with `'failed'` when proforma documents return `None` from extraction
- **Auto-apply threshold (int)**: `_get_auto_apply_threshold` catches `ValueError` on non-integer ICP value (prevents invoice save crashes)
- **Memory import wizard**: validates JSON is a list, validates `field_name` against whitelist, uses `.get()` to prevent `KeyError` on malformed entries
- **XSS defense**: rounding note escapes AI-controlled line description via `markupsafe.escape()` before `message_post`
- **Sync extraction safety net**: `_ai_extract_sync` ensures status is not stuck at `'processing'` when extraction returns `None`
- **Factur-X pipeline crash**: `_ai_run_pipeline` returned Factur-X XML string instead of wrapper dict — consumer tried `data['_facturx']` on a string, causing `TypeError`
- **N+1 browse in correction tracking**: `_ai_detect_line_corrections` re-browsed the move record instead of using the existing recordset
- **Cron auto-apply safety**: `_ai_cron_extract_one` now wraps auto-apply in `try/except` to prevent cron crash on apply failure
- **Prompt injection**: vendor name/VAT sanitization now strips `\r\n` in addition to angle brackets and special characters
- **Vendor memory ordering**: `record_correction` search now uses deterministic `order='correction_count desc, id desc'`
- **Wizard vendor name**: `_apply_vendor_creation` now validates vendor name is not empty after strip
- **IBAN validation clarity**: replaced magic number `55` with `ord('A') - 10` for readability
- **Test fix**: `test_line_sum_match_no_warning` now patches the correct logger (`ai_line_builder` instead of `account_move`)

### Changed

- **Deduplicated `validate_api_key`**: extracted shared `_validate_with_request()` method into `AIProvider` base class, reducing ~90 lines of duplicated code across 3 providers
- **Deduplicated `get_available_models`/`estimate_cost`**: moved identical implementations from 3 providers into `AIProvider` base class via `self.MODELS` attribute
- **MAX_OUTPUT_TOKENS constant**: extracted `MAX_OUTPUT_TOKENS = 8192` on `AIProvider` ABC — providers reference `self.MAX_OUTPUT_TOKENS` instead of duplicating magic numbers
- **M2M optimization**: vendor tax lookup in `get_vendor_taxes` uses `mapped('tax_ids').ids` instead of per-line iteration
- **Refactor**: extract `ai_auto_apply.py` — auto-apply policy (`_ai_can_auto_apply`, `_ai_is_vendor_reliable`) from `account_move.py` into dedicated `_inherit` module
- **Refactor**: extract `ai_rounding_fixer.py` — TTC rounding correction (4 methods) from `ai_line_builder.py` into dedicated `_inherit` module
- **Refactor**: split `ai_test_wizard.py` — extract 3 test modes (text extraction, preprocessing, prompt preview) into dedicated `_inherit` modules, keeping router + full pipeline + shared helpers
- **Provider factory refactor**: `get_provider()` replaced with dict-based `_import_provider()` registry to support 7 providers while staying under PLR0911 (max 6 return statements)
- **Provider count**: 4 → 6 active AI providers (Anthropic, OpenAI, Google, xAI, DeepSeek, Mistral)
- **JS widget cleanup**: removed unused `useState`/`state.polling` from extraction status widget, added `console.warn` on polling errors

## [19.0.0.2.0] - 2026-02-26

### Changed

- **Settings page reorganization**: 5 logical sections — Document Recognition, Invoice Analysis, Estimated Cost, Post-processing, Advanced Options
- **Menu consolidation**: single "AI Digitization" parent menu under Configuration with 3 entries (Extraction Logs, Vendor Memory, Vendor Scores) — removed 3 redundant dashboard shortcuts (Cost & Volume, Token Statistics, Vendor Accuracy) since graph/pivot views are accessible via the view switcher
- **Account matching tier reorder**: category mapping (AI-suggested category → account prefix) now takes priority over vendor default account. Ensures that shipping/consulting lines get appropriate accounts even when a vendor's history is dominated by merchandise (e.g. `shipping` → 6241 instead of vendor default 607)
- **Refactor**: split `ai_matcher.py` (620 lines, 4 domains) into `ai_matcher_partner.py` (~120 lines), `ai_matcher_tax.py` (~90 lines), `ai_matcher_account.py` (~180 lines); `ai_matcher.py` remains as facade (~130 lines) with `VendorMatchCache` and payment term matching
- **Refactor**: extract cron methods (`_ai_cron_process_queue`, `_ai_cron_extract_one`) from `account_move.py` into dedicated `ai_cron_processor.py` (~65 lines) via `_inherit` pattern
- **Refactor**: reduce `_ai_call_and_validate()` from 11 to 6 parameters by passing `cfg` dict and storing `raw_data`/`mimetype` in `doc_info`; same pattern applied to `_ai_call_provider()` and `_ai_retry_vision()`
- **Refactor**: replace magic values with named constants `_AI_MIN_EXTRACTIONS_FOR_RELIABILITY` and `_AI_MIN_RELIABILITY_RATE` in `account_move.py`
- **Refactor**: extract shared normalization builders into `ai_preprocessor_normalize.py` — both Azure DI and AWS Textract now use `build_vendor/build_buyer/build_invoice/build_totals/build_line/build_result` for consistent extraction schema
- **Refactor**: extract `_increment_memory()` helper in `ai_vendor_memory.py` to deduplicate the update-correction-count pattern (used by `record_correction` and `record_line_correction`)
- **Refactor**: inline `_fetch_vendor_taxes()` into `_get_vendor_taxes()` in `ai_matcher_tax.py` — removes duplicate of `VendorMatchCache.get_vendor_taxes()` logic
- **Refactor**: replace remaining magic numbers with named constants (`_VENDOR_LINE_HISTORY_LIMIT`, `_PAYMENT_TERM_HISTORY_LIMIT`, `HISTORY_LIMIT`, `_CRON_BATCH_SIZE`, `_CRON_STALE_MINUTES`)
- **Fiscal cache invalidation**: `ai_fiscal_cache_invalidator.py` hooks into `account.account` and `account.tax` create/write to clear the fiscal context cache — new accounts/taxes are immediately available in AI prompts
- **Partner validation**: `force_partner_id` from preview wizard is now validated against company isolation (partner restricted to a different company is rejected)
- **Dead code removal**: removed unreachable `try/except` in `ai_fiscal_context.py` (list append cannot raise)
- **Background Extraction help text**: rewritten to explain the user-facing behavior (queue + scheduled action every 30s + auto-refresh) instead of developer jargon
- **Minimum Reliability Score help text**: clarified that in Full recognition mode, when confidence is below threshold, the recognition result is discarded and the AI service takes over
- **French translations**: comprehensive overhaul — accountant-friendly labels, contextual help texts, rounding/learning/post-processing translations

### Added

- **Rounding correction chatter note**: when rounding compensation is applied (adjust or line strategy), an internal note is posted on the invoice chatter explaining the correction
- **Cron stale extraction guard**: background extractions queued for more than 10 minutes are automatically marked as failed, preventing queue blockage
- **Vendor memory index**: composite DB index `(partner_id, company_id, field_name)` for faster account override lookups during line matching
- **Learning toggle** (`ai_learning_enabled`): enable/disable learning from corrections in settings, with threshold visible only when enabled
- **Accounting module detection** (`ai_has_accounting`): contextual help under "Extract Invoice Lines" shows different text depending on whether `account_accountant` is installed
- **Rounding strategy**: configurable rounding compensation — choose between adjusting an existing line's unit price or adding a dedicated rounding line with customizable label (Settings > Post-processing)
- **Currency symbol display**: rounding tolerance now shows the installation currency symbol
- Unit tests: `test_prompt_schema.py` — 22 tests (JSON schema integrity, required keys, enums, category enum bidirectional consistency with `_ACCOUNT_CATEGORY_MAP`, template formatting, label dicts, keyword lists)
- Unit tests: `test_edge_cases.py` — 8 tests (credit note type conversion, validator penalties for arithmetic/sum mismatch with tolerance, reverse charge warning generation)
- Unit tests: `test_account_matching.py` — 5 tests (category overrides vendor default, `VendorMatchCache` caching behavior, partner isolation)
- Unit tests: `test_qr_extraction.py` — 9 tests (PDF extraction pipeline, dependency guards, deduplication, max_pages, exception handling)

### Fixed

- **Inline text translations**: `<span>` text content not translated by Odoo 19 PO import — replaced with `<div class="d-inline">` for reliable translation
- **Multiline view text**: inline `<div>` text with newlines caused PO msgid mismatch — put text on single line

## [19.0.0.1.0] - 2026-02-26

### Added

- **Rounding correction**: configurable option (Settings > Advanced Options) that automatically adjusts one line item's unit price when the TTC total computed by Odoo differs from the extracted total due to HT rounding accumulation. Enabled by default with 0.01 tolerance. Adjusts the highest-priced line to minimize relative impact, with automatic rollback if the fix doesn't help.
- **Tax rate mismatch warning**: orange banner on invoice form when extracted tax rates (e.g. 5.5%, 10%, 20%) have no matching purchase tax in Odoo — directs user to create missing taxes
- **Tax category code recognition**: prompt now instructs the AI to read tax category codes on receipts (e.g. `(1)`, `(2)`, `(3)` with legend) instead of guessing from product names

### Changed

- **Refactor**: extract extraction pipeline from `account_move.py` into `ai_extraction_engine.py` via `_inherit` pattern (~310 lines: config, orchestration, Factur-X shortcut, API call, vision retry)
- **Refactor**: extract PO + product matching from `ai_matcher.py` into `ai_matcher_po.py` (~270 lines: 3-tier PO matching, product matching, line matching)
- **Refactor**: extract AWS SigV4 signing from `ai_preprocessor_aws.py` into `ai_aws_sigv4.py` (~100 lines)
- **Refactor**: extract `_match_tax_from_vendor()` helper from `match_tax_by_rate()` to fix C901=11 violation

### Fixed

- Extraction summary always showed "Missing: total" even when total was extracted — key mismatch (`amount_total` vs `totals` in confidence JSON)
- Buyer VAT verification triggered false positive when company has no VAT configured — now skips check
- `data.xml` cron incompatible with Odoo 19: `interval_type=seconds` not valid, `numbercall` field removed — changed to `minutes`, removed field
- Missing French translations for preview wizard: "Select Existing Vendor", "Purchase Order" (`arch_db` entries)
- `match_tax_by_rate()` exceeded C901 cyclomatic complexity limit (11 > 10) after cache optimization
- `_match_po_by_amount()` returned `None` instead of `(None, None)` tuple on no match
- `record_correction()` / `record_line_correction()` used `cr.rollback()` which rolls back entire transaction — replaced with `cr.savepoint()` context manager
- `except (ValueError, Exception)` redundant exception hierarchy in `_ai_apply_facturx` — simplified to `except Exception`
- TOCTOU race condition in `_get_company_cache()` — added double-checked locking pattern
- `write()` override queried ICP on every `account.move` write — added early return when no AI-tracked fields are touched
- `match_tax_by_rate()` returned recordset instead of single record when exactly 1 match — inconsistent return type
- `_ai_pre_identify_vendor()` issued N DB queries for N VAT numbers — replaced with single OR-domain query

## [19.0.0.0.19] - 2026-02-25

### Added

- **QR code extraction** (Swiss QR-bill SPC + EPC/BCD QR codes): extracts payment data (IBAN, amount, currency, reference) from embedded QR code images in PDF invoices
  - Pure utility module `ai_qr_decoder.py`: PDF image extraction, pyzbar QR decoding, SPC/EPC payload parsing, QRR mod-10 and SCOR mod-97 reference validation
  - QR data injected into AI prompt as high-confidence structured source
  - Cross-validation: QR IBAN/amount/currency/reference verified against AI extraction, with automatic override for IBAN and currency, penalty for amount mismatch
  - Configurable setting "QR Code Extraction" (enabled by default) in Advanced Options
  - Optional dependency: `pyzbar` (+ system `libzbar`) — graceful degradation when not installed
- Unit tests: `test_qr_extraction.py` — 33 tests (SPC parsing, EPC parsing, reference validation, cross-validation, context formatting, dispatch)

## [19.0.0.0.18] - 2026-02-25

### Added

- **Document language detection** (7 languages: fr, de, en, es, it, nl, pt) — keyword-based heuristic injected into AI prompt
- **Account-level learning via vendor memory** — memorizes account corrections per vendor + description
- **Auto-apply when confidence is high** — skips preview wizard for reliable vendors with high scores
- **Client-side polling widget** — auto-refreshes during background extraction, opens results when done
- Settings: "Auto-apply High Confidence" and "Minimum Confidence for Auto-apply"

### Changed

- Extraction status indicator now uses custom OWL widget with polling instead of statusbar
- Account matching now checks vendor memory overrides (tier 0) before standard matching
- `write()` override refactored into helper methods for header and line correction detection

## [19.0.0.0.17] - 2026-02-24

### Added

- **Always-on lightweight extraction log**: `ai.extraction.log` is now created for every extraction, not just in debug mode. Lightweight fields (vendor name, overall confidence, duration, provider, extraction mode) are always populated. Full prompt/response content is stored only when "Detailed Logging" is enabled.
- **Token-based fuzzy partner matching** (tier 2b): when exact/ilike name matching fails, a new token-based matcher normalizes company names (strips legal suffixes like SA, GmbH, Ltd, etc.) and scores candidates using asymmetric Jaccard similarity. Handles abbreviated names ("Infomaniak" → "Infomaniak Network SA") and reordered names ("Services ACME" → "ACME Services SARL").
- **Extraction result cache**: last extraction result is cached on `account.move`. If the user discards and re-clicks "Digitize" with the same attachment, the cached result is reused without calling the API. Cache is invalidated on attachment change.
- **"Re-extract" button**: visible when a cached extraction exists — forces a fresh API call (clears cache).
- **"View Results" button**: visible after async extraction completes — opens the preview wizard from cached data.
- **Background extraction** (`ai_async_extraction` setting): optional cron-based extraction mode. Clicking "Digitize" queues the invoice, and a cron job (every 30s) processes the queue in background. Falls back to synchronous mode when disabled.
- `ir.cron` record: `AI Invoice Extraction Queue` — processes up to 5 queued invoices per run.
- Unit tests: `test_lightweight_log.py` (7 tests), `test_token_matching.py` (12 tests), `test_extraction_cache.py` (6 tests), `test_async_extraction.py` (6 tests)

### Changed

- `action_ai_extract()` refactored: prerequisites extracted to `_ai_check_prerequisites()`, synchronous extraction to `_ai_extract_sync()`, preview wizard opening to `_ai_open_preview_wizard()` — satisfies PLR0911 (max 6 return statements)
- `match_partner()` docstring updated: now documents 4 tiers (VAT, name ilike, name tokens, email)
- Extraction log list/form/search views: added vendor_name, extraction_mode, provider_name, overall_confidence, duration_seconds columns and filters

## [19.0.0.0.16] - 2026-02-24

### Changed

- **Settings page UX overhaul**: restructured from 10 blocks to 5 logical sections
  - **Document Recognition** (was "Document Pre-processing") — first block, reflecting the logical processing order
  - **AI Service** (was "AI Provider") — clearer naming, now includes Extraction Mode and Extract Invoice Lines
  - **Estimated Cost per Invoice** — standalone block (cost depends on recognition + AI + mode + lines)
  - **Advanced Options** (new) — merges Automatic Extraction, Confidence Indicators, Learning Threshold, Detailed Logging, Knowledge Base
- **Terminology**: replaced developer jargon with accountant-friendly language
  - "Document Pre-processor" → "Document Recognition Service"
  - "Pre-processing Mode" → "Recognition Mode"
  - "None (use internal pipeline)" → "None (built-in)"
  - "OCR Replacement / AI Enrichment / OCR Only" → "Full recognition / Combined / Text extraction only"
  - "Debug Mode" → "Detailed Logging"
  - "Auto-apply Threshold" → "Learning Threshold"
  - "Knowledge Base (RAG)" → "Knowledge Base"
  - "RAG Endpoint / RAG API Key" → "Service URL / Service API Key"
- **Dual help text pattern**: short visible description under each setting + detailed tooltip on hover (?) for Extract Lines, Confidence Indicators, Learning Threshold, Detailed Logging
- **Cost estimate**: separate block with note "Actual cost may vary depending on the document"; format strings now translatable via `env._()`
- **Test wizard**: "Pre-processing" mode renamed to "Document Recognition"
- **French translations**: complete overhaul — ~60 `arch_db` entries for all settings view strings, updated field tooltips, "per invoice" → "par facture" in cost estimates

### Added

- **Extraction Mode** (`ai_extraction_mode`): 3-mode setting in AI Service block
  - **Guided** (default): full fiscal context (chart of accounts, taxes, vendor memory) + full matching (partner, taxes, accounts, products, PO) + all validations + learning
  - **Simplified**: taxes only in prompt, partner + tax matching, no account/product/PO matching, no duplicate/anomaly detection, no learning
  - **Free**: no Odoo context, no matching, arithmetic validation only — raw extraction for manual allocation or external import
- **Confidence Indicators toggle**: new setting to show/hide colored confidence badges on extracted fields (enabled by default)
- **Learning Threshold in UI**: field was defined but not visible in settings — now added to Advanced Options
- Unit tests: `test_extraction_modes.py` — 14 tests for the 3 modes (context, matching, warnings, learning, cost)

## [19.0.0.0.15] - 2026-02-24

### Fixed

- **Odoo 19 full compatibility**: all 367 tests pass (was 61 failures)
  - `account.account`: `company_id` → `company_ids` (many2many), `deprecated` → `active` field — runtime detection with `_fields` check in 3 modules
  - `account.move`: `journal_id` NOT NULL constraint — `message_new` now provides default purchase journal
  - `account.move`: `message_new` uses `msg_dict['from']` instead of `msg_dict['email_from']` — check both keys
  - `account.move`: `message_new` partner_id override — Odoo 19 ignores custom_values, re-applied after super()
  - `account.move`: `action_post` requires invoice lines — tests updated
  - `res.partner`: `property_account_expense_id` removed — guarded with `_fields` check
  - Model record attributes read-only — `patch.object(record, ...)` → `patch.object(type(record), ...)`
  - `cr.commit()` blocked in test mode — batch wizard catches `AssertionError`
  - `time.sleep` mock path corrected: `ai_provider.time.sleep` (not `ai_provider_anthropic`)
  - Preview mode in `action_ai_extract()` — tests use `_ai_trigger_extraction()` directly
- **Retry logic**: 5xx errors on last retry attempt now return `max_retries` error (was returning generic `api` error)
- **Duplicate detection**: `total_amount=0.0` no longer skips exact match check (Python falsy fix)
- **PO normalization**: whitespace-only input now returns empty string (was returning `'0'`)
- **Factur-X parser**: `ExchangedDocument` and `TypeCode` searched from XML root (sibling of `SupplyChainTradeTransaction`, not child)

## [19.0.0.0.14] - 2026-02-24

### Added

- **Multi-provider AI**: OpenAI (GPT), Google (Gemini), and xAI (Grok) are now fully functional alongside Anthropic (Claude)
  - **OpenAI provider** (`ai_provider_openai.py`): GPT-4o, GPT-4o Mini — function calling with `strict: true`, vision support, retry with backoff
  - **Google provider** (`ai_provider_google.py`): Gemini 2.0 Flash, 2.5 Flash, 2.5 Pro — `functionDeclarations`, vision via `inlineData`, retry with backoff
  - **xAI provider** (`ai_provider_xai.py`): Grok 3, Grok 3 Mini, Grok 2 — inherits OpenAI-compatible implementation, different endpoint/models
  - Content format translation: pipeline builds Anthropic-format blocks, each provider converts internally
  - All providers: validate_api_key, estimate_cost, supports_vision, structured output (guaranteed JSON)
  - `test_providers.py`: 30+ tests — extract, validate, fallback, retry, content conversion, factory, model aggregation
- Settings: removed "Coming soon" labels for OpenAI, Google, xAI
- Settings: test connection button now works with all providers (removed Anthropic-only restriction)
- Settings: pre-processing mode labels now provider-agnostic ("AI" instead of "Claude")

## [19.0.0.0.13] - 2026-02-24

### Added

- **Purchase order matching**: optional PO reconciliation when `purchase` module is installed
  - 3-tier header matching: exact reference, fuzzy (normalized), amount/date proximity
  - Line-level matching: product code, description keywords, quantity/price
  - Runtime detection (`'purchase.order' in env`) — no hard dependency on `purchase`
  - PO reference extracted from Factur-X/ZUGFeRD XML (`BuyerOrderReferencedDocument`)
  - Explicit multilingual extraction instructions in AI prompt
  - PO reference shown in extraction preview dialog
  - PO warning banner on invoice form (unmatched ref or low-confidence match)
  - `ai_po_warning` computed field on `account.move`
  - `purchase_order` entry in extraction summary labels
  - `test_po_matching.py`: normalization, graceful fallback, exact/fuzzy/amount matching, line matching, Factur-X extraction, pipeline integration, preview wizard

## [19.0.0.0.12] - 2026-02-24

### Added

- **Odoo 19 support**: dedicated branch `19.0` with full compatibility

### Changed

- `security.xml`: `category_id` → `privilege_id`, `users` → `user_ids` (Odoo 19 `res.groups` API)
- `ai_vendor_memory.py`, `ai_vendor_score.py`: `_sql_constraints` → `models.UniqueIndex()` (Odoo 19 ORM API)
- `confidence_widget.xml`: `t-esc` → `t-out` (QWeb deprecation)
- `__manifest__.py`: version `19.0.0.0.12`

## [18.0.0.0.12] - 2026-02-24

### Added

- **Structured outputs**: extraction now uses Anthropic's tool_use API with a JSON Schema (`EXTRACTION_TOOL_SCHEMA`), guaranteeing valid JSON output and eliminating parsing failures. Legacy text fallback preserved for backward compatibility
- **Network error retry**: timeout, connection errors, and server errors (5xx) now retry with exponential backoff (max 3 attempts), matching the existing rate-limit retry behavior
- **Prompt size estimation**: `_ai_estimate_prompt_tokens()` estimates input tokens before API call, logs a warning for documents exceeding 50k tokens
- **Test extraction wizard**: "Test Extraction" button in settings with 4 test modes:
  - _Full Pipeline_: sends a sample or uploaded invoice through the full extraction pipeline
  - _Text Extraction_: extracts text from an uploaded PDF (metadata, number format, tables, qualification) — no API call
  - _Pre-processing_: sends document to Azure DI / AWS Textract — tests pre-processor configuration
  - _Prompt Preview_: builds the full prompt and shows estimated tokens — no API call
- **Mermaid sequence diagram**: detailed extraction pipeline diagram added to README
- Unit tests: `test_v012_improvements.py` — network retry (timeout/connection/5xx), structured outputs (tool_use + text fallback), prompt estimation, test wizard (full pipeline + 4 mode tests)

### Changed

- `ai_provider_anthropic.py`: `extract()` now sends `tools` and `tool_choice` in payload; `_parse_success()` handles both `tool_use` and text content blocks
- `ai_prompt.py`: added `EXTRACTION_TOOL_SCHEMA` — full JSON Schema with vendor, buyer, invoice, totals, tax_lines, table_analysis, lines sub-schemas
- `_extract_json()` retained as fallback parser (no longer primary path for Anthropic)

## [18.0.0.0.11] - 2026-02-24

### Added

- **Mistral AI placeholder**: added as AI provider option (Coming soon)
- **Google Document AI placeholder**: added as pre-processor option (Coming soon)
- **Claude Sonnet 4.6**: added to model selection

### Changed

- **Dynamic model selection**: AI model dropdown now populated dynamically from providers via `get_all_provider_models()` — adding models to a provider automatically updates the settings UI
- **Updated Anthropic pricing**: Haiku 4.5 ($0.80→$1.00 input, $4→$5 output), Opus 4.6 ($15→$5 input, $75→$25 output) per MTok
- **Fixed Opus label**: "Claude Opus 4" → "Claude Opus 4.6 (maximum accuracy)"
- **Provider onchange**: model selection resets to provider's first model when switching AI provider

## [18.0.0.0.10] - 2026-02-24

### Added

- **Document pre-processing**: optional external OCR providers (Azure Document Intelligence, AWS Textract) as configurable pre-processors before Claude extraction
- `DocumentPreprocessor` abstract base class + factory (`ai_preprocessor.py`): same pattern as `AIProvider`, with `extract_structured()`, `extract_text()`, `validate_credentials()`
- Azure Document Intelligence provider (`ai_preprocessor_azure.py`): prebuilt-invoice model with async polling, field normalization to extraction schema
- AWS Textract provider (`ai_preprocessor_aws.py`): AnalyzeExpense API, pure-Python SigV4 signing (zero external deps), field normalization
- Three user-configurable pre-processing modes: OCR Replacement (pre-processor only, Claude as fallback), Claude Enrichment (pre-processor + Claude validates), OCR Only (pre-processor text replaces PyPDF2)
- Settings: pre-processor provider dropdown, mode dropdown, confidence threshold, Azure/AWS credentials (admin-only), "Test Pre-processor Connection" button
- Cost estimate in settings now includes pre-processor cost when enabled
- Unit tests: `test_preprocessing.py` — Azure/AWS normalization, pipeline integration (3 modes + fallbacks), credentials, SigV4 signing

### Changed

- Refactor: extract `_ai_preprocess_or_prepare()`, `_ai_check_ocr_replacement()`, and `_ai_call_and_validate()` from `_ai_trigger_extraction()` to keep complexity under C901/PLR limits
- Refactor: extract Factur-X CII XML parser (11 functions) from `ai_document.py` into dedicated `ai_facturx_parser.py` (706→401 + 315 lines)
- Refactor: extract 7 pre-processing pipeline methods from `account_move.py` into `ai_preprocess_pipeline.py` via Odoo `_inherit` pattern (1391→1176 + 237 lines)

## [18.0.0.0.9] - 2026-02-24

### Fixed

- **Vision retry**: debug log was created before checking API success — moved log creation after success check
- **Line sum validation**: `_ai_apply_lines` now warns when sum of line amounts diverges from extracted total (>1% tolerance)
- **Security**: debug log read access restricted from all accountants to module users only (`group_ai_digitize_user`)
- **Partner matching**: combined exact + fuzzy name search into single DB query (4→3 queries max)
- **Rate limiting**: `action_ai_extract` now blocks if invoice already processing or if >5 concurrent extractions company-wide
- **Table extraction**: added `MAX_TABLE_PAGES = 50` limit to prevent pdfplumber from iterating all pages on large PDFs
- **Confidence widget**: explicit field list (`partner_id`, `ref`, `invoice_date`, `invoice_date_due`, `totals`) instead of averaging all numeric values
- **Fiscal context**: replaced manual `seen` set deduplication with `.mapped('account_id')` (Odoo ORM deduplicates natively)

### Added

- `VendorMatchCache`: per-extraction cache for vendor past lines and taxes lookups — reduces 3×N DB queries to 2 total for N-line invoices
- `_ai_check_rate_limit()`: extracted rate limiting guards from `action_ai_extract` to satisfy PLR0911
- Unit tests: `test_improvements.py` — vision retry, line sum validation, partner matching, rate limiting, table page limit, VendorMatchCache

## [18.0.0.0.8] - 2026-02-24

### Fixed

- **Bug**: `quantity=0.0` and `price_unit=0.0` were treated as missing due to Python falsy check (`or` operator) — now uses explicit `is not None` checks
- **Bug**: TTC→HT back-calculation could produce floating-point artifacts — now wrapped in `round(..., 2)`
- **Bug**: Factur-X XML parse failure did not set `ai_extraction_status = 'failed'` — invoice stayed stuck in `processing`
- **Bug**: `_match_account_by_category()` could return revenue or asset accounts — added `account_type` filter restricting to expense accounts
- **Bug**: `match_partner()` could return archived (inactive) partners — added `('active', '=', True)` to all partner search domains
- **Robustness**: Fiscal context cache (`_fiscal_cache`) was not thread-safe — added `threading.Lock()` for all read/write/evict operations
- **Robustness**: `record_correction()` in vendor memory had a race condition between `search()` and `create()` — added `try/except IntegrityError` with retry fallback
- **Robustness**: N+1 query pattern in `match_tax_by_rate()` — `mapped('tax_ids')` triggered lazy loads per line; replaced with single `search()` using collected tax IDs
- **Robustness**: `_ai_handle_doc_issue()` set inconsistent `ai_confidence` JSON (missing `overall` key) — normalized structure
- **Minor**: API request timeout increased from 120s to 180s to accommodate vision mode on large documents
- **Minor**: `reliability_rate` computation could produce long floating-point values — added `round(..., 2)`

### Changed

- Extracted `_get_vendor_taxes()` helper from `match_tax_by_rate()` to keep complexity under C901 threshold

## [18.0.0.0.7] - 2026-02-24

### Added

- Product matching: `match_product()` in `ai_matcher.py` — matches vendor product codes (`product_code`) against `product.supplierinfo` (vendor-specific, then any vendor) and `product.product` (`default_code`), assigns `product_id` on invoice lines
- Extraction preview wizard (`ai.preview.wizard`): shows extracted data (vendor, reference, date, total, line count, warnings) in a dialog before applying to the invoice — user clicks "Apply" or "Discard"
- Extraction summary banner on invoice form: blue info banner showing which fields were extracted and which are missing (e.g., "Extracted: vendor, reference, date, total (4 fields). Missing: payment terms")
- Failed extraction banner: red alert when extraction fails
- Dashboard "Token Statistics" menu: graph view showing token usage by model, plus `input_tokens` and `output_tokens` as pivot measures
- "Last 7 Days" filter in extraction log search view
- Store assets: `icon.svg` (module icon placeholder), `index.html` (Odoo Apps Store description page with features, compatibility, pipeline diagram)
- Unit tests: `test_product_matching.py` — supplier info vendor-specific, any-vendor, default_code, case-insensitive, unknown code, vendor fallthrough

### Changed

- Batch wizard: each extraction is now committed individually (`cr.commit()`) to save progress and avoid losing work on timeout
- Batch wizard: added `estimated_time` field ("~X minutes") and large batch warning (>20 invoices)
- `action_ai_extract()` now opens preview wizard instead of applying extraction directly
- `_ai_trigger_extraction()` supports `preview=True` parameter to return data without applying
- Refactor: extract `_ai_check_api_result()` from `_ai_trigger_extraction()` to reduce cyclomatic complexity (C901 12→9)
- Refactor: extract `_ai_qualify_document()` and `_ai_extract_tables()` from `_ai_prepare_document()` (C901 12→8)
- Refactor: extract `_ai_map_invoice_fields()` and `_ai_map_currency()` from `_ai_map_header_fields()` (C901 13→7)
- Refactor: extract `_get_vendor_payment_term()` from `match_payment_term()` (C901 11→7)
- Refactor: replace multiple return statements with status dict in `validate_api_key()` (PLR0911 7→4)
- Extraction pipeline SVG diagram added to README

## [18.0.0.0.6] - 2026-02-23

### Added

- Factur-X / ZUGFeRD full XML parser (`parse_facturx_xml`): parses CII XML into the same dict format as Claude's response, supports all profiles (Minimum, Basic, EN16931, Extended), handles invoices and credit notes (TypeCode 380/381)
- Factur-X data now flows through the standard extraction pipeline (`_ai_apply_extraction`) instead of being a stub
- Vision retry fallback: when text-mode extraction fails cross-validation (≥2 failures), automatically retries in vision mode
- Fiscal context cache: company-level expense accounts and purchase taxes cached in module-level dict with daily TTL, avoiding repeated DB queries per extraction
- `invalidate_fiscal_cache()` for manual cache invalidation
- Payment terms matching (`match_payment_term`): 3-tier search — vendor history, fuzzy name match, day-count regex heuristic
- Account matching for invoice lines (`match_account`): 5-tier search — vendor history with keyword similarity, vendor default account, category→prefix mapping, partner default, company fallback
- Category→account prefix mapping (`_ACCOUNT_CATEGORY_MAP`): 21 categories (consulting, shipping, telecom, etc.) mapped to French PCG account prefixes
- Batch AI extraction wizard (`ai.batch.extract.wizard`): select multiple invoices in list view and trigger extraction in batch, with summary report (X ok, Y failed, Z skipped)
- Server action binding on `account.move` list view for batch extraction
- Unit tests: `test_facturx.py` — full XML parsing, vendor/buyer/invoice/totals/tax_lines/lines extraction, credit note detection, invalid/empty XML handling
- Unit tests: `test_payment_terms.py` — exact match, partial match, day-count heuristic, vendor history, empty/none text
- Unit tests: `test_account_matching.py` — fallback, category mapping, unknown category
- Unit tests: `test_batch_extraction.py` — wizard creation, skip no attachment, batch processing with mock API, no API key
- End-to-end tests: `test_extraction.py::TestEndToEndPipeline` — full pipeline (extraction→fields→confidence→lines), credit note detection

### Changed

- `cross_validate()` now returns `int` (failure count) instead of `None` — enables vision retry logic
- `_ai_apply_facturx()` rewritten: parses CII XML via `parse_facturx_xml()` then feeds through `_ai_apply_extraction()` (same pipeline as Claude)
- `_ai_map_header_fields()` now matches payment terms from `payment_terms_text`
- `_ai_apply_lines()` now matches accounts via `match_account()` for each line item

## [18.0.0.0.5] - 2026-02-23

### Added

- Multi-company support: `company_id` field on `ai.vendor.memory` and `ai.vendor.score` with proper record rules
- Company-scoped anomaly detection: only compares against same-company invoice history
- Company-scoped duplicate detection: only flags duplicates within the same company
- Company parameter propagated to all vendor memory, score, and detector calls from extraction pipeline
- Vendor memory export wizard: export corrections to JSON (all vendors or selected, company-scoped)
- Vendor memory import wizard: import corrections from JSON with vendor matching by VAT then name
- Unit tests: `test_multi_company.py` — company isolation for memory, score, anomaly, and duplicate detection
- Unit tests: `test_memory_wizard.py` — export all/selected, import create/update/skip, VAT matching

### Changed

- Refactored `account_move.py`: split `_ai_trigger_extraction` (107 statements) into orchestrator + `_ai_prepare_document`, `_ai_build_content`, `_ai_format_preprocess_context`; split `_ai_apply_extraction` (73 statements) into orchestrator + `_ai_map_header_fields`, `_ai_apply_partner_overrides`, `_ai_check_warnings` — all methods now under ruff PLR limits
- `ai.vendor.memory` SQL constraint now includes `company_id`: `UNIQUE(partner_id, company_id, field_name, ai_value)`
- `ai.vendor.score` SQL constraint now includes `company_id`: `UNIQUE(partner_id, company_id)`
- Security record rules for vendor memory and score now filter by `company_id` (previously wildcards)

## [18.0.0.0.4] - 2026-02-23

### Added

- IBAN validation: mod-97 checksum verification on extracted vendor IBANs (catches OCR errors in bank details)
- Buyer verification: compares extracted buyer (VAT, name) against the active Odoo company, warns on mismatch
- Buyer mismatch warning banner (blue) on invoice form
- Unit tests: `test_iban_validation.py` — valid/invalid IBANs, checksum, format, spaces/dashes
- Unit tests: `test_buyer_verification.py` — VAT match/mismatch, name match/substring, edge cases
- Number format detection: pre-API heuristic scan to detect decimal separator convention (comma vs dot), injected into prompt and cross-validated against Claude's response
- PDF metadata extraction: reads author, creator software, title, creation date from PDF metadata before API call
- Number format cross-validation: warns when pre-detected format disagrees with Claude's `table_analysis.number_format`
- Unit tests: `test_number_format.py` — FR/DE/EN/CH formats, ambiguous, empty, edge cases
- Unit tests: `test_pdf_metadata.py` — valid metadata, no metadata, exceptions, special chars
- Structured table extraction: optional `pdfplumber` integration extracts tabular data from text-based PDFs, formats as markdown, and injects into the AI prompt for better line item accuracy
- Unit tests: `test_table_extraction.py` — pdfplumber mock, table validation, multi-page merge, markdown formatting, graceful degradation
- Email integration: `message_new()` override auto-creates vendor bills from incoming emails via `mail.alias`
- Vendor pre-identification from email sender address (partner lookup by email)
- Auto-extract on email arrival: optional toggle in settings to trigger AI extraction automatically
- `mail.alias` record (`vendor-invoices@domain`) for email routing to vendor bills
- Extraction dashboard: graph/pivot views for cost tracking, extraction volume, and vendor accuracy
- Search view on extraction logs with date/model/success filters and group-by options
- `total_tokens` computed field on extraction logs for dashboard aggregation
- Unit tests: `test_email_integration.py` — message_new, partner matching, auto-extract toggle, exception handling

### Changed

- Refactored `_compute_ai_warnings()` to use a data-driven loop instead of repetitive blocks
- Code quality: applied ruff formatting, fixed all pylint-odoo warnings (redundant string=, except-pass, `self.env._()` convention)

## [18.0.0.0.3] - 2026-02-23

### Added

- Document qualification: pre-API heuristic keyword scan to detect pro-forma/quotes and "PAID" stamps before calling the AI (saves API credits)
- Post-API document type check: warns when Claude classifies a document as pro-forma or detects a paid stamp
- Pro-forma warning banner (red) and paid stamp warning banner (orange) on invoice form
- Enhanced system prompt with explicit document type classification and paid stamp detection instructions
- Duplicate invoice detection: warns when an invoice with the same vendor + reference (+ date + amount) already exists
- Amount anomaly detection: flags invoices with amounts significantly higher or lower than the vendor's historical average
- Warning banners on invoice form for duplicate and anomaly alerts
- Unit tests: `test_document_qualification.py` — invoice/proforma/paid keywords, multilingual, case-insensitive, empty/garbage text
- Unit tests: `test_duplicate_detection.py` — exact match, partial match, edge cases
- Unit tests: `test_anomaly_detection.py` — high/low anomaly, insufficient history, edge cases

### Changed

- Extracted fiscal context building (accounts, taxes, vendor history) from `account_move.py` into dedicated `ai_fiscal_context.py` module

## [18.0.0.0.2] - 2026-02-23

### Added

- `ai.vendor.memory` model: per-vendor learning from user corrections (field, AI value, user value, correction count, auto-apply threshold)
- `ai.vendor.score` model: per-vendor extraction reliability scoring (total/correct extractions, reliability rate, degradation detection)
- Vendor memory views: list, form, search with auto-apply filter and group-by
- Vendor score views: list with color-coded reliability (green/yellow/red), form, search
- `write()` override on `account.move`: detects corrections to AI-filled fields (partner, reference, dates) and records them in vendor memory
- Vendor memory context injection into extraction prompt (past corrections for the vendor)
- Auto-apply overrides: when correction count reaches configurable threshold, future extractions automatically use the user's preferred value
- Vendor-aware tax matching: `match_tax_by_rate()` now prioritizes taxes historically used with the vendor
- AI snapshot (`ai_extracted_values` field): stores extracted values for correction detection
- Vendor score update after each successful extraction
- Security: access rights for `ai.vendor.memory` and `ai.vendor.score` (user/manager groups)
- Security: multi-company record rules for both new models
- Unit tests: `test_vendor_memory.py` — correction recording, counting, auto-apply, context generation, correction detection via write() override
- Unit tests: `test_vendor_score.py` — score creation, reliability computation, degradation detection, uniqueness constraint

## [18.0.0.0.1] - 2026-02-23

### Added

- Project initialization and technical specification
- Project documentation: ARCHITECTURE.md, README.md, CONTRIBUTING.md, CHANGELOG.md
- .gitignore configured for Odoo/Python development
- Module manifest (`__manifest__.py`) for Odoo 18 (v18.0.0.0.1)
- Module scaffolding: directory structure, `__init__.py` files
- `res.config.settings` extension: AI provider, API key, model selection, debug mode, line extraction toggle
- `account.move` extension: AI extraction status, confidence scores, extraction log link
- `ai.extraction.log` model: prompt, response, tokens, cost tracking
- Security: groups (user/manager), access rights, multi-company record rule
- Views: settings form, invoice form (extraction status), extraction log list/form
- LICENSE (LGPL-3)
- AI Provider abstraction layer (`AIProvider` ABC + factory pattern)
- Anthropic (Claude) provider: full implementation (text + vision, retry on 429, error handling)
- OpenAI, Google, xAI provider placeholders (disabled, "Coming soon")
- Prompt templates: system prompt, full JSON extraction schema, multilingual total labels
- Extraction pipeline in `account.move`: PDF text extraction, Factur-X detection, vendor pre-identification, prompt construction with fiscal context (prioritized accounts, taxes), API call, response parsing, cross-validation, partner/tax matching, field mapping
- "Digitize with AI" button on vendor bill form (draft state, user group)
- "Test Connection" button in settings (validates API key against provider)
- Computed cost estimate per invoice in settings (based on model + options)
- Extraction log creation in debug mode (tokens, cost, prompt, response)
- `ai_document.py`: extracted document processing utilities (PDF text extraction, Factur-X detection, VAT pattern matching) from `account_move.py` for separation of concerns
- OWL confidence indicator widget: colored badge (green/yellow/red) with icon on invoice form
- Confidence badge displayed in invoice header (visible after AI extraction)
- Unit tests: `test_config.py` (10 tests) — settings defaults, config storage, selections, cost estimate
- Unit tests: `test_extraction.py` (~25 tests) — extraction pipeline, API errors, partner/tax matching, cross-validation, document utilities, provider factory (all API calls mocked)
- `ai_matcher.py`: extracted partner matching (VAT, name, email) and tax matching (rate) from `account_move.py`
- `ai_validator.py`: extracted mathematical cross-validation of amounts from `account_move.py`
- French translation (`i18n/fr.po`): all user-facing strings (UI, notifications, field labels, selections)
