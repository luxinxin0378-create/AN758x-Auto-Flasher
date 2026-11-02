#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""不依赖 git 协议、直接用 GitHub Git Data API 提交本地文件（用于 github.com 直连不通时）。"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

TOKEN = os.environ.get("GH_TOKEN", "")
OWNER = "luxinxin0378-create"
REPO = "AN758x-Auto-Flasher"
API = "https://api.github.com/repos/%s/%s" % (OWNER, REPO)


def req(url, data=None, method=None, timeout=120):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, method=method or ("POST" if body else "GET"))
    r.add_header("Authorization", "Bearer " + TOKEN)
    r.add_header("Accept", "application/vnd.github+json")
    r.add_header("User-Agent", "api-push")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            if attempt == 2:
                raise
            sys.stderr.write("retry %s: %s\n" % (url, e))
            time.sleep(3)


def main():
    files = subprocess.check_output(["git", "ls-files"], text=True).split()
    files = [f.strip() for f in files if f.strip()]
    print("待提交文件: %d" % len(files))

    tree = []
    for path in files:
        with open(path, "rb") as f:
            raw = f.read()
        got = req(API + "/git/blobs",
                  {"content": base64.b64encode(raw).decode(), "encoding": "base64"})
        tree.append({"path": path.replace("\\", "/"), "mode": "100644",
                     "type": "blob", "sha": got["sha"]})
        print("  blob %-56s %8d B" % (path, len(raw)))

    t = req(API + "/git/trees", {"tree": tree})
    # 远端 main 已有提交则作为 parent（保留历史），否则新历史
    parents = []
    try:
        ref = req(API + "/git/refs/heads/main")
        parents = [ref["object"]["sha"]]
    except Exception:
        pass
    c = req(API + "/git/commits", {
        "message": "AN758x 自动刷机工具：内置 stock2ubi + AN758x 全系引导包\n\n"
                   "- 12 机型免拆刷机 / 备份 / 还原，网页式操作\n"
                   "- 内置 an758x-stock2ubi v0.1.14（免拆工具，开箱即用）\n"
                   "- 内置 uboot-an758x v2026.07-4 全 23 个引导包资产（10 机型离线可用）\n"
                   "- factory 3M->1M 转换与全链路 CRC 校验（fhfactory，GPL-2.0）\n"
                   "- 源码 AGPL-3.0-only",
        "tree": t["sha"], "parents": parents})
    print("commit:", c["sha"])

    try:
        req(API + "/git/refs", {"ref": "refs/heads/main", "sha": c["sha"]})
        print("已创建 refs/heads/main")
    except urllib.error.HTTPError as e:
        if e.code in (422, 409):  # ref 已存在
            r = urllib.request.Request(API + "/git/refs/heads/main",
                                       data=json.dumps({"sha": c["sha"],
                                                        "force": True}).encode(),
                                       method="PATCH")
            r.add_header("Authorization", "Bearer " + TOKEN)
            r.add_header("Accept", "application/vnd.github+json")
            urllib.request.urlopen(r, timeout=60)
            print("已更新 refs/heads/main")
        else:
            print("创建 ref 失败，HTTP %s" % e.code)
            print(e.read().decode("utf-8", "replace")[:400])
            raise

    info = req("https://api.github.com/repos/%s/%s" % (OWNER, REPO))
    print("\n仓库: %s" % info["html_url"])
    print("默认分支: %s | 大小: %d KB" % (info.get("default_branch"), info.get("size", 0)))


if __name__ == "__main__":
    main()
