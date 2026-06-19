from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPdfMetadataExtraction(TransactionCase):
    """Test PDF metadata extraction utility."""

    def _extract(self, pdf_bytes):
        from odoo.addons.account_invoice_digitize_ai.models.ai_document import extract_pdf_metadata

        return extract_pdf_metadata(pdf_bytes)

    def test_valid_metadata(self):
        """Metadata fields are extracted and stripped."""
        mock_metadata = {
            '/Author': '  Sage 100  ',
            '/Creator': 'wkhtmltopdf',
            '/Title': 'Invoice 2024-001',
            '/Subject': '',
            '/CreationDate': "D:20240115120000+01'00'",
        }
        mock_reader = MagicMock()
        mock_reader.metadata = mock_metadata

        with patch(
            'odoo.addons.account_invoice_digitize_ai.models.ai_document._PdfReader',
            return_value=mock_reader,
        ):
            result = self._extract(b'fake-pdf-bytes')

        self.assertEqual(result['author'], 'Sage 100')
        self.assertEqual(result['creator'], 'wkhtmltopdf')
        self.assertEqual(result['title'], 'Invoice 2024-001')
        self.assertNotIn('subject', result)  # empty string → excluded
        self.assertIn('creation_date', result)

    def test_no_metadata(self):
        """PDF with no metadata → empty dict."""
        mock_reader = MagicMock()
        mock_reader.metadata = None

        with patch(
            'odoo.addons.account_invoice_digitize_ai.models.ai_document._PdfReader',
            return_value=mock_reader,
        ):
            result = self._extract(b'fake-pdf-bytes')

        self.assertEqual(result, {})

    def test_reader_exception(self):
        """Exception during reading → empty dict (graceful)."""
        with patch(
            'odoo.addons.account_invoice_digitize_ai.models.ai_document._PdfReader',
            side_effect=Exception('corrupt PDF'),
        ):
            result = self._extract(b'corrupt-data')

        self.assertEqual(result, {})

    def test_no_reader_available(self):
        """No PDF reader installed → empty dict."""
        with patch(
            'odoo.addons.account_invoice_digitize_ai.models.ai_document._PdfReader',
            None,
        ):
            result = self._extract(b'fake-pdf-bytes')

        self.assertEqual(result, {})

    def test_metadata_with_special_chars(self):
        """Metadata with unicode characters."""
        mock_metadata = {
            '/Author': 'Société Générale',
            '/Creator': 'Ré©ursif™',
        }
        mock_reader = MagicMock()
        mock_reader.metadata = mock_metadata

        with patch(
            'odoo.addons.account_invoice_digitize_ai.models.ai_document._PdfReader',
            return_value=mock_reader,
        ):
            result = self._extract(b'fake-pdf-bytes')

        self.assertEqual(result['author'], 'Société Générale')
        self.assertEqual(result['creator'], 'Ré©ursif™')

    def test_producer_field(self):
        """Producer field (PDF generation library) is extracted."""
        mock_metadata = {
            '/Producer': 'ReportLab PDF Library',
        }
        mock_reader = MagicMock()
        mock_reader.metadata = mock_metadata

        with patch(
            'odoo.addons.account_invoice_digitize_ai.models.ai_document._PdfReader',
            return_value=mock_reader,
        ):
            result = self._extract(b'fake-pdf-bytes')

        self.assertEqual(result['producer'], 'ReportLab PDF Library')
