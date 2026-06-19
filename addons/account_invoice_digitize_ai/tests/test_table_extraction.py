from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTableExtraction(TransactionCase):
    """Test pdfplumber-based table extraction from PDFs."""

    # -----------------------------------------------------------------
    # extract_tables_from_pdf
    # -----------------------------------------------------------------

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_document.PDFPLUMBER_AVAILABLE', False)
    def test_pdfplumber_not_available(self):
        """When pdfplumber is not installed, return empty list."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_document import extract_tables_from_pdf

        self.assertEqual(extract_tables_from_pdf(b'fake pdf'), [])

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_document.PDFPLUMBER_AVAILABLE', True)
    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_document._pdfplumber')
    def test_valid_single_table(self, mock_plumber):
        """Single page with a valid table → returns 1 table."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_document import extract_tables_from_pdf

        mock_page = MagicMock()
        mock_page.extract_tables.return_value = [
            [
                ['Description', 'Qty', 'Unit Price', 'Total'],
                ['Consulting', '10', '80.00', '800.00'],
                ['Travel', '1', '200.00', '200.00'],
            ]
        ]
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_plumber.open.return_value = mock_pdf

        result = extract_tables_from_pdf(b'fake pdf')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['headers'], ['Description', 'Qty', 'Unit Price', 'Total'])
        self.assertEqual(len(result[0]['rows']), 2)
        self.assertEqual(result[0]['rows'][0], ['Consulting', '10', '80.00', '800.00'])
        self.assertIn(1, result[0]['page_numbers'])

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_document.PDFPLUMBER_AVAILABLE', True)
    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_document._pdfplumber')
    def test_no_tables_found(self, mock_plumber):
        """Pages with no tables → returns empty list."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_document import extract_tables_from_pdf

        mock_page = MagicMock()
        mock_page.extract_tables.return_value = []
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_plumber.open.return_value = mock_pdf

        self.assertEqual(extract_tables_from_pdf(b'fake pdf'), [])

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_document.PDFPLUMBER_AVAILABLE', True)
    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_document._pdfplumber')
    def test_none_cells_cleaned(self, mock_plumber):
        """Cells with None values are replaced by empty strings."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_document import extract_tables_from_pdf

        mock_page = MagicMock()
        mock_page.extract_tables.return_value = [
            [
                ['Description', 'Qty', 'Total'],
                ['Item A', None, '100.00'],
                [None, '2', '200.00'],
            ]
        ]
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_plumber.open.return_value = mock_pdf

        result = extract_tables_from_pdf(b'fake pdf')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['rows'][0], ['Item A', '', '100.00'])
        self.assertEqual(result[0]['rows'][1], ['', '2', '200.00'])

    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_document.PDFPLUMBER_AVAILABLE', True)
    @patch('odoo.addons.account_invoice_digitize_ai.models.ai_document._pdfplumber')
    def test_pdfplumber_exception(self, mock_plumber):
        """pdfplumber.open() raises → returns empty list (graceful)."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_document import extract_tables_from_pdf

        mock_plumber.open.side_effect = Exception('corrupt PDF')

        self.assertEqual(extract_tables_from_pdf(b'bad data'), [])

    # -----------------------------------------------------------------
    # _validate_table
    # -----------------------------------------------------------------

    def test_invalid_no_numeric_column(self):
        """Table with only text columns → invalid."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_document import _validate_table

        table = {
            'headers': ['Description', 'Notes', 'Category'],
            'rows': [
                ['Office supplies', 'Urgent', 'Admin'],
                ['Paper clips', 'Standard', 'Office'],
            ],
        }
        self.assertFalse(_validate_table(table))

    def test_invalid_inconsistent_columns(self):
        """Most rows have different column count than header → invalid."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_document import _validate_table

        table = {
            'headers': ['A', 'B', 'C', 'D'],
            'rows': [
                ['x', 'y'],  # 2 cols instead of 4
                ['a', 'b'],
                ['m', 'n'],
                ['1', '2', '3', '4'],  # only this one matches
            ],
        }
        self.assertFalse(_validate_table(table))

    def test_invalid_no_data_rows(self):
        """Table with headers but no data rows → invalid."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_document import _validate_table

        table = {
            'headers': ['Description', 'Qty', 'Price'],
            'rows': [],
        }
        self.assertFalse(_validate_table(table))

    def test_single_row_valid(self):
        """Table with header + 1 numeric data row → valid."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_document import _validate_table

        table = {
            'headers': ['Description', 'Qty', 'Price'],
            'rows': [
                ['Consulting services', '10', '800.00'],
            ],
        }
        self.assertTrue(_validate_table(table))

    # -----------------------------------------------------------------
    # _merge_multipage_tables
    # -----------------------------------------------------------------

    def test_multipage_merge(self):
        """Two pages with same structure → merged into 1 table."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_document import _merge_multipage_tables

        raw = [
            (1, ['Description', 'Qty', 'Total'], [['Item A', '1', '100']]),
            (2, ['Description', 'Qty', 'Total'], [['Item B', '2', '200']]),
        ]
        result = _merge_multipage_tables(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]['rows']), 2)
        self.assertEqual(result[0]['page_numbers'], [1, 2])

    def test_multipage_different_tables(self):
        """Two pages with different structure → 2 separate tables."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_document import _merge_multipage_tables

        raw = [
            (1, ['Description', 'Qty', 'Total'], [['Item A', '1', '100']]),
            (2, ['Rate', 'Base', 'Tax', 'Total'], [['20%', '100', '20', '120']]),
        ]
        result = _merge_multipage_tables(raw)
        self.assertEqual(len(result), 2)

    # -----------------------------------------------------------------
    # format_tables_as_markdown
    # -----------------------------------------------------------------

    def test_format_markdown(self):
        """Known table → valid markdown output."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_document import format_tables_as_markdown

        tables = [
            {
                'headers': ['Description', 'Qty', 'Total'],
                'rows': [
                    ['Consulting', '10', '800.00'],
                    ['Travel', '1', '200.00'],
                ],
                'page_numbers': [1],
            }
        ]
        md = format_tables_as_markdown(tables)
        self.assertIn('| Description | Qty | Total |', md)
        self.assertIn('|---|---|---|', md)
        self.assertIn('| Consulting | 10 | 800.00 |', md)
        self.assertIn('| Travel | 1 | 200.00 |', md)

    def test_format_empty(self):
        """No tables → empty string."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_document import format_tables_as_markdown

        self.assertEqual(format_tables_as_markdown([]), '')
