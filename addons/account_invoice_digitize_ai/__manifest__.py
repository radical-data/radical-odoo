{
    'name': 'AI Invoice Digitization',
    'version': '19.0.0.3.1',
    'depends': ['account'],
    'license': 'AGPL-3',
    'price': 100.00,
    'currency': 'EUR',
    'author': 'Paul ARGOUD',
    'website': 'https://github.com/PaulArgoud/account-invoice-digitize-ai',
    'category': 'Accounting/Accounting',
    'summary': 'AI-powered vendor bill digitization with learning',
    'description': """
AI Invoice Digitization
=======================

Replace Odoo's native invoice digitization with an AI-powered extraction
pipeline. Upload a vendor bill (PDF or image), and get structured data
auto-filled in seconds.

**Key features:**
- AI-powered extraction using Claude, GPT, Gemini, or Grok
- Learns from user corrections per vendor
- Factur-X / ZUGFeRD structured extraction (zero AI cost)
- Multi-language, multi-currency support
- Confidence indicators on each extracted field
- Works on Community AND Enterprise editions

**Supported Odoo versions:** 19
    """,
    # 'images': ['static/description/banner.png'],  # TODO: add screenshot
    'application': True,
    'installable': True,
    # NOTE: pdfplumber (tables), facturx (Factur-X/ZUGFeRD) and pyzbar (QR codes)
    # are OPTIONAL. They are guarded by try/except imports with *_AVAILABLE flags
    # and the module degrades gracefully when they are missing — so they are
    # deliberately NOT declared in 'external_dependencies', which would make Odoo
    # refuse to install the module when they are absent (pyzbar in particular also
    # needs the system library libzbar0). Install them to enable the extra
    # features; the settings page shows their availability status.
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/data.xml',
        'views/res_config_settings_views.xml',
        'views/account_move_views.xml',
        'views/ai_extraction_log_views.xml',
        # ai_memory_wizard_views.xml defines ai_memory_export_action /
        # ai_memory_import_action, which the ai.vendor.memory list header
        # references — it must load first or a fresh install fails with
        # "External ID not found".
        'views/ai_memory_wizard_views.xml',
        'views/ai_vendor_memory_views.xml',
        'views/ai_vendor_score_views.xml',
        'views/ai_dashboard_views.xml',
        'views/ai_batch_extract_views.xml',
        'views/ai_preview_wizard_views.xml',
        'views/ai_test_wizard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'account_invoice_digitize_ai/static/src/js/**/*',
            'account_invoice_digitize_ai/static/src/xml/**/*',
        ],
    },
}
