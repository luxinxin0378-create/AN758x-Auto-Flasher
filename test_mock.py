#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端 mock 测试：模拟 stock2ubi 与 U-Boot web 的真实 API 形态，
验证 Stock2ubiClient / UbootClient / 分区表解析 / multipart / 任务流转。"""

import json
import sys
import threading
import time
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))

import flasher

PARTS = [
    {"index": 0, "name": "boot", "size": 0x00280000},
    {"index": 13, "name": "factory", "size": 0x00300000},
]


def stock2ubi_page():
    rows = []
    for p in PARTS:
        rows.append(
            '<tr><td>mtd%d</td><td>%s</td><td>2.5 MiB</td><td>128 KiB</td>'
            '<td>0x%08x</td><td><a data-t="download" href="/backup/%d">Download</a></td></tr>'
            % (p["index"], p["name"], p["index"] * 0x400000, p["index"]))
    return ("<!doctype html><html><body><table><thead><tr><th>Device</th></tr></thead>"
            "<tbody>" + "".join(rows) + "</tbody></table></body></html>")


class MockStock(BaseHTTPRequestHandler):
    flashed = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/":
            body = stock2ubi_page().encode()
        elif self.path.startswith("/backup/"):
            idx = int(self.path.split("/")[-1])
            part = next(p for p in PARTS if p["index"] == idx)
            body = bytes(range(256)) * (part["size"] // 256)
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "text/html" if self.path == "/" else "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        assert self.path == "/api/flash"
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        assert b'name="bl2"' in body and b'name="fip"' in body, "multipart 字段缺失"
        MockStock.flashed = {"len": len(body)}
        out = b'{"message":"Boot chain written, rebooting"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class MockUboot(BaseHTTPRequestHandler):
    state = {"busy": False, "state": "done", "code": 0, "phase": "", "upload_id": None}
    uploads = {}

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        out = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):
        if self.path == "/api/device":
            self._json({"model": "FiberHome HG5585F CU", "ram_bytes": 512 * 1024 * 1024,
                        "flash_bytes": 256 * 1024 * 1024, "ubi": True, "rebuild": True,
                        "board_data": ["factory"]})
        elif self.path == "/api/storage":
            self._json({"mtd": [{"name": "bl2", "size": 131072}],
                        "ubi": [{"name": "factory", "used_bytes": 1048576}]})
        elif self.path == "/api/task":
            MockUboot.state["busy"] = False  # 模拟任务很快完成
            self._json(MockUboot.state)
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if self.path == "/api/upload":
            uid = len(MockUboot.uploads) + 1
            MockUboot.uploads[uid] = body
            self._json({"upload_id": uid, "size": len(body)}, 201)
        elif self.path == "/api/task":
            req = json.loads(body.decode())
            op = req["operation"]
            assert op in ("flash", "flash-bl2", "flash-uboot", "flash-board-data",
                          "ubi-rebuild", "flash-all", "boot-production", "reset",
                          "backup", "write", "command", "env-set", "boot-upload",
                          "nand-scrub", "exit"), "未知 operation: " + op
            if op == "flash-board-data":
                uid = req.get("upload_id")
                data = MockUboot.uploads[uid]
                assert len(data) == 0x100000, "板级数据必须正好 1MiB，收到 %d" % len(data)
            if op == "flash":
                uid = req.get("upload_id")
                assert len(MockUboot.uploads[uid]) > 1000, "固件太小"
            MockUboot.state = {"busy": True, "state": "running", "code": 0, "phase": op}
            self._json({"task": op, "accepted": True}, 202)
        else:
            self.send_response(404); self.end_headers()


def run():
    s1 = ThreadingHTTPServer(("127.0.0.1", 13433), MockStock)
    s2 = ThreadingHTTPServer(("127.0.0.1", 13400), MockUboot)
    threading.Thread(target=s1.serve_forever, daemon=True).start()
    threading.Thread(target=s2.serve_forever, daemon=True).start()
    time.sleep(0.5)

    logs = []
    # --- stock2ubi 客户端 ---
    c = flasher.Stock2ubiClient("127.0.0.1", 13433, logger=logs.append)
    assert c.alive()
    parts = c.partitions()
    assert len(parts) == 2, parts
    assert parts[0]["name"] == "boot" and parts[1]["name"] == "factory"
    assert parts[1]["index"] == 13 and parts[1]["offset"] == 13 * 0x400000
    print("分区表解析 OK:", parts)

    import tempfile
    tmp = Path(tempfile.mkdtemp())
    info = c.download_partition(parts[1], tmp / "mtd13-factory.bin")
    assert info["size"] == PARTS[1]["size"]
    assert info["sha256"] == hashlib.sha256(bytes(range(256)) * (PARTS[1]["size"] // 256)).hexdigest()
    print("分区下载+SHA256 OK:", info["size"], "bytes")

    msg = c.flash_boot(b"\x00" * 128, b"\x11" * 512)
    assert "rebooting" in msg
    print("multipart 刷引导 OK:", msg)

    # --- U-Boot 客户端 ---
    u = flasher.UbootClient("127.0.0.1", 13400, logger=logs.append)
    # 覆盖 base 端口（mock 在 13400）
    
    dev = u.alive()
    assert dev["model"] == "FiberHome HG5585F CU"
    assert u.board_data_targets() == ["factory"]
    print("设备探测 OK:", dev["model"])

    up = u.upload(b"\xff" * 0x100000)
    uid = up["upload_id"]
    print("上传 OK: upload_id=%d size=%d" % (uid, up["size"]))

    r = u.run_task("flash-board-data", upload_id=uid, target="factory", timeout=10)
    print("板级数据任务 OK:", r)

    up = u.upload(b"FIT-SYSUPGRADE" * 10000)
    r = u.run_task("flash", upload_id=up["upload_id"], timeout=10)
    print("系统刷写任务 OK:", r)

    r = u.run_task("boot-production", timeout=10)
    print("启动任务 OK")

    print("\n=== Mock 端到端测试全部通过 ===")
    s1.shutdown(); s2.shutdown()


if __name__ == "__main__":
    run()
