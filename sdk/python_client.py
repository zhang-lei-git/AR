"""Minimal client example for a new business module."""

import json
import urllib.request


class ARPlatformClient:
    def __init__(self, base_url="http://127.0.0.1:8088", role="developer"):
        self.base_url = base_url.rstrip("/")
        self.role = role

    def bootstrap(self):
        return self._request("/api/bootstrap")

    def run_quality_check(self, torque=320, surface_ok=True):
        return self._request(
            "/api/plugins/quality-check/run",
            "POST",
            {"torque": torque, "surface_ok": surface_ok},
        )

    def _request(self, path, method="GET", body=None):
        request = urllib.request.Request(
            self.base_url + path,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "X-Demo-Role": self.role},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)


if __name__ == "__main__":
    print(json.dumps(ARPlatformClient().run_quality_check(), ensure_ascii=False, indent=2))
