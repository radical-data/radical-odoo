"""AWS Textract AnalyzeExpense pre-processor.

Uses AnalyzeExpense for structured invoice extraction and
DetectDocumentText for OCR-only text extraction.
Implements AWS Signature Version 4 manually (zero external dependencies).

API reference:
    POST https://textract.{region}.amazonaws.com/
    Auth: AWS SigV4 signing
    Sync: returns result directly for documents ≤5 pages.
    Async: StartExpenseAnalysis/GetExpenseAnalysis for larger documents.
"""

import base64
import json
import logging

import requests

from .ai_aws_sigv4 import sign_request as _sign_request
from .ai_preprocessor import DocumentPreprocessor
from . import ai_preprocessor_normalize as _norm

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SERVICE = 'textract'
_REQUEST_TIMEOUT = 180
_POLL_INTERVAL = 2
_MAX_POLL_ATTEMPTS = 60
_COST_PER_PAGE = 0.01
_SYNC_PAGE_LIMIT = 5  # AnalyzeExpense sync works up to ~5 pages


class AWSTextract(DocumentPreprocessor):
    """AWS Textract (AnalyzeExpense) pre-processor."""

    def get_provider_name(self):
        return 'AWS Textract'

    def estimate_cost_per_page(self):
        return _COST_PER_PAGE

    # ------------------------------------------------------------------
    # Credential validation
    # ------------------------------------------------------------------

    def validate_credentials(self, credentials):
        """Validate AWS credentials with a minimal Textract call."""
        access_key = credentials.get('access_key_id', '')
        secret_key = credentials.get('secret_access_key', '')
        region = credentials.get('region', 'eu-west-1')
        if not access_key or not secret_key:
            return False, 'Access Key ID and Secret Access Key are required.'
        # Try listing Textract adapters (lightweight operation)
        try:
            endpoint = 'https://textract.%s.amazonaws.com/' % region
            body = '{}'
            headers = {
                'Content-Type': 'application/x-amz-json-1.1',
                'X-Amz-Target': 'Textract.ListAdapters',
            }
            signed_headers = _sign_request(
                'POST',
                endpoint,
                headers,
                body,
                region,
                access_key,
                secret_key,
            )
            resp = requests.post(
                endpoint,
                data=body,
                headers=signed_headers,
                timeout=_REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                return True, 'Connection successful.'
            msg = (
                'Invalid AWS credentials or insufficient permissions.'
                if resp.status_code in (401, 403)
                else 'Unexpected status %d.' % resp.status_code
            )
            return False, msg
        except requests.Timeout:
            return False, 'Connection timed out.'
        except requests.ConnectionError:
            return False, 'Could not reach AWS Textract endpoint.'
        except Exception:
            _logger.exception('AWS Textract API key validation failed')
            return False, 'Validation failed. Check server logs for details.'

    # ------------------------------------------------------------------
    # Structured extraction (AnalyzeExpense)
    # ------------------------------------------------------------------

    def extract_structured(self, credentials, document_bytes, mimetype):
        access_key = credentials.get('access_key_id', '')
        secret_key = credentials.get('secret_access_key', '')
        region = credentials.get('region', 'eu-west-1')

        try:
            result = self._analyze_expense(
                access_key,
                secret_key,
                region,
                document_bytes,
            )
        except Exception:
            _logger.exception('AWS Textract structured extraction failed')
            return self._error_result('AWS Textract extraction failed. Check server logs.')

        expense_docs = result.get('ExpenseDocuments', [])
        if not expense_docs:
            return self._error_result('No expense documents found in Textract response')

        page_count = result.get('DocumentMetadata', {}).get('Pages', 1)
        data = _normalize_textract_response(expense_docs[0])
        overall_confidence = _compute_overall_confidence(expense_docs[0])

        return {
            'success': True,
            'data': data,
            'text': _extract_full_text(expense_docs[0]),
            'confidence': overall_confidence,
            'page_count': page_count,
            'cost_per_page': _COST_PER_PAGE,
            'provider': 'aws_textract',
            'raw_response': result,
            'error': None,
        }

    # ------------------------------------------------------------------
    # Text-only extraction (DetectDocumentText)
    # ------------------------------------------------------------------

    def extract_text(self, credentials, document_bytes, mimetype):
        access_key = credentials.get('access_key_id', '')
        secret_key = credentials.get('secret_access_key', '')
        region = credentials.get('region', 'eu-west-1')

        try:
            result = self._detect_text(access_key, secret_key, region, document_bytes)
        except Exception as exc:
            _logger.warning('AWS Textract text extraction failed: %s', exc)
            return {
                'success': False,
                'text': '',
                'page_count': 0,
                'cost_per_page': _COST_PER_PAGE,
                'provider': 'aws_textract',
                'error': str(exc),
            }

        blocks = result.get('Blocks', [])
        lines = [b.get('Text', '') for b in blocks if b.get('BlockType') == 'LINE']
        page_count = result.get('DocumentMetadata', {}).get('Pages', 1)

        return {
            'success': True,
            'text': '\n'.join(lines),
            'page_count': page_count,
            'cost_per_page': _COST_PER_PAGE,
            'provider': 'aws_textract',
            'error': None,
        }

    # ------------------------------------------------------------------
    # Internal API calls
    # ------------------------------------------------------------------

    def _call_textract(self, access_key, secret_key, region, target, document_bytes):
        """Call a Textract API action with retry on transient errors."""
        import time as _time

        endpoint = 'https://textract.%s.amazonaws.com/' % region
        payload = {
            'Document': {
                'Bytes': base64.b64encode(document_bytes).decode('ascii'),
            },
        }
        body = json.dumps(payload)
        headers = {
            'Content-Type': 'application/x-amz-json-1.1',
            'X-Amz-Target': 'Textract.%s' % target,
        }

        last_exc = None
        for attempt in range(3):
            signed_headers = _sign_request(
                'POST',
                endpoint,
                headers,
                body,
                region,
                access_key,
                secret_key,
            )
            try:
                resp = requests.post(
                    endpoint,
                    data=body,
                    headers=signed_headers,
                    timeout=_REQUEST_TIMEOUT,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_exc = exc
                _logger.warning('Textract %s attempt %d failed: %s', target, attempt + 1, exc)
                _time.sleep(2 ** (attempt + 1))
                continue
            if resp.status_code in (429, 503):
                _logger.warning('Textract %s returned %d, retrying…', target, resp.status_code)
                _time.sleep(2 ** (attempt + 1))
                continue
            if resp.status_code != 200:
                raise RuntimeError('Textract returned HTTP %d: %s' % (resp.status_code, resp.text[:500]))
            return resp.json()

        raise RuntimeError('Textract %s failed after 3 retries: %s' % (target, last_exc))

    def _analyze_expense(self, access_key, secret_key, region, document_bytes):
        """Call AnalyzeExpense (synchronous)."""
        return self._call_textract(access_key, secret_key, region, 'AnalyzeExpense', document_bytes)

    def _detect_text(self, access_key, secret_key, region, document_bytes):
        """Call DetectDocumentText (synchronous)."""
        return self._call_textract(access_key, secret_key, region, 'DetectDocumentText', document_bytes)

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
            'provider': 'aws_textract',
            'raw_response': {},
            'error': message,
        }


# ======================================================================
# Normalization: Textract response → extraction schema
# ======================================================================


def _normalize_textract_response(expense_doc):
    """Convert Textract ExpenseDocument to our extraction schema."""
    summary = _build_summary_map(expense_doc.get('SummaryFields', []))

    vendor = _norm.build_vendor(
        name=_summary_value(summary, 'VENDOR_NAME', ''),
        vat=_summary_value(summary, 'VENDOR_VAT_NUMBER', ''),
        address=_summary_value(summary, 'VENDOR_ADDRESS', ''),
        phone=_summary_value(summary, 'VENDOR_PHONE', None),
        website=_summary_value(summary, 'VENDOR_URL', None),
        confidence=_summary_confidence(summary, 'VENDOR_NAME'),
    )

    buyer = _norm.build_buyer(
        name=_summary_value(summary, 'RECEIVER_NAME', ''),
        address=_summary_value(summary, 'RECEIVER_ADDRESS', None),
        confidence=_summary_confidence(summary, 'RECEIVER_NAME'),
    )

    invoice = _norm.build_invoice(
        reference=_summary_value(summary, 'INVOICE_RECEIPT_ID', ''),
        invoice_date=_summary_value(summary, 'INVOICE_RECEIPT_DATE', None),
        invoice_date_raw=_summary_value(summary, 'INVOICE_RECEIPT_DATE', ''),
        due_date=_summary_value(summary, 'DUE_DATE', None),
        due_date_raw=_summary_value(summary, 'DUE_DATE', ''),
        currency=_summary_value(summary, 'CURRENCY_CODE', ''),
        payment_terms_text=_summary_value(summary, 'PAYMENT_TERMS', None),
        purchase_order_ref=_summary_value(summary, 'PO_NUMBER', None),
        confidence=_summary_confidence(summary, 'INVOICE_RECEIPT_ID'),
    )

    totals = _norm.build_totals(
        untaxed_amount=_summary_amount(summary, 'SUBTOTAL'),
        tax_amount=_summary_amount(summary, 'TAX'),
        total_amount=_summary_amount(summary, 'TOTAL'),
        confidence=_summary_confidence(summary, 'TOTAL'),
    )

    lines = _normalize_textract_lines(expense_doc.get('LineItemGroups', []))

    return _norm.build_result(vendor, buyer, invoice, totals, lines)


def _normalize_textract_lines(line_item_groups):
    """Extract line items from Textract LineItemGroups."""
    lines = []
    for group in line_item_groups:
        for item in group.get('LineItems', []):
            fields_map = {}
            for field in item.get('LineItemExpenseFields', []):
                ftype = field.get('Type', {}).get('Text', '')
                fvalue = field.get('ValueDetection', {}).get('Text', '')
                fconf = field.get('ValueDetection', {}).get('Confidence', 0.0)
                fields_map[ftype] = {'value': fvalue, 'confidence': fconf}

            line = _norm.build_line(
                description=_line_value(fields_map, 'ITEM', ''),
                product_code=_line_value(fields_map, 'PRODUCT_CODE', None),
                quantity=_line_amount(fields_map, 'QUANTITY', 1.0),
                unit_price=_line_amount(fields_map, 'UNIT_PRICE', 0.0),
                subtotal_untaxed=_line_amount(fields_map, 'PRICE', 0.0),
                confidence=_line_conf(fields_map, 'PRICE'),
            )
            lines.append(line)
    return lines


def _compute_overall_confidence(expense_doc):
    """Compute average confidence from key summary fields."""
    summary = _build_summary_map(expense_doc.get('SummaryFields', []))
    key_types = ['VENDOR_NAME', 'INVOICE_RECEIPT_ID', 'INVOICE_RECEIPT_DATE', 'TOTAL']
    confidences = []
    for t in key_types:
        if t in summary:
            confidences.append(summary[t].get('confidence', 0.0) / 100.0)
    if not confidences:
        return 0.0
    return sum(confidences) / len(confidences)


def _extract_full_text(expense_doc):
    """Reconstruct full text from summary fields and line items."""
    parts = []
    for field in expense_doc.get('SummaryFields', []):
        label = field.get('LabelDetection', {}).get('Text', '')
        value = field.get('ValueDetection', {}).get('Text', '')
        if label and value:
            parts.append('%s: %s' % (label, value))
        elif value:
            parts.append(value)
    for group in expense_doc.get('LineItemGroups', []):
        for item in group.get('LineItems', []):
            line_parts = []
            for f in item.get('LineItemExpenseFields', []):
                v = f.get('ValueDetection', {}).get('Text', '')
                if v:
                    line_parts.append(v)
            if line_parts:
                parts.append(' | '.join(line_parts))
    return '\n'.join(parts)


# ======================================================================
# Textract field access helpers
# ======================================================================


def _build_summary_map(summary_fields):
    """Build a dict mapping field type → {value, confidence}."""
    result = {}
    for field in summary_fields:
        ftype = field.get('Type', {}).get('Text', '')
        if ftype:
            result[ftype] = {
                'value': field.get('ValueDetection', {}).get('Text', ''),
                'confidence': field.get('ValueDetection', {}).get('Confidence', 0.0),
            }
    return result


def _summary_value(summary, type_text, default):
    """Get string value from summary map."""
    f = summary.get(type_text)
    if not f:
        return default
    return f['value'] or default


def _summary_confidence(summary, type_text):
    """Get confidence from summary map (Textract 0-100 → 0.0-1.0)."""
    f = summary.get(type_text)
    if not f:
        return 0.0
    return f.get('confidence', 0.0) / 100.0


def _summary_amount(summary, type_text):
    """Parse numeric amount from summary value."""
    f = summary.get(type_text)
    if not f:
        return 0.0
    val = f.get('value', '').replace(',', '').replace(' ', '').strip()
    # Remove currency symbols
    for sym in ('$', '€', '£', '¥', 'USD', 'EUR', 'GBP'):
        val = val.replace(sym, '')
    val = val.strip()
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _line_value(fields_map, type_text, default):
    """Get string value from line item fields map."""
    f = fields_map.get(type_text)
    if not f:
        return default
    return f['value'] or default


def _line_amount(fields_map, type_text, default):
    """Parse numeric amount from line item field."""
    f = fields_map.get(type_text)
    if not f:
        return default
    val = f.get('value', '').replace(',', '').replace(' ', '').strip()
    for sym in ('$', '€', '£', '¥', 'USD', 'EUR', 'GBP'):
        val = val.replace(sym, '')
    val = val.strip()
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _line_conf(fields_map, type_text):
    """Get confidence from line item field (0-100 → 0.0-1.0)."""
    f = fields_map.get(type_text)
    if not f:
        return 0.0
    return f.get('confidence', 0.0) / 100.0
