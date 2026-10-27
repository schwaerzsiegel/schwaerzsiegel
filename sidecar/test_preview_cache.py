import unittest
import fitz
from core import pdf_processor as pp

class PreviewCacheTests(unittest.TestCase):
    def setUp(self):
        pp.clear_preview_cache()
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), 'Original')
        self.original = doc.tobytes()
        doc.close()
        pp.register('test-preview', self.original)

    def tearDown(self):
        pp.clear_preview_cache()
        pp._RAW_BYTES.pop('test-preview', None)

    def test_repeat_preview_and_cache_hit(self):
        first = pp.render_page_image('test-preview', 0)
        self.assertEqual(first, pp.render_page_image('test-preview', 0))
        self.assertEqual(len(pp._PREVIEW_CACHE), 1)

    def test_register_invalidates_preview(self):
        first = pp.render_page_image('test-preview', 0)
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), 'Changed')
        pp.register('test-preview', doc.tobytes())
        doc.close()
        self.assertNotEqual(first, pp.render_page_image('test-preview', 0))

    def test_cache_size_is_bounded(self):
        for i in range(8):
            pp._cache_preview(('x', i, 1.0), b'0' * (6 * 1024 * 1024))
        self.assertLessEqual(pp._PREVIEW_CACHE_SIZE, pp._PREVIEW_CACHE_LIMIT)

if __name__ == '__main__':
    unittest.main()
