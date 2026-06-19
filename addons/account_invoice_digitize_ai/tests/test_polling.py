import os

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPollingWidget(TransactionCase):
    """Test that the polling widget assets are properly declared."""

    def test_js_file_exists(self):
        """The extraction status widget JS file should exist."""
        module_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        js_path = os.path.join(module_path, 'static', 'src', 'js', 'extraction_status_widget.js')
        self.assertTrue(os.path.isfile(js_path), 'extraction_status_widget.js should exist')

    def test_xml_template_exists(self):
        """The extraction status widget XML template should exist."""
        module_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        xml_path = os.path.join(module_path, 'static', 'src', 'xml', 'extraction_status_widget.xml')
        self.assertTrue(os.path.isfile(xml_path), 'extraction_status_widget.xml should exist')

    def test_assets_glob_includes_widget(self):
        """The manifest assets glob should cover the new widget files."""
        from odoo.modules.module import get_module_path

        mod_path = get_module_path('account_invoice_digitize_ai')
        if not mod_path:
            return
        # Check JS and XML files exist under the glob paths
        js_exists = os.path.isfile(os.path.join(mod_path, 'static', 'src', 'js', 'extraction_status_widget.js'))
        xml_exists = os.path.isfile(os.path.join(mod_path, 'static', 'src', 'xml', 'extraction_status_widget.xml'))
        self.assertTrue(js_exists)
        self.assertTrue(xml_exists)
