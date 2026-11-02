#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""XR1710G chainloader 路线支持模块。

路线说明（naoki66/XR1710G-http-uboot，fork 自 YYH2913/http-uboot）：
  1. 首次安装：TTL 进原厂 ECNT U-Boot，TFTP 下载 *-flash-slot.bin，
     擦写 0x600000 起的 1MB chainloader 槽位（原 bootcmd 不用改，双入口镜像）。
  2. 之后：长按 reset 进 HTTP 恢复页 http://192.168.255.1（带 DHCP），
     POST /upload/firmware?layout=2.0|1.5|1.0 写入 *-sysupgrade.itb，
     GET /status 轮询进度，POST /reboot 重启。
  3. 布局必须与固件内嵌设备树的 ubi 分区 reg 一致，选错会导致
     “内核能启动但 Linux 一直等 /dev/fit0”。本模块解析 FIT 自动判定。

本文件为 PonFlasher 的一部分（AGPL-3.0-only）。
"""
import fnmatch
import json
import socket
import struct
import threading
import time
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------- 常量
RECOVERY_IP = "192.168.255.1"
UBOOT_IP = "192.168.1.1"          # TTL 阶段给设备临时指定的地址
TFTP_LOAD_ADDR = "0x89000000"
CHAIN_SLOT_ADDR = "0x600000"      # chainloader 槽物理地址
CHAIN_SLOT_SIZE = "0x100000"      # 1 MiB

CHAINLOADER_REPO = "naoki66/XR1710G-http-uboot"
FIRMWARE_REPO = "naoki66/ImmortalWrt-for-Gemtek-XR1710G"

# ubi 分区大小 → 恢复页布局选择（见上游 README 的对照表）
UBI_LAYOUTS = (
    (0x1B700000, "2.0", "439 MiB"),
    (0x1D9C0000, "1.5", "471.75 MiB"),
    (0x1F700000, "1.0", "507 MiB"),
)

# GitHub 直连不通时依次回退的加速镜像（前缀 + 完整 GitHub URL）
GH_MIRRORS = ("", "https://ghproxy.net/", "https://ghfast.top/")

LOGF = None


def log(msg):
    if LOGF:
        LOGF(msg)


# ---------------------------------------------------------------- FDT / FIT 解析
FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9


def _fdt_walk(data: bytes):
    """yield (path, prop_name, prop_bytes)。极简 FDT 结构块遍历。"""
    if len(data) < 40 or struct.unpack(">I", data[:4])[0] != 0xD00DFEED:
        raise ValueError("不是合法 FDT（magic 不符）")
    off_struct = struct.unpack(">I", data[8:12])[0]
    off_strings = struct.unpack(">I", data[12:16])[0]
    pos = off_struct
    path = []
    while pos < len(data) - 4:
        token = struct.unpack(">I", data[pos:pos + 4])[0]
        pos += 4
        if token == FDT_BEGIN_NODE:
            end = data.index(b"\x00", pos)
            name = data[pos:end].decode("utf-8", "replace")
            path.append(name)
            pos = (end + 1 + 3) & ~3
        elif token == FDT_END_NODE:
            if path:
                path.pop()
        elif token == FDT_PROP:
            length, nameoff = struct.unpack(">II", data[pos:pos + 8])
            pos += 8
            val = data[pos:pos + length]
            pos = (pos + length + 3) & ~3
            nend = data.index(b"\x00", off_strings + nameoff)
            pname = data[off_strings + nameoff:nend].decode("utf-8", "replace")
            yield "/" + "/".join(p for p in path if p), pname, val
        elif token == FDT_NOP:
            continue
        elif token == FDT_END:
            break


def fit_ubi_info(itb_path) -> dict:
    """解析 sysupgrade.itb：找到 fdt 组件，读 ubi 分区大小，判布局。

    返回 {"layout": "2.0"|..., "ubi_size": int, "desc": str, "fdt_found": bool}
    """
    data = Path(itb_path).read_bytes()
    # uImage 头（64B）之后是 FIT（本身也是 FDT）。也兼容直接就是 FDT 的文件。
    offset = 0
    if len(data) > 68 and struct.unpack(">I", data[:4])[0] != 0xD00DFEED:
        # mkimage 产物：64B legacy uImage 头（magic 0x27051956）+ FIT
        if struct.unpack(">I", data[:4])[0] == 0x27051956 and \
                struct.unpack(">I", data[64:68])[0] == 0xD00DFEED:
            offset = 64
        else:
            raise ValueError("既不是 FDT 也不是 uImage/FIT 文件")
    fit = data[offset:]
    fdt_data = _find_fdt_image(fit)
    if fdt_data is None:
        return {"layout": None, "ubi_size": None, "desc": "未找到内嵌设备树",
                "fdt_found": False}
    for path, pname, val in _fdt_walk(fdt_data):
        if pname == "label" and val.rstrip(b"\x00") == b"ubi":
            # 同节点的 reg：取第二个 cell（size）
            reg = _fdt_prop(fdt_data, path, "reg")
            if reg and len(reg) >= 8:
                size = struct.unpack(">II", reg[:8])[1]
                for want, lay, desc in UBI_LAYOUTS:
                    if size == want:
                        return {"layout": lay, "ubi_size": size,
                                "desc": "ubi %s（0x%X）→ 布局 %s" % (desc, size, lay),
                                "fdt_found": True}
                return {"layout": None, "ubi_size": size,
                        "desc": "ubi 大小 0x%X 不在已知布局表，请人工确认" % size,
                        "fdt_found": True}
    return {"layout": None, "ubi_size": None,
            "desc": "设备树里没找到 ubi 分区", "fdt_found": True}


def _find_fdt_image(fit: bytes):
    """在 FIT 里找到 type=flat_dt 的组件数据。支持内联 data / 外置
    data-offset（相对 FDT 末尾对齐边界）/ data-position（文件绝对偏移）。"""
    from collections import defaultdict
    props = defaultdict(dict)
    for path, pname, val in _fdt_walk(fit):
        props[path][pname] = val
    totalsize = struct.unpack(">I", fit[4:8])[0]
    for path in sorted(props):
        if not path.startswith("/images/") or path.count("/") != 2:
            continue
        if props[path].get("type", b"").rstrip(b"\x00") != b"flat_dt":
            continue
        p = props[path]
        if "data" in p:
            return p["data"]
        if "data-position" in p and "data-size" in p:
            pos = struct.unpack(">I", p["data-position"])[0]
            size = struct.unpack(">I", p["data-size"])[0]
            return fit[pos:pos + size]
        if "data-offset" in p and "data-size" in p:
            off = struct.unpack(">I", p["data-offset"])[0]
            size = struct.unpack(">I", p["data-size"])[0]
            bases = ((totalsize + 3) & ~3,
                     (totalsize + 0xFFF) & ~0xFFF,
                     totalsize)
            for base in dict.fromkeys(bases):
                cand = fit[base + off:base + off + size]
                if len(cand) == size and cand[:4] == b"\xd0\x0d\xfe\xed":
                    return cand
    return None


def _fdt_prop(fdt: bytes, node: str, want: str) -> bytes:
    for path, pname, val in _fdt_walk(fdt):
        if path == node and pname == want:
            return val
    return b""


# ---------------------------------------------------------------- 下载
def _gh(url, timeout=30):
    for prefix in GH_MIRRORS:
        try:
            r = urllib.request.Request(prefix + url,
                                        headers={"User-Agent": "ponflasher"})
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return resp.read()
        except Exception:
            continue
    raise RuntimeError("下载失败（直连与镜像均不可用）：%s" % url)


def _release_assets(repo):
    j = json.loads(_gh("https://api.github.com/repos/%s/releases" % repo))
    if not isinstance(j, list) or not j:
        raise RuntimeError("%s 没有可用 Releases" % repo)
    return j


def chainloader_fetch(dest_dir) -> dict:
    """下载最新 *-flash-slot.bin（chainloader 槽镜像）。"""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    rels = _release_assets(CHAINLOADER_REPO)
    for rel in rels:
        hits = [a for a in rel.get("assets") or []
                if fnmatch.fnmatch(a["name"].lower(), "*flash-slot*.bin")]
        if hits:
            a = sorted(hits, key=lambda x: x["size"])[-1]
            out = dest / a["name"]
            log("下载 chainloader %s（%d B，tag %s）…" % (a["name"], a["size"], rel["tag_name"]))
            data = _gh(a["browser_download_url"], timeout=300)
            if len(data) != a["size"]:
                raise RuntimeError("chainloader 大小不符（%d/%d）" % (len(data), a["size"]))
            out.write_bytes(data)
            return {"tag": rel["tag_name"], "path": str(out), "size": a["size"]}
    raise RuntimeError("%s 的 Releases 里没有 *-flash-slot.bin" % CHAINLOADER_REPO)


def firmware_fetch(dest_dir) -> dict:
    """下载最新的 XR1710G sysupgrade.itb。"""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    rels = _release_assets(FIRMWARE_REPO)
    for rel in rels:
        hits = [a for a in rel.get("assets") or []
                if fnmatch.fnmatch(a["name"].lower(), "*xr1710g*sysupgrade*.itb")]
        if hits:
            a = hits[0]
            out = dest / a["name"]
            if out.exists() and out.stat().st_size == a["size"]:
                return {"tag": rel["tag_name"], "path": str(out), "size": a["size"]}
            log("下载固件 %s（%.1f MB，tag %s）…"
                % (a["name"], a["size"] / 1048576, rel["tag_name"]))
            data = _gh(a["browser_download_url"], timeout=600)
            if len(data) != a["size"]:
                raise RuntimeError("固件大小不符（%d/%d）" % (len(data), a["size"]))
            out.write_bytes(data)
            return {"tag": rel["tag_name"], "path": str(out), "size": a["size"]}
    raise RuntimeError("%s 的 Releases 里没有 XR1710G sysupgrade.itb" % FIRMWARE_REPO)


# ---------------------------------------------------------------- TFTP 服务器
class MiniTFTPServer:
    """单文件 TFTP 服务器（octet，仅 RRQ）。给 TTL 阶段的 tftpboot 用。"""

    def __init__(self, filepath, bind_port=69, logger=None):
        self.path = Path(filepath)
        self.port = int(bind_port)
        self.logf = logger or log
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", self.port))
        self.sock.settimeout(1.0)
        self.stop = threading.Event()
        self.served = 0          # 已完整服务的 RRQ 数
        self.last_peer = None
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self.thread.start()
        self.logf("TFTP 服务已启动（0.0.0.0:%d，文件 %s，%d B）"
                  % (self.port, self.path.name, self.path.stat().st_size))

    def shutdown(self):
        self.stop.set()
        try:
            self.sock.close()
        except OSError:
            pass

    def _loop(self):
        while not self.stop.is_set():
            try:
                pkt, peer = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(pkt) < 4:
                continue
            op = struct.unpack(">H", pkt[:2])[0]
            if op == 1:  # RRQ
                self.last_peer = peer
                self._serve_rrq(pkt, peer)
            # 其他 opcode（WRQ/ACK/ERROR）忽略

    def _serve_rrq(self, pkt, peer):
        try:
            parts = pkt[2:].split(b"\x00")
            fname = parts[0].decode("utf-8", "replace") if parts else ""
            if fname not in (self.path.name, self.path.name.split("/")[-1]):
                self.sock.sendto(struct.pack(">HH", 5, 1) + b"file not found\x00", peer)
                return
            data = self.path.read_bytes()
        except Exception as e:
            self.sock.sendto(struct.pack(">HH", 5, 2) + b"read error\x00", peer)
            self.logf("TFTP 读文件失败：%s" % e)
            return
        self.logf("TFTP RRQ 来自 %s：%s（%d B）" % (peer[0], self.path.name, len(data)))
        block = 1
        pos = 0
        retries = 0
        while pos < len(data) or block == 1:
            chunk = data[pos:pos + 512]
            pkt_out = struct.pack(">HH", 3, block) + chunk
            try:
                self.sock.sendto(pkt_out, peer)
            except OSError:
                return
            # 等 ACK
            acked = False
            for _ in range(10):
                if self.stop.is_set():
                    return
                try:
                    r, _ = self.sock.recvfrom(516)
                except socket.timeout:
                    try:
                        self.sock.sendto(pkt_out, peer)
                    except OSError:
                        return
                    continue
                if len(r) >= 4 and struct.unpack(">HH", r[:4]) == (4, block):
                    acked = True
                    break
            if not acked:
                retries += 1
                if retries > 5:
                    self.logf("TFTP 传输中止：块 %d 多次未确认" % block)
                    return
                continue
            retries = 0
            pos += len(chunk)
            block += 1
            if not chunk:  # 最后一个空块已确认
                break
        self.served += 1
        self.logf("TFTP 传输完成（%d 块）→ %s" % (block - 1, peer[0]))


# ---------------------------------------------------------------- 恢复页客户端
class XRRecoveryClient:
    """XR1710G HTTP 恢复页客户端（192.168.255.1）。"""

    def __init__(self, ip=RECOVERY_IP, port=80, logger=None):
        self.base = "http://%s:%d" % (ip, int(port))
        self.logf = logger or log

    def _get(self, path, timeout=6):
        with urllib.request.urlopen(self.base + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def _post(self, path, body: bytes, timeout=1800):
        req = urllib.request.Request(self.base + path, data=body, method="POST",
                                     headers={"Content-Type": "application/octet-stream"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()

    def about(self):
        return self._get("/about")

    def status(self):
        return self._get("/status")

    def alive(self) -> bool:
        try:
            return "u_boot" in self.about()
        except Exception:
            return False

    def upload_firmware(self, data: bytes, layout: str):
        if layout not in ("2.0", "1.5", "1.0"):
            raise ValueError("布局必须是 2.0 / 1.5 / 1.0，收到 %r" % layout)
        code, body = self._post("/upload/firmware?layout=%s" % layout, data)
        if code != 200:
            raise RuntimeError("固件上传被拒（HTTP %d）：%s" % (code, body[:200]))
        return True

    def upload_uboot(self, data: bytes):
        code, body = self._post("/upload/uboot", data)
        if code != 200:
            raise RuntimeError("U-Boot 上传被拒（HTTP %d）：%s" % (code, body[:200]))
        return True

    def reboot(self):
        req = urllib.request.Request(self.base + "/reboot", data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status

    def wait_done(self, timeout=1800, interval=1.5, progress=None):
        """轮询 /status 直到 ok=1。progress(done,total,phase) 回调可选。"""
        deadline = time.time() + timeout
        last = -1
        while time.time() < deadline:
            try:
                st = self.status()
            except Exception as e:
                self.logf("读 /status 失败（%s），继续等待…" % e)
                time.sleep(interval)
                continue
            if st.get("error"):
                raise RuntimeError("恢复页报告写入失败（error=1）")
            done, total = int(st.get("done") or 0), int(st.get("total") or 0)
            if done != last and progress:
                progress(done, total, st.get("phase"))
                last = done
            if st.get("ok"):
                return st
            time.sleep(interval)
        raise TimeoutError("等待写入完成超时（%ds）" % timeout)


# ---------------------------------------------------------------- TTL 命令清单
def ttl_script(pc_ip: str, filename: str) -> str:
    """生成 TTL 阶段要粘贴进串口终端的命令（当前双入口镜像，无需改 bootcmd）。"""
    return "\n".join([
        "setenv serverip %s" % pc_ip,
        "setenv ipaddr %s" % UBOOT_IP,
        "tftpboot %s %s" % (TFTP_LOAD_ADDR, filename),
        "flash erase %s %s" % (CHAIN_SLOT_ADDR, CHAIN_SLOT_SIZE),
        "flash write %s %s %s" % (CHAIN_SLOT_ADDR, CHAIN_SLOT_SIZE, TFTP_LOAD_ADDR),
        "reset",
    ])


def ttl_script_old_env(pc_ip: str, filename: str) -> str:
    """旧环境（bootcmd 非 0x602100 入口）时需要补的环境修正。"""
    return "\n".join([
        "setenv serverip %s" % pc_ip,
        "setenv ipaddr %s" % UBOOT_IP,
        "tftpboot %s %s" % (TFTP_LOAD_ADDR, filename),
        "flash erase %s %s" % (CHAIN_SLOT_ADDR, CHAIN_SLOT_SIZE),
        "flash write %s %s %s" % (CHAIN_SLOT_ADDR, CHAIN_SLOT_SIZE, TFTP_LOAD_ADDR),
        "setenv loadaddr 0x81800000",
        "setenv fdt_high 0xac000000",
        "setenv bootcmd 'flash read 0x602100 0x4000000 $loadaddr; bootm'",
        "saveenv",
        "reset",
    ])
