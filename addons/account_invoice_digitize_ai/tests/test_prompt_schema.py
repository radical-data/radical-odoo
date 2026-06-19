"""Tests for ai_prompt.py schema integrity and template formatting.

Validates that:
- EXTRACTION_TOOL_SCHEMA has correct structure and required keys
- Category enum in line schema matches _ACCOUNT_CATEGORY_MAP keys
- Template format strings contain expected placeholders
- ALL_TOTAL_PATTERNS is populated
- Label dictionaries are non-empty
"""

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPromptSchema(TransactionCase):
    """Validate JSON schema structure and consistency."""

    def _get_schema(self):
        from odoo.addons.account_invoice_digitize_ai.models.ai_prompt import EXTRACTION_TOOL_SCHEMA

        return EXTRACTION_TOOL_SCHEMA

    def test_top_level_required_keys(self):
        """EXTRACTION_TOOL_SCHEMA must require document_type, vendor, invoice, totals, table_analysis."""
        schema = self._get_schema()
        self.assertIn('required', schema)
        for key in ('document_type', 'vendor', 'invoice', 'totals', 'table_analysis'):
            self.assertIn(key, schema['required'])

    def test_top_level_properties(self):
        """All top-level properties must be defined."""
        schema = self._get_schema()
        props = schema.get('properties', {})
        expected = {
            'document_type',
            'is_marked_paid',
            'vendor',
            'buyer',
            'invoice',
            'totals',
            'tax_lines',
            'table_analysis',
            'lines',
        }
        for key in expected:
            self.assertIn(key, props, 'Missing top-level property: %s' % key)

    def test_document_type_enum(self):
        """document_type must have enum with valid values."""
        schema = self._get_schema()
        dt = schema['properties']['document_type']
        self.assertIn('enum', dt)
        for val in ('invoice', 'credit_note', 'proforma', 'unknown'):
            self.assertIn(val, dt['enum'])

    def test_vendor_schema_required(self):
        """Vendor schema must require name and confidence."""
        schema = self._get_schema()
        vendor = schema['properties']['vendor']
        self.assertIn('name', vendor.get('required', []))
        self.assertIn('confidence', vendor.get('required', []))

    def test_invoice_schema_required(self):
        """Invoice schema must require reference, invoice_date, confidence."""
        schema = self._get_schema()
        inv = schema['properties']['invoice']
        for key in ('reference', 'invoice_date', 'confidence'):
            self.assertIn(key, inv.get('required', []))

    def test_totals_schema_required(self):
        """Totals must require all three amounts and confidence."""
        schema = self._get_schema()
        totals = schema['properties']['totals']
        for key in ('untaxed_amount', 'tax_amount', 'total_amount', 'confidence'):
            self.assertIn(key, totals.get('required', []))

    def test_line_schema_has_description_required(self):
        """Line schema must require description and confidence."""
        schema = self._get_schema()
        line = schema['properties']['lines']['items']
        self.assertIn('description', line.get('required', []))
        self.assertIn('confidence', line.get('required', []))

    def test_line_schema_has_category_enum(self):
        """Line suggested_account_category must have enum."""
        schema = self._get_schema()
        line = schema['properties']['lines']['items']
        cat_field = line['properties']['suggested_account_category']
        self.assertIn('enum', cat_field)
        self.assertIn('shipping', cat_field['enum'])
        self.assertIn('merchandise', cat_field['enum'])

    def test_category_enum_matches_matcher(self):
        """Category enum in schema must include all keys from _ACCOUNT_CATEGORY_MAP."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import _ACCOUNT_CATEGORY_MAP

        schema = self._get_schema()
        line = schema['properties']['lines']['items']
        cat_field = line['properties']['suggested_account_category']
        cat_enum = set(cat_field['enum']) - {None, ''}
        map_keys = set(_ACCOUNT_CATEGORY_MAP.keys())
        missing = map_keys - cat_enum
        self.assertFalse(missing, 'Categories in _ACCOUNT_CATEGORY_MAP but not in schema enum: %s' % missing)

    def test_category_enum_no_extras(self):
        """Schema enum should not have categories absent from _ACCOUNT_CATEGORY_MAP."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_matcher import _ACCOUNT_CATEGORY_MAP

        schema = self._get_schema()
        line = schema['properties']['lines']['items']
        cat_field = line['properties']['suggested_account_category']
        cat_enum = set(cat_field['enum']) - {None, ''}
        map_keys = set(_ACCOUNT_CATEGORY_MAP.keys())
        extras = cat_enum - map_keys
        self.assertFalse(extras, 'Categories in schema enum but not in _ACCOUNT_CATEGORY_MAP: %s' % extras)

    def test_table_analysis_pricing_mode_enum(self):
        """table_analysis.pricing_mode must have valid enum values."""
        schema = self._get_schema()
        ta = schema['properties']['table_analysis']
        pm = ta['properties']['pricing_mode']
        self.assertIn('enum', pm)
        for val in ('ht_to_ttc', 'ttc_only', 'ht_only'):
            self.assertIn(val, pm['enum'])

    def test_invoice_has_reverse_charge_fields(self):
        """Invoice schema must include is_reverse_charge and reverse_charge_text."""
        schema = self._get_schema()
        inv_props = schema['properties']['invoice']['properties']
        self.assertIn('is_reverse_charge', inv_props)
        self.assertIn('reverse_charge_text', inv_props)

    def test_line_has_shipping_field(self):
        """Line schema must include is_shipping_line as boolean."""
        schema = self._get_schema()
        line_props = schema['properties']['lines']['items']['properties']
        self.assertIn('is_shipping_line', line_props)
        self.assertEqual(line_props['is_shipping_line']['type'], 'boolean')


@tagged('post_install', '-at_install')
class TestPromptTemplates(TransactionCase):
    """Validate prompt templates and label dictionaries."""

    def test_fiscal_context_template_formatting(self):
        """FISCAL_CONTEXT_TEMPLATE should format without errors."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_prompt import FISCAL_CONTEXT_TEMPLATE

        result = FISCAL_CONTEXT_TEMPLATE.format(
            company_name='Test Company',
            country_code='FR',
            currency='EUR',
            chart_name='Plan Comptable Général',
            account_section='6XXXXX - Charges',
            tax_list='TVA 20%',
            vendor_context='',
        )
        self.assertIn('Test Company', result)
        self.assertIn('FR', result)

    def test_fiscal_context_tax_only_template(self):
        """FISCAL_CONTEXT_TAX_ONLY_TEMPLATE should format without errors."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_prompt import FISCAL_CONTEXT_TAX_ONLY_TEMPLATE

        result = FISCAL_CONTEXT_TAX_ONLY_TEMPLATE.format(
            company_name='Test Company',
            country_code='CH',
            currency='CHF',
            tax_list='TVA 7.7%',
        )
        self.assertIn('Test Company', result)

    def test_account_section_templates(self):
        """Account section templates should format without errors."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_prompt import (
            ACCOUNT_SECTION_NEW_COMPANY,
            ACCOUNT_SECTION_NO_VENDOR,
            ACCOUNT_SECTION_VENDOR,
        )

        ACCOUNT_SECTION_VENDOR.format(
            vendor_accounts='607100 - Marchandises',
            company_accounts='606400 - Fournitures',
        )
        ACCOUNT_SECTION_NO_VENDOR.format(
            company_accounts='606400 - Fournitures',
            all_accounts='601 - Achats',
        )
        ACCOUNT_SECTION_NEW_COMPANY.format(
            all_accounts='601 - Achats',
        )

    def test_all_total_patterns_populated(self):
        """ALL_TOTAL_PATTERNS should contain patterns from all languages."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_prompt import ALL_TOTAL_PATTERNS

        self.assertGreater(len(ALL_TOTAL_PATTERNS), 50)
        self.assertIn('Total HT', ALL_TOTAL_PATTERNS)
        self.assertIn('Subtotal', ALL_TOTAL_PATTERNS)
        self.assertIn('Nettobetrag', ALL_TOTAL_PATTERNS)
        self.assertIn('Base imponible', ALL_TOTAL_PATTERNS)

    def test_label_dicts_non_empty(self):
        """Each label dictionary must have entries for at least fr and en."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_prompt import (
            TAX_LABELS,
            TOTAL_LABELS,
            UNTAXED_LABELS,
        )

        for labels, name in [
            (UNTAXED_LABELS, 'UNTAXED_LABELS'),
            (TAX_LABELS, 'TAX_LABELS'),
            (TOTAL_LABELS, 'TOTAL_LABELS'),
        ]:
            self.assertIn('fr', labels, '%s missing French labels' % name)
            self.assertIn('en', labels, '%s missing English labels' % name)
            self.assertGreater(len(labels['fr']), 0, '%s has empty French list' % name)
            self.assertGreater(len(labels['en']), 0, '%s has empty English list' % name)

    def test_extraction_schema_text_mentions_categories(self):
        """EXTRACTION_SCHEMA text should mention category values."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_prompt import EXTRACTION_SCHEMA

        self.assertIn('suggested_account_category', EXTRACTION_SCHEMA)
        self.assertIn('shipping', EXTRACTION_SCHEMA)
        self.assertIn('merchandise', EXTRACTION_SCHEMA)

    def test_system_prompt_not_empty(self):
        """SYSTEM_PROMPT should be non-empty and mention JSON."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_prompt import SYSTEM_PROMPT

        self.assertGreater(len(SYSTEM_PROMPT), 100)
        self.assertIn('JSON', SYSTEM_PROMPT)

    def test_keyword_lists_non_empty(self):
        """INVOICE_KEYWORDS, PROFORMA_KEYWORDS, PAID_KEYWORDS must be non-empty."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_prompt import (
            INVOICE_KEYWORDS,
            PAID_KEYWORDS,
            PROFORMA_KEYWORDS,
        )

        self.assertGreater(len(INVOICE_KEYWORDS), 0)
        self.assertGreater(len(PROFORMA_KEYWORDS), 0)
        self.assertGreater(len(PAID_KEYWORDS), 0)
        self.assertIn('Facture', INVOICE_KEYWORDS)
        self.assertIn('Invoice', INVOICE_KEYWORDS)
