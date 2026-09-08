import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from server import make_server


DEFAULT_ID = "WO-HYD-2026-0828-017"


class ApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = make_server("127.0.0.1", 0, Path(cls.temp.name) / "test.db", quiet=True, enable_external=False)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.temp.cleanup()

    def request(self, path, method="GET", body=None, role="operator", headers=None, raw=False):
        payload = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=payload, method=method,
            headers={"Content-Type": "application/json", "X-Demo-Role": role, **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                content = response.read()
                return response.status, content if raw else json.loads(content)
        except urllib.error.HTTPError as error:
            content = error.read()
            return error.code, content if raw else json.loads(content)

    def reset(self):
        status, _ = self.request("/api/admin/reset", "POST", {}, "admin")
        self.assertEqual(status, 200)

    def dispatch_and_claim(self, task_id=DEFAULT_ID):
        self.request(f"/api/tasks/{task_id}/dispatch", "POST", {"device_id": "AR-01"}, "engineer")
        return self.request(f"/api/tasks/{task_id}/claim", "POST", {"operator": "张工/A017", "device_id": "AR-01"})

    def identify_current_part(self, step, task_id=DEFAULT_ID):
        order = task_id[-3:]
        return self.request(f"/api/tasks/{task_id}/parts/identify", "POST", {
            "code": f"PART-ZZ18-S{step:02d}-{order}", "step_no": step, "device_id": "AR-01"
        })

    def test_01_pages_health_and_bootstrap(self):
        self.reset()
        status, page = self.request("/", raw=True)
        self.assertEqual(status, 200)
        self.assertIn("AR生产现场作业支撑平台".encode(), page)
        self.assertIn("新建装配工单".encode(), page)
        self.assertIn(b"expertArFeed", page)
        self.assertIn("AR眼镜第一视角实时画面".encode(), page)
        status, terminal = self.request("/terminal.html", raw=True)
        self.assertEqual(status, 200)
        self.assertIn("AR眼镜端模拟器".encode(), terminal)
        status, health = self.request("/api/health")
        self.assertEqual((status, health["status"], health["version"]), (200, "ok", "3.3.0"))
        _, data = self.request("/api/bootstrap")
        self.assertEqual((data["task"]["status"], data["task"]["current_step"]), ("draft", 1))
        self.assertEqual(len(data["task"]["steps"]), 8)
        self.assertEqual(len(data["contentPackages"]), 8)
        self.assertEqual(len(data["tasks"]), 4)
        self.assertEqual(len(data["digitalTwin"]["objects"]), 4)
        self.assertGreaterEqual(len(data["maintenanceCases"]), 3)
        self.assertEqual(data["partIdentifications"], [])
        self.assertEqual(data["digitalTwin"]["source_system"], "产线数字孪生平台")
        self.assertIn("产线三维模型", data["digitalTwin"]["display_content"])
        self.assertEqual(data["maintenanceIntegration"]["source_system"], "维修管理系统")
        self.assertGreaterEqual(len(data["knowledgeDocuments"]), 5)
        self.assertGreaterEqual(len(data["processSteps"]), 24)
        self.assertEqual(len(data["recordings"]), 2)
        status, twin_terminal = self.request("/twin-terminal.html", raw=True)
        self.assertEqual(status, 200)
        self.assertIn("AR产线透视".encode(), twin_terminal)

    def test_02_permissions_are_kept_simple_but_effective(self):
        self.reset()
        status, body = self.request("/api/admin/backup", "POST", {}, "operator")
        self.assertEqual((status, body["code"]), (403, "FORBIDDEN"))
        status, body = self.request(f"/api/tasks/{DEFAULT_ID}/dispatch", "POST", {}, "operator")
        self.assertEqual((status, body["code"]), (403, "FORBIDDEN"))

    def test_03_management_dispatch_terminal_claim_and_field_data(self):
        self.reset()
        status, claimed = self.dispatch_and_claim()
        self.assertEqual((status, claimed["task"]["status"]), (200, "running"))
        status, captured = self.request(f"/api/tasks/{DEFAULT_ID}/observations", "POST", {
            "kind": "scan", "label": "底座结构件编码", "value": "BASE-ZZ18-017", "step_no": 1, "device_id": "AR-01"
        })
        self.assertEqual(status, 200)
        self.assertTrue(captured["observation"]["id"].startswith("OBS-"))
        _, synced = self.request("/api/bootstrap")
        event_types = {event["type"] for event in synced["events"]}
        self.assertTrue({"WORK_ORDER_DISPATCHED", "WORK_ORDER_CLAIMED", "FIELD_DATA_CAPTURED"}.issubset(event_types))

    def test_04_eight_stage_state_machine_and_idempotency(self):
        self.reset()
        self.dispatch_and_claim()
        status, blocked = self.request(f"/api/tasks/{DEFAULT_ID}/steps/1/confirm", "POST", {})
        self.assertEqual((status, blocked["code"]), (400, "VALIDATION_ERROR"))
        self.assertIn("零部件编码校验", blocked["error"])
        self.assertEqual(self.identify_current_part(1)[1]["identification"]["result"], "matched")
        first = self.request(f"/api/tasks/{DEFAULT_ID}/steps/1/confirm", "POST", {}, headers={"Idempotency-Key": "confirm-1"})
        second = self.request(f"/api/tasks/{DEFAULT_ID}/steps/1/confirm", "POST", {}, headers={"Idempotency-Key": "confirm-1"})
        self.assertEqual(first, second)
        self.assertEqual(first[1]["task"]["current_step"], 2)
        status, body = self.request(f"/api/tasks/{DEFAULT_ID}/steps/4/confirm", "POST", {})
        self.assertEqual((status, body["code"]), (400, "VALIDATION_ERROR"))
        for step in range(2, 9):
            if step <= 6:
                self.assertEqual(self.identify_current_part(step)[1]["identification"]["result"], "matched")
            status, body = self.request(f"/api/tasks/{DEFAULT_ID}/steps/{step}/confirm", "POST", {}, headers={"Idempotency-Key": f"confirm-{step}"})
            self.assertEqual(status, 200)
        _, data = self.request("/api/bootstrap")
        self.assertEqual(data["task"]["status"], "completed")
        self.assertTrue(any(event["type"] == "WORK_ORDER_COMPLETED" for event in data["events"]))

    def test_04b_part_identification_match_review_and_block(self):
        self.reset()
        self.dispatch_and_claim()
        cases = [
            ("PART-ZZ16-S01-017", "blocked", "model"),
            ("PART-ZZ18-S01-018", "blocked", "work_order"),
            ("PART-ZZ18-S06-017", "blocked", "process"),
            ("CODE-UNREADABLE", "needs_review", "code"),
            ("PART-ZZ18-S01-017", "matched", None),
        ]
        for code, expected, failed_check in cases:
            status, result = self.request(f"/api/tasks/{DEFAULT_ID}/parts/identify", "POST", {"code": code, "step_no": 1, "device_id": "AR-01"})
            self.assertEqual(status, 200)
            item = result["identification"]
            self.assertEqual(item["result"], expected)
            if failed_check:
                self.assertFalse(item["checks"][failed_check])
        _, data = self.request("/api/bootstrap")
        self.assertEqual(len(data["partIdentifications"]), 5)
        self.assertTrue(any(event["type"] == "PART_BLOCKED" for event in data["events"]))
        self.assertTrue(any(event["type"] == "PART_IDENTIFIED" for event in data["events"]))

    def test_05_parallel_work_orders_remain_independent(self):
        self.reset()
        self.dispatch_and_claim()
        self.identify_current_part(1)
        self.request(f"/api/tasks/{DEFAULT_ID}/steps/1/confirm", "POST", {})
        _, current = self.request("/api/bootstrap")
        _, other = self.request("/api/bootstrap?task_id=WO-HYD-2026-0828-018")
        self.assertEqual(current["task"]["current_step"], 2)
        self.assertEqual((other["task"]["status"], other["task"]["current_step"]), ("running", 3))

    def test_06_knowledge_citation_refusal_and_context_trace(self):
        self.reset()
        _, answer = self.request("/api/ai/ask", "POST", {"task_id": DEFAULT_ID, "question": "液压胶管接口不一致如何处理？", "step_no": 6})
        self.assertFalse(answer["refused"])
        self.assertGreaterEqual(len(answer["citations"]), 1)
        _, refusal = self.request("/api/ai/ask", "POST", {"task_id": DEFAULT_ID, "question": "明天厂区天气如何？", "step_no": 6})
        self.assertTrue(refusal["refused"])
        _, data = self.request("/api/bootstrap")
        self.assertEqual(len(data["aiInteractions"]), 2)

    def test_07_incident_remote_expert_and_archive(self):
        self.reset()
        self.dispatch_and_claim()
        status, incident = self.request("/api/incidents", "POST", {"task_id": DEFAULT_ID, "type": "结构件孔位偏差", "description": "现场复核需要专家确认", "step_no": 1})
        self.assertEqual(status, 200)
        status, session = self.request("/api/collaboration", "POST", {"task_id": DEFAULT_ID, "incident_id": incident["id"]}, "expert")
        self.assertEqual(status, 200)
        for kind in ("freeze", "annotation", "document"):
            self.assertEqual(self.request(f"/api/collaboration/{session['id']}/annotations", "POST", {"kind": kind, "payload": {"text": "复核标注与工艺资料"}}, "expert")[0], 200)
        self.assertEqual(self.request(f"/api/collaboration/{session['id']}/end", "POST", {"resolution": "复核后确认可按工艺继续装配"}, "expert")[0], 200)
        _, data = self.request("/api/bootstrap")
        self.assertEqual(data["incidents"][0]["status"], "closed")
        self.assertEqual(data["sessions"][0]["status"], "archived")
        self.assertTrue(data["sessions"][0]["recording_index"].startswith("REC-"))

    def test_08_maintenance_case_query_and_formation(self):
        self.reset()
        status, result = self.request(f"/api/maintenance/cases?q={urllib.parse.quote('液压')}")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(result["items"]), 1)
        status, created = self.request("/api/maintenance/cases", "POST", {"task_id": DEFAULT_ID, "component": "立柱", "symptom": "动作迟缓"}, "engineer")
        self.assertEqual(status, 200)
        _, data = self.request("/api/bootstrap")
        self.assertTrue(any(item["id"] == created["id"] and item["status"] == "待审核" for item in data["maintenanceCases"]))

    def test_08b_ar_view_state_is_shared_by_work_order(self):
        self.reset()
        status, initial = self.request(f"/api/tasks/{DEFAULT_ID}/view-state")
        self.assertEqual((status, initial["rotation_x"], initial["rotation_y"]), (200, -0.05, -0.48))
        status, saved = self.request(f"/api/tasks/{DEFAULT_ID}/view-state", "POST", {
            "rotation_x": 0.18, "rotation_y": 1.24, "camera_distance": 9.6, "device_id": "AR-01",
        })
        self.assertEqual(status, 200)
        self.assertEqual((saved["rotation_x"], saved["rotation_y"], saved["camera_distance"], saved["device_id"]), (0.18, 1.24, 9.6, "AR-01"))
        _, loaded = self.request(f"/api/tasks/{DEFAULT_ID}/view-state")
        self.assertEqual((loaded["rotation_y"], loaded["camera_distance"]), (1.24, 9.6))

    def test_09_create_work_order_and_process_snapshot(self):
        self.reset()
        payload = {"product":"液压支架第021架","model":"ZZ18000/35/70D","station":"总装一线 · ZP-07","process_version":"V3.0","device_id":"AR-02"}
        status, created = self.request("/api/tasks", "POST", payload, "engineer")
        self.assertEqual(status, 200)
        task_id = created["task"]["id"]
        self.assertEqual((created["task"]["status"], len(created["task"]["steps"])), ("draft", 8))
        self.assertEqual(self.request(f"/api/tasks/{task_id}/dispatch", "POST", {"device_id":"AR-02"}, "engineer")[0], 200)

    def test_10_trace_exports_and_backup_restore(self):
        self.reset()
        status, csv_data = self.request("/api/trace/export?format=csv&task_id=WO-HYD-2026-0828-018", raw=True)
        self.assertEqual(status, 200)
        self.assertIn(b"WO-HYD-2026-0828-018", csv_data)
        status, backup = self.request("/api/admin/backup", "POST", {}, "admin")
        self.assertEqual(status, 200)
        self.assertGreater(backup["size"], 0)
        status, restored = self.request("/api/admin/restore", "POST", {}, "admin")
        self.assertEqual(status, 200)
        self.assertTrue(restored["ok"])

    def test_11_knowledge_document_management(self):
        self.reset()
        status, created = self.request("/api/knowledge/documents", "POST", {
            "title": "装配补充说明", "category": "作业指导书", "version": "V1.0",
            "tags": "装配,补充资料", "section": "工序1-2", "file_name": "装配补充说明.txt", "content": "现场装配补充要求"
        }, "engineer")
        self.assertEqual(status, 200)
        doc_id = created["id"]
        status, detail = self.request(f"/api/knowledge/documents/{doc_id}")
        self.assertEqual((status, detail["category"], detail["file_name"]), (200, "作业指导书", "装配补充说明.txt"))
        status, raw = self.request(f"/api/knowledge/documents/{doc_id}/download", raw=True)
        self.assertEqual(status, 200)
        self.assertIn("现场装配补充要求".encode(), raw)

    def test_12_process_version_and_step_management(self):
        self.reset()
        status, created = self.request("/api/processes", "POST", {"version": "V3.2-test", "base_version": "V3.0", "change_note": "测试工艺管理"}, "engineer")
        self.assertEqual((status, created["version"]), (200, "V3.2-test"))
        status, saved = self.request("/api/processes/V3.2-test/steps", "POST", {
            "step_no": 1, "title": "装配准备与编码核验", "instruction": "核对工单和物料编码",
            "parameter": "工单、型号和BOM一致", "safety": "不一致时停止作业", "media": "text,image,3d"
        }, "engineer")
        self.assertEqual((status, saved["step_no"]), (200, 1))
        _, data = self.request("/api/bootstrap")
        step = next(item for item in data["processSteps"] if item["version"] == "V3.2-test" and item["step_no"] == 1)
        self.assertEqual(step["title"], "装配准备与编码核验")


if __name__ == "__main__":
    unittest.main(verbosity=2)
