"""Lightweight API security tests; run with python -m unittest test_session_security.py."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('REDACTION_SESSION_TOKEN', 'a' * 64)

class SessionSecurityTests(unittest.TestCase):
    def test_missing_and_wrong_token_rejected(self):
        from fastapi.testclient import TestClient
        from main import app
        with patch('main.models.start_preload'):
            with TestClient(app) as client:
                for headers in ({}, {'X-Redaction-Session': 'wrong'}):
                    for endpoint in ('/status', '/documents/fake/progress', '/documents/fake/result/download'):
                        self.assertEqual(client.get(endpoint, headers=headers).status_code, 401)

    def test_valid_token_reaches_router(self):
        from fastapi.testclient import TestClient
        from main import app
        with patch('main.models.start_preload'):
            with TestClient(app) as client:
                self.assertEqual(client.get('/documents/fake/progress', headers={'X-Redaction-Session': os.environ['REDACTION_SESSION_TOKEN']}).status_code, 404)

if __name__ == '__main__':
    unittest.main()
