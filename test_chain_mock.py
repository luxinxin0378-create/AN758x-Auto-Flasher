#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""XR1710G chainloader 路线离线端到端测试。

1. mock 一个 XR1710G HTTP 恢复页（接口与 naoki66/XR1710G-http-uboot 的
   mock_server.py 一致：/about /status /upload/firmware?layout= /upload/uboot /reboot）
2. 用真实下载的 sysupgrade.itb 跑 ChainSession 全流程（校验→等恢复页→上传→进度→重启）
3. MiniTFTPServer：起服务 + 极简 TFTP 客户端回读，验证传输完整性
"""
import json
import socket
import struct
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")

import chainroute  # noqa: E402

FW = HERE / "firmware" / ("immortalwrt-naoki66-20261029-8f57dd40-airoha-an7581-"
                          "gemtek_xr1710g-squashfs-sysupgrade.itb")
MOCK_PORT = 18999
received = {"firmware": b"", "layout": None, "rebooted": False, "uboot": b""}


class MockRecovery(BaseHTTPRequestHandler):
    state = {"phase": 0, "done": 0, "total": 0}

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = (json.dumps(obj) + "\n").encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/about":
            self._json({"u_boot": "U-Boot XR1710G-mock", "recovery_version": "mock",
                        "credit": "test", "detected_layout": "2.0"})
        elif p == "/status":
            st = MockRecovery.state
            self._json({"in_progress": 1 if 0 < st["phase"] < 3 else 0,
                        "done": st["done"], "total": st["total"],
                        "erase_done": st["done"] // 2, "erase_total": st["total"] // 2,
                        "write_done": st["done"] // 2, "write_total": st["total"] // 2,
                        "ok": 1 if st["phase"] == 3 else 0,
                        "error": 0, "phase": st["phase"]})
        else:
            self.send_error(404)

    def do_POST(self):
        p = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        if p == "/reboot":
            received["rebooted"] = True
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"OK")
            return
        if p.startswith("/upload/firmware"):
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            layout = [kv.split("=")[1] for kv in q.split("&") if kv.startswith("layout=")]
            received["layout"] = layout[0] if layout else None
            body = self.rfile.read(length)
            received["firmware"] = body
            # 模拟擦写进度线程
            total = max(len(body), 1 << 20)

            def worker():
                MockRecovery.state.update(phase=1, done=0, total=total * 2)
                for i in range(1, 6):
                    time.sleep(0.1)
                    MockRecovery.state["done"] = total * i // 5
                MockRecovery.state.update(phase=2, done=total)
                for i in range(6, 11):
                    time.sleep(0.1)
                    MockRecovery.state["done"] = total * i // 5
                MockRecovery.state.update(phase=3, done=total * 2)

            threading.Thread(target=worker, daemon=True).start()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_error(404)


def tftp_get(server_port, filename):
    """极简 TFTP 客户端：RRQ → 收 DATA → ACK，返回完整文件。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(5)
    s.sendto(struct.pack(">H", 1) + filename.encode() + b"\x00octet\x00",
             ("127.0.0.1", server_port))
    out = b""
    block = 1
    while True:
        pkt, _ = s.recvfrom(65535)
        op, blk = struct.unpack(">HH", pkt[:4])
        assert op == 3, "期望 DATA，得到 op=%d" % op
        assert blk == block, "块序号错位 %d != %d" % (blk, block)
        out += pkt[4:]
        s.sendto(struct.pack(">HH", 4, blk), ("127.0.0.1", server_port))
        if len(pkt) < 516:
            break
        block += 1
    s.close()
    return out


def main():
    assert FW.is_file(), "缺少测试固件（先跑 chainroute.firmware_fetch）：%s" % FW

    # --- A. FIT 布局解析（真实固件） ---
    info = chainroute.fit_ubi_info(FW)
    assert info["layout"] == "2.0", info
    print("[A] FIT 布局解析 OK:", info["desc"])

    # --- B. TFTP 服务器 ---
    small = HERE / "firmware" / "tftp_test.bin"
    payload = b"\xA5" * (512 * 9 + 100)   # 9 个整块 + 尾块，覆盖分块逻辑
    small.write_bytes(payload)
    srv = chainroute.MiniTFTPServer(small, 9069, logger=lambda m: None)
    srv.start()
    time.sleep(0.3)
    got = tftp_get(9069, "tftp_test.bin")
    assert got == payload, "TFTP 回读不一致（%d/%d）" % (len(got), len(payload))
    for _ in range(50):            # 服务器异步消化最后一个 ACK
        if srv.served == 1:
            break
        time.sleep(0.1)
    assert srv.served == 1, "served=%d" % srv.served
    srv.shutdown()
    small.unlink()
    print("[B] TFTP 服务器 OK：9+ 块传输与回读一致")

    # --- C. ChainSession 全流程（mock 恢复页 + 真实固件上传） ---
    httpd = ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), MockRecovery)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.3)

    import flasher
    flasher.chainroute.LOGF = lambda m: None
    sess = flasher.ChainSession({
        "device": "xr1710g", "firmware": str(FW), "ip": "127.0.0.1:%d" % MOCK_PORT})
    sess.confirm_gate_timeout = None
    # 自动放行危险确认
    orig_ask = sess._ask
    sess._ask = lambda sid, msg: orig_ask(sid, msg) if (
        threading.Thread(target=lambda: (time.sleep(0.2), sess.confirm(True))).start()
        or True) else None
    sess.start()
    sess.join(timeout=120)
    assert sess.finished, "会话未结束"
    assert not sess.error, "会话报错：%s\n%s" % (sess.error, "\n".join(sess.logs[-8:]))
    assert received["layout"] == "2.0", "mock 收到的布局：%r" % received["layout"]
    assert len(received["firmware"]) == FW.stat().st_size, "上传不完整"
    assert received["rebooted"], "没有触发 reboot"
    states = [s["state"] for s in sess.state()["steps"]]
    assert all(s == "done" for s in states), states
    httpd.shutdown()
    print("[C] ChainSession 全流程 OK：布局 2.0、上传 %d B、进度轮询、reboot"
          % len(received["firmware"]))

    print("\n=== XR1710G chainloader 路线全部测试通过 ===")


if __name__ == "__main__":
    main()
