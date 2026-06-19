"""QR code extraction and parsing for Swiss QR-bill and EPC QR codes.

Pure utility functions for extracting QR codes from PDF images and
parsing SPC (Swiss QR-bill) and EPC/BCD payment QR formats.
These are not Odoo models -- they are imported by account_move.py.
"""

import io
import logging
import re

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# pyzbar -- optional dependency for QR code decoding
# ---------------------------------------------------------------------------
try:
    from pyzbar.pyzbar import decode as _pyzbar_decode

    PYZBAR_AVAILABLE = True
except ImportError:
    _pyzbar_decode = None
    PYZBAR_AVAILABLE = False

# ---------------------------------------------------------------------------
# Pillow -- available in Odoo (used by web module)
# ---------------------------------------------------------------------------
try:
    from PIL import Image as _PILImage

    PILLOW_AVAILABLE = True
except ImportError:
    _PILImage = None
    PILLOW_AVAILABLE = False

# ---------------------------------------------------------------------------
# pypdf/PyPDF2 -- already used for text extraction in ai_document.py
# ---------------------------------------------------------------------------
try:
    from PyPDF2 import PdfReader as _PdfReader
except ImportError:
    try:
        from pypdf import PdfReader as _PdfReader
    except ImportError:
        _PdfReader = None

# ---------------------------------------------------------------------------
# QRR mod-10 recursive table (same as ISR)
# ---------------------------------------------------------------------------
_MOD10_TABLE = [0, 9, 4, 6, 8, 2, 7, 1, 3, 5]

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------
_EPC_AMOUNT_RE = re.compile(r'^EUR(\d+\.?\d*)$', re.IGNORECASE)
_SCOR_RE = re.compile(r'^RF\d{2}[A-Z0-9]{1,21}$')
_QRR_RE = re.compile(r'^\d{27}$')

# ---------------------------------------------------------------------------
# Minimum SPC line count (header through EPD trailer)
# ---------------------------------------------------------------------------
_SPC_MIN_LINES = 30


# ===================================================================
# PDF QR extraction
# ===================================================================


def extract_qr_from_pdf(pdf_bytes, max_pages=5):
    """Extract and decode QR codes from embedded images in a PDF.

    Uses pypdf/PyPDF2 to extract XObject images from PDF pages,
    then pyzbar to decode QR codes from those images.

    Args:
        pdf_bytes: Raw PDF file content (bytes).
        max_pages: Maximum pages to scan (performance guard).

    Returns:
        list[str]: Decoded QR code payloads (UTF-8 strings).
        Empty list if no QR codes found, pyzbar unavailable,
        or extraction fails.  Never raises.
    """
    if not PYZBAR_AVAILABLE or not PILLOW_AVAILABLE or _PdfReader is None:
        return []
    try:
        reader = _PdfReader(io.BytesIO(pdf_bytes))
        pages = reader.pages[: min(len(reader.pages), max_pages)]
        payloads = []
        seen = set()
        for page in pages:
            images = _extract_images_from_page(page)
            for img in images:
                decoded = _decode_qr_from_image(img)
                for text in decoded:
                    if text and text not in seen:
                        seen.add(text)
                        payloads.append(text)
        return payloads
    except Exception:
        _logger.debug('QR extraction from PDF failed', exc_info=True)
        return []


def _extract_images_from_page(page):
    """Extract PIL Image objects from a single PDF page.

    Tries pypdf ``page.images`` (>= 3.0) first, then falls back to
    manual XObject iteration for PyPDF2.

    Returns:
        list[PIL.Image.Image]: Extracted images.
    """
    images = _extract_images_pypdf3(page)
    if not images:
        images = _extract_images_xobject(page)
    return images


def _extract_images_pypdf3(page):
    """Try pypdf >= 3.0 page.images API. Returns list or empty list."""
    images = []
    try:
        for img_obj in page.images:
            try:
                pil_img = _PILImage.open(io.BytesIO(img_obj.data))
                images.append(pil_img)
            except Exception:
                continue
    except (AttributeError, NotImplementedError):
        _logger.debug('pypdf page.images API not available', exc_info=True)
    return images


def _extract_images_xobject(page):
    """Fallback: manual XObject extraction for PyPDF2."""
    images = []
    try:
        resources = page.get('/Resources', {})
        if hasattr(resources, 'get_object'):
            resources = resources.get_object()
        xobjects = resources.get('/XObject', {})
        if hasattr(xobjects, 'get_object'):
            xobjects = xobjects.get_object()
        for obj_name in xobjects:
            obj = xobjects[obj_name]
            if hasattr(obj, 'get_object'):
                obj = obj.get_object()
            if obj.get('/Subtype', '') == '/Image':
                try:
                    data = obj.get_data()
                    images.append(_PILImage.open(io.BytesIO(data)))
                except Exception:
                    continue
    except Exception:
        _logger.debug('XObject image extraction failed', exc_info=True)
    return images


def _decode_qr_from_image(pil_image):
    """Decode QR codes from a PIL Image using pyzbar.

    Returns:
        list[str]: Decoded QR code data strings.
    """
    if not PYZBAR_AVAILABLE or pil_image is None:
        return []
    try:
        results = _pyzbar_decode(pil_image)
        return [r.data.decode('utf-8') for r in results if r.type == 'QRCODE' and r.data]
    except Exception:
        _logger.debug('pyzbar decode failed', exc_info=True)
        return []


# ===================================================================
# Payload dispatch
# ===================================================================


def parse_qr_payload(payload):
    """Identify and parse a QR code payload.

    Detects the format (SPC Swiss QR-bill, EPC/BCD, or unknown)
    and delegates to the appropriate parser.

    Returns:
        dict: Parsed QR data with ``format`` key.
    """
    if not payload:
        return {'format': 'unknown'}
    lines = payload.split('\n')
    if len(lines) >= 3 and lines[0].strip() == 'SPC':
        return _parse_spc(payload)
    if len(lines) >= 4 and lines[0].strip() == 'BCD':
        return _parse_epc(payload)
    return {'format': 'unknown'}


# ===================================================================
# Swiss QR-bill (SPC) parser
# ===================================================================


def _safe_line(lines, idx):
    """Return stripped line at index, or '' if out of range."""
    if idx < len(lines):
        return lines[idx].strip()
    return ''


def _parse_spc(payload):
    """Parse Swiss QR-bill SPC format.

    Returns dict with format='spc' and extracted fields,
    or format='unknown' if the payload is invalid.
    """
    lines = payload.split('\n')
    if len(lines) < _SPC_MIN_LINES:
        return {'format': 'unknown'}

    header = _safe_line(lines, 0)
    version = _safe_line(lines, 1)
    coding = _safe_line(lines, 2)
    if header != 'SPC' or version != '0200' or coding != '1':
        return {'format': 'unknown'}

    iban = _safe_line(lines, 3) or None

    # Creditor address
    cr_type = _safe_line(lines, 4)
    cr_name = _safe_line(lines, 5) or None
    cr_address = _format_address(cr_type, lines, 6)

    # Amount and currency
    amount_str = _safe_line(lines, 17)
    amount = None
    if amount_str:
        try:
            amount = float(amount_str)
        except ValueError:
            pass
    currency = _safe_line(lines, 18) or None

    # Debtor address
    db_type = _safe_line(lines, 19)
    db_name = _safe_line(lines, 20) or None
    db_address = _format_address(db_type, lines, 21)

    # Reference
    ref_type = _safe_line(lines, 26) or None
    reference = _safe_line(lines, 27) or None
    message = _safe_line(lines, 28) or None

    return {
        'format': 'spc',
        'iban': iban,
        'amount': amount,
        'currency': currency,
        'creditor_name': cr_name,
        'creditor_address': cr_address,
        'reference_type': ref_type,
        'reference': reference,
        'bic': None,
        'message': message,
        'debtor_name': db_name,
        'debtor_address': db_address,
    }


def _format_address(addr_type, lines, start_idx):
    """Format a structured (S) or combined (K) SPC address into a string."""
    if addr_type == 'S':
        return _format_address_structured(lines, start_idx)
    if addr_type == 'K':
        return _format_address_combined(lines, start_idx)
    return None


def _format_address_structured(lines, start_idx):
    """Format structured (S) SPC address: street, building, postal, city, country."""
    parts = []
    street = _safe_line(lines, start_idx)
    building = _safe_line(lines, start_idx + 1)
    if street and building:
        parts.append(f'{street} {building}')
    elif street:
        parts.append(street)
    postal = _safe_line(lines, start_idx + 2)
    city = _safe_line(lines, start_idx + 3)
    if postal and city:
        parts.append(f'{postal} {city}')
    elif city:
        parts.append(city)
    country = _safe_line(lines, start_idx + 4)
    if country:
        parts.append(country)
    return ', '.join(parts) if parts else None


def _format_address_combined(lines, start_idx):
    """Format combined (K) SPC address: line 1, line 2, country."""
    parts = []
    line1 = _safe_line(lines, start_idx)
    line2 = _safe_line(lines, start_idx + 1)
    if line1:
        parts.append(line1)
    if line2:
        parts.append(line2)
    country = _safe_line(lines, start_idx + 4)
    if country:
        parts.append(country)
    return ', '.join(parts) if parts else None


# ===================================================================
# EPC QR (BCD) parser
# ===================================================================


def _parse_epc(payload):
    """Parse EPC/BCD QR code format.

    Returns dict with format='epc' and extracted fields,
    or format='unknown' if the payload is invalid.
    """
    lines = payload.split('\n')
    if len(lines) < 7:
        return {'format': 'unknown'}

    header = _safe_line(lines, 0)
    version = _safe_line(lines, 1)
    coding = _safe_line(lines, 2)
    ident = _safe_line(lines, 3)

    if header != 'BCD' or coding != '1' or ident != 'SCT':
        return {'format': 'unknown'}
    if version not in ('001', '002'):
        return {'format': 'unknown'}

    bic = _safe_line(lines, 4) or None
    creditor_name = _safe_line(lines, 5) or None
    iban = _safe_line(lines, 6) or None

    # Amount: format 'EUR1234.56' or empty
    amount = None
    currency = 'EUR'
    amount_str = _safe_line(lines, 7) if len(lines) > 7 else ''
    if amount_str:
        m = _EPC_AMOUNT_RE.match(amount_str)
        if m:
            try:
                amount = float(m.group(1))
            except ValueError:
                pass

    # Structured reference (line 9) or remittance text (line 10)
    reference = _safe_line(lines, 9) if len(lines) > 9 else None
    message = _safe_line(lines, 10) if len(lines) > 10 else None

    return {
        'format': 'epc',
        'iban': iban,
        'amount': amount,
        'currency': currency,
        'creditor_name': creditor_name,
        'creditor_address': None,
        'reference_type': 'SCOR' if reference and reference.upper().startswith('RF') else None,
        'reference': reference or None,
        'bic': bic,
        'message': message or None,
        'debtor_name': None,
        'debtor_address': None,
    }


# ===================================================================
# Reference validation
# ===================================================================


def validate_qrr_reference(reference):
    """Validate a Swiss QRR reference (26 digits + 1 check digit).

    Uses the recursive mod-10 algorithm (same as ISR).

    Returns:
        bool: True if checksum is valid.
    """
    if not reference:
        return False
    cleaned = reference.replace(' ', '')
    if not _QRR_RE.match(cleaned):
        return False
    carry = 0
    for digit in cleaned:
        carry = _MOD10_TABLE[(carry + int(digit)) % 10]
    return carry == 0


def validate_scor_reference(reference):
    """Validate a SCOR/ISO 11649 structured creditor reference.

    Starts with 'RF' + 2 check digits + up to 21 alphanumeric chars.
    Validation: move 'RF' + check to end, convert letters to numbers,
    mod 97 must equal 1.

    Returns:
        bool: True if checksum is valid.
    """
    if not reference:
        return False
    cleaned = reference.replace(' ', '').upper()
    if not _SCOR_RE.match(cleaned):
        return False
    rearranged = cleaned[4:] + cleaned[:4]
    numeric = ''.join(str(ord(c) - 55) if c.isalpha() else c for c in rearranged)
    return int(numeric) % 97 == 1


# ===================================================================
# Prompt context formatting
# ===================================================================


def format_qr_context(qr_data_list):
    """Format parsed QR data for injection into the AI prompt.

    Args:
        qr_data_list: List of parsed QR data dicts.

    Returns:
        str: Formatted context string, or '' if no valid QR data.
    """
    if not qr_data_list:
        return ''
    parts = ['QR CODE DATA (high-confidence structured source -- use to verify amounts, IBAN, and reference):']
    for qr in qr_data_list:
        qr_parts = _format_single_qr(qr)
        if qr_parts:
            parts.extend(qr_parts)
    return '\n'.join(parts) if len(parts) > 1 else ''


def _format_single_qr(qr):
    """Format a single QR data dict into context lines."""
    fmt = qr.get('format', 'unknown')
    _FORMAT_LABELS = {'spc': 'Swiss QR-bill (SPC)', 'epc': 'EPC QR code (BCD/SCT)'}
    label = _FORMAT_LABELS.get(fmt)
    if not label:
        return []
    parts = ['Format: ' + label]
    _append_qr_fields(parts, qr, fmt)
    return parts


def _append_qr_fields(parts, qr, fmt):
    """Append field lines from a single QR dict to *parts*."""
    if qr.get('iban'):
        parts.append('IBAN: %s' % qr['iban'])
    if qr.get('bic'):
        parts.append('BIC: %s' % qr['bic'])
    if qr.get('creditor_name'):
        creditor_label = 'Creditor' if fmt == 'spc' else 'Beneficiary'
        addr_parts = [qr['creditor_name']]
        if qr.get('creditor_address'):
            addr_parts.append(qr['creditor_address'])
        parts.append('%s: %s' % (creditor_label, ', '.join(addr_parts)))
    if qr.get('amount') is not None and qr.get('currency'):
        parts.append('Amount: %.2f %s' % (qr['amount'], qr['currency']))
    for key, prefix in (
        ('reference_type', 'Reference type'),
        ('reference', 'Reference'),
        ('message', 'Unstructured message'),
    ):
        if qr.get(key):
            parts.append('%s: %s' % (prefix, qr[key]))
