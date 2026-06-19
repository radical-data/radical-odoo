"""Abstract base class for AI providers.

This module defines the interface that all AI providers must implement.
Adding a new provider requires only a new ai_provider_xxx.py file
implementing this interface — no changes to the extraction pipeline.

These are pure Python utility classes, not Odoo models.
"""

import json
import logging
import re
import time

import requests

from abc import ABC, abstractmethod

_logger = logging.getLogger(__name__)


class AIProvider(ABC):
    """Abstract interface for AI-powered invoice extraction providers."""

    MAX_RETRIES = 3
    MAX_OUTPUT_TOKENS = 8192
    REQUEST_TIMEOUT = 180

    @abstractmethod
    def extract(self, api_key, system_prompt, user_content, model):
        """Send document to AI provider and return extraction result.

        Args:
            api_key: Provider API key.
            system_prompt: System-level instructions.
            user_content: List of content blocks (text and/or images).
            model: Model identifier string.

        Returns:
            dict with keys:
                success (bool), raw_text (str), data (dict or None),
                input_tokens (int), output_tokens (int), model (str),
                and optionally error (str), message (str), parse_error (str).
        """

    @abstractmethod
    def validate_api_key(self, api_key):
        """Test API key validity.

        Returns:
            tuple (bool, str): (success, message).
        """

    def get_available_models(self):
        """Return available models with pricing.

        Subclasses must define a ``MODELS`` class attribute (dict mapping
        model IDs to dicts with keys: name, input_price, output_price).

        Returns:
            list of dicts with keys: id, name, input_price, output_price
            (prices per 1M tokens in USD).
        """
        return [
            {
                'id': model_id,
                'name': info['name'],
                'input_price': info['input_price'],
                'output_price': info['output_price'],
            }
            for model_id, info in self.MODELS.items()
        ]

    def estimate_cost(self, input_tokens, output_tokens, model):
        """Estimate extraction cost in USD.

        Uses the ``MODELS`` class attribute for pricing lookup.

        Returns:
            float: estimated cost.
        """
        info = self.MODELS.get(model, {})
        input_cost = (input_tokens / 1_000_000) * info.get('input_price', 0)
        output_cost = (output_tokens / 1_000_000) * info.get('output_price', 0)
        return input_cost + output_cost

    @abstractmethod
    def get_provider_name(self):
        """Return human-readable provider name."""

    @abstractmethod
    def supports_vision(self):
        """Return True if provider supports image/vision input."""

    # --- Shared helpers (avoid duplication across providers) ----------------

    def _validate_with_request(self, endpoint, headers, payload):
        """Shared API key validation: POST a minimal request and map the status code.

        Returns:
            tuple (bool, str): (success, message).
        """
        try:
            resp = requests.post(endpoint, headers=headers, json=payload, timeout=15)
            status_map = {
                200: (True, 'Connection successful!'),
                400: (False, 'Invalid API key or malformed request.'),
                401: (False, 'Invalid API key.'),
                403: (False, 'API key does not have the required permissions.'),
            }
            return status_map.get(
                resp.status_code,
                (False, 'Unexpected error (HTTP %d).' % resp.status_code),
            )
        except requests.Timeout:
            return False, 'Connection timed out. Please try again.'
        except requests.ConnectionError:
            return False, 'Could not reach the %s API. Check your network.' % self.get_provider_name()
        except Exception:
            _logger.exception('%s API key validation failed', self.get_provider_name())
            return False, 'Validation failed. Check server logs for details.'

    @staticmethod
    def _make_error(code, message):
        """Build a standard error result dict."""
        return {
            'success': False,
            'error': code,
            'message': message,
            'data': None,
            'raw_text': '',
            'input_tokens': 0,
            'output_tokens': 0,
            'model': '',
        }

    def _safe_json(self, resp):
        """Parse JSON response, returning error dict on failure."""
        try:
            return resp.json()
        except (ValueError, json.JSONDecodeError):
            return self._make_error(
                'parse', 'Invalid JSON in %s API response.' % self.get_provider_name()
            )

    @staticmethod
    def _retry_wait(resp, attempt):
        """Compute backoff seconds, honoring the ``Retry-After`` header.

        Providers return ``Retry-After`` (integer seconds) on 429/503 to tell
        the client exactly how long to wait.  When present we respect it
        (clamped to 1-60s so a worker is never blocked too long); otherwise we
        fall back to exponential backoff (2, 4, 8, … seconds).
        """
        header = resp.headers.get('Retry-After')
        if header:
            try:
                return max(1, min(int(float(header)), 60))
            except (TypeError, ValueError):
                pass
        return 2 ** (attempt + 1)

    def _call_with_retries(self, endpoint, headers, payload):
        """Execute HTTP POST with retry logic for transient errors.

        Retries on 429 (rate limit), 5xx (server errors), and network
        errors (timeout, connection).  Exponential backoff.

        Args:
            endpoint: Full URL to POST to.
            headers: Request headers dict.
            payload: JSON-serializable request body.

        Returns:
            requests.Response on success, or an error result dict.
        """
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = requests.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                    timeout=self.REQUEST_TIMEOUT,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt < self.MAX_RETRIES - 1:
                    wait = 2 ** (attempt + 1)
                    _logger.warning(
                        '%s network error (attempt %d/%d): %s — retrying in %ds…',
                        self.get_provider_name(),
                        attempt + 1,
                        self.MAX_RETRIES,
                        type(exc).__name__,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                error_type = 'timeout' if isinstance(exc, requests.Timeout) else 'connection'
                msg = (
                    'Request timed out.'
                    if error_type == 'timeout'
                    else 'Could not reach the %s API.' % self.get_provider_name()
                )
                return self._make_error(error_type, msg + ' (%d retries exhausted)' % self.MAX_RETRIES)
            except Exception:
                _logger.exception('%s API call failed', self.get_provider_name())
                return self._make_error(
                    'unknown', '%s API call failed. Check server logs for details.' % self.get_provider_name()
                )

            if resp.status_code == 200:
                return resp

            if resp.status_code == 429 and attempt < self.MAX_RETRIES - 1:
                wait = self._retry_wait(resp, attempt)
                _logger.warning(
                    '%s rate-limited (attempt %d/%d), retrying in %ds…',
                    self.get_provider_name(),
                    attempt + 1,
                    self.MAX_RETRIES,
                    wait,
                )
                time.sleep(wait)
                continue

            if resp.status_code >= 500:
                if attempt < self.MAX_RETRIES - 1:
                    wait = self._retry_wait(resp, attempt)
                    _logger.warning(
                        '%s server error %d (attempt %d/%d), retrying in %ds…',
                        self.get_provider_name(),
                        resp.status_code,
                        attempt + 1,
                        self.MAX_RETRIES,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                return self._make_error(
                    'max_retries', 'API request failed after %d retries.' % self.MAX_RETRIES
                )

            return self._handle_error_status(resp)

        return self._make_error('max_retries', 'API request failed after %d retries.' % self.MAX_RETRIES)

    def _handle_error_status(self, resp):
        """Map HTTP error codes to standard error result dict.

        Subclasses may override to handle provider-specific codes.
        """
        status = resp.status_code
        error_map = {
            400: ('auth', 'Invalid API key or malformed request.'),
            401: ('auth', 'Invalid API key.'),
            402: ('credits', 'Insufficient API credits.'),
            403: ('forbidden', 'API key does not have the required permissions.'),
            429: ('rate_limit', 'Rate limited. Please try again later.'),
        }
        if status in error_map:
            code, msg = error_map[status]
            return self._make_error(code, msg)
        return self._make_error('api', 'API error (HTTP %d).' % status)

    @staticmethod
    def _extract_json_from_text(text):
        """Try to extract a JSON object from text (fallback parser).

        Strategies (in order):
        1. Parse entire text as JSON.
        2. Find JSON inside a ```json code block.
        3. Find outermost { … } braces.
        """
        if not text:
            return None

        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass

        match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except (json.JSONDecodeError, ValueError):
                pass

        brace_start = text.find('{')
        brace_end = text.rfind('}')
        if brace_start != -1 and brace_end > brace_start:
            try:
                return json.loads(text[brace_start : brace_end + 1])
            except (json.JSONDecodeError, ValueError):
                pass

        return None


def get_all_provider_models():
    """Return models from all providers as Odoo selection tuples.

    Used by ``res.config.settings`` for the dynamic AI model dropdown.
    Only providers that have models defined are included.

    Returns:
        list of (model_id, label) tuples.
    """
    result = []
    for name in ('anthropic', 'openai', 'google', 'xai', 'deepseek', 'mistral', 'local'):
        try:
            provider = get_provider(name)
            for m in provider.get_available_models():
                result.append((m['id'], m['name']))
        except (ValueError, NotImplementedError):
            continue
    return result or [('claude-haiku-4-5-20251001', 'Claude Haiku 4.5 (fast, affordable)')]


def _import_provider(name):
    """Lazy-import a provider class by name and return an instance."""
    registry = {
        'anthropic': ('ai_provider_anthropic', 'AnthropicProvider'),
        'openai': ('ai_provider_openai', 'OpenAIProvider'),
        'google': ('ai_provider_google', 'GoogleProvider'),
        'xai': ('ai_provider_xai', 'XAIProvider'),
        'deepseek': ('ai_provider_deepseek', 'DeepSeekProvider'),
        'mistral': ('ai_provider_mistral', 'MistralProvider'),
        'local': ('ai_provider_local', 'LocalAIProvider'),
    }
    entry = registry.get(name)
    if not entry:
        return None
    import importlib
    module = importlib.import_module('.' + entry[0], __package__)
    return getattr(module, entry[1])()


def get_provider(provider_name):
    """Factory: return provider instance by name.

    Args:
        provider_name: One of 'anthropic', 'openai', 'google', 'xai', 'deepseek', 'mistral', 'local'.

    Returns:
        AIProvider instance.

    Raises:
        ValueError if provider_name is unknown.
    """
    provider = _import_provider(provider_name)
    if provider is None:
        raise ValueError(f'Unknown AI provider: {provider_name}')
    return provider
