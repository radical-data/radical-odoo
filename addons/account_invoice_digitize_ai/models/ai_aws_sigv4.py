"""AWS Signature Version 4 — pure Python implementation.

Zero external dependencies: uses only stdlib ``hmac``, ``hashlib``,
``urllib.parse``, and ``datetime``.

Ref: https://docs.aws.amazon.com/general/latest/gr/sigv4_signing.html
"""

import datetime
import hashlib
import hmac
import urllib.parse


def sign_request(method, url, headers, body, region, access_key, secret_key, service='textract'):
    """Sign an HTTP request using AWS Signature Version 4.

    Returns a new headers dict including Authorization and other required
    SigV4 headers. The caller should use these headers for the request.
    """
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    path = parsed.path or '/'

    now = datetime.datetime.now(datetime.timezone.utc)
    amz_date = now.strftime('%Y%m%dT%H%M%SZ')
    date_stamp = now.strftime('%Y%m%d')

    # Canonical headers (must include host and x-amz-date)
    signed_headers_dict = dict(headers)
    signed_headers_dict['host'] = host
    signed_headers_dict['x-amz-date'] = amz_date

    # Sorted lowercase header names
    header_names = sorted(k.lower() for k in signed_headers_dict)
    signed_headers_str = ';'.join(header_names)

    # Canonical headers string
    canonical_headers = ''
    for name in header_names:
        val = next((v for k, v in signed_headers_dict.items() if k.lower() == name), '')
        canonical_headers += '%s:%s\n' % (name, val.strip())

    # Payload hash
    payload_hash = hashlib.sha256(body.encode('utf-8') if isinstance(body, str) else body).hexdigest()

    # Canonical request
    canonical_request = '\n'.join(
        [
            method,
            path,
            '',  # query string (empty for POST)
            canonical_headers,
            signed_headers_str,
            payload_hash,
        ]
    )

    # String to sign
    credential_scope = '%s/%s/%s/aws4_request' % (date_stamp, region, service)
    string_to_sign = '\n'.join(
        [
            'AWS4-HMAC-SHA256',
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode('utf-8')).hexdigest(),
        ]
    )

    # Signing key
    signing_key = _get_signature_key(secret_key, date_stamp, region, service)

    # Signature
    signature = hmac.new(signing_key, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()

    # Authorization header
    authorization = 'AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s' % (
        access_key,
        credential_scope,
        signed_headers_str,
        signature,
    )

    # Build final headers (original + SigV4 additions)
    result_headers = dict(headers)
    result_headers['x-amz-date'] = amz_date
    result_headers['Authorization'] = authorization
    return result_headers


def _get_signature_key(secret_key, date_stamp, region, service):
    """Derive the SigV4 signing key via HMAC chain."""
    k_date = hmac.new(
        ('AWS4' + secret_key).encode('utf-8'),
        date_stamp.encode('utf-8'),
        hashlib.sha256,
    ).digest()
    k_region = hmac.new(k_date, region.encode('utf-8'), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode('utf-8'), hashlib.sha256).digest()
    k_signing = hmac.new(k_service, b'aws4_request', hashlib.sha256).digest()
    return k_signing
