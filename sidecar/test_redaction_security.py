"""Run: python -m unittest discover -s sidecar -p 'test_redaction_security.py'"""
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import fitz
from core import pdf_processor as pdf


class RedactionSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def make_pdf(self, *, attachment=False):
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 100), 'SECRET123 public')
        if attachment:
            doc.embfile_add('secret.txt', b'confidential attachment')
        data = doc.tobytes()
        doc.close()
        return data

    def test_redaction_removes_selected_text(self):
        data = self.make_pdf()
        source = fitz.open(stream=data, filetype='pdf')
        word = next(w for w in source[0].get_text('words') if w[4] == 'SECRET123')
        source.close()
        doc_id = 'test-' + next(tempfile._get_candidate_names())
        pdf.register(doc_id, data)
        suggestion = {'id': 's1', 'bbox': list(word[:4]), 'rects': [list(word[:4])], 'kind': 'area',
                      'text': 'SECRET123', 'label': 'Test', 'status': 'confirmed'}
        page = pdf.Page(index=0, suggestions=[suggestion])
        output, selected = pdf.finalize_redaction({'_doc_id': doc_id, 'pages': [page]})
        self.addCleanup(lambda: output.unlink(missing_ok=True))
        self.addCleanup(lambda: pdf._RAW_BYTES.pop(doc_id, None))
        result = pdf.verify_no_residual_data(output, selected)
        self.assertTrue(result.passed, result.residual_text_found)
        with fitz.open(output) as finished:
            self.assertNotIn('SECRET123', finished[0].get_text())
            self.assertIn('public', finished[0].get_text())

    def test_attachment_blocks_release(self):
        path = Path(self.tmp.name) / 'attachment.pdf'
        path.write_bytes(self.make_pdf(attachment=True))
        result = pdf.verify_no_residual_data(path, [])
        self.assertFalse(result.passed)
        self.assertTrue(any('eingebettete' in x for x in result.residual_text_found))

    def test_visible_text_in_target_blocks_release(self):
        path = Path(self.tmp.name) / 'unsafe.pdf'
        path.write_bytes(self.make_pdf())
        result = pdf.verify_no_residual_data(path, [{'page': 0, 'bbox': [70, 85, 155, 105], 'text': 'SECRET123'}])
        self.assertFalse(result.passed)


if __name__ == '__main__':
    unittest.main()
