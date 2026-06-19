from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestQrSpcParsing(TransactionCase):
    """Test Swiss QR-bill (SPC) payload parsing."""

    def _parse(self, payload):
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        return ai_qr_decoder.parse_qr_payload(payload)

    def _make_spc(self, **overrides):
        """Build a minimal valid SPC payload (30+ lines)."""
        lines = [''] * 32
        lines[0] = 'SPC'
        lines[1] = '0200'
        lines[2] = '1'
        lines[3] = overrides.get('iban', 'CH93 0076 2011 6238 5295 7')
        lines[4] = overrides.get('cr_type', 'S')
        lines[5] = overrides.get('cr_name', 'Robert Schneider AG')
        lines[6] = overrides.get('cr_street', 'Rue du Lac')
        lines[7] = overrides.get('cr_building', '1268')
        lines[8] = overrides.get('cr_postal', '2501')
        lines[9] = overrides.get('cr_city', 'Biel')
        lines[10] = overrides.get('cr_country', 'CH')
        # lines 11-16: ultimate creditor (empty)
        lines[17] = overrides.get('amount', '1949.75')
        lines[18] = overrides.get('currency', 'CHF')
        lines[19] = overrides.get('db_type', 'S')
        lines[20] = overrides.get('db_name', 'Pia-Maria Rutschmann-Schnyder')
        lines[21] = overrides.get('db_street', 'Grosse Marktgasse')
        lines[22] = overrides.get('db_building', '28')
        lines[23] = overrides.get('db_postal', '9400')
        lines[24] = overrides.get('db_city', 'Rorschach')
        lines[25] = overrides.get('db_country', 'CH')
        lines[26] = overrides.get('ref_type', 'QRR')
        lines[27] = overrides.get('reference', '210000000003139471430009017')
        lines[28] = overrides.get('message', '')
        lines[29] = 'EPD'
        if overrides.get('extra_lines'):
            lines.extend(overrides['extra_lines'])
        return '\n'.join(lines)

    def test_parse_spc_valid_qrr(self):
        """Valid SPC with QRR reference is parsed correctly."""
        result = self._parse(self._make_spc())
        self.assertEqual(result['format'], 'spc')
        self.assertEqual(result['iban'], 'CH93 0076 2011 6238 5295 7')
        self.assertEqual(result['amount'], 1949.75)
        self.assertEqual(result['currency'], 'CHF')
        self.assertEqual(result['creditor_name'], 'Robert Schneider AG')
        self.assertEqual(result['reference_type'], 'QRR')
        self.assertEqual(result['reference'], '210000000003139471430009017')
        self.assertEqual(result['debtor_name'], 'Pia-Maria Rutschmann-Schnyder')
        self.assertIsNone(result['bic'])

    def test_parse_spc_valid_scor(self):
        """SPC with SCOR reference type."""
        result = self._parse(self._make_spc(ref_type='SCOR', reference='RF18539007547034'))
        self.assertEqual(result['format'], 'spc')
        self.assertEqual(result['reference_type'], 'SCOR')
        self.assertEqual(result['reference'], 'RF18539007547034')

    def test_parse_spc_no_amount(self):
        """SPC with empty amount (open amount)."""
        result = self._parse(self._make_spc(amount=''))
        self.assertEqual(result['format'], 'spc')
        self.assertIsNone(result['amount'])
        self.assertEqual(result['currency'], 'CHF')

    def test_parse_spc_no_debtor(self):
        """SPC without debtor info."""
        result = self._parse(
            self._make_spc(
                db_type='', db_name='', db_street='', db_building='', db_postal='', db_city='', db_country=''
            )
        )
        self.assertEqual(result['format'], 'spc')
        self.assertIsNone(result['debtor_name'])

    def test_parse_spc_eur(self):
        """SPC with EUR currency."""
        result = self._parse(self._make_spc(currency='EUR', amount='250.00'))
        self.assertEqual(result['format'], 'spc')
        self.assertEqual(result['currency'], 'EUR')
        self.assertEqual(result['amount'], 250.0)

    def test_parse_spc_invalid_header(self):
        """SPC with wrong header is rejected."""
        payload = self._make_spc().replace('SPC', 'XXX', 1)
        result = self._parse(payload)
        self.assertEqual(result['format'], 'unknown')

    def test_parse_spc_too_few_lines(self):
        """SPC with too few lines is rejected."""
        result = self._parse('SPC\n0200\n1\nCH93...\n')
        self.assertEqual(result['format'], 'unknown')

    def test_parse_spc_combined_address(self):
        """SPC with combined address type K."""
        result = self._parse(self._make_spc(cr_type='K', cr_street='Rue du Lac 1268', cr_building='2501 Biel'))
        self.assertEqual(result['format'], 'spc')
        self.assertIn('Rue du Lac 1268', result['creditor_address'])
        self.assertIn('2501 Biel', result['creditor_address'])


@tagged('post_install', '-at_install')
class TestQrEpcParsing(TransactionCase):
    """Test EPC/BCD QR code payload parsing."""

    def _parse(self, payload):
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        return ai_qr_decoder.parse_qr_payload(payload)

    def _make_epc(self, **overrides):
        """Build a minimal valid EPC/BCD payload."""
        lines = [
            overrides.get('header', 'BCD'),
            overrides.get('version', '002'),
            overrides.get('coding', '1'),
            overrides.get('ident', 'SCT'),
            overrides.get('bic', 'BPOTBEB1'),
            overrides.get('name', 'Red Cross of Belgium'),
            overrides.get('iban', 'BE72000000001616'),
            overrides.get('amount', 'EUR1.00'),
            overrides.get('purpose', ''),
            overrides.get('reference', ''),
            overrides.get('message', 'Donation'),
        ]
        return '\n'.join(lines)

    def test_parse_epc_valid(self):
        """Valid EPC QR is parsed correctly."""
        result = self._parse(self._make_epc())
        self.assertEqual(result['format'], 'epc')
        self.assertEqual(result['iban'], 'BE72000000001616')
        self.assertEqual(result['amount'], 1.0)
        self.assertEqual(result['currency'], 'EUR')
        self.assertEqual(result['creditor_name'], 'Red Cross of Belgium')
        self.assertEqual(result['bic'], 'BPOTBEB1')
        self.assertEqual(result['message'], 'Donation')

    def test_parse_epc_no_bic(self):
        """EPC without BIC."""
        result = self._parse(self._make_epc(bic=''))
        self.assertEqual(result['format'], 'epc')
        self.assertIsNone(result['bic'])

    def test_parse_epc_with_reference(self):
        """EPC with structured SCOR reference."""
        result = self._parse(self._make_epc(reference='RF18539007547034'))
        self.assertEqual(result['format'], 'epc')
        self.assertEqual(result['reference'], 'RF18539007547034')
        self.assertEqual(result['reference_type'], 'SCOR')

    def test_parse_epc_no_amount(self):
        """EPC without amount."""
        result = self._parse(self._make_epc(amount=''))
        self.assertEqual(result['format'], 'epc')
        self.assertIsNone(result['amount'])

    def test_parse_epc_invalid_header(self):
        """EPC with wrong header is rejected."""
        result = self._parse(self._make_epc(header='XXX'))
        self.assertEqual(result['format'], 'unknown')


@tagged('post_install', '-at_install')
class TestQrReferenceValidation(TransactionCase):
    """Test QRR and SCOR reference validation."""

    def test_qrr_valid(self):
        """Valid QRR reference passes mod-10 check."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        # Standard Swiss QR reference (27 digits, valid mod-10)
        self.assertTrue(ai_qr_decoder.validate_qrr_reference('210000000003139471430009017'))

    def test_qrr_invalid(self):
        """QRR with wrong check digit fails."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        self.assertFalse(ai_qr_decoder.validate_qrr_reference('210000000003139471430009010'))

    def test_qrr_wrong_length(self):
        """QRR with wrong length is rejected."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        self.assertFalse(ai_qr_decoder.validate_qrr_reference('12345'))

    def test_qrr_empty(self):
        """Empty QRR returns False."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        self.assertFalse(ai_qr_decoder.validate_qrr_reference(''))
        self.assertFalse(ai_qr_decoder.validate_qrr_reference(None))

    def test_scor_valid(self):
        """Valid SCOR reference passes mod-97 check."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        self.assertTrue(ai_qr_decoder.validate_scor_reference('RF18539007547034'))

    def test_scor_invalid(self):
        """SCOR with wrong check digits fails."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        self.assertFalse(ai_qr_decoder.validate_scor_reference('RF00539007547034'))

    def test_scor_empty(self):
        """Empty SCOR returns False."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        self.assertFalse(ai_qr_decoder.validate_scor_reference(''))
        self.assertFalse(ai_qr_decoder.validate_scor_reference(None))


@tagged('post_install', '-at_install')
class TestQrCrossValidation(TransactionCase):
    """Test QR data cross-validation against AI extraction."""

    def _validate(self, qr_list, data):
        from odoo.addons.account_invoice_digitize_ai.models import ai_validator

        data['_qr_data'] = qr_list
        return ai_validator.cross_validate(data)

    def _make_qr(self, **overrides):
        return {
            'format': overrides.get('format', 'spc'),
            'iban': overrides.get('iban', 'CH93 0076 2011 6238 5295 7'),
            'amount': overrides.get('amount', 1949.75),
            'currency': overrides.get('currency', 'CHF'),
            'creditor_name': overrides.get('creditor_name', 'Test AG'),
            'creditor_address': None,
            'reference_type': overrides.get('reference_type', 'QRR'),
            'reference': overrides.get('reference', '210000000003139471430009017'),
            'bic': None,
            'message': None,
            'debtor_name': None,
            'debtor_address': None,
        }

    def _make_data(self, **overrides):
        return {
            'vendor': {
                'iban': overrides.get('iban', 'CH93 0076 2011 6238 5295 7'),
                'confidence': 0.9,
            },
            'invoice': {
                'currency': overrides.get('currency', 'CHF'),
                'payment_reference': overrides.get('payment_reference', None),
                'confidence': 0.9,
            },
            'totals': {
                'total_amount': overrides.get('total_amount', 1949.75),
                'untaxed_amount': overrides.get('untaxed_amount', 1949.75),
                'tax_amount': overrides.get('tax_amount', 0.0),
                'confidence': 0.9,
            },
        }

    def test_cv_iban_mismatch(self):
        """QR IBAN overrides AI IBAN when different."""
        qr = self._make_qr(iban='CH93 0076 2011 6238 5295 7')
        data = self._make_data(iban='DE89 3704 0044 0532 0130 00')
        failures = self._validate([qr], data)
        self.assertGreaterEqual(failures, 1)
        # QR IBAN should override AI IBAN (cleaned — no spaces)
        self.assertEqual(data['vendor']['iban'], 'CH9300762011623852957')

    def test_cv_iban_injected(self):
        """QR IBAN injected when AI has none."""
        qr = self._make_qr(iban='CH93 0076 2011 6238 5295 7')
        data = self._make_data(iban=None)
        data['vendor']['iban'] = None
        failures = self._validate([qr], data)
        self.assertEqual(data['vendor']['iban'], 'CH9300762011623852957')
        # Injection is not a failure
        self.assertEqual(failures, 0)

    def test_cv_amount_mismatch(self):
        """Amount mismatch is penalized but not overridden."""
        qr = self._make_qr(amount=2000.00)
        data = self._make_data(total_amount=1949.75)
        failures = self._validate([qr], data)
        self.assertGreaterEqual(failures, 1)
        # AI total is NOT overridden
        self.assertEqual(data['totals']['total_amount'], 1949.75)

    def test_cv_amount_match(self):
        """Matching amounts produce no failure."""
        qr = self._make_qr(amount=1949.75)
        data = self._make_data(total_amount=1949.75)
        failures = self._validate([qr], data)
        self.assertEqual(failures, 0)

    def test_cv_currency_mismatch(self):
        """QR currency overrides AI currency."""
        qr = self._make_qr(currency='CHF')
        data = self._make_data(currency='EUR')
        failures = self._validate([qr], data)
        self.assertGreaterEqual(failures, 1)
        self.assertEqual(data['invoice']['currency'], 'CHF')

    def test_cv_no_qr_data(self):
        """No QR data produces zero failures."""
        data = self._make_data()
        from odoo.addons.account_invoice_digitize_ai.models import ai_validator

        failures = ai_validator.cross_validate(data)
        self.assertEqual(failures, 0)

    def test_cv_reference_injected(self):
        """QR reference injected when AI has none."""
        qr = self._make_qr(reference='210000000003139471430009017')
        data = self._make_data(payment_reference=None)
        self._validate([qr], data)
        self.assertEqual(data['invoice']['payment_reference'], '210000000003139471430009017')


@tagged('post_install', '-at_install')
class TestQrContextFormatting(TransactionCase):
    """Test QR data formatting for prompt injection."""

    def test_format_spc_context(self):
        """SPC QR data is formatted for prompt."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        qr_data = [
            {
                'format': 'spc',
                'iban': 'CH93 0076 2011 6238 5295 7',
                'amount': 1949.75,
                'currency': 'CHF',
                'creditor_name': 'Robert Schneider AG',
                'creditor_address': 'Rue du Lac 1268, 2501 Biel, CH',
                'reference_type': 'QRR',
                'reference': '210000000003139471430009017',
                'bic': None,
                'message': None,
                'debtor_name': None,
                'debtor_address': None,
            }
        ]
        ctx = ai_qr_decoder.format_qr_context(qr_data)
        self.assertIn('QR CODE DATA', ctx)
        self.assertIn('Swiss QR-bill', ctx)
        self.assertIn('CH93 0076 2011 6238 5295 7', ctx)
        self.assertIn('1949.75', ctx)
        self.assertIn('Robert Schneider AG', ctx)

    def test_format_epc_context(self):
        """EPC QR data is formatted for prompt."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        qr_data = [
            {
                'format': 'epc',
                'iban': 'BE72000000001616',
                'amount': 1.0,
                'currency': 'EUR',
                'creditor_name': 'Red Cross',
                'creditor_address': None,
                'reference_type': None,
                'reference': None,
                'bic': 'BPOTBEB1',
                'message': 'Donation',
                'debtor_name': None,
                'debtor_address': None,
            }
        ]
        ctx = ai_qr_decoder.format_qr_context(qr_data)
        self.assertIn('EPC QR code', ctx)
        self.assertIn('BE72000000001616', ctx)
        self.assertIn('BPOTBEB1', ctx)

    def test_format_empty(self):
        """Empty QR data list returns empty string."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        self.assertEqual(ai_qr_decoder.format_qr_context([]), '')
        self.assertEqual(ai_qr_decoder.format_qr_context(None), '')


@tagged('post_install', '-at_install')
class TestQrDispatch(TransactionCase):
    """Test QR payload dispatch to correct parser."""

    def test_dispatch_spc(self):
        """SPC payload is dispatched correctly."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        lines = [''] * 32
        lines[0] = 'SPC'
        lines[1] = '0200'
        lines[2] = '1'
        lines[3] = 'CH9300762011623852957'
        lines[18] = 'CHF'
        lines[26] = 'NON'
        lines[29] = 'EPD'
        payload = '\n'.join(lines)
        result = ai_qr_decoder.parse_qr_payload(payload)
        self.assertEqual(result['format'], 'spc')

    def test_dispatch_epc(self):
        """BCD payload is dispatched to EPC parser."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        payload = 'BCD\n002\n1\nSCT\nBPOTBEB1\nRed Cross\nBE72000000001616\nEUR1.00\n\n\nDonation'
        result = ai_qr_decoder.parse_qr_payload(payload)
        self.assertEqual(result['format'], 'epc')

    def test_dispatch_unknown(self):
        """Unknown payload returns format=unknown."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        result = ai_qr_decoder.parse_qr_payload('random text here')
        self.assertEqual(result['format'], 'unknown')

    def test_dispatch_empty(self):
        """Empty payload returns format=unknown."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        result = ai_qr_decoder.parse_qr_payload('')
        self.assertEqual(result['format'], 'unknown')
        result = ai_qr_decoder.parse_qr_payload(None)
        self.assertEqual(result['format'], 'unknown')


@tagged('post_install', '-at_install')
class TestQrPdfExtraction(TransactionCase):
    """Test QR code extraction from PDF images pipeline."""

    def test_returns_empty_without_pyzbar(self):
        """extract_qr_from_pdf returns [] when pyzbar is unavailable."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        with patch.object(ai_qr_decoder, 'PYZBAR_AVAILABLE', False):
            result = ai_qr_decoder.extract_qr_from_pdf(b'%PDF-1.4 fake')
            self.assertEqual(result, [])

    def test_returns_empty_without_pillow(self):
        """extract_qr_from_pdf returns [] when Pillow is unavailable."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        with patch.object(ai_qr_decoder, 'PILLOW_AVAILABLE', False):
            result = ai_qr_decoder.extract_qr_from_pdf(b'%PDF-1.4 fake')
            self.assertEqual(result, [])

    def test_returns_empty_without_pdfreader(self):
        """extract_qr_from_pdf returns [] when PdfReader is unavailable."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        with patch.object(ai_qr_decoder, '_PdfReader', None):
            result = ai_qr_decoder.extract_qr_from_pdf(b'%PDF-1.4 fake')
            self.assertEqual(result, [])

    def test_decode_qr_from_image_none(self):
        """_decode_qr_from_image(None) returns empty list."""
        from odoo.addons.account_invoice_digitize_ai.models.ai_qr_decoder import _decode_qr_from_image

        self.assertEqual(_decode_qr_from_image(None), [])

    def test_decode_qr_pyzbar_unavailable(self):
        """_decode_qr_from_image returns [] when pyzbar is not available."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        mock_img = MagicMock()
        with patch.object(ai_qr_decoder, 'PYZBAR_AVAILABLE', False):
            result = ai_qr_decoder._decode_qr_from_image(mock_img)
            self.assertEqual(result, [])

    def test_pipeline_with_mocked_dependencies(self):
        """Full pipeline should return decoded payloads from mocked PDF."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        mock_page = MagicMock()
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]
        mock_img = MagicMock()
        mock_qr = MagicMock()
        mock_qr.type = 'QRCODE'
        mock_qr.data = b'SPC\n0200\n1\nCH9300762011623852957'

        with patch.object(ai_qr_decoder, 'PYZBAR_AVAILABLE', True), \
                patch.object(ai_qr_decoder, 'PILLOW_AVAILABLE', True), \
                patch.object(ai_qr_decoder, '_PdfReader', return_value=mock_reader), \
                patch.object(ai_qr_decoder, '_extract_images_from_page', return_value=[mock_img]), \
                patch.object(ai_qr_decoder, '_pyzbar_decode', return_value=[mock_qr]):
            result = ai_qr_decoder.extract_qr_from_pdf(b'%PDF fake')
            self.assertEqual(len(result), 1)
            self.assertIn('SPC', result[0])

    def test_deduplicates_identical_payloads(self):
        """extract_qr_from_pdf should deduplicate identical payloads across pages."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        mock_reader = MagicMock()
        mock_reader.pages = [MagicMock(), MagicMock()]
        mock_img = MagicMock()
        mock_qr = MagicMock()
        mock_qr.type = 'QRCODE'
        mock_qr.data = b'same-payload'

        with patch.object(ai_qr_decoder, 'PYZBAR_AVAILABLE', True), \
                patch.object(ai_qr_decoder, 'PILLOW_AVAILABLE', True), \
                patch.object(ai_qr_decoder, '_PdfReader', return_value=mock_reader), \
                patch.object(ai_qr_decoder, '_extract_images_from_page', return_value=[mock_img]), \
                patch.object(ai_qr_decoder, '_pyzbar_decode', return_value=[mock_qr]):
            result = ai_qr_decoder.extract_qr_from_pdf(b'%PDF fake')
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0], 'same-payload')

    def test_max_pages_respected(self):
        """extract_qr_from_pdf should respect max_pages parameter."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        mock_reader = MagicMock()
        mock_reader.pages = [MagicMock() for _ in range(10)]
        extract_calls = []

        def mock_extract(page):
            extract_calls.append(page)
            return []

        with patch.object(ai_qr_decoder, 'PYZBAR_AVAILABLE', True), \
                patch.object(ai_qr_decoder, 'PILLOW_AVAILABLE', True), \
                patch.object(ai_qr_decoder, '_PdfReader', return_value=mock_reader), \
                patch.object(ai_qr_decoder, '_extract_images_from_page', side_effect=mock_extract):
            ai_qr_decoder.extract_qr_from_pdf(b'%PDF fake', max_pages=3)
            self.assertEqual(len(extract_calls), 3)

    def test_exception_returns_empty(self):
        """extract_qr_from_pdf should return [] on any exception."""
        from odoo.addons.account_invoice_digitize_ai.models import ai_qr_decoder

        with patch.object(ai_qr_decoder, 'PYZBAR_AVAILABLE', True), \
                patch.object(ai_qr_decoder, 'PILLOW_AVAILABLE', True), \
                patch.object(ai_qr_decoder, '_PdfReader', side_effect=Exception('corrupted PDF')):
            result = ai_qr_decoder.extract_qr_from_pdf(b'corrupted')
            self.assertEqual(result, [])
