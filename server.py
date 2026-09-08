#!/usr/bin/env python3
"""AR production verification demo server.

No third-party runtime dependencies are required. The service exposes a small
OpenAPI-style REST surface, persists demo state in SQLite and serves the Web UI.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"


def load_local_env():
    """Load deployment-only values without requiring a dotenv dependency."""
    env_file = ROOT / ".env.local"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_local_env()
DB_PATH = Path(os.environ.get("AR_DEMO_DB", DATA_DIR / "ar_demo.db"))
DEFAULT_TASK_ID = "WO-HYD-2026-0828-017"
DEMO_DATA_VERSION = "3.3.0-management-trace-recording"
ROLES = {"operator", "engineer", "expert", "admin"}
WRITE_ROLES = {
    "step": {"operator", "admin"},
    "dispatch": {"engineer", "admin"},
    "claim": {"operator", "admin"},
    "capture": {"operator", "admin"},
    "incident": {"operator", "engineer", "admin"},
    "expert": {"expert", "engineer", "admin"},
    "process": {"engineer", "admin"},
    "knowledge": {"engineer", "admin"},
    "device": {"admin"},
    "admin": {"admin"},
}

STEPS = [
    (1, "装配准备与部件核验", "扫描工单和主要结构件编码，核对架次、工位、物料齐套状态及当前受控工艺版本", "完成工单确认、物料核验和吊装前检查", "资料、物料或编码不一致时停止流转并提交问题", "text,image,3d"),
    (2, "底座及连杆机构装配", "按三维作业指导完成底座定位、前后连杆及销轴装配，上传关键连接点照片", "销轴、挡圈和连接方向符合装配图要求", "吊装区域设置警戒，严禁人员进入受力构件下方", "text,image,3d"),
    (3, "掩护梁与顶梁装配", "调用对应动画片段，完成掩护梁、顶梁与连杆机构连接并确认活动间隙", "结构件方向、连接孔位和活动范围符合工艺要求", "大件翻转和对孔过程中防止挤压、碰撞和构件失稳", "text,image,video,3d"),
    (4, "立柱与千斤顶安装", "识别立柱和千斤顶编码，匹配安装位置、接口方向和紧固要求后完成装配", "左右位置、销轴连接和接口朝向与受控图纸一致", "液压元件装配前保持封口和清洁，禁止带污染物安装", "text,image,3d"),
    (5, "推移机构装配", "完成推移杆、推移千斤顶和连接件安装，检查运动方向及机械干涉", "推移机构连接完整，运动方向与行程满足工艺要求", "试动前确认人员、工具和临时支撑已撤离运动区域", "text,image,video,3d"),
    (6, "操纵阀组与高压胶管连接", "根据产品编码和液压原理图自动匹配阀组端口与胶管，逐项连接并整理走向", "胶管标识、端口对应和敷设路径以受控资料为准", "匹配结果有疑义时不得凭经验连接，应暂停并发起专家协助", "text,image,3d"),
    (7, "整架调整与功能检查", "检查结构动作、限位、胶管干涉和液压功能，记录试验参数及发现的问题", "功能检查项目全部完成，异常均有处置结论", "试验过程设置安全区域，发现异常立即停止动作并泄压", "text,image,video"),
    (8, "完工复检与资料归档", "完成整架外观、连接、标识和资料复核，提交完工照片并形成全过程记录", "工单、工艺快照、现场数据、问答和专家记录完整", "确认工具、余料、临时支撑和防护用品已清点复位", "text,image"),
]

KNOWLEDGE = [
    ("KB-001", "液压支架液压控制系统及阀技术要求", "受控版 2026.08", "工序6-7", "液压控制系统、操纵阀组、高压胶管和功能试验的技术要求与检查依据。"),
    ("KB-002", "ZZ18000液压支架总装工艺路线", "V3.0", "工序1-8", "完整装配过程的主要工序、必要步骤、质量要求和安全要求。"),
    ("KB-003", "ZZ18000液压原理图与接口清单", "受控版 V3.2", "工序4、6、7", "产品级端口对应、胶管连接关系和液压功能检查依据。"),
    ("KB-004", "结构件装配三维作业指导", "发布版 V3.0", "工序2-5", "由完整装配动画拆分形成的结构件装配动画片段、视角和操作说明。"),
    ("KB-005", "现场异常与维修处置经验", "持续更新", "维修案例库", "经审核的故障现象、排查过程、专家意见、备件和最终处置结果。"),
]

MAINTENANCE_CASES = [
    ("MC-2026-031", "液压支架", "操纵阀组", "阀芯动作迟滞", "油液污染或阀芯局部卡阻", "隔离压力后清洁阀组，检查阀芯运动并复测动作时间", "密封件包、清洁组件", "已审核"),
    ("MC-2026-027", "液压支架", "高压胶管", "接头处渗漏", "密封件损伤或接头紧固不到位", "泄压后更换密封件，按工艺要求重新连接并保压复检", "O形圈、接头", "已审核"),
    ("MC-2026-019", "液压支架", "推移机构", "推移行程不足", "机械干涉或行程限位设置不正确", "检查推移杆连接和干涉点，调整限位后进行空载与负载复测", "连接销、限位件", "已审核"),
]

PART_STAGES = {
    1: ("底座主要结构件", "BOM-01-BASE"),
    2: ("底座连杆组件", "BOM-02-LINK"),
    3: ("顶梁与掩护梁组件", "BOM-03-CANOPY"),
    4: ("立柱与千斤顶组件", "BOM-04-CYLINDER"),
    5: ("推移机构组件", "BOM-05-PUSH"),
    6: ("操纵阀组与高压胶管", "BOM-06-HYDRAULIC"),
}


class KnowledgeServiceAdapter:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.base_url = os.environ.get("KNOWLEDGE_SERVICE_BASE_URL", "").rstrip("/")
        self.api_key = os.environ.get("KNOWLEDGE_SERVICE_API_KEY", "")
        self.chat_id = os.environ.get("KNOWLEDGE_SERVICE_CHAT_ID", "")
        self.timeout = float(os.environ.get("KNOWLEDGE_SERVICE_TIMEOUT", "20"))
        self._status_cache = None
        self._status_at = 0.0

    @property
    def configured(self):
        return bool(self.enabled and self.base_url and self.api_key and self.chat_id)

    def _request(self, path, payload=None, authenticated=False, timeout=None):
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if authenticated:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(f"{self.base_url}{path}", data=body, headers=headers, method="POST" if body is not None else "GET")
        try:
            with urlopen(request, timeout=timeout or self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"知识服务 HTTP {error.code}: {detail}") from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise RuntimeError(f"知识服务连接失败: {error}") from error

    def status(self, force=False):
        if not self.enabled:
            return {"provider": "企业知识服务", "mode": "disabled", "configured": False, "online": False, "version": "--"}
        now = time.monotonic()
        if not force and self._status_cache and now - self._status_at < 20:
            return self._status_cache
        result = {"provider": "企业知识服务", "mode": "live" if self.configured else "local-fallback", "configured": self.configured, "online": False, "version": "--"}
        if self.base_url:
            try:
                response = self._request("/api/v1/system/version", timeout=3)
                result["online"] = response.get("code") == 0
                result["version"] = response.get("data", "--")
            except RuntimeError:
                pass
        self._status_cache, self._status_at = result, now
        return result

    @staticmethod
    def _citations(response):
        choices = response.get("choices") or []
        choice = choices[0] if choices else {}
        reference = (
            response.get("reference")
            or (choice.get("message") or {}).get("reference")
            or (choice.get("delta") or {}).get("reference")
            or response.get("data", {}).get("reference")
            or {}
        )
        chunks = reference.get("chunks", []) if isinstance(reference, dict) else reference if isinstance(reference, list) else []
        if isinstance(chunks, dict):
            chunks = list(chunks.values())
        citations = []
        for index, chunk in enumerate(chunks[:5], start=1):
            if not isinstance(chunk, dict):
                continue
            positions = chunk.get("positions") or []
            page = ""
            if positions and isinstance(positions[0], (list, tuple)) and positions[0]:
                page = str(positions[0][0] + 1)
            citations.append({
                "id": chunk.get("id") or chunk.get("chunk_id") or f"RAG-{index}",
                "title": chunk.get("document_name") or chunk.get("doc_name") or chunk.get("dataset_name") or "知识库检索片段",
                "version": chunk.get("document_keyword") or "受控知识库",
                "section": f"第{page}页" if page else chunk.get("section", "检索片段"),
            })
        return citations

    def ask(self, question, context):
        if not self.configured:
            raise RuntimeError("企业知识服务尚未完成API配置")
        payload = {
            "model": "model",
            "messages": [
                {"role": "system", "content": context},
                {"role": "user", "content": question},
            ],
            "stream": False,
            "extra_body": {"reference": True, "reference_metadata": {"include": True}},
        }
        response = self._request(f"/api/v1/openai/{self.chat_id}/chat/completions", payload, authenticated=True)
        choices = response.get("choices") or []
        if not choices:
            raise RuntimeError(f"知识服务未返回有效回答: {str(response)[:300]}")
        message = choices[0].get("message") or {}
        answer = message.get("content", "").strip()
        if not answer:
            raise RuntimeError("知识服务返回空回答")
        return {
            "answer": answer,
            "citations": self._citations(response),
            "model": response.get("model") or "企业知识问答模型",
            "usage": response.get("usage") or {},
        }


class IntegrationRegistry:
    def __init__(self, enabled=True):
        self.knowledge = KnowledgeServiceAdapter(enabled=enabled)

    def status(self):
        return {
            "knowledge": self.knowledge.status(),
            "collaboration": {
                "provider": "自建远程专家服务",
                "mode": "interactive-simulator",
                "configured": True,
                "online": True,
                "note": "协作请求、会话、冻屏标注、资料发送和归档链路可交互演示",
            },
            "digital_twin": {
                "provider": "数字孪生标准接口",
                "mode": "sample-data",
                "configured": True,
                "online": True,
                "note": "使用标准对象编码和样例数据演示产线透视",
            },
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def uid(prefix: str) -> str:
    return f"{prefix}-{datetime.now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"


class DemoDB:
    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.backup_dir = self.path.parent / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.init()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self):
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY,name TEXT,status TEXT,battery INTEGER,capabilities TEXT,config TEXT,last_seen TEXT,adapter TEXT);
            CREATE TABLE IF NOT EXISTS process_versions(id TEXT PRIMARY KEY,version TEXT,status TEXT,checksum TEXT,change_note TEXT,created_at TEXT,published_at TEXT);
            CREATE TABLE IF NOT EXISTS process_steps(version TEXT,step_no INTEGER,title TEXT,instruction TEXT,parameter TEXT,safety TEXT,media TEXT,PRIMARY KEY(version,step_no));
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,product TEXT,model TEXT,station TEXT,operator TEXT,device_id TEXT,process_version TEXT,status TEXT,current_step INTEGER,created_at TEXT,updated_at TEXT);
            CREATE TABLE IF NOT EXISTS step_executions(task_id TEXT,step_no INTEGER,status TEXT,confirmed_at TEXT,actor TEXT,PRIMARY KEY(task_id,step_no));
            CREATE TABLE IF NOT EXISTS field_observations(id TEXT PRIMARY KEY,task_id TEXT,step_no INTEGER,kind TEXT,label TEXT,value TEXT,media_ref TEXT,device_id TEXT,actor TEXT,created_at TEXT);
            CREATE TABLE IF NOT EXISTS part_identifications(id TEXT PRIMARY KEY,task_id TEXT,step_no INTEGER,raw_code TEXT,component TEXT,part_model TEXT,bom_item TEXT,checks TEXT,result TEXT,message TEXT,actor TEXT,device_id TEXT,created_at TEXT);
            CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY,task_id TEXT,step_no INTEGER,type TEXT,description TEXT,status TEXT,owner TEXT,resolution TEXT,created_at TEXT,closed_at TEXT);
            CREATE TABLE IF NOT EXISTS knowledge(id TEXT PRIMARY KEY,title TEXT,version TEXT,section TEXT,content TEXT,status TEXT,created_at TEXT);
            CREATE TABLE IF NOT EXISTS knowledge_assets(id TEXT PRIMARY KEY,knowledge_id TEXT,category TEXT,tags TEXT,file_name TEXT,file_type TEXT,file_size TEXT,source TEXT,status TEXT,created_at TEXT);
            CREATE TABLE IF NOT EXISTS ai_interactions(id TEXT PRIMARY KEY,task_id TEXT,step_no INTEGER,question TEXT,answer TEXT,citations TEXT,refused INTEGER,model TEXT,kb_version TEXT,latency_ms INTEGER,created_at TEXT,actor TEXT);
            CREATE TABLE IF NOT EXISTS collaboration_sessions(id TEXT PRIMARY KEY,task_id TEXT,incident_id TEXT,status TEXT,expert TEXT,provider TEXT,recording_index TEXT,started_at TEXT,ended_at TEXT);
            CREATE TABLE IF NOT EXISTS annotations(id TEXT PRIMARY KEY,session_id TEXT,kind TEXT,payload TEXT,created_at TEXT,actor TEXT);
            CREATE TABLE IF NOT EXISTS ar_view_states(task_id TEXT PRIMARY KEY,rotation_x REAL,rotation_y REAL,camera_distance REAL,device_id TEXT,updated_at TEXT);
            CREATE TABLE IF NOT EXISTS maintenance_cases(id TEXT PRIMARY KEY,equipment TEXT,component TEXT,symptom TEXT,cause TEXT,resolution TEXT,parts TEXT,source_task TEXT,status TEXT,created_at TEXT);
            CREATE TABLE IF NOT EXISTS spatial_anchors(id TEXT PRIMARY KEY,station TEXT,label TEXT,method TEXT,x REAL,y REAL,z REAL,error_mm REAL,status TEXT,updated_at TEXT);
            CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,task_id TEXT,type TEXT,title TEXT,detail TEXT,actor TEXT,source TEXT,created_at TEXT);
            CREATE TABLE IF NOT EXISTS audits(id TEXT PRIMARY KEY,role TEXT,actor TEXT,method TEXT,path TEXT,result TEXT,detail TEXT,created_at TEXT);
            CREATE TABLE IF NOT EXISTS plugins(id TEXT PRIMARY KEY,name TEXT,version TEXT,status TEXT,entrypoint TEXT,description TEXT,created_at TEXT);
            CREATE TABLE IF NOT EXISTS idempotency(key TEXT PRIMARY KEY,response TEXT,created_at TEXT);
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
            """)
            version = c.execute("SELECT value FROM metadata WHERE key='demo_data_version'").fetchone()
            if not version or version[0] != DEMO_DATA_VERSION:
                for table in ("annotations", "collaboration_sessions", "ar_view_states", "maintenance_cases", "ai_interactions", "incidents", "part_identifications", "field_observations", "events", "step_executions", "tasks", "process_steps", "process_versions", "knowledge_assets", "knowledge", "spatial_anchors", "devices", "plugins", "idempotency"):
                    c.execute(f"DELETE FROM {table}")
                self._seed(c)
                c.execute("INSERT OR REPLACE INTO metadata VALUES('demo_data_version',?)", (DEMO_DATA_VERSION,))

    def _seed(self, c):
        now = utc_now()
        devices = [
            ("AR-01", "安全帽式AR终端一", "online", 82, ["display", "camera", "audio", "voice", "button"], "通用AR终端适配器"),
            ("AR-02", "安全帽式AR终端二", "online", 74, ["display", "camera", "audio", "voice", "button"], "通用AR终端适配器"),
            ("AR-03", "普通佩戴式AR终端", "online", 91, ["display", "camera", "audio", "voice"], "通用AR终端适配器"),
        ]
        c.executemany("INSERT INTO devices VALUES(?,?,?,?,?,?,?,?)", [
            (device_id, name, status, battery, json.dumps(capabilities, ensure_ascii=False), json.dumps({"brightness": 68, "offlineCache": True, "identification": "工单二维码"}, ensure_ascii=False), now, adapter)
            for device_id, name, status, battery, capabilities, adapter in devices
        ])
        versions = [
            ("PROC-V29", "V2.9", "archived", "SHA-4A18", "历史版本：液压支架总装工艺路线", now, now),
            ("PROC-V30", "V3.0", "published", "SHA-7C29", "完整装配过程八个主要工序演示基线", now, now),
            ("PROC-V31", "V3.1-draft", "draft", "SHA-9D31", "待审核：增加完工照片辅助检查", now, None),
        ]
        c.executemany("INSERT INTO process_versions VALUES(?,?,?,?,?,?,?)", versions)
        for version in ("V2.9", "V3.0", "V3.1-draft"):
            c.executemany("INSERT INTO process_steps VALUES(?,?,?,?,?,?,?)", [(version, *s) for s in STEPS])
        tasks = [
            (DEFAULT_TASK_ID, "液压支架第017架", "ZZ18000/35/70D", "总装一线 · ZP-03", "待分配", "AR-01", "V3.0", "draft", 1),
            ("WO-HYD-2026-0828-018", "液压支架第018架", "ZZ18000/35/70D", "总装一线 · ZP-04", "李工/A023", "AR-02", "V3.0", "running", 3),
            ("WO-HYD-2026-0828-019", "液压支架第019架", "ZZ18000/35/70D", "总装一线 · ZP-05", "王工/A031", "AR-03", "V3.0", "paused", 6),
            ("WO-HYD-2026-0828-020", "液压支架第020架", "ZZ18000/35/70D", "总装一线 · ZP-06", "赵工/A045", "AR-01", "V3.0", "completed", 8),
        ]
        c.executemany("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)", [(*task, now, now) for task in tasks])
        for task in tasks:
            task_id, operator, current_step = task[0], task[4], task[8]
            for n in range(1, len(STEPS) + 1):
                completed = n < current_step or task[7] == "completed"
                c.execute("INSERT INTO step_executions VALUES(?,?,?,?,?)", (task_id, n, "completed" if completed else "pending", now if completed else None, operator if completed else None))
        c.executemany("INSERT INTO knowledge VALUES(?,?,?,?,?,?,?)", [(*x, "published", now) for x in KNOWLEDGE])
        knowledge_assets = [
            ("KA-001", "KB-001", "工艺标准", "液压系统,阀组,标准", "液压支架液压控制系统及阀技术要求.pdf", "PDF", "3.8 MB", "受控标准库", "已索引", now),
            ("KA-002", "KB-002", "作业指导书", "液压支架,总装,工艺路线", "ZZ18000液压支架总装工艺路线.docx", "DOCX", "1.2 MB", "工艺部门", "已索引", now),
            ("KA-003", "KB-003", "图纸与接口", "液压原理图,接口,胶管", "ZZ18000液压原理图与接口清单.pdf", "PDF", "6.4 MB", "设计部门", "已索引", now),
            ("KA-004", "KB-004", "三维作业指导", "结构件,三维,动画", "结构件装配三维作业指导.zip", "ZIP", "128 MB", "三维内容库", "已发布", now),
            ("KA-005", "KB-005", "维修案例", "异常,维修,专家经验", "现场异常与维修处置经验.md", "MD", "86 KB", "维修管理系统", "持续更新", now),
        ]
        c.executemany("INSERT INTO knowledge_assets VALUES(?,?,?,?,?,?,?,?,?,?)", knowledge_assets)
        c.executemany("INSERT INTO maintenance_cases VALUES(?,?,?,?,?,?,?,?,?,?)", [(*x[:-1], "历史案例", x[-1], now) for x in MAINTENANCE_CASES])
        for task in tasks:
            task_id, station, operator, device_id, current_step = task[0], task[3], task[4], task[5], task[8]
            self._event(c, task_id, "WORK_ORDER_CREATED", "装配工单已创建", f"已绑定完整装配工艺V3.0和工位{station}。", "生产管理/WEB", "work-order-service")
            if task[7] != "draft":
                self._event(c, task_id, "WORK_ORDER_DISPATCHED", "并行装配工单已下发", f"绑定完整装配工艺V3.0和工位{station}。", "生产管理/WEB", "work-order-service")
            if operator not in ("待领取", "待分配"):
                self._event(c, task_id, "DEVICE_BOUND", "AR终端与操作员已绑定", f"{operator}通过{device_id}领取工单。", f"{operator}/{device_id}", "ar-terminal-adapter")
            for n in range(1, current_step):
                self._event(c, task_id, "PROCESS_CONFIRMED", f"主要工序{n}已确认", f"{STEPS[n-1][1]}执行结果已保存。", f"{operator}/{device_id}", "work-order-service")
            if task[7] == "completed":
                self._event(c, task_id, "WORK_ORDER_COMPLETED", "整架装配工单已完工", "八个主要工序、工艺快照和全过程记录已归档。", f"{operator}/{device_id}", "work-order-service")
        c.execute("INSERT INTO incidents VALUES(?,?,?,?,?,?,?,?,?,?)", ("INC-DEMO-019", "WO-HYD-2026-0828-019", 6, "胶管标识与接口清单不一致", "扫描胶管后系统匹配结果与现场标识不一致，已停止连接并等待工艺确认。", "open", "工艺负责人", "", now, None))
        self._event(c, "WO-HYD-2026-0828-019", "INCIDENT_REPORTED", "现场异常已上报", "胶管标识与接口清单不一致，工单已暂停。", "王工/AR-03", "incident-service")

    def _event(self, c, task_id, kind, title, detail, actor, source):
        c.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?)", (uid("EVT"), task_id, kind, title, detail, actor, source, utc_now()))

    @staticmethod
    def rows(cursor):
        return [dict(r) for r in cursor.fetchall()]

    def audit(self, role, actor, method, path, result, detail=""):
        with self.connect() as c:
            c.execute("INSERT INTO audits VALUES(?,?,?,?,?,?,?,?)", (uid("AUD"), role, actor, method, path, result, detail[:500], utc_now()))

    def bootstrap(self, task_id=DEFAULT_TASK_ID):
        with self.connect() as c:
            row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                row = c.execute("SELECT * FROM tasks ORDER BY id LIMIT 1").fetchone()
            task = dict(row)
            task_id = task["id"]
            task["steps"] = self.rows(c.execute("SELECT ps.*,se.status,se.confirmed_at FROM process_steps ps JOIN step_executions se ON se.step_no=ps.step_no AND se.task_id=? WHERE ps.version=? ORDER BY ps.step_no", (task_id, task["process_version"])))
            device = dict(c.execute("SELECT * FROM devices WHERE id=?", (task["device_id"],)).fetchone())
            device["capabilities"] = json.loads(device["capabilities"])
            device["config"] = json.loads(device["config"])
            incidents = self.rows(c.execute("SELECT * FROM incidents WHERE task_id=? ORDER BY created_at DESC", (task_id,)))
            sessions = self.rows(c.execute("SELECT * FROM collaboration_sessions WHERE task_id=? ORDER BY started_at DESC", (task_id,)))
            for session in sessions:
                session["annotations"] = self.rows(c.execute("SELECT * FROM annotations WHERE session_id=? ORDER BY created_at", (session["id"],)))
                for annotation in session["annotations"]:
                    annotation["payload"] = json.loads(annotation["payload"])
            identifications = self.rows(c.execute("SELECT * FROM part_identifications WHERE task_id=? ORDER BY created_at,id", (task_id,)))
            for identification in identifications:
                identification["checks"] = json.loads(identification["checks"])
            knowledge_documents = self.rows(c.execute("""SELECT k.id,k.title,k.version,k.section,k.content,k.status,k.created_at,
                a.category,a.tags,a.file_name,a.file_type,a.file_size,a.source,a.status AS index_status
                FROM knowledge k LEFT JOIN knowledge_assets a ON a.knowledge_id=k.id ORDER BY a.category,k.title"""))
            all_tasks = self.rows(c.execute("SELECT * FROM tasks ORDER BY CASE status WHEN 'paused' THEN 0 WHEN 'running' THEN 1 WHEN 'pending' THEN 2 WHEN 'draft' THEN 3 ELSE 4 END,id"))
            content_packages = [
                {
                    "process_no": row["step_no"],
                    "title": row["title"],
                    "formats": row["media"].split(","),
                    "version": task["process_version"],
                    "status": "published",
                    "terminal_variant": "轻量版",
                }
                for row in task["steps"]
            ]
            twin_objects = [
                {
                    "station": item["station"],
                    "work_order": item["id"],
                    "product": item["product"],
                    "status": item["status"],
                    "progress": 100 if item["status"] == "completed" else round((item["current_step"] - 1) / len(STEPS) * 100),
                    "alert": item["status"] == "paused",
                    "twin_object_id": f"TWIN-{item['station'].split('·')[-1].strip()}",
                    "equipment_status": "告警" if item["status"] == "paused" else "运行",
                    "hydraulic_pressure": 28.6 if item["status"] == "running" else 0,
                    "temperature": 36 + item["current_step"],
                }
                for item in all_tasks
            ]
            return {
                "task": task,
                "tasks": all_tasks,
                "device": device,
                "devices": self.rows(c.execute("SELECT id,name,status,battery,last_seen,adapter FROM devices ORDER BY id")),
                "incidents": incidents,
                "sessions": sessions,
                "events": self.rows(c.execute("SELECT * FROM events WHERE task_id=? ORDER BY created_at,id", (task_id,))),
                "observations": self.rows(c.execute("SELECT * FROM field_observations WHERE task_id=? ORDER BY created_at,id", (task_id,))),
                "partIdentifications": identifications,
                "processVersions": self.rows(c.execute("SELECT * FROM process_versions ORDER BY created_at DESC,version DESC")),
                "processSteps": self.rows(c.execute("SELECT * FROM process_steps ORDER BY version,step_no")),
                "knowledge": self.rows(c.execute("SELECT id,title,version,section,status,created_at FROM knowledge ORDER BY title")),
                "knowledgeDocuments": knowledge_documents,
                "maintenanceCases": self.rows(c.execute("SELECT * FROM maintenance_cases ORDER BY created_at DESC,id")),
                "maintenanceIntegration": {
                    "source_system": "维修管理系统",
                    "interface_status": "online",
                    "equipment": {"id": "EQ-HYD-017", "name": "液压支架第017架", "model": "ZZ18000/35/70D", "location": "总装一线 · ZP-03"},
                    "work_order": {"id": "MWO-2026-0829-006", "status": "待处置", "priority": "一般", "symptom": "高压胶管接头处渗漏"},
                    "knowledge_sources": ["维修管理系统历史工单", "受控维修知识库", "专家协同处置记录"],
                    "writeback": "处置结果、现场证据和审核状态回写维修工单",
                },
                "contentPackages": content_packages,
                "digitalTwin": {
                    "line": "液压支架总装一线",
                    "source_system": "产线数字孪生平台",
                    "model_id": "DT-LINE-HYD-01",
                    "model_version": "2026.08.3",
                    "model_format": "轻量三维场景",
                    "model_updated_at": utc_now(),
                    "data_interface": "内部实时数据接口",
                    "display_content": ["产线三维模型", "设备与工位对象", "装配进度", "运行参数", "告警", "维修记录"],
                    "updated_at": utc_now(),
                    "objects": twin_objects,
                    "metrics": {
                        "work_orders": len(all_tasks),
                        "running": sum(1 for item in all_tasks if item["status"] == "running"),
                        "alerts": sum(1 for item in all_tasks if item["status"] == "paused"),
                        "online_terminals": c.execute("SELECT COUNT(*) FROM devices WHERE status='online'").fetchone()[0],
                    },
                },
                "anchors": self.rows(c.execute("SELECT * FROM spatial_anchors ORDER BY id")),
                "aiInteractions": self.rows(c.execute("SELECT * FROM ai_interactions WHERE task_id=? ORDER BY created_at", (task_id,))),
                "recordings": [
                    {"id": "REC-AR-017-01", "task_id": task_id, "step_no": 1, "device_id": task["device_id"], "operator": task["operator"], "title": "装配准备与部件核验", "started_at": task["created_at"], "duration": "00:42", "path": "/assets/ar-recording.mp4", "poster": "/assets/ar-recording-poster.png"},
                    {"id": "REC-AR-017-02", "task_id": task_id, "step_no": 6, "device_id": task["device_id"], "operator": task["operator"], "title": "阀组与高压胶管连接", "started_at": task["updated_at"], "duration": "01:18", "path": "/assets/ar-recording.mp4", "poster": "/assets/ar-recording-poster.png"},
                ],
                "auditCount": c.execute("SELECT COUNT(*) FROM audits").fetchone()[0],
                "process": {"name": "液压支架完整装配过程", "scope": "一架次工单、八个主要工序、多架次并行", "standard": "客户确认工艺路线与受控技术资料"},
                "knowledgeBinding": {"provider": "企业知识服务", "chat_scope": "液压支架装配工艺、技术要求和维修案例", "binding": "产品型号 + 工艺版本 + 当前工序 + 资料标签", "sync_mode": "知识服务管理索引，业务平台管理适用范围、版本和引用记录"},
            }

    def idempotent(self, key):
        if not key:
            return None
        with self.connect() as c:
            row = c.execute("SELECT response FROM idempotency WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def store_idempotent(self, key, response):
        if key:
            with self.connect() as c:
                c.execute("INSERT OR REPLACE INTO idempotency VALUES(?,?,?)", (key, json.dumps(response, ensure_ascii=False), utc_now()))

    def confirm_step(self, task_id, step_no, actor, key):
        prior = self.idempotent(key)
        if prior:
            return prior
        with self.lock, self.connect() as c:
            task = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task or step_no != task["current_step"]:
                raise ValueError("只能确认当前待执行工序")
            if task["status"] != "running":
                raise ValueError("工单不在执行状态")
            incident = c.execute("SELECT 1 FROM incidents WHERE task_id=? AND step_no=? AND status!='closed'", (task_id, step_no)).fetchone()
            if incident:
                raise ValueError("当前工序存在未关闭异常")
            if step_no in PART_STAGES and not c.execute(
                "SELECT 1 FROM part_identifications WHERE task_id=? AND step_no=? AND result='matched'",
                (task_id, step_no),
            ).fetchone():
                raise ValueError("当前工序尚未完成零部件编码校验")
            now = utc_now()
            c.execute("UPDATE step_executions SET status='completed',confirmed_at=?,actor=? WHERE task_id=? AND step_no=?", (now, actor, task_id, step_no))
            if step_no == len(STEPS):
                c.execute("UPDATE tasks SET status='completed',updated_at=? WHERE id=?", (now, task_id))
                self._event(c, task_id, "WORK_ORDER_COMPLETED", "整架装配工单已完工", "八个主要工序、工艺快照、现场数据和过程记录已归档。", actor, "work-order-service")
            else:
                c.execute("UPDATE tasks SET current_step=?,status='running',updated_at=? WHERE id=?", (step_no + 1, now, task_id))
            self._event(c, task_id, "PROCESS_CONFIRMED", f"主要工序{step_no}已确认", f"工序{step_no}执行结果已持久化。", actor, "work-order-service")
        result = {"ok": True, "task": self.bootstrap(task_id)["task"]}
        self.store_idempotent(key, result)
        return result

    def dispatch_task(self, task_id, data, actor):
        with self.lock, self.connect() as c:
            task = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                raise ValueError("工单不存在")
            if task["status"] != "draft":
                raise ValueError("只有草稿工单可以下发")
            device_id = data.get("device_id") or task["device_id"]
            if not c.execute("SELECT 1 FROM devices WHERE id=?", (device_id,)).fetchone():
                raise ValueError("指定终端不存在")
            now = utc_now()
            c.execute("UPDATE tasks SET status='pending',operator='待领取',device_id=?,updated_at=? WHERE id=?", (device_id, now, task_id))
            self._event(c, task_id, "WORK_ORDER_DISPATCHED", "装配工单已下发到现场", f"工单已发送至{device_id}，等待操作员扫码领取。", actor, "work-order-service")
        return {"ok": True, "task": self.bootstrap(task_id)["task"]}

    def view_state(self, task_id):
        with self.connect() as c:
            if not c.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone():
                raise ValueError("工单不存在")
            row = c.execute("SELECT * FROM ar_view_states WHERE task_id=?", (task_id,)).fetchone()
        return dict(row) if row else {
            "task_id": task_id, "rotation_x": -0.05, "rotation_y": -0.48,
            "camera_distance": 15.4, "device_id": "", "updated_at": None,
        }

    def save_view_state(self, task_id, data):
        try:
            rotation_x = max(-0.42, min(0.28, float(data.get("rotation_x", -0.05))))
            rotation_y = max(-100.0, min(100.0, float(data.get("rotation_y", -0.48))))
            camera_distance = max(7.0, min(17.0, float(data.get("camera_distance", 15.4))))
        except (TypeError, ValueError):
            raise ValueError("AR视角参数格式不正确")
        with self.lock, self.connect() as c:
            if not c.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone():
                raise ValueError("工单不存在")
            now = utc_now()
            c.execute("""INSERT INTO ar_view_states VALUES(?,?,?,?,?,?)
                ON CONFLICT(task_id) DO UPDATE SET rotation_x=excluded.rotation_x,rotation_y=excluded.rotation_y,
                camera_distance=excluded.camera_distance,device_id=excluded.device_id,updated_at=excluded.updated_at""",
                (task_id, rotation_x, rotation_y, camera_distance, str(data.get("device_id", "")), now))
        return self.view_state(task_id)

    def create_task(self, data, actor):
        required = {"product": "产品名称", "model": "产品型号", "station": "装配工位", "device_id": "目标终端", "process_version": "工艺版本"}
        values = {}
        for field, label in required.items():
            value = str(data.get(field, "")).strip()
            if not value:
                raise ValueError(f"{label}不能为空")
            values[field] = value
        with self.lock, self.connect() as c:
            if not c.execute("SELECT 1 FROM devices WHERE id=?", (values["device_id"],)).fetchone():
                raise ValueError("目标终端不存在")
            process = c.execute("SELECT status FROM process_versions WHERE version=?", (values["process_version"],)).fetchone()
            if not process or process["status"] != "published":
                raise ValueError("只能选择已发布工艺版本")
            prefix = f"WO-HYD-{datetime.now():%Y-%m%d}-"
            rows = c.execute("SELECT id FROM tasks WHERE id LIKE ?", (f"{prefix}%",)).fetchall()
            numbers = [int(row["id"].rsplit("-", 1)[-1]) for row in rows if row["id"].rsplit("-", 1)[-1].isdigit()]
            task_id = f"{prefix}{max(numbers or [0]) + 1:03d}"
            now = utc_now()
            c.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                task_id, values["product"], values["model"], values["station"], "待分配",
                values["device_id"], values["process_version"], "draft", 1, now, now,
            ))
            steps = c.execute("SELECT step_no FROM process_steps WHERE version=? ORDER BY step_no", (values["process_version"],)).fetchall()
            if not steps:
                raise ValueError("所选工艺版本没有可执行工序")
            c.executemany("INSERT INTO step_executions VALUES(?,?,?,?,?)", [(task_id, row["step_no"], "pending", None, None) for row in steps])
            self._event(c, task_id, "WORK_ORDER_CREATED", "装配工单已创建", f"产品{values['product']}，工位{values['station']}，工艺{values['process_version']}，目标终端{values['device_id']}。", actor, "work-order-service")
        return {"ok": True, "task": self.bootstrap(task_id)["task"]}

    def claim_task(self, task_id, data, actor):
        with self.lock, self.connect() as c:
            task = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                raise ValueError("工单不存在")
            if task["status"] != "pending":
                raise ValueError("工单尚未下发或已被领取")
            operator = (data.get("operator") or actor).strip()
            device_id = data.get("device_id") or task["device_id"]
            if not operator:
                raise ValueError("操作员不能为空")
            if not c.execute("SELECT 1 FROM devices WHERE id=?", (device_id,)).fetchone():
                raise ValueError("指定终端不存在")
            now = utc_now()
            c.execute("UPDATE tasks SET status='running',operator=?,device_id=?,updated_at=? WHERE id=?", (operator, device_id, now, task_id))
            self._event(c, task_id, "WORK_ORDER_CLAIMED", "操作员已扫码领取工单", f"{operator}通过{device_id}领取工单，工艺{task['process_version']}与八个主要工序内容已同步。", f"{operator}/{device_id}", "ar-terminal-adapter")
        return {"ok": True, "task": self.bootstrap(task_id)["task"]}

    def add_observation(self, task_id, data, actor):
        kinds = {"scan": "部件扫码", "photo": "现场照片", "voice": "语音记录", "measurement": "测量数据"}
        kind = data.get("kind", "scan")
        if kind not in kinds:
            raise ValueError("不支持的现场数据类型")
        with self.lock, self.connect() as c:
            task = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                raise ValueError("工单不存在")
            if task["status"] not in ("running", "paused"):
                raise ValueError("工单领取后才能采集现场数据")
            step_no = int(data.get("step_no") or task["current_step"])
            if step_no < 1 or step_no > len(STEPS):
                raise ValueError("工序编号无效")
            observation_id = uid("OBS")
            label = (data.get("label") or kinds[kind]).strip()
            value = (data.get("value") or "").strip()
            media_ref = (data.get("media_ref") or "").strip()
            if not value:
                raise ValueError("采集结果不能为空")
            device_id = data.get("device_id") or task["device_id"]
            now = utc_now()
            c.execute("INSERT INTO field_observations VALUES(?,?,?,?,?,?,?,?,?,?)", (observation_id, task_id, step_no, kind, label, value, media_ref, device_id, actor, now))
            detail = f"工序{step_no} · {label}：{value}"
            self._event(c, task_id, "FIELD_DATA_CAPTURED", f"{kinds[kind]}已回传", detail, f"{actor}/{device_id}", "ar-terminal-adapter")
        return {"ok": True, "observation": {"id": observation_id, "task_id": task_id, "step_no": step_no, "kind": kind, "label": label, "value": value, "media_ref": media_ref, "device_id": device_id, "actor": actor, "created_at": now}}

    def identify_part(self, task_id, data, actor):
        raw_code = str(data.get("code", "")).strip().upper()
        if not raw_code:
            raise ValueError("请输入或扫描零部件编码")
        with self.lock, self.connect() as c:
            task = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                raise ValueError("工单不存在")
            if task["status"] not in ("running", "paused"):
                raise ValueError("工单领取后才能识别零部件")
            step_no = int(data.get("step_no") or task["current_step"])
            if step_no != task["current_step"]:
                raise ValueError("只能校验当前工序的零部件")
            match = re.fullmatch(r"PART-(ZZ\d{2})-S(\d{2})-(\d{3})", raw_code)
            expected_family_match = re.match(r"(ZZ\d{2})", task["model"].upper())
            expected_family = expected_family_match.group(1) if expected_family_match else task["model"].upper()
            expected_order = task_id.rsplit("-", 1)[-1][-3:]
            parsed_family = match.group(1) if match else ""
            parsed_step = int(match.group(2)) if match else 0
            parsed_order = match.group(3) if match else ""
            component, bom_item = PART_STAGES.get(parsed_step, ("未识别零部件", "--"))
            checks = {
                "code": bool(match),
                "model": bool(match and parsed_family == expected_family),
                "work_order": bool(match and parsed_order == expected_order),
                "process": bool(match and parsed_step == step_no),
                "bom": bool(match and parsed_family == expected_family and parsed_step in PART_STAGES),
            }
            if not match:
                result = "needs_review"
                message = "编码无法解析，不能自动确认；请重新扫描、手工录入或转人工复核。"
            elif all(checks.values()):
                result = "matched"
                message = f"{component}与型号、工单、当前工序及BOM全部匹配，可以确认使用。"
            else:
                labels = {"model": "产品型号", "work_order": "装配工单", "process": "当前工序", "bom": "BOM"}
                mismatches = [label for key, label in labels.items() if not checks[key]]
                result = "blocked"
                message = "、".join(mismatches) + "不匹配，已拦截该零部件。"
            identification_id = uid("PID")
            now = utc_now()
            device_id = data.get("device_id") or task["device_id"]
            c.execute("INSERT INTO part_identifications VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                identification_id, task_id, step_no, raw_code, component,
                parsed_family or "--", bom_item, json.dumps(checks, ensure_ascii=False),
                result, message, actor, device_id, now,
            ))
            event_type = {"matched": "PART_IDENTIFIED", "needs_review": "PART_REVIEW_REQUIRED", "blocked": "PART_BLOCKED"}[result]
            event_title = {"matched": "零部件编码校验通过", "needs_review": "零部件编码需要人工复核", "blocked": "零部件错误已拦截"}[result]
            self._event(c, task_id, event_type, event_title, f"工序{step_no} · {raw_code} · {message}", f"{actor}/{device_id}", "part-identification-service")
        return {
            "ok": True, "identification": {
                "id": identification_id, "task_id": task_id, "step_no": step_no,
                "raw_code": raw_code, "component": component, "part_model": parsed_family or "--",
                "bom_item": bom_item, "checks": checks, "result": result,
                "message": message, "actor": actor, "device_id": device_id, "created_at": now,
            },
        }

    def task_action(self, task_id, action, actor):
        labels = {"pause": ("paused", "工单已暂停"), "resume": ("running", "工单已恢复"), "back": ("running", "工单已回退")}
        if action not in labels:
            raise ValueError("不支持的工单操作")
        status, title = labels[action]
        with self.connect() as c:
            task = c.execute("SELECT current_step,status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                raise ValueError("工单不存在")
            if task["status"] not in ("running", "paused"):
                raise ValueError("工单领取后才能执行该操作")
            step = task[0]
            if action == "back":
                step = max(1, step - 1)
                c.execute("UPDATE step_executions SET status='pending',confirmed_at=NULL,actor=NULL WHERE task_id=? AND step_no>=?", (task_id, step))
            c.execute("UPDATE tasks SET status=?,current_step=?,updated_at=? WHERE id=?", (status, step, utc_now(), task_id))
            self._event(c, task_id, f"WORK_ORDER_{action.upper()}", title, f"当前主要工序：{step}", actor, "work-order-service")
        return {"ok": True}

    def create_incident(self, task_id, data, actor, key):
        prior = self.idempotent(key)
        if prior:
            return prior
        with self.connect() as c:
            incident_id = uid("INC")
            step = int(data.get("step_no", 3))
            if not c.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone():
                raise ValueError("工单不存在")
            c.execute("INSERT INTO incidents VALUES(?,?,?,?,?,?,?,?,?,?)", (incident_id, task_id, step, data.get("type", "现场异常"), data.get("description", ""), "open", "工艺负责人", "", utc_now(), None))
            c.execute("UPDATE tasks SET status='paused',updated_at=? WHERE id=?", (utc_now(), task_id))
            self._event(c, task_id, "INCIDENT_REPORTED", "现场异常已上报", data.get("description", ""), actor, "incident-service")
        result = {"ok": True, "id": incident_id}
        self.store_idempotent(key, result)
        return result

    def start_collaboration(self, task_id, incident_id, actor):
        with self.connect() as c:
            existing = c.execute("SELECT id FROM collaboration_sessions WHERE task_id=? AND status='active'", (task_id,)).fetchone()
            if existing:
                return {"ok": True, "id": existing[0]}
            session_id = uid("CX")
            c.execute("INSERT INTO collaboration_sessions VALUES(?,?,?,?,?,?,?,?,?)", (session_id, task_id, incident_id, "active", "李工/液压系统装配", "自建远程专家服务", "", utc_now(), None))
            self._event(c, task_id, "COLLAB_STARTED", "远程专家会话已建立", f"会话{session_id}已连接专家李工。", actor, "collaboration-adapter")
        return {"ok": True, "id": session_id}

    def add_annotation(self, session_id, data, actor):
        with self.connect() as c:
            session = c.execute("SELECT task_id FROM collaboration_sessions WHERE id=? AND status='active'", (session_id,)).fetchone()
            if not session:
                raise ValueError("协同会话未建立或已结束")
            annotation_id = uid("ANN")
            payload = data.get("payload") or {"x": 0.47, "y": 0.58, "text": "检查胶管标识与阀组接口对应关系"}
            c.execute("INSERT INTO annotations VALUES(?,?,?,?,?,?)", (annotation_id, session_id, data.get("kind", "marker"), json.dumps(payload, ensure_ascii=False), utc_now(), actor))
            self._event(c, session[0], "ANNOTATION_SYNCED", "专家内容已同步到AR端", payload.get("text", "冻屏标注"), actor, "collaboration-service")
        return {"ok": True, "id": annotation_id}

    def end_collaboration(self, session_id, resolution, actor):
        with self.connect() as c:
            row = c.execute("SELECT incident_id,task_id FROM collaboration_sessions WHERE id=? AND status='active'", (session_id,)).fetchone()
            if not row:
                raise ValueError("协同会话未建立或已结束")
            now = utc_now()
            recording = f"REC-{session_id}"
            c.execute("UPDATE collaboration_sessions SET status='archived',recording_index=?,ended_at=? WHERE id=?", (recording, now, session_id))
            if row[0]:
                c.execute("UPDATE incidents SET status='closed',resolution=?,closed_at=? WHERE id=?", (resolution, now, row[0]))
            task_id = row[1]
            c.execute("UPDATE tasks SET status='running',updated_at=? WHERE id=?", (now, task_id))
            self._event(c, task_id, "COLLAB_ARCHIVED", "协同会话结束并归档", f"录像索引{recording}、截图、冻屏标注、资料发送记录和处置意见已归档。", actor, "collaboration-service")
            self._event(c, task_id, "INCIDENT_CLOSED", "异常已复检关闭", resolution, actor, "incident-service")
        return {"ok": True, "recording_index": recording}

    def ask_local(self, question, step_no):
        tokens = set(question.lower().replace("？", "").replace("，", " ").split())
        with self.connect() as c:
            docs = self.rows(c.execute("SELECT * FROM knowledge WHERE status='published'"))
            scored = []
            keywords = {"液压", "阀组", "胶管", "接口", "连接", "标识", "敷设", "清洁", "标准", "图纸", "原理图", "安全"}
            for doc in docs:
                score = sum(3 for k in keywords if k in question and k in doc["content"])
                score += sum(1 for token in tokens if len(token) > 1 and token in doc["content"])
                if score:
                    scored.append((score, doc))
            scored.sort(key=lambda item: item[0], reverse=True)
            refused = not scored
            if refused:
                answer = "当前受控知识库未检索到可靠依据，无法确认。请改问当前装配工单相关问题，或联系工艺负责人。"
                citations = []
            else:
                selected = [d for _, d in scored[:2]]
                answer = "根据受控资料：" + "；".join(d["content"] for d in selected) + "。AI结果仅作辅助，以正式工艺文件为准。"
                citations = [{"id": d["id"], "title": d["title"], "version": d["version"], "section": d["section"]} for d in selected]
        return {"answer": answer, "citations": citations, "refused": refused, "model": "本地演示降级模型", "knowledge_version": "KB-ASSY-2026.08", "usage": {}}

    def ask(self, task_id, question, step_no, actor, knowledge_service=None):
        if not question.strip():
            raise ValueError("请输入问题")
        started = time.perf_counter()
        provider, degraded = "local-fallback", False
        with self.connect() as c:
            task = c.execute("SELECT product,model,process_version FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            raise ValueError("工单不存在")
        context = (
            "你是生产现场AR作业助手。只根据已授权知识库回答，必须保留来源引用；"
            "资料不足时明确说明并建议转远程专家。AI结果仅作辅助，以正式工艺文件为准。"
            f"\n当前工单：{task_id}；产品：{task['product']} / {task['model']}；工艺版本：{task['process_version']}；当前主要工序：{step_no}。"
        )
        try:
            if knowledge_service is None:
                raise RuntimeError("企业知识服务适配器未启用")
            result = knowledge_service.ask(question, context)
            refusal_markers = ("知识库中未找到", "未检索到", "无法根据知识库", "资料不足")
            result.update({"refused": any(marker in result["answer"] for marker in refusal_markers), "knowledge_version": "企业受控知识库"})
            provider = "knowledge-live"
        except RuntimeError as error:
            result = self.ask_local(question, step_no)
            result["degrade_reason"] = str(error)
            degraded = True
        latency = max(1, round((time.perf_counter() - started) * 1000))
        interaction_id = uid("AI")
        result.update({"id": interaction_id, "latency_ms": latency, "provider": provider, "degraded": degraded})
        with self.connect() as c:
            c.execute("INSERT INTO ai_interactions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                interaction_id, task_id, step_no, question, result["answer"], json.dumps(result["citations"], ensure_ascii=False),
                int(result["refused"]), result["model"], result["knowledge_version"], latency, utc_now(), actor,
            ))
            title = "智能问答已降级" if degraded else "企业知识问答已返回"
            self._event(c, task_id, "AI_INTERACTION", title, f"问题：{question}", actor, "knowledge-service" if not degraded else "local-knowledge-fallback")
        return result

    def maintenance_cases(self, query=""):
        with self.connect() as c:
            if query:
                token = f"%{query}%"
                rows = c.execute(
                    "SELECT * FROM maintenance_cases WHERE equipment LIKE ? OR component LIKE ? OR symptom LIKE ? OR cause LIKE ? OR resolution LIKE ? ORDER BY created_at DESC,id",
                    (token, token, token, token, token),
                )
            else:
                rows = c.execute("SELECT * FROM maintenance_cases ORDER BY created_at DESC,id")
            return self.rows(rows)

    def add_maintenance_case(self, data, actor):
        task_id = data.get("task_id") or DEFAULT_TASK_ID
        with self.connect() as c:
            task = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                raise ValueError("工单不存在")
            incident = c.execute("SELECT * FROM incidents WHERE task_id=? ORDER BY created_at DESC LIMIT 1", (task_id,)).fetchone()
            case_id = uid("MC")
            symptom = data.get("symptom") or (incident["type"] if incident else "现场装配异常")
            resolution = data.get("resolution") or (incident["resolution"] if incident and incident["resolution"] else "依据工艺资料复检并记录处置结果")
            c.execute("INSERT INTO maintenance_cases VALUES(?,?,?,?,?,?,?,?,?,?)", (
                case_id, "液压支架", data.get("component", "装配系统"), symptom,
                data.get("cause", "由现场记录和专家意见综合分析"), resolution,
                data.get("parts", "按处置方案确定"), task_id, "待审核", utc_now(),
            ))
            self._event(c, task_id, "MAINTENANCE_CASE_CREATED", "维修案例已形成待审核记录", f"案例{case_id}已关联当前工单和专家处置结果。", actor, "maintenance-knowledge-service")
        return {"ok": True, "id": case_id}

    def add_knowledge(self, data):
        with self.connect() as c:
            doc_id = uid("KB")
            now = utc_now()
            title = str(data.get("title", "")).strip()
            content = str(data.get("content", "")).strip()
            if not title or not content:
                raise ValueError("资料名称和内容不能为空")
            c.execute("INSERT INTO knowledge VALUES(?,?,?,?,?,?,?)", (doc_id, title, data.get("version", "V1.0"), data.get("section", "全文"), content, "published", now))
            c.execute("INSERT INTO knowledge_assets VALUES(?,?,?,?,?,?,?,?,?,?)", (
                uid("KA"), doc_id, data.get("category", "未分类"), data.get("tags", "待标注"),
                data.get("file_name") or f"{title}.txt", data.get("file_type", "TXT"),
                data.get("file_size", f"{len(content.encode('utf-8'))} B"), data.get("source", "管理端上传"), "待索引", now,
            ))
        return {"ok": True, "id": doc_id, "chunks": 1}

    def knowledge_document(self, doc_id):
        with self.connect() as c:
            row = c.execute("""SELECT k.id,k.title,k.version,k.section,k.content,k.status,k.created_at,
                a.category,a.tags,a.file_name,a.file_type,a.file_size,a.source,a.status AS index_status
                FROM knowledge k LEFT JOIN knowledge_assets a ON a.knowledge_id=k.id WHERE k.id=?""", (doc_id,)).fetchone()
            if not row:
                raise ValueError("知识资料不存在")
            return dict(row)

    def create_process(self, data, actor):
        version = str(data.get("version", "")).strip()
        base_version = str(data.get("base_version", "V3.0")).strip()
        note = str(data.get("change_note", "")).strip()
        if not version or not note:
            raise ValueError("版本号和变更说明不能为空")
        with self.lock, self.connect() as c:
            if c.execute("SELECT 1 FROM process_versions WHERE version=?", (version,)).fetchone():
                raise ValueError("工艺版本已存在")
            base = c.execute("SELECT 1 FROM process_versions WHERE version=?", (base_version,)).fetchone()
            if not base:
                raise ValueError("基础工艺版本不存在")
            now = utc_now()
            checksum = f"SHA-{uuid.uuid4().hex[:4].upper()}"
            c.execute("INSERT INTO process_versions VALUES(?,?,?,?,?,?,?)", (uid("PROC"), version, "draft", checksum, note, now, None))
            steps = c.execute("SELECT step_no,title,instruction,parameter,safety,media FROM process_steps WHERE version=? ORDER BY step_no", (base_version,)).fetchall()
            c.executemany("INSERT INTO process_steps VALUES(?,?,?,?,?,?,?)", [(version, *tuple(row)) for row in steps])
        return {"ok": True, "version": version}

    def save_process_step(self, version, data, actor):
        step_no = int(data.get("step_no", 0))
        if step_no < 1:
            raise ValueError("工序编号无效")
        with self.lock, self.connect() as c:
            process = c.execute("SELECT status FROM process_versions WHERE version=?", (version,)).fetchone()
            if not process:
                raise ValueError("工艺版本不存在")
            if process["status"] != "draft":
                raise ValueError("只有草稿工艺可以编辑")
            values = [str(data.get(name, "")).strip() for name in ("title", "instruction", "parameter", "safety")]
            if not all(values):
                raise ValueError("工序内容不能为空")
            c.execute("""INSERT INTO process_steps(version,step_no,title,instruction,parameter,safety,media) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(version,step_no) DO UPDATE SET title=excluded.title,instruction=excluded.instruction,
                parameter=excluded.parameter,safety=excluded.safety,media=excluded.media""",
                (version, step_no, *values, data.get("media", "text,image,3d")))
        return {"ok": True, "version": version, "step_no": step_no}

    def process_action(self, version, action, actor):
        with self.connect() as c:
            target = c.execute("SELECT * FROM process_versions WHERE version=?", (version,)).fetchone()
            if not target:
                raise ValueError("工艺版本不存在")
            if action == "publish":
                c.execute("UPDATE process_versions SET status='archived' WHERE status='published'")
                c.execute("UPDATE process_versions SET status='published',published_at=? WHERE version=?", (utc_now(), version))
            elif action == "rollback":
                c.execute("UPDATE process_versions SET status='archived' WHERE status='published'")
                c.execute("UPDATE process_versions SET status='published',published_at=? WHERE version=?", (utc_now(), version))
            else:
                raise ValueError("不支持的版本操作")
            affected = self.rows(c.execute("SELECT id FROM tasks WHERE status IN ('draft','pending')"))
            for task in affected:
                c.execute("UPDATE tasks SET process_version=?,updated_at=? WHERE id=?", (version, utc_now(), task["id"]))
                self._event(c, task["id"], "PROCESS_VERSION_CHANGED", f"工艺版本已{ '发布' if action == 'publish' else '回滚' }", f"待领取工单工艺版本切换为{version}；执行中工单保留原快照。", actor, "process-service")
        return {"ok": True, "version": version}

    def trace(self, filters):
        clauses, values = ["1=1"], []
        mapping = {"task_id": "e.task_id", "product": "t.product", "station": "t.station", "actor": "e.actor", "incident_type": "i.type"}
        for key, column in mapping.items():
            if filters.get(key):
                clauses.append(f"{column} LIKE ?")
                values.append(f"%{filters[key]}%")
        sql = f"""SELECT e.*,t.product,t.model,t.station,t.operator,t.device_id,t.process_version,i.type AS incident_type
                  FROM events e JOIN tasks t ON t.id=e.task_id LEFT JOIN incidents i ON i.task_id=e.task_id
                  WHERE {' AND '.join(clauses)} ORDER BY e.created_at,e.id"""
        with self.connect() as c:
            return self.rows(c.execute(sql, values))

    def backup(self):
        target = self.backup_dir / f"ar_demo_{datetime.now():%Y%m%d_%H%M%S_%f}.db"
        with self.lock, self.connect() as source:
            dest = sqlite3.connect(target)
            try:
                source.backup(dest)
            finally:
                dest.close()
        return {"ok": True, "file": target.name, "size": target.stat().st_size}

    def restore_latest(self):
        backups = sorted(self.backup_dir.glob("ar_demo_*.db"))
        if not backups:
            raise ValueError("暂无可恢复备份")
        with self.lock:
            source = sqlite3.connect(backups[-1])
            try:
                with self.connect() as dest:
                    source.backup(dest)
            finally:
                source.close()
        return {"ok": True, "file": backups[-1].name}

    def reset(self):
        with self.lock:
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(str(self.path) + suffix)
                if candidate.exists():
                    candidate.unlink()
            self.init()
        return {"ok": True}


OPENAPI = {
    "openapi": "3.0.3",
    "info": {"title": "AR生产现场作业支撑平台API", "version": "3.3.0"},
    "servers": [{"url": "/api"}],
    "paths": {
        "/health": {"get": {"summary": "运行健康检查"}},
        "/bootstrap": {"get": {"summary": "加载演示业务快照"}},
        "/integrations": {"get": {"summary": "查看知识、远程协作和数字孪生服务状态"}},
        "/tasks": {"post": {"summary": "管理端创建装配工单"}},
        "/tasks/{taskId}/dispatch": {"post": {"summary": "管理端下发工单"}},
        "/tasks/{taskId}/claim": {"post": {"summary": "AR端扫码领取工单"}},
        "/tasks/{taskId}/observations": {"post": {"summary": "眼镜端回传现场采集数据"}},
        "/tasks/{taskId}/view-state": {"get": {"summary": "读取AR端三维视角"}, "post": {"summary": "同步AR端三维视角"}},
        "/tasks/{taskId}/parts/identify": {"post": {"summary": "识别零部件编码并校验型号、工单、工序和BOM"}},
        "/tasks/{taskId}/steps/{stepNo}/confirm": {"post": {"summary": "确认当前主要工序", "parameters": [{"name": "Idempotency-Key", "in": "header", "required": True}]}},
        "/tasks/{taskId}/action": {"post": {"summary": "暂停、恢复或回退工单"}},
        "/incidents": {"post": {"summary": "提交现场异常"}},
        "/ai/ask": {"post": {"summary": "基于受控知识问答"}},
        "/collaboration": {"post": {"summary": "发起专家协同"}},
        "/collaboration/{sessionId}/annotations": {"post": {"summary": "同步专家标注"}},
        "/collaboration/{sessionId}/end": {"post": {"summary": "结束并归档协同"}},
        "/processes": {"post": {"summary": "新建工艺草稿版本"}},
        "/processes/{version}/steps": {"post": {"summary": "新增或编辑工艺步骤"}},
        "/processes/{version}/{action}": {"post": {"summary": "发布或回滚工艺版本"}},
        "/knowledge": {"post": {"summary": "导入知识条目"}},
        "/knowledge/documents/{id}": {"get": {"summary": "查看知识资料详情"}},
        "/knowledge/documents/{id}/download": {"get": {"summary": "下载知识资料"}},
        "/maintenance/cases": {"get": {"summary": "查询维修案例"}, "post": {"summary": "形成维修案例待审核记录"}},
        "/trace": {"get": {"summary": "多条件追溯查询"}},
        "/trace/export": {"get": {"summary": "导出JSON或CSV"}},
        "/admin/backup": {"post": {"summary": "创建SQLite一致性备份"}},
        "/admin/restore": {"post": {"summary": "恢复最近备份"}},
    },
}


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "ARDemo/3.3"

    @property
    def db(self):
        return self.server.db

    def log_message(self, fmt, *args):
        if getattr(self.server, "quiet", False):
            return
        super().log_message(fmt, *args)

    def role(self):
        role = self.headers.get("X-Demo-Role", "operator")
        return role if role in ROLES else "operator"

    def actor(self):
        encoded = self.headers.get("X-Demo-Actor")
        return unquote(encoded) if encoded else {"operator": "张工/A017", "engineer": "王工/工艺", "expert": "李工/专家", "admin": "系统管理员"}[self.role()]

    def require(self, domain):
        if self.role() not in WRITE_ROLES[domain]:
            raise PermissionError(f"当前角色无{domain}操作权限")

    def json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2_000_000:
            raise ValueError("请求体超过2MB限制")
        return json.loads(self.rfile.read(length) or b"{}")

    def send_json(self, payload, status=200):
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def send_bytes(self, raw, content_type, filename=None, cache_control=None, compress=False):
        accepts_gzip = "gzip" in self.headers.get("Accept-Encoding", "")
        encoded = gzip.compress(raw, compresslevel=6) if compress and accepts_gzip and len(raw) > 512 else raw
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        if encoded is not raw:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            parts = [p for p in parsed.path.split("/") if p]
            if parsed.path == "/openapi.json":
                return self.send_json(OPENAPI)
            if parsed.path == "/api/health":
                return self.send_json({"status": "ok", "database": "sqlite", "time": utc_now(), "version": "3.3.0"})
            if parsed.path == "/api/bootstrap":
                query = parse_qs(parsed.query)
                payload = self.db.bootstrap(query.get("task_id", [DEFAULT_TASK_ID])[0])
                payload["integrations"] = self.server.integrations.status()
                return self.send_json(payload)
            if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "view-state":
                return self.send_json(self.db.view_state(parts[2]))
            if parsed.path == "/api/integrations":
                return self.send_json(self.server.integrations.status())
            if parsed.path == "/api/maintenance/cases":
                query = parse_qs(parsed.query).get("q", [""])[0]
                return self.send_json({"items": self.db.maintenance_cases(query)})
            if len(parts) == 4 and parts[:3] == ["api", "knowledge", "documents"]:
                return self.send_json(self.db.knowledge_document(parts[3]))
            if len(parts) == 5 and parts[:3] == ["api", "knowledge", "documents"] and parts[4] == "download":
                item = self.db.knowledge_document(parts[3])
                raw = (f"{item['title']}\n版本：{item['version']}\n分类：{item.get('category') or '未分类'}\n标签：{item.get('tags') or ''}\n适用范围：{item['section']}\n\n{item['content']}\n").encode("utf-8")
                return self.send_bytes(b"\xef\xbb\xbf" + raw, "text/plain; charset=utf-8", "knowledge-document.txt")
            if parsed.path == "/api/trace":
                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                return self.send_json({"items": self.db.trace(query)})
            if parsed.path == "/api/trace/export":
                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                items = self.db.trace(query)
                if query.get("format") == "csv":
                    output = io.StringIO()
                    fields = list(items[0].keys()) if items else ["id", "task_id", "type", "title", "created_at"]
                    writer = csv.DictWriter(output, fieldnames=fields)
                    writer.writeheader(); writer.writerows(items)
                    return self.send_bytes(b"\xef\xbb\xbf" + output.getvalue().encode(), "text/csv; charset=utf-8", "ar-work-order-trace.csv")
                return self.send_bytes(json.dumps(items, ensure_ascii=False, indent=2).encode(), "application/json", "ar-work-order-trace.json")
            if parsed.path == "/api/audits":
                self.require("admin")
                with self.db.connect() as c:
                    return self.send_json({"items": self.db.rows(c.execute("SELECT * FROM audits ORDER BY created_at DESC LIMIT 100"))})
            return self.serve_static(parsed.path)
        except PermissionError as e:
            self.db.audit(self.role(), self.actor(), "GET", parsed.path, "denied", str(e)); self.send_json({"error": str(e), "code": "FORBIDDEN"}, 403)
        except Exception as e:
            self.send_json({"error": str(e), "code": "SERVER_ERROR"}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            data = self.json_body()
            parts = [p for p in path.split("/") if p]
            audit_request = True
            if path == "/api/tasks":
                self.require("dispatch"); result = self.db.create_task(data, self.actor())
            elif path == "/api/processes":
                self.require("process"); result = self.db.create_process(data, self.actor())
            elif len(parts) == 6 and parts[:2] == ["api", "tasks"] and parts[3] == "steps" and parts[5] == "confirm":
                self.require("step")
                result = self.db.confirm_step(parts[2], int(parts[4]), self.actor(), self.headers.get("Idempotency-Key"))
            elif len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "dispatch":
                self.require("dispatch"); result = self.db.dispatch_task(parts[2], data, self.actor())
            elif len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "claim":
                self.require("claim"); result = self.db.claim_task(parts[2], data, self.actor())
            elif len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "observations":
                self.require("capture"); result = self.db.add_observation(parts[2], data, self.actor())
            elif len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "view-state":
                self.require("capture"); result = self.db.save_view_state(parts[2], data); audit_request = False
            elif len(parts) == 5 and parts[:2] == ["api", "tasks"] and parts[3:] == ["parts", "identify"]:
                self.require("capture"); result = self.db.identify_part(parts[2], data, self.actor())
            elif len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "action":
                self.require("step"); result = self.db.task_action(parts[2], data.get("action"), self.actor())
            elif path == "/api/incidents":
                self.require("incident"); result = self.db.create_incident(data.get("task_id", DEFAULT_TASK_ID), data, self.actor(), self.headers.get("Idempotency-Key"))
            elif path == "/api/ai/ask":
                result = self.db.ask(data.get("task_id", DEFAULT_TASK_ID), data.get("question", ""), int(data.get("step_no", 3)), self.actor(), self.server.integrations.knowledge)
            elif path == "/api/collaboration":
                self.require("expert"); result = self.db.start_collaboration(data.get("task_id", DEFAULT_TASK_ID), data.get("incident_id"), self.actor())
            elif len(parts) == 4 and parts[:2] == ["api", "collaboration"] and parts[3] == "annotations":
                self.require("expert"); result = self.db.add_annotation(parts[2], data, self.actor())
            elif len(parts) == 4 and parts[:2] == ["api", "collaboration"] and parts[3] == "end":
                self.require("expert"); result = self.db.end_collaboration(parts[2], data.get("resolution", "核对胶管标识、阀组接口和受控液压原理图后，按确认结果重新连接并复检。"), self.actor())
            elif path == "/api/knowledge":
                self.require("knowledge"); result = self.db.add_knowledge(data)
            elif path == "/api/knowledge/documents":
                self.require("knowledge"); result = self.db.add_knowledge(data)
            elif path == "/api/maintenance/cases":
                self.require("knowledge"); result = self.db.add_maintenance_case(data, self.actor())
            elif len(parts) == 4 and parts[:2] == ["api", "processes"] and parts[3] == "steps":
                self.require("process"); result = self.db.save_process_step(parts[2], data, self.actor())
            elif len(parts) == 4 and parts[:2] == ["api", "processes"]:
                self.require("process"); result = self.db.process_action(parts[2], parts[3], self.actor())
            elif len(parts) == 4 and parts[:2] == ["api", "devices"] and parts[3] == "heartbeat":
                with self.db.connect() as c:
                    c.execute("UPDATE devices SET status='online',battery=?,last_seen=? WHERE id=?", (int(data.get("battery", 82)), utc_now(), parts[2]))
                result = {"ok": True}
            elif path == "/api/admin/backup":
                self.require("admin"); result = self.db.backup()
            elif path == "/api/admin/restore":
                self.require("admin"); result = self.db.restore_latest()
            elif path == "/api/admin/reset":
                self.require("admin"); result = self.db.reset()
            elif path == "/api/demo/reset":
                result = self.db.reset() | {"task_id": DEFAULT_TASK_ID}
            else:
                return self.send_json({"error": "API不存在", "code": "NOT_FOUND"}, 404)
            if audit_request:
                self.db.audit(self.role(), self.actor(), "POST", path, "success")
            self.send_json(result)
        except PermissionError as e:
            self.db.audit(self.role(), self.actor(), "POST", path, "denied", str(e)); self.send_json({"error": str(e), "code": "FORBIDDEN"}, 403)
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            self.db.audit(self.role(), self.actor(), "POST", path, "failed", str(e)); self.send_json({"error": str(e), "code": "VALIDATION_ERROR"}, 400)
        except Exception as e:
            self.db.audit(self.role(), self.actor(), "POST", path, "failed", str(e)); self.send_json({"error": str(e), "code": "SERVER_ERROR"}, 500)

    def serve_static(self, path):
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (ROOT / relative).resolve()
        if ROOT not in target.parents and target != ROOT:
            return self.send_json({"error": "非法路径"}, 403)
        if not target.is_file() or target.name == "server.py" or "data" in target.parts:
            target = ROOT / "index.html" if "." not in Path(relative).name else target
        if not target.is_file():
            return self.send_json({"error": "资源不存在"}, 404)
        mime = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "application/javascript; charset=utf-8", ".png": "image/png", ".mp4": "video/mp4", ".json": "application/json; charset=utf-8"}.get(target.suffix, "application/octet-stream")
        immutable = target.suffix in {".js", ".css", ".png", ".mp4"}
        cache_control = "public, max-age=604800, immutable" if immutable else "no-cache"
        self.send_bytes(target.read_bytes(), mime, cache_control=cache_control, compress=target.suffix in {".html", ".css", ".js", ".json"})


def make_server(host="127.0.0.1", port=8088, db_path=DB_PATH, quiet=False, enable_external=True):
    server = ThreadingHTTPServer((host, port), DemoHandler)
    server.db = DemoDB(Path(db_path))
    server.integrations = IntegrationRegistry(enabled=enable_external)
    server.quiet = quiet
    return server


def main():
    parser = argparse.ArgumentParser(description="AR技术验证Demo服务")
    parser.add_argument("--host", default=os.environ.get("AR_DEMO_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AR_DEMO_PORT", "8088")))
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()
    server = make_server(args.host, args.port, Path(args.db))
    print(f"AR Demo running at http://{args.host}:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
