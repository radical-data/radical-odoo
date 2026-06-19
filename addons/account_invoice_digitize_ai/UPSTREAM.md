# Local patches

- OpenAI extraction uses non-strict function calling:
  `addons/account_invoice_digitize_ai/models/ai_provider_openai.py`
  sets `strict: False`.
  The upstream extraction schema works in normal tool-calling mode, but OpenAI
  rejects it with HTTP 400 when strict structured-output validation is enabled.

# Upstream source

Source: https://github.com/PaulArgoud/account-invoice-digitize-ai
Odoo branch: 19.0
Imported commit: 00c4dc58ea9bc10cdceb3e65dd8af107ab054585
Imported date: 2026-06-16

Record the upstream source and commit for this vendored addon here whenever it is updated.
