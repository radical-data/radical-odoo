"""Azure Document Intelligence pre-processor.

Uses the prebuilt-invoice model for structured invoice extraction and
prebuilt-read for OCR-only text extraction. Zero external dependencies
— raw HTTP via ``requests``.

API reference:
    POST {endpoint}/documentintelligence/documentModels/prebuilt-invoice:analyze
    Auth: Ocp-Apim-Subscription-Key header
    Async: POST returns 202 + Operation-Location, poll GET until succeeded.
"""

import base64
import logging
import time

import requests

from .ai_preprocessor import DocumentPreprocessor
from . import ai_preprocessor_normalize as _norm

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_API_VERSION = '2024-11-30'
_POLL_INTERVAL = 2  # seconds between status checks
_MAX_POLL_ATTEMPTS = 60  # 2 minutes max
_REQUEST_TIMEOUT = 30  # seconds for individual HTTP calls
_COST_PER_PAGE = 0.01  # USD per page (prebuilt models)


class AzureDocumentIntelligence(DocumentPreprocessor):
    """Azure Document Intelligence (prebuilt-invoice) pre-processor."""

    def get_provider_name(self):
        return 'Azure Document Intelligence'

    def estimate_cost_per_page(self):
        return _COST_PER_PAGE

    # ------------------------------------------------------------------
    # Credential validation
    # ------------------------------------------------------------------

    def validate_credentials(self, credentials):
        """Validate by listing available document models."""
        endpoint = (credentials.get('endpoint') or '').rstrip('/')
        api_key = credentials.get('api_key') or ''
        if not endpoint or not api_key:
            return False, 'Endpoint and API key are required.'
        try:
            resp = requests.get(
                '%s/documentintelligence/documentModels?api-version=%s' % (endpoint, _API_VERSION),
                headers={'Ocp-Apim-Subscription-Key': api_key},
                timeout=_REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                return True, 'Connection successful.'
            msg = 'Invalid API key.' if resp.status_code == 401 else 'Unexpected status %d.' % resp.status_code
            return False, msg
        except requests.Timeout:
            return False, 'Connection timed out.'
        except requests.ConnectionError:
            return False, 'Could not reach Azure endpoint.'
        except Exception:
            _logger.exception('Azure API key validation failed')
            return False, 'Validation failed. Check server logs for details.'

    # ------------------------------------------------------------------
    # Structured extraction (prebuilt-invoice)
    # ------------------------------------------------------------------

    def extract_structured(self, credentials, document_bytes, mimetype):
        endpoint = (credentials.get('endpoint') or '').rstrip('/')
        api_key = credentials.get('api_key') or ''

        try:
            result = self._analyze(endpoint, api_key, 'prebuilt-invoice', document_bytes)
        except Exception:
            _logger.exception('Azure structured extraction failed')
            return self._error_result('Azure extraction failed. Check server logs.')

        if result.get('status') != 'succeeded':
            msg = 'Analysis did not succeed (status=%s)' % result.get('status')
            return self._error_result(msg)

        analyze_result = result.get('analyzeResult', {})
        documents = analyze_result.get('documents', [])
        page_count = len(analyze_result.get('pages', []))

        # Normalize first document (usually one invoice per PDF)
        fields = documents[0].get('fields', {}) if documents else {}
        data = _normalize_azure_fields(fields)
        overall_confidence = _compute_overall_confidence(fields)
        full_text = analyze_result.get('content', '')

        return {
            'success': True,
            'data': data,
            'text': full_text,
            'confidence': overall_confidence,
            'page_count': page_count,
            'cost_per_page': _COST_PER_PAGE,
            'provider': 'azure_di',
            'raw_response': result,
            'error': None,
        }

    # ------------------------------------------------------------------
    # Text-only extraction (prebuilt-read)
    # ------------------------------------------------------------------

    def extract_text(self, credentials, document_bytes, mimetype):
        endpoint = (credentials.get('endpoint') or '').rstrip('/')
        api_key = credentials.get('api_key') or ''

        try:
            result = self._analyze(endpoint, api_key, 'prebuilt-read', document_bytes)
        except Exception as exc:
            _logger.warning('Azure text extraction failed: %s', exc)
            return {
                'success': False,
                'text': '',
                'page_count': 0,
                'cost_per_page': _COST_PER_PAGE,
                'provider': 'azure_di',
                'error': str(exc),
            }

        if result.get('status') != 'succeeded':
            return {
                'success': False,
                'text': '',
                'page_count': 0,
                'cost_per_page': _COST_PER_PAGE,
                'provider': 'azure_di',
                'error': 'Analysis did not succeed (status=%s)' % result.get('status'),
            }

        analyze_result = result.get('analyzeResult', {})
        full_text = analyze_result.get('content', '')
        page_count = len(analyze_result.get('pages', []))

        return {
            'success': True,
            'text': full_text,
            'page_count': page_count,
            'cost_per_page': _COST_PER_PAGE,
            'provider': 'azure_di',
            'error': None,
        }

    # ------------------------------------------------------------------
    # Internal: submit + poll
    # ------------------------------------------------------------------

    def _analyze(self, endpoint, api_key, model_id, document_bytes):
        """Submit a document for analysis and poll until complete."""
        url = '%s/documentintelligence/documentModels/%s:analyze?api-version=%s' % (
            endpoint,
            model_id,
            _API_VERSION,
        )
        headers = {
            'Ocp-Apim-Subscription-Key': api_key,
            'Content-Type': 'application/json',
        }
        body = {'base64Source': base64.b64encode(document_bytes).decode('ascii')}

        # Retry on transient errors (429, 5xx, network)
        last_exc = None
        for attempt in range(3):
            try:
                resp = requests.post(url, json=body, headers=headers, timeout=_REQUEST_TIMEOUT)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_exc = exc
                _logger.warning('Azure submit attempt %d failed: %s', attempt + 1, exc)
                time.sleep(2 ** (attempt + 1))
                continue
            if resp.status_code in (429, 503):
                _logger.warning('Azure submit returned %d, retrying…', resp.status_code)
                time.sleep(2 ** (attempt + 1))
                continue
            if resp.status_code not in (200, 202):
                raise RuntimeError('Azure returned HTTP %d: %s' % (resp.status_code, resp.text[:500]))
            break
        else:
            raise RuntimeError('Azure submit failed after 3 retries: %s' % last_exc)

        # Synchronous result (rare)
        if resp.status_code == 200:
            return resp.json()

        # Async: poll Operation-Location
        operation_url = resp.headers.get('Operation-Location') or resp.headers.get('operation-location')
        if not operation_url:
            raise RuntimeError('No Operation-Location header in Azure 202 response')

        return self._poll(operation_url, api_key)

    def _poll(self, operation_url, api_key):
        """Poll an async operation until it completes or times out."""
        headers = {'Ocp-Apim-Subscription-Key': api_key}
        for _attempt in range(_MAX_POLL_ATTEMPTS):
            time.sleep(_POLL_INTERVAL)
            try:
                resp = requests.get(operation_url, headers=headers, timeout=_REQUEST_TIMEOUT)
            except (requests.Timeout, requests.ConnectionError):
                _logger.warning('Azure poll attempt %d failed, retrying…', _attempt + 1)
                continue
            if resp.status_code >= 500:
                _logger.warning('Azure poll returned %d, retrying…', resp.status_code)
                continue
            if resp.status_code != 200:
                raise RuntimeError('Azure poll returned HTTP %d' % resp.status_code)
            data = resp.json()
            status = data.get('status', '')
            if status in ('succeeded', 'failed', 'canceled'):
                return data
        raise RuntimeError('Azure analysis timed out after %d seconds' % (_MAX_POLL_ATTEMPTS * _POLL_INTERVAL))

    # ------------------------------------------------------------------
    # Error helper
    # ------------------------------------------------------------------

    @staticmethod
    def _error_result(message):
        return {
            'success': False,
            'data': None,
            'text': '',
            'confidence': 0.0,
            'page_count': 0,
            'cost_per_page': _COST_PER_PAGE,
            'provider': 'azure_di',
            'raw_response': {},
            'error': message,
        }


# ======================================================================
# Normalization: Azure fields → extraction schema
# ======================================================================


def _normalize_azure_fields(fields):
    """Convert Azure prebuilt-invoice fields to our extraction schema."""
    vendor = _norm.build_vendor(
        name=_field_value(fields, 'VendorName', ''),
        vat=_field_value(fields, 'VendorTaxId', ''),
        address=_field_value(fields, 'VendorAddress', ''),
        confidence=_field_confidence(fields, 'VendorName'),
    )

    buyer = _norm.build_buyer(
        name=_field_value(fields, 'CustomerName', ''),
        vat=_field_value(fields, 'CustomerTaxId', None),
        address=_field_value(fields, 'CustomerAddress', None),
        confidence=_field_confidence(fields, 'CustomerName'),
    )

    invoice = _norm.build_invoice(
        reference=_field_value(fields, 'InvoiceId', ''),
        invoice_date=_date_value(fields, 'InvoiceDate'),
        invoice_date_raw=_field_value(fields, 'InvoiceDate', ''),
        due_date=_date_value(fields, 'DueDate'),
        due_date_raw=_field_value(fields, 'DueDate', ''),
        currency=_currency_value(fields),
        payment_reference=_field_value(fields, 'PaymentTerm', None),
        payment_terms_text=_field_value(fields, 'PaymentTerm', None),
        purchase_order_ref=_field_value(fields, 'PurchaseOrder', None),
        original_invoice_ref=_field_value(fields, 'PreviousUnpaidInvoiceId', None),
        confidence=_field_confidence(fields, 'InvoiceId'),
    )

    totals = _norm.build_totals(
        untaxed_amount=_amount_value(fields, 'SubTotal'),
        tax_amount=_amount_value(fields, 'TotalTax'),
        total_amount=_amount_value(fields, 'InvoiceTotal'),
        confidence=_field_confidence(fields, 'InvoiceTotal'),
    )

    lines = _normalize_lines(fields)

    return _norm.build_result(vendor, buyer, invoice, totals, lines)


def _normalize_lines(fields):
    """Extract line items from Azure Items array."""
    items_field = fields.get('Items')
    if not items_field:
        return []
    items = items_field.get('valueArray', [])
    lines = []
    for item in items:
        item_fields = item.get('valueObject', {})
        line = {
            'description': _obj_value(item_fields, 'Description', ''),
            'product_code': _obj_value(item_fields, 'ProductCode', None),
            'quantity': _obj_amount(item_fields, 'Quantity', 1.0),
            'unit_price': _obj_amount(item_fields, 'UnitPrice', 0.0),
            'subtotal_untaxed': _obj_amount(item_fields, 'Amount', 0.0),
            'tax_rate': _obj_amount(item_fields, 'Tax', None),
            'suggested_account_category': None,
            'discount_percent': None,
            'confidence': _obj_confidence(item_fields, 'Amount'),
        }
        lines.append(line)
    return lines


def _compute_overall_confidence(fields):
    """Compute an overall confidence score from key Azure fields."""
    key_fields = ['VendorName', 'InvoiceId', 'InvoiceDate', 'InvoiceTotal']
    confidences = []
    for fname in key_fields:
        f = fields.get(fname)
        if f and 'confidence' in f:
            confidences.append(f['confidence'])
    if not confidences:
        return 0.0
    return sum(confidences) / len(confidences)


# ======================================================================
# Azure field access helpers
# ======================================================================


def _field_value(fields, key, default):
    """Get the content/value string from an Azure field."""
    f = fields.get(key)
    if not f:
        return default
    return f.get('content') or f.get('valueString') or default


def _field_confidence(fields, key):
    """Get confidence from an Azure field (0.0-1.0)."""
    f = fields.get(key)
    if not f:
        return 0.0
    return f.get('confidence', 0.0)


def _date_value(fields, key):
    """Get a date from an Azure field as ISO YYYY-MM-DD string."""
    f = fields.get(key)
    if not f:
        return None
    # Azure valueDate is already YYYY-MM-DD for date fields
    val = f.get('valueDate')
    if val:
        return str(val)
    # Fallback: try content string
    content = f.get('content', '')
    if content and len(content) == 10 and content[4] == '-':
        return content
    return None


def _amount_value(fields, key):
    """Get a numeric amount from an Azure field."""
    f = fields.get(key)
    if not f:
        return 0.0
    # Azure valueCurrency has amount and currencyCode
    vc = f.get('valueCurrency')
    if vc and 'amount' in vc:
        return float(vc['amount'])
    # valueNumber fallback
    vn = f.get('valueNumber')
    if vn is not None:
        return float(vn)
    return 0.0


def _currency_value(fields):
    """Extract currency code from InvoiceTotal or first currency field."""
    for key in ('InvoiceTotal', 'SubTotal', 'TotalTax'):
        f = fields.get(key)
        if f:
            vc = f.get('valueCurrency')
            if vc and vc.get('currencyCode'):
                return vc['currencyCode']
    return ''


def _obj_value(obj, key, default):
    """Get value from an Azure valueObject sub-field."""
    f = obj.get(key)
    if not f:
        return default
    return f.get('content') or f.get('valueString') or default


def _obj_amount(obj, key, default):
    """Get numeric amount from an Azure valueObject sub-field."""
    f = obj.get(key)
    if not f:
        return default
    vc = f.get('valueCurrency')
    if vc and 'amount' in vc:
        return float(vc['amount'])
    vn = f.get('valueNumber')
    if vn is not None:
        return float(vn)
    return default


def _obj_confidence(obj, key):
    """Get confidence from an Azure valueObject sub-field."""
    f = obj.get(key)
    if not f:
        return 0.0
    return f.get('confidence', 0.0)
