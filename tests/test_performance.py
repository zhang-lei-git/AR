import json
import statistics
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from server import make_server


class PerformanceTest(unittest.TestCase):
    def test_health_bootstrap_and_rag_p95(self):
        with tempfile.TemporaryDirectory() as directory:
            server = make_server("127.0.0.1", 0, Path(directory) / "perf.db", quiet=True, enable_external=False)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            samples = {"health": [], "bootstrap": [], "knowledge": []}
            try:
                for _ in range(30):
                    for key, path, body in (
                        ("health", "/api/health", None),
                        ("bootstrap", "/api/bootstrap", None),
                        ("knowledge", "/api/ai/ask", {"task_id": "WO-HYD-2026-0828-017", "question": "液压胶管接口不一致如何处置？", "step_no": 6}),
                    ):
                        request = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
                        started = time.perf_counter()
                        with urllib.request.urlopen(request, timeout=3) as response:
                            self.assertEqual(response.status, 200)
                            response.read()
                        samples[key].append((time.perf_counter() - started) * 1000)
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)
            for name, values in samples.items():
                p95 = statistics.quantiles(values, n=20)[18]
                self.assertLess(p95, 250, f"{name} P95={p95:.2f}ms")


if __name__ == "__main__":
    unittest.main(verbosity=2)
