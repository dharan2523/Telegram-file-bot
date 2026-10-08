import os
import unittest
from urllib.request import urlopen
from unittest.mock import patch

from health_server import health_port, start_health_server


class HealthServerTests(unittest.TestCase):
    def test_health_port_defaults_to_8000(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(health_port(), 8000)

    def test_health_port_uses_port_environment_variable(self):
        with patch.dict(os.environ, {"PORT": "9123"}):
            self.assertEqual(health_port(), 9123)

    def test_root_endpoint_returns_running_status(self):
        server = start_health_server(port=0)
        try:
            with urlopen(
                f"http://127.0.0.1:{server.server_port}/",
                timeout=3,
            ) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    response.read(),
                    b"Telegram file bot is running",
                )
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()