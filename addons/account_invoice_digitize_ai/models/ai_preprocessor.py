"""Abstract base class for document pre-processing providers.

These are pure Python utility classes, not Odoo models.
Adding a new pre-processor requires only a new ai_preprocessor_xxx.py file
implementing this interface — no pipeline changes needed.

Pre-processors handle OCR and structured data extraction from documents
(PDF/image) before the AI extraction step. They can be used to improve
extraction quality or reduce AI costs.
"""

from abc import ABC, abstractmethod


class DocumentPreprocessor(ABC):
    """Abstract interface for document pre-processing providers."""

    @abstractmethod
    def extract_structured(self, credentials, document_bytes, mimetype):
        """Extract structured invoice data from a document.

        Used in OCR Replacement and Claude Enrichment modes.

        Args:
            credentials: dict with provider-specific auth keys.
            document_bytes: Raw file bytes (PDF or image).
            mimetype: MIME type string.

        Returns:
            dict with keys:
                success (bool),
                data (dict or None) — normalized to extraction schema,
                text (str) — full-text OCR result,
                confidence (float) — overall confidence 0.0-1.0,
                page_count (int),
                cost_per_page (float),
                provider (str),
                raw_response (dict) — original API response,
                error (str or None).
        """

    @abstractmethod
    def extract_text(self, credentials, document_bytes, mimetype):
        """Extract raw text from a document (OCR Only mode).

        Args:
            credentials: dict with provider-specific auth keys.
            document_bytes: Raw file bytes.
            mimetype: MIME type string.

        Returns:
            dict with keys:
                success (bool),
                text (str),
                page_count (int),
                cost_per_page (float),
                provider (str),
                error (str or None).
        """

    @abstractmethod
    def validate_credentials(self, credentials):
        """Test credentials validity.

        Args:
            credentials: dict with provider-specific auth keys.

        Returns:
            tuple (bool, str): (success, message).
        """

    @abstractmethod
    def get_provider_name(self):
        """Return human-readable provider name."""

    @abstractmethod
    def estimate_cost_per_page(self):
        """Return estimated cost per page in USD."""


def get_preprocessor(provider_name):
    """Factory: return pre-processor instance by name.

    Args:
        provider_name: One of 'azure_di', 'aws_textract', 'google_docai' (planned).

    Returns:
        DocumentPreprocessor instance.

    Raises:
        ValueError if provider_name is unknown.
    """
    if provider_name == 'azure_di':
        from .ai_preprocessor_azure import AzureDocumentIntelligence

        return AzureDocumentIntelligence()
    if provider_name == 'aws_textract':
        from .ai_preprocessor_aws import AWSTextract

        return AWSTextract()
    if provider_name == 'google_docai':
        raise ValueError('Google Document AI is not yet available. Please select Azure DI or AWS Textract.')
    raise ValueError('Unknown pre-processor: %s' % provider_name)
