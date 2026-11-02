#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AN758x 多机型免拆刷机 / 备份 / 还原工具。

流程（烽火家族示例）：
  1. telnet 登录光猫（密码按机型规则或用户填写）
  2. 本机起 HTTP 服务，光猫 wget an758x-stock2ubi 并运行（:3333）
  3. 通过 stock2ubi 网页 API 逐分区备份到 backups/（含 SHA256 清单）
  4. 烽火机型：把 3M factory 分区转换为 1M 板级数据镜像
  5. POST /api/flash 写入 BL2 + U-Boot，设备自动重启
  6. 轮询 U-Boot 恢复页（192.168.0.1）就绪，校验机型
  7. ubi-rebuild → flash-board-data → flash-uboot → flash(系统) → boot-production

仅依赖 Python 标准库；烽火自动开 telnet 需要 `pip install fh_tool-cli`（可选）。

用法：python flasher.py [--port 8799]
启动后自动打开浏览器。
"""

import fnmatch
import hashlib
import html as html_mod
import json
import os
import re
import secrets
import shutil
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# PyInstaller 单文件模式下 __file__ 指向临时解压目录；
# files/（stock2ubi 工具）与 backups/（分区备份）必须落在 exe 旁边，随用户保管。
if getattr(sys, "frozen", False):
    HERE = Path(sys.executable).resolve().parent
else:
    HERE = Path(__file__).resolve().parent
FILES_DIR = HERE / "files"
BACKUP_ROOT = HERE / "backups"
FILES_DIR.mkdir(exist_ok=True)
BACKUP_ROOT.mkdir(exist_ok=True)

from devices import DEVICES, BY_KEY, get as get_device, mac_suffix, normalize_mac  # noqa: E402
import fhfactory  # noqa: E402
from ui import UI_HTML  # noqa: E402

STOCK2UBI_NAME = "an758x-stock2ubi"
STOCK2UBI_PATH = FILES_DIR / STOCK2UBI_NAME
FIRMWARE_DIR = HERE / "firmware"
FIRMWARE_DIR.mkdir(exist_ok=True)

# stock2ubi 发行版（v0.1.14，ELF 1014744 字节）随 exe 打包在 bundled/ 下
STOCK2UBI_VERSION = "v0.1.14"
STOCK2UBI_URL = ("https://github.com/pbs05/an758x-stock2ubi/releases/download/"
                 "%s/an758x-stock2ubi" % STOCK2UBI_VERSION)
UBOOT_RELEASES_API = "https://api.github.com/repos/pbs05/uboot-an758x/releases"


# ---------------------------------------------------------------- utilities
def log(*a):
    print(*a, file=sys.stderr, flush=True)


def local_ip_for(target_ip: str) -> str:
    """取本机到 target 的出口 IP（UDP connect 不实际发包）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target_ip, 80))
        return s.getsockname()[0]
    finally:
        s.close()


def http_get(url, timeout=10, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), int(r.headers.get("Content-Length") or 0), r.status


def http_get_json(url, timeout=10):
    data, _, _ = http_get(url, timeout)
    try:
        return json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        return {"_raw": data[:400].decode("utf-8", "replace")}


def http_post(url, body: bytes, content_type="application/octet-stream", timeout=180):
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": content_type})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.status


def http_post_json(url, obj, timeout=30):
    data, status = http_post(url, json.dumps(obj).encode(), "application/json", timeout)
    try:
        return json.loads(data.decode("utf-8", "replace")), status
    except ValueError:
        return {"_raw": data[:400].decode("utf-8", "replace")}, status


def multipart_body(files: dict) -> bytes:
    """files: {field: (filename, bytes)}，生成 multipart/form-data body。"""
    boundary = "----PonFlasher" + secrets.token_hex(8)
    out = bytearray()
    for name, (filename, data) in files.items():
        out += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), "multipart/form-data; boundary=" + boundary


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(256 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def guess_matches(patterns, directory: Path) -> list:
    """在目录里按 glob 模式找文件（不区分大小写）。"""
    found = []
    if not directory or not Path(directory).is_dir():
        return found
    for p in Path(directory).iterdir():
        if not p.is_file():
            continue
        low = p.name.lower()
        for pat in patterns:
            if fnmatch.fnmatch(low, pat.lower()):
                found.append(str(p))
                break
    return sorted(set(found))


# ---------------------------------------------------------------- 内置工具与在线固件
def _extract_bundled_tool():
    """打包版：把内置的 stock2ubi 释放到 files/（仅当缺失时）。"""
    if STOCK2UBI_PATH.exists():
        return
    base = getattr(sys, "_MEIPASS", "")
    cand = Path(base) / "bundled" / STOCK2UBI_NAME if base else None
    if cand and cand.is_file():
        shutil.copyfile(cand, STOCK2UBI_PATH)
        try:
            os.chmod(STOCK2UBI_PATH, 0o755)
        except OSError:
            pass
        log("已从 exe 内置资源释放 stock2ubi（%s）→ %s" % (STOCK2UBI_VERSION, STOCK2UBI_PATH))


_extract_bundled_tool()


# ---------------------------------------------------------------- 内置 AN758x 引导包
def bundled_fw_dir() -> Path:
    """内置引导包目录：打包版在 sys._MEIPASS 下，源码版就在脚本旁 bundled_fw/。"""
    base = getattr(sys, "_MEIPASS", "")
    return (Path(base) / "bundled_fw") if base else (HERE / "bundled_fw")


def bundled_fw_index() -> dict:
    """读取内置引导包清单 INDEX.json（{"tag":..., "files":{name:size}}）。"""
    idx = bundled_fw_dir() / "INDEX.json"
    if not idx.is_file():
        return {}
    try:
        return json.loads(idx.read_text(encoding="utf-8"))
    except Exception:
        return {}


def bundled_boot_files(device_key: str) -> dict:
    """在内置资源里按机型匹配 BL2 / FIP，返回 {"bl2":Path,"fip":Path,"tag":str} 或 {}。"""
    dev = get_device(device_key)
    d = bundled_fw_dir()
    if not d.is_dir():
        return {}
    names = [p.name for p in d.iterdir() if p.is_file()]

    def match(patterns):
        hits = [n for n in names
                if any(fnmatch.fnmatch(n.lower(), p.lower()) for p in patterns)]
        # firstblock 优先于 preloader
        hits.sort(key=lambda n: 0 if "firstblock" in n.lower() else 1)
        return hits

    bl2, fip = match(dev.bl2_patterns), match(dev.fip_patterns)
    if not bl2 or not fip:
        return {}
    return {"bl2": d / bl2[0], "fip": d / fip[0],
            "tag": bundled_fw_index().get("tag", "")}


def uboot_release_assets(limit=3) -> list:
    rels = http_get_json(UBOOT_RELEASES_API, timeout=20)
    if not isinstance(rels, list):
        msg = rels.get("message") if isinstance(rels, dict) else str(rels)[:200]
        raise RuntimeError("GitHub API 返回异常（可能是限流或网络不通）：%s" % msg)
    assets = []
    for r in rels[:limit]:
        for a in r.get("assets") or []:
            a = dict(a)
            a["tag"] = r.get("tag_name", "")
            assets.append(a)
    if not assets:
        raise RuntimeError("uboot-an758x Releases 没有可用资产")
    return assets


# GitHub 直连不通时依次回退的加速镜像（前缀 + 完整 GitHub URL）
GH_MIRRORS = ("", "https://ghproxy.net/", "https://ghfast.top/")


def _download_to(url: str, dest: Path, expected_size: int, tries=2) -> Path:
    """直连失败自动切镜像；每个候选重试 tries 次。"""
    last_err = None
    for prefix in GH_MIRRORS:
        candidate = prefix + url
        for attempt in range(1, tries + 1):
            try:
                with urllib.request.urlopen(candidate, timeout=90) as r, open(dest, "wb") as f:
                    total = 0
                    while True:
                        chunk = r.read(256 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        total += len(chunk)
                if expected_size and total != expected_size:
                    raise RuntimeError("%s 大小不符（%d/%d）" % (dest.name, total, expected_size))
                if prefix:
                    log("经镜像 %s 下载成功" % prefix)
                return dest
            except Exception as e:
                last_err = e
                dest.unlink(missing_ok=True)
                log("下载失败（%s第 %d 次）：%s" % (prefix or "直连", attempt, e))
                if attempt < tries:
                    time.sleep(2 * attempt)
        log("切换下一个下载源…")
    raise RuntimeError("下载 %s 失败（直连与镜像均不可用）：%s" % (dest.name, last_err))


def firmware_fetch(device_key: str, dest_dir=None, online=False) -> dict:
    """按机型取 BL2 + FIP。默认优先用 exe 内置的引导包，online=True 则强制联网拉最新。"""
    dev = get_device(device_key)
    dest = Path(dest_dir) if dest_dir else FIRMWARE_DIR
    dest.mkdir(parents=True, exist_ok=True)

    # ① 内置资源优先（离线可用）
    if not online:
        b = bundled_boot_files(device_key)
        if b:
            out = {"tag": b["tag"], "source": "bundled", "dir": str(dest),
                   "bl2": None, "fip": None, "bl2_size": None, "fip_size": None}
            for cat, src in (("bl2", b["bl2"]), ("fip", b["fip"])):
                target = dest / src.name
                if not target.exists() or target.stat().st_size != src.stat().st_size:
                    shutil.copyfile(src, target)
                out[cat] = str(target)
                out[cat + "_size"] = target.stat().st_size
            log("已使用内置引导包（%s）：%s / %s"
                % (b["tag"] or "内置", b["bl2"].name, b["fip"].name))
            return out

    # ② 联网拉取
    assets = uboot_release_assets()

    def match(patterns):
        hits = [a for a in assets
                if any(fnmatch.fnmatch(a["name"].lower(), p.lower()) for p in patterns)]
        # firstblock 优先于 preloader（两者 0x800 后等价，firstblock 可直接刷）
        hits.sort(key=lambda a: 0 if "firstblock" in a["name"].lower() else 1)
        return hits

    bl2_hits, fip_hits = match(dev.bl2_patterns), match(dev.fip_patterns)
    if not bl2_hits and not fip_hits:
        if not getattr(dev, "bootloader_release", True):
            raise RuntimeError(
                "%s 上游未发布预编译引导包。uboot-an758x 源码里有它的 defconfig"
                "（%s），但 CI 只上传到 Actions artifacts、未进 Releases。\n"
                "解决办法二选一：\n"
                "  1) 打开 https://github.com/pbs05/uboot-an758x/actions ，登录后在"
                "最新的 Build AN758x U-Boot 运行中下载 an758x-%s 产物；\n"
                "  2) 自行编译：git clone && ./scripts/build-an758x.sh %s ，"
                "产物在 output/%s/ 下。\n"
                "拿到后放进固件目录点「扫描匹配」即可。"
                % (dev.name, dev.uboot_defconfig, dev.uboot_target,
                   dev.uboot_target, dev.uboot_target))
        raise RuntimeError("Releases 里没有匹配 %s 的引导文件（命名模式：%s）"
                           % (dev.name, dev.bl2_patterns[:2]))
    result = {"tag": (bl2_hits or fip_hits)[0]["tag"], "bl2": None, "fip": None,
              "bl2_size": None, "fip_size": None, "dir": str(dest),
              "source": "online"}
    for cat, hits in (("bl2", bl2_hits[:1]), ("fip", fip_hits[:1])):
        for a in hits:
            dest_f = dest / a["name"]
            log("下载 %s（%d B）…" % (a["name"], a["size"]))
            _download_to(a["browser_download_url"], dest_f, int(a["size"]))
            result[cat] = str(dest_f)
            result[cat + "_size"] = int(a["size"])
    return result


# ---------------------------------------------------------------- telnet
class MiniTelnet:
    """极简 telnet 客户端：IAC 协商（全部拒绝）+ expect + su + run。"""

    def __init__(self, host, port=23, timeout=15, logger=None):
        self.host, self.port, self.timeout = host, int(port), timeout
        self.logf = logger or (lambda *a: None)
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        self.sock.settimeout(0.4)
        self.buf = b""
        self.root = False

    # -- 底层读，剥离 IAC 并应答 WONT/DONT --
    def _pump(self) -> None:
        try:
            chunk = self.sock.recv(8192)
        except socket.timeout:
            return
        if not chunk:
            raise ConnectionError("telnet 连接被关闭")
        out, i, reply = bytearray(), 0, bytearray()
        while i < len(chunk):
            b = chunk[i]
            if b == 0xFF and i + 1 < len(chunk):
                cmd = chunk[i + 1]
                if cmd in (0xFB, 0xFD):          # WILL / DO → 一律拒绝
                    opt = chunk[i + 2] if i + 2 < len(chunk) else 0
                    reply += bytes((0xFF, 0xFC if cmd == 0xFB else 0xFE, opt))
                    i += 3
                    continue
                if cmd in (0xFC, 0xFE):          # WONT / DONT → 知道了
                    i += 3
                    continue
                if cmd == 0xFA:                   # SB 子协商 → 跳到 IAC SE
                    j = chunk.find(b"\xff\xf0", i)
                    i = len(chunk) if j < 0 else j + 2
                    continue
                i += 2
                continue
            if b not in (0x00,):
                out.append(b)
            i += 1
        if reply:
            try:
                self.sock.sendall(bytes(reply))
            except OSError:
                pass
        self.buf += bytes(out)

    def expect(self, patterns, timeout=None) -> tuple:
        """等任意 pattern 出现；返回 (index, buffer_text)。超时返回 (-1, text)。"""
        deadline = time.time() + (timeout or self.timeout)
        pats = [p if isinstance(p, bytes) else p.encode() for p in patterns]
        while time.time() < deadline:
            for idx, p in enumerate(pats):
                if re.search(p, self.buf, re.IGNORECASE):
                    text = self.buf.decode("utf-8", "replace")
                    self.buf = b""
                    return idx, text
            self._pump()
        text = self.buf.decode("utf-8", "replace")
        self.buf = b""
        return -1, text

    def send(self, line: str) -> None:
        self.sock.sendall((line + "\n").encode())

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    # -- 高层 --
    def login(self, user: str, password: str, tries: tuple = ()) -> None:
        """tries：密码备选列表（含主密码），逐个尝试。"""
        idx, text = self.expect([b"login:", b"username:", b"account:"], 12)
        if idx < 0 and "login" not in text.lower():
            self.logf("未等到 login 提示，直接尝试输入账号（缓冲区：%s…）" % text[:60])
        for pwd in [password] + [t for t in tries if t and t != password]:
            self.send(user)
            idx, _ = self.expect([b"assword:"], 8)
            if idx < 0:
                raise ConnectionError("没有出现密码提示，telnet 登录流程异常")
            self.send(pwd)
            idx, text = self.expect([rb"[#>$] ?$", b"login:", b"incorrect", b"failed"], 10)
            if idx == 0 or (idx < 0 and re.search(r"[#>$]", text)):
                self.logf("telnet 登录成功（用户 %s）" % user)
                return
            self.logf("密码 %r 被拒绝，尝试下一个候选…" % _mask(pwd))
            # 再等一次 login 提示
            self.expect([b"login:", b"username:"], 5)
        raise ConnectionError("所有候选密码均被拒绝；请核对机型/MAC 或在页面手工填写")

    def become_root(self, su_user: str, su_password: str) -> None:
        idx, _ = self.expect([rb"[#$>] ?$"], 5)
        self.send("su - %s" % su_user)
        idx, _ = self.expect([b"assword:", rb"[#$>] ?$"], 8)
        if idx == 0:
            self.send(su_password)
            idx, text = self.expect([rb"# ?$", b"incorrect", b"denied"], 10)
            if idx != 0 and not text.rstrip().endswith(("#", "$", ">")):
                raise ConnectionError("su %s 失败（密码不对？）" % su_user)
        self.root = True
        self.logf("已提权为 %s" % su_user)

    def run(self, cmd: str, timeout: int = 90) -> str:
        marker = "__PFDONE_%s__" % secrets.token_hex(4)
        self.buf = b""
        self.sock.sendall(("%s; echo %s$?\n" % (cmd, marker)).encode())
        deadline = time.time() + timeout
        acc = b""
        while time.time() < deadline:
            self._pump()
            if self.buf:
                acc += self.buf
                self.buf = b""
            m = re.search((marker + rb"(\d+)\r?\n").encode(), acc)
            if m:
                code = int(m.group(1))
                body = acc[: m.start()].decode("utf-8", "replace")
                body = re.sub(r"^%s.*?\r?\n" % re.escape(cmd.split(";")[0][:40]), "", body, count=1)
                if code != 0:
                    raise RuntimeError("命令失败（退出码 %d）：%s" % (code, body.strip()[:300]))
                return body
        raise TimeoutError("命令超时：%s" % cmd[:80])


def _mask(s: str) -> str:
    return (s[:3] + "***") if s else "(空)"


# ---------------------------------------------------------------- stock2ubi
class Stock2ubiClient:
    """an758x-stock2ubi 在光猫上开的网页 API（默认 0.0.0.0:3333）。"""

    ROW_RE = re.compile(
        r"<tr><td>mtd(\d+)</td><td>([^<]*)</td><td>([^<]*)</td><td>([^<]*)</td>"
        r"<td>([^<]*)</td>.*?href=\"/backup/(\d+)\"", re.S)

    def __init__(self, ip, port=3333, logger=None):
        self.base = "http://%s:%d" % (ip, int(port))
        self.logf = logger or (lambda *a: None)

    def alive(self) -> bool:
        try:
            http_get(self.base + "/", timeout=4)
            return True
        except Exception:
            return False

    def partitions(self) -> list:
        data, _, _ = http_get(self.base + "/", timeout=8)
        page = data.decode("utf-8", "replace")
        parts = []
        for m in self.ROW_RE.finditer(page):
            idx, name = int(m.group(1)), html_mod.unescape(m.group(2)).strip()
            offset_txt = m.group(5).strip()
            offset = int(offset_txt, 16) if offset_txt.startswith("0x") else None
            parts.append({"index": idx, "name": name, "offset": offset})
        if not parts:
            raise RuntimeError("无法从 stock2ubi 页面解析分区表；页面可能已更新")
        return parts

    def download_partition(self, part: dict, dest: Path) -> dict:
        url = "%s/backup/%d" % (self.base, part["index"])
        with urllib.request.urlopen(url, timeout=600) as r:
            expected = int(r.headers.get("Content-Length") or 0)
            h = hashlib.sha256()
            total = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    total += len(chunk)
        if expected and total != expected:
            raise RuntimeError("分区 %s 下载不完整（%d/%d 字节）" % (part["name"], total, expected))
        return {"file": str(dest), "size": total, "sha256": h.hexdigest(),
                "index": part["index"], "name": part["name"], "offset": part["offset"]}

    def flash_boot(self, bl2: bytes, fip: bytes) -> str:
        body, ctype = multipart_body({"bl2": ("bl2.bin", bl2), "fip": ("bl31-uboot.fip", fip)})
        data, status = http_post(self.base + "/api/flash", body, ctype, timeout=300)
        msg = data.decode("utf-8", "replace")[:500]
        if status != 200:
            raise RuntimeError("stock2ubi 拒绝刷写（HTTP %d）：%s" % (status, msg))
        return msg


# ---------------------------------------------------------------- uboot
class UbootClient:
    """AN758x U-Boot web 恢复页 API（默认 192.168.0.1）。"""

    def __init__(self, ip="192.168.0.1", port=80, logger=None):
        self.base = "http://%s:%d" % (ip, int(port))
        self.logf = logger or (lambda *a: None)

    def alive(self) -> dict:
        try:
            return http_get_json(self.base + "/api/device", timeout=5)
        except Exception:
            return None

    def storage(self) -> dict:
        return http_get_json(self.base + "/api/storage", timeout=10)

    def upload(self, data: bytes, all_flash=False) -> dict:
        url = self.base + ("/api/upload-all-flash" if all_flash else "/api/upload")
        r, status = http_post(url, data, timeout=900)
        try:
            obj = json.loads(r.decode("utf-8", "replace"))
        except ValueError:
            obj = {"_raw": r[:300].decode("utf-8", "replace"), "_status": status}
        return obj

    def task(self, operation, upload_id=None, **kw) -> dict:
        obj = {"operation": operation}
        if upload_id is not None:
            obj["upload_id"] = upload_id
        obj.update(kw)
        r, status = http_post_json(self.base + "/api/task", obj, timeout=60)
        r["_status"] = status
        return r

    def poll_task(self, timeout=600, interval=1.0) -> dict:
        """轮询 GET /api/task 直到空闲。返回最终状态对象。"""
        deadline = time.time() + timeout
        last = {}
        while time.time() < deadline:
            try:
                st = http_get_json(self.base + "/api/task", timeout=6)
            except Exception as e:
                self.logf("读取任务状态失败（%s），继续等待…" % e)
                time.sleep(interval)
                continue
            last = st
            # busy 字段存在时以它为准；否则用 state 推断
            if st.get("busy") is not None:
                busy = bool(st.get("busy"))
            else:
                busy = st.get("state") in ("running", "pending", "queued", "started")
            if not busy:
                return st
            out = str(st.get("phase") or st.get("output") or "")[:120]
            if out:
                self.logf("[uboot] %s" % out)
            time.sleep(interval)
        raise TimeoutError("U-Boot 任务超时未完成：%s" % json.dumps(last)[:300])

    def run_task(self, operation, upload_id=None, timeout=600, **kw) -> dict:
        r = self.task(operation, upload_id, **kw)
        if r.get("_status") in (409,):
            raise RuntimeError("设备忙（409）：请稍候或重启设备再试")
        if r.get("_status") not in (200, 201, 202, None):
            raise RuntimeError("任务 %s 提交失败（HTTP %s）：%s"
                               % (operation, r.get("_status"), r.get("message") or r.get("_raw")))
        final = self.poll_task(timeout)
        code = final.get("code")
        if code not in (None, 0, "0", 200, "OK", "ok", "success", "done"):
            raise RuntimeError("任务 %s 失败：%s" % (operation, json.dumps(final, ensure_ascii=False)[:400]))
        return final

    def board_data_targets(self) -> list:
        dev = self.alive() or {}
        bd = dev.get("board_data")
        if isinstance(bd, list) and bd:
            return [str(x) for x in bd]
        if isinstance(bd, dict):
            return list(bd.keys())
        return []


# ---------------------------------------------------------------- session
MODES = ("full", "backup", "restore")

STEP_DEFS = [
    ("probe",        "探测光猫与 stock2ubi",         False),
    ("telnet",       "telnet 登录并提权",             False),
    ("upload_tool",  "上传并运行 stock2ubi",          False),
    ("backup",       "备份全部分区",                  False),
    ("convert",      "转换板级数据（factory→1M）",    False),
    ("flash_bl2",    "刷入 BL2 + U-Boot 引导",        True),
    ("wait_uboot",   "等待 U-Boot 恢复页就绪",        False),
    ("check_model",  "校验设备型号匹配",              False),
    ("ubi_rebuild",  "重建 UBI（⚠ 擦除全部数据）",    True),
    ("board_data",   "刷入板级数据（身份/校准）",     True),
    ("flash_uboot",  "刷写 U-Boot（fip 卷）",         True),
    ("flash_sys",    "刷写系统固件（sysupgrade）",    True),
    ("boot",         "启动系统",                      False),
]


class FlashSession(threading.Thread):
    """一次刷机会话。daemon 线程跑流程，Web 层轮询它的状态。"""

    def __init__(self, cfg: dict):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.dev = get_device(cfg.get("device"))
        self.mode = cfg.get("mode", "full")
        self.lock = threading.Lock()
        self.steps = [{"id": i, "name": n, "state": "pending", "detail": ""}
                      for i, n, _ in STEP_DEFS]
        self.logs = []
        self.error = None
        self.finished = False
        self.abort = threading.Event()
        self.confirm_gate = threading.Event()
        self.pending_confirm = None    # {"step":..., "message":...}
        self.backup_dir = None
        self._teln = None

    # ---- 状态输出 ----
    def state(self) -> dict:
        with self.lock:
            return {
                "mode": self.mode,
                "device": self.dev.key,
                "steps": [{"id": s["id"], "name": s["name"],
                           "state": s["state"], "detail": s["detail"]} for s in self.steps],
                "logs": self.logs[-500:],
                "error": self.error,
                "finished": self.finished,
                "pending_confirm": self.pending_confirm,
                "backup_dir": str(self.backup_dir) if self.backup_dir else None,
            }

    def log(self, msg: str):
        stamp = time.strftime("%H:%M:%S")
        with self.lock:
            self.logs.append("[%s] %s" % (stamp, msg))

    def _step(self, sid, state=None, detail=None):
        with self.lock:
            for s in self.steps:
                if s["id"] == sid:
                    if state:
                        s["state"] = state
                    if detail is not None:
                        s["detail"] = detail

    def _set_error(self, msg):
        with self.lock:
            self.error = msg
            self.logs.append("[!!] %s" % msg)
        self.log("流程中止：%s" % msg)

    def confirm(self, ok: bool):
        if self.pending_confirm:
            self.log("用户%s了危险操作确认" % ("通过" if ok else "拒绝"))
            if not ok:
                self.abort.set()
            self.confirm_gate.set()

    def _ask(self, sid, message):
        with self.lock:
            self.pending_confirm = {"step": sid, "message": message}
        self.confirm_gate.clear()
        self._step(sid, "confirm")
        self.confirm_gate.wait()
        with self.lock:
            self.pending_confirm = None
        if self.abort.is_set():
            raise RuntimeError("用户取消")

    def _check_abort(self):
        if self.abort.is_set():
            raise RuntimeError("用户中止")

    # ---- 流程主体 ----
    def run(self):
        try:
            if self.mode == "restore":
                self.run_restore()
            else:
                self.run_flash()
        except Exception as e:
            self._set_error("%s: %s" % (type(e).__name__, e))
            self.log(traceback.format_exc(limit=6))
        finally:
            with self.lock:
                self.finished = True

    # ---- 备份 + 刷机 ----
    def run_flash(self):
        cfg, dev = self.cfg, self.dev
        ip = cfg.get("ip") or dev.stock_ip
        mac = normalize_mac(cfg.get("mac") or "")
        suffix = mac_suffix(mac)

        # probe
        self._step("probe", "running")
        s2u = Stock2ubiClient(ip, logger=self.log)
        if not s2u.alive():
            self.log("stock2ubi 未运行（%s:3333 无响应），需要通过 telnet 启动" % ip)
        else:
            self.log("检测到 stock2ubi 已在运行，跳过上传步骤")
        self._step("probe", "done")

        if not s2u.alive():
            # telnet
            self._step("telnet", "running")
            user = cfg.get("telnet_user") or dev.telnet.user
            pwd = cfg.get("telnet_pass") or dev.telnet_password_for(suffix)
            tries = tuple(cfg.get("telnet_tries", ())) or dev.telnet.password_fallbacks
            su_user = cfg.get("su_user") or dev.telnet.su_user
            su_pwd = cfg.get("su_pass") or dev.su_password_for(suffix)
            t = MiniTelnet(ip, dev.telnet.port, logger=self.log)
            self._teln = t
            t.login(user, pwd, tries)
            t.become_root(su_user, su_pwd)
            self._step("telnet", "done")

            # upload tool
            self._step("upload_tool", "running")
            if not STOCK2UBI_PATH.exists():
                raise RuntimeError(
                    "缺少 %s：请在 GitHub pbs05/an758x-stock2ubi 的 Releases 下载后"
                    "放入 files/ 目录，或在页面「工具」里上传" % STOCK2UBI_PATH)
            host_ip = local_ip_for(ip)
            self.log("本机对 %s 的地址是 %s，光猫将从这里下载工具" % (ip, host_ip))
            payload_url = self.cfg["_payload_url"]  # 由 WebApp 注入
            out = t.run("cd /tmp && wget -O /tmp/%s %s && chmod +x /tmp/%s"
                        % (STOCK2UBI_NAME, payload_url, STOCK2UBI_NAME), timeout=180)
            self.log("wget 输出：%s" % out.strip()[:200])
            t.send("/tmp/%s &" % STOCK2UBI_NAME)
            self.log("已在光猫上启动 stock2ubi，等待 :3333 就绪…")
            deadline = time.time() + 60
            while time.time() < deadline:
                self._check_abort()
                if s2u.alive():
                    break
                time.sleep(2)
            else:
                raise TimeoutError("stock2ubi 启动后 60 秒内未监听 3333；"
                                   "检查光猫内核版本是否为 5.4.55")
            self._step("upload_tool", "done")

        # backup
        self._step("backup", "running")
        parts = s2u.partitions()
        self.log("发现 %d 个 MTD 分区" % len(parts))
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.backup_dir = BACKUP_ROOT / ("%s_%s" % (dev.key, stamp))
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        manifest = {"device": dev.key, "created": stamp, "mac": mac, "partitions": []}
        for p in parts:
            self._check_abort()
            fname = "mtd%d-%s.bin" % (p["index"], re.sub(r"[^0-9A-Za-z._-]", "_", p["name"]))
            dest = self.backup_dir / fname
            self.log("备份 %s（%s）…" % (p["name"], fname))
            info = s2u.download_partition(p, dest)
            manifest["partitions"].append(info)
        with open(self.backup_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        self.log("备份完成 → %s（务必另行拷贝一份！）" % self.backup_dir)
        self._step("backup", "done", "%d 个分区" % len(parts))

        # 板级数据准备
        board_file = None
        if dev.board_convert == "fiberhome":
            self._step("convert", "running")
            fac = self._find_factory(manifest)
            raw = Path(fac["file"]).read_bytes()
            self.log("解析 factory 分区（%d 字节，fhdata 卷）…" % len(raw))
            files, warn = fhfactory.extract_files(raw)
            if warn:
                self.log("⚠ factory 解析警告：%s" % warn)
            need = [k for k in ("factory_conf",) if k not in files]
            if need:
                raise RuntimeError("factory 备份里缺少 %s，无法生成板级数据" % need)
            img = fhfactory.build_factory_image(files)
            board_file = self.backup_dir / "factory-1m.bin"
            board_file.write_bytes(img)
            info = fhfactory.describe(img)
            self.log("板级数据已生成：MAC=%(mac)s PON_SN=%(pon_sn)s WiFi=%(wifi_present)s"
                     % info)
            self._step("convert", "done", "factory-1m.bin")
        else:
            self._step("convert", "done", "此机型无需转换（%s）" % dev.board_data)

        if self.mode == "backup":
            self.log("仅备份模式：到此结束。")
            return

        # 刷 BL2
        self._step("flash_bl2", "running")
        bl2_path = Path(cfg["bl2"])
        fip_path = Path(cfg["fip"])
        self.log("BL2 = %s（%d B）" % (bl2_path.name, bl2_path.stat().st_size))
        self.log("FIP = %s（%d B）" % (fip_path.name, fip_path.stat().st_size))
        self._ask("flash_bl2",
                  "即将向 NAND 写入 BL2 引导与 U-Boot（%s + %s）。\n"
                  "写入过程中绝对不能断电，否则需要编程器/TTL 救砖。\n"
                  "确认继续？" % (bl2_path.name, fip_path.name))
        msg = s2u.flash_boot(bl2_path.read_bytes(), fip_path.read_bytes())
        self.log("stock2ubi 回复：%s" % msg.strip()[:200])
        self._step("flash_bl2", "done", "设备将在 1 秒后自动重启")

        # U-Boot 阶段
        self.enter_uboot_phase(cfg, dev, board_file)

    def _find_factory(self, manifest) -> dict:
        for p in manifest["partitions"]:
            if p["name"].lower() == "factory":
                return p
        for p in manifest["partitions"]:
            if "factory" in p["name"].lower():
                return p
        raise RuntimeError("备份里没有 factory 分区（烽火机型必需）")

    def enter_uboot_phase(self, cfg, dev, board_file):
        ub = UbootClient(dev.uboot_ip, logger=self.log)

        self._step("wait_uboot", "running")
        self.log("等待 U-Boot 恢复页（http://%s）就绪，首次约 1 分钟…" % dev.uboot_ip)
        deadline = time.time() + 300
        devinfo = None
        while time.time() < deadline:
            self._check_abort()
            devinfo = ub.alive()
            if devinfo:
                break
            time.sleep(3)
        if not devinfo:
            raise TimeoutError("5 分钟内 U-Boot 恢复页未就绪；"
                               "检查网线是否插千兆口（2.5G 口无驱动）")
        self._step("wait_uboot", "done", "恢复页已就绪")

        # 校验型号
        self._step("check_model", "running")
        model = str(devinfo.get("model") or devinfo.get("device") or "")
        self.log("U-Boot 报告机型：%s" % (model or devinfo.get("_raw", "?")[:80]))
        target = (dev.uboot_target + " " + dev.ponwrt_profile).lower()
        if model and not any(t.lower() in model.lower() or model.lower() in t.lower()
                             for t in target.split() if len(t) > 3):
            self._ask("check_model",
                      "设备报告机型「%s」与你选择的「%s」不完全匹配。\n"
                      "继续可能导致刷入错误固件而无法启动。仍然继续？" % (model, dev.name))
        self._step("check_model", "done", model or "未知（已跳过校验）")

        # 重建 UBI
        self._step("ubi_rebuild", "running")
        self._ask("ubi_rebuild",
                  "即将重建 UBI——擦除 NAND 上 UBI 区域的全部数据（含原厂分区）。\n"
                  "此步之后必须完成板级数据与 U-Boot 刷写，中途放弃设备将无法启动。\n"
                  "确认继续？")
        self.log("重建 UBI…")
        ub.run_task("ubi-rebuild", timeout=900)
        self._step("ubi_rebuild", "done")

        # 板级数据
        self._step("board_data", "running")
        targets = ub.board_data_targets()
        self.log("U-Boot 支持的板级数据卷：%s" % (targets or "（未报告）"))
        if board_file is None:
            # 非转换机型：从备份里找对应分区/卷直接写
            board_file = self._direct_board_file()
        if board_file is None:
            raise RuntimeError("找不到板级数据来源（备份目录中无 %s 相关分区）" % dev.board_data)
        bd_target = cfg.get("board_target") or (targets[0] if targets else dev.board_data.split("+")[0])
        self._ask("board_data",
                  "即将写入板级数据（%s，%s）。\n"
                  "必须使用本机自己的备份转换出的数据，使用他人数据 PON 将无法注册。\n"
                  "确认继续？" % (bd_target, Path(board_file).name))
        up = ub.upload(Path(board_file).read_bytes())
        uid = up.get("upload_id") or up.get("id")
        self.log("上传完成，upload_id=%s" % uid)
        ub.run_task("flash-board-data", upload_id=uid, target=bd_target, timeout=900)
        self._step("board_data", "done")

        # U-Boot
        self._step("flash_uboot", "running")
        fip_path = Path(self.cfg["fip"])
        up = ub.upload(fip_path.read_bytes())
        uid = up.get("upload_id") or up.get("id")
        ub.run_task("flash-uboot", upload_id=uid, timeout=600)
        self._step("flash_uboot", "done", fip_path.name)

        # 系统
        self._step("flash_sys", "running")
        sys_path = Path(self.cfg["sys"])
        self._ask("flash_sys",
                  "即将刷写系统固件 %s（%d B）。\n"
                  "U-Boot 会校验 FIT 与设备树兼容性，不匹配会拒绝。\n确认继续？"
                  % (sys_path.name, sys_path.stat().st_size))
        up = ub.upload(sys_path.read_bytes())
        uid = up.get("upload_id") or up.get("id")
        ub.run_task("flash", upload_id=uid, timeout=1800)
        self._step("flash_sys", "done", sys_path.name)

        # 启动
        self._step("boot", "running")
        ub.run_task("boot-production", timeout=120)
        self._step("boot", "done", "设备正在启动新系统")
        self.log("🎉 全部完成！系统启动后 LAN 口 DHCP，管理地址见固件默认配置。")

    def _direct_board_file(self) -> Path:
        """非烽火机型：从备份里直接挑板级数据分区。"""
        names = [n.lower() for n in self.dev.board_data.split("+")] + [self.dev.board_data.lower()]
        if not self.backup_dir:
            return None
        for f in sorted(self.backup_dir.glob("mtd*.bin")):
            low = f.name.lower()
            if any(n in low for n in names):
                return f
        return None

    # ---- 还原模式 ----
    def run_restore(self):
        cfg, dev = self.cfg, self.dev
        bdir = Path(cfg["backup_dir"])
        if not (bdir / "manifest.json").is_file():
            raise RuntimeError("备份目录 %s 里没有 manifest.json" % bdir)
        manifest = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
        self.backup_dir = bdir
        self.log("还原模式：备份目录 %s（%d 个分区）" % (bdir, len(manifest["partitions"])))

        ub = UbootClient(dev.uboot_ip, logger=self.log)
        self._step("wait_uboot", "running")
        deadline = time.time() + 300
        while time.time() < deadline:
            self._check_abort()
            if ub.alive():
                break
            time.sleep(3)
        else:
            raise TimeoutError("U-Boot 恢复页未就绪（需先刷入恢复引导才可整片还原）")
        self._step("wait_uboot", "done")
        self._step("check_model", "done", "还原模式跳过校验")

        self._step("ubi_rebuild", "running")
        self._ask("ubi_rebuild",
                  "整片还原会重写全部 NAND（含 BL2、分区表、全部数据）。\n"
                  "过程中断电 = 设备变砖（需编程器）。写入约需 10-30 分钟。\n"
                  "确认继续？")
        # 拼接整片镜像
        devinfo = ub.alive() or {}
        flash_bytes = int(devinfo.get("flash_bytes") or 0) or 256 * 1024 * 1024
        image = bytearray(b"\xff" * flash_bytes)
        for p in manifest["partitions"]:
            data = Path(p["file"]).read_bytes()
            off = p.get("offset")
            if off is None:
                raise RuntimeError("分区 %s 无物理偏移信息，无法拼整片镜像" % p["name"])
            if off + len(data) > flash_bytes:
                raise RuntimeError("分区 %s 超出闪存大小" % p["name"])
            image[off:off + len(data)] = data
            self.log("拼接 %s → 0x%x (%d B)" % (p["name"], off, len(data)))
        all_img = bdir / "all-flash.bin"
        all_img.write_bytes(bytes(image))
        self.log("整片镜像已生成：%s（%d B）" % (all_img, len(image)))

        up = ub.upload(bytes(image), all_flash=True)
        uid = up.get("upload_id") or up.get("id")
        ub.run_task("flash-all", upload_id=uid, timeout=3600)
        self._step("ubi_rebuild", "done", "整片已写入并校验")
        ub.run_task("reset", timeout=30)
        self._step("board_data", "done", "随整片还原")
        self._step("flash_uboot", "done", "随整片还原")
        self._step("flash_sys", "done", "随整片还原")
        self._step("boot", "done", "设备已重启进入原厂系统")
        self.log("🎉 还原完成。")


# ---------------------------------------------------------------- web app
class WebApp:
    def __init__(self, port=8799):
        self.port = port
        self.session = None
        self.session_lock = threading.Lock()

    def start_session(self, cfg: dict):
        with self.session_lock:
            if self.session and not self.session.finished:
                raise RuntimeError("已有会话在进行中，请先等待完成或中止")
            cfg = dict(cfg)
            cfg["_payload_url"] = "http://%s:%d/payload/%s" % (
                local_ip_for(cfg.get("ip") or "192.168.1.1"), self.port, STOCK2UBI_NAME)
            self.session = FlashSession(cfg)
            self.session.start()
            return True

    def handler(self):
        app = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def _send(self, code, body, ctype="application/json; charset=utf-8"):
                if isinstance(body, str):
                    body = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?")[0]
                if path in ("/", "/index.html"):
                    self._send(200, UI_HTML, "text/html; charset=utf-8")
                elif path == "/api/devices":
                    payload = {"devices": [d.as_dict() for d in DEVICES]}
                    for d in payload["devices"]:
                        d["bundled"] = bool(bundled_boot_files(d["key"]))
                    idx = bundled_fw_index()
                    payload["bundled_tag"] = idx.get("tag", "")
                    payload["bundled_count"] = len(idx.get("files") or {})
                    self._send(200, json.dumps(payload, ensure_ascii=False))
                elif path == "/api/state":
                    if app.session:
                        self._send(200, json.dumps(app.session.state(), ensure_ascii=False))
                    else:
                        self._send(200, json.dumps({"steps": [], "logs": [], "finished": True,
                                                    "idle": True}))
                elif path == "/api/tool":
                    self._send(200, json.dumps({"present": STOCK2UBI_PATH.exists(),
                                                "path": str(STOCK2UBI_PATH)}))
                elif path.startswith("/payload/"):
                    name = path[len("/payload/"):]
                    if name == STOCK2UBI_NAME and STOCK2UBI_PATH.exists():
                        data = STOCK2UBI_PATH.read_bytes()
                        self._send(200, data, "application/octet-stream")
                    else:
                        self._send(404, "not found", "text/plain")
                else:
                    self._send(404, '{"error":"not found"}')

            def do_POST(self):
                path = self.path.split("?")[0]
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""

                if path == "/api/scan":
                    try:
                        req = json.loads(body.decode("utf-8"))
                        d = Path(req.get("dir") or ".")
                        result = {}
                        for dev in DEVICES:
                            result[dev.key] = {
                                "bl2": guess_matches(dev.bl2_patterns, d)[:1],
                                "fip": guess_matches(dev.fip_patterns, d)[:1],
                                "sys": guess_matches(dev.sysupgrade_patterns, d)[:1],
                            }
                        self._send(200, json.dumps({"dir": str(d.resolve()), "matches": result},
                                                   ensure_ascii=False))
                    except Exception as e:
                        self._send(400, json.dumps({"error": str(e)}, ensure_ascii=False))
                elif path == "/api/start":
                    try:
                        req = json.loads(body.decode("utf-8"))
                        self._validate(req)
                        app.start_session(req)
                        self._send(200, '{"ok":true}')
                    except Exception as e:
                        self._send(400, json.dumps({"error": str(e)}, ensure_ascii=False))
                elif path == "/api/confirm":
                    if app.session:
                        req = json.loads(body.decode("utf-8") or "{}")
                        app.session.confirm(bool(req.get("ok", True)))
                        self._send(200, '{"ok":true}')
                    else:
                        self._send(400, '{"error":"no session"}')
                elif path == "/api/abort":
                    if app.session:
                        app.session.abort.set()
                        app.session.confirm(False)
                        self._send(200, '{"ok":true}')
                    else:
                        self._send(200, '{"ok":true}')
                elif path == "/api/probe_mac":
                    try:
                        req = json.loads(body.decode("utf-8"))
                        ip = req.get("ip") or "192.168.1.1"
                        port = int(req.get("port") or 8080)
                        url = "http://%s:%d/cgi-bin/ajax?ajaxmethod=web_get_brmad" % (ip, port)
                        rq = urllib.request.Request(url, headers={
                            "User-Agent": "Mozilla/5.0",
                            "X-Requested-With": "XMLHttpRequest",
                            "Referer": "http://%s:%d/" % (ip, port)})
                        raw = urllib.request.urlopen(rq, timeout=6).read().decode("utf-8", "replace")
                        m = re.search(r'"brmad"\s*:\s*"([^"]+)"', raw)
                        mac = normalize_mac(m.group(1)) if m else ""
                        if not re.fullmatch(r"[0-9A-F]{12}", mac or ""):
                            raise RuntimeError("接口返回无有效 MAC：%s" % raw[:120])
                        self._send(200, json.dumps({"mac": mac}, ensure_ascii=False))
                    except Exception as e:
                        self._send(400, json.dumps({"error": "读取 MAC 失败：%s" % e},
                                                   ensure_ascii=False))
                elif path == "/api/firmware_fetch":
                    try:
                        req = json.loads(body.decode("utf-8"))
                        key = req.get("device")
                        if key not in BY_KEY:
                            raise ValueError("未知机型：%s" % key)
                        d = req.get("dir") or None
                        out = firmware_fetch(key, d, online=bool(req.get("online")))
                        self._send(200, json.dumps(out, ensure_ascii=False))
                    except Exception as e:
                        self._send(400, json.dumps({"error": str(e)}, ensure_ascii=False))
                elif path == "/api/upload_tool":
                    ct = self.headers.get("Content-Type", "")
                    data = body
                    m = re.search(r'filename="([^"]+)"', ct)
                    if data[:4] == b"\x7fELF" or len(data) > 100000:
                        STOCK2UBI_PATH.write_bytes(data)
                        os.chmod(STOCK2UBI_PATH, 0o755)
                        self._send(200, json.dumps({"ok": True, "path": str(STOCK2UBI_PATH)},
                                                   ensure_ascii=False))
                    else:
                        self._send(400, json.dumps(
                            {"error": "上传内容不像 stock2ubi 可执行文件（%d 字节）" % len(data)},
                            ensure_ascii=False))
                else:
                    self._send(404, '{"error":"not found"}')

            def _validate(self, req):
                mode = req.get("mode")
                if mode not in MODES:
                    raise ValueError("mode 必须是 %s" % MODES)
                if req.get("device") not in BY_KEY:
                    raise ValueError("未知机型")
                dev = get_device(req["device"])
                if mode in ("full",):
                    for k, label in (("bl2", "BL2/firstblock"), ("fip", "U-Boot FIP"),
                                     ("sys", "系统固件")):
                        if not req.get(k) or not Path(req[k]).is_file():
                            raise ValueError("缺少%s文件：%s" % (label, req.get(k)))
                        low = Path(req[k]).name.lower()
                        pats = {"bl2": dev.bl2_patterns, "fip": dev.fip_patterns,
                                "sys": dev.sysupgrade_patterns}[k]
                        if not any(fnmatch.fnmatch(low, p.lower()) for p in pats):
                            raise ValueError(
                                "文件 %s 与机型 %s 不匹配（期望形如 %s）。"
                                "请核对是否选错文件或机型。" % (low, dev.name, pats[0]))
                if mode == "restore":
                    if not req.get("backup_dir") or not (Path(req["backup_dir"]) / "manifest.json").is_file():
                        raise ValueError("还原模式需要包含 manifest.json 的备份目录")

        return H

    def serve(self):
        httpd = ThreadingHTTPServer(("0.0.0.0", self.port), self.handler())
        log("PonFlasher 监听 http://0.0.0.0:%d （UI 请用 http://127.0.0.1:%d）" % (self.port, self.port))
        url = "http://127.0.0.1:%d" % self.port
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


def main():
    port = 8799
    args = sys.argv[1:]
    if "--port" in args:
        port = int(args[args.index("--port") + 1])
    WebApp(port).serve()


if __name__ == "__main__":
    main()
