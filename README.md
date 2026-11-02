# PonFlasher — AN758x 多机型免拆刷机 / 备份 / 还原工具

为 Airoha AN7581/AN7583 方案的光猫（烽火、贝尔、九联、兆能、H3C、Gemtek 等 12 个机型）
提供网页式"傻瓜"操作：选机型 → 扫描固件 → 一键备份/刷机/还原，全程免拆机。

```
python flasher.py            # 启动后自动打开浏览器（默认 http://127.0.0.1:8799）
python flasher.py --port 9000
```

仅依赖 Python 3.9+ 标准库。烽火机型自动读 MAC / 开 telnet 需要可选依赖
`pip install fh_tool-cli`（不装也可以手动开好 telnet 后使用）。

## 免 Python 环境的 exe 版本

成品在 `dist/AN758x自动刷机工具.exe`（约 14 MB，PyInstaller 单文件，无需安装任何运行时）：

```
dist\AN758x自动刷机工具.exe                双击即用，自动打开浏览器
dist\AN758x自动刷机工具.exe --port 9000           换端口
```

exe 特性：

- **内置 stock2ubi（v0.1.14）**：首次运行自动释放到 exe 旁的 `files/` 目录，
  开箱即可备份/刷引导，**不再需要手动下载这一步**。
- **内置「在线拉取引导」**：页面里点一下，自动从 uboot-an758x Releases 按当前
  机型匹配下载 BL2（firstblock 优先，无则 preloader）+ U-Boot FIP 到 `firmware/`
  目录；GitHub 直连不通时自动经 ghproxy.net / ghfast.top 镜像回退下载。
  *系统固件 sysupgrade.itb 仍需自备*（无官方集中发布渠道，恩山论坛或自行编译）。
- **内置 AN758x 全系引导包（离线可用）**：exe 里打包了 uboot-an758x
  `v2026.07-4` 的全部 23 个资产（10 个机型的 BL2 + U-Boot FIP，含 INDEX.json 校验清单）。
  选好机型点「**使用内置引导包**」即可离线就位，无需联网；
  想追新可点「**联网更新**」强制从 Releases 拉最新版。
- XR1710G 走专属 chainloader 路线（chainloader 已内置 + 固件可在线下载，见上文专节）。
- H3C HM2004-DU 未内置引导包：上游源码有 defconfig，但 Releases 未发布预编译包
  （CI 只传到 Actions artifacts），页面打「引导包需自备」标签并给出获取方式。
- **数据目录跟着 exe 走**：`backups/`、`files/`、`firmware/` 都在 exe 所在目录，
  不写注册表、不留临时文件，整个目录拷 U 盘即可随身携带。
- 首次运行 Windows Defender / 部分杀软可能因「PyInstaller 单文件 + 无数字签名」
  弹拦截提示，选「允许运行」即可；介意的话可自行用源码重建。
- 想分发给朋友：把 `AN758x自动刷机工具.exe` 单发即可，其余什么都不用装。

自己重建 exe（源码有改动时）：

```
pip install pyinstaller
pyinstaller --onefile --name "AN758x自动刷机工具" --clean --noconfirm ^
    --add-data "files/an758x-stock2ubi;bundled" ^
    --add-data "bundled_fw;bundled_fw" flasher.py
```

`bundled_fw/` 是内置的 AN758x 引导包目录（含 `INDEX.json` 清单），
刷新内置版本时替换该目录里的文件并更新 `INDEX.json` 的 `tag` 即可。

产物在 `dist/AN758x自动刷机工具.exe`（旧名 PonFlasher.exe），`PonFlasher.spec` 为构建配置。

## 机型支持表

| 机型 | 定位 | 板级数据 | 自动转换 |
|---|---|---|---|
| 烽火 HG5585F（联通 CU / 电信 CT） | XGPON 万兆 | factory | ✅ 3M→1M |
| 烽火 HG5382A | XGPON | factory | ✅ |
| H3C HM2004-DU | XGPON | factory | 直写 |
| 九联 UNG00A、兆能 ZN504XG-D / ZN515XG-D | 10G PON | reservearea | 直写 |
| 贝尔 XG-040G-MD / -TF / -MF | 10G PON | bosa + ri | 直写 |
| Gemtek XG2010G | 10G PON | dsd | 直写 |
| **Gemtek XR1710G** | 双万兆 WiFi 路由 | DSD（恢复页自动保） | **chainloader 路线** |

## XR1710G 专属：chainloader 刷机路线

XR1710G（Brightspeed 双万兆）不走 stock2ubi 路线，走的是社区
[naoki66/XR1710G-http-uboot](https://github.com/naoki66/XR1710G-http-uboot)
（fork 自 YYH2913/http-uboot）的链式加载方案。选择该机型后页面自动切换为专属流程：

**① 首次安装（原厂固件 → chainloader，需要拆机接 TTL）**

1. 页面点「在线下载」取 chainloader（exe 已内置最新 `*-flash-slot.bin`，离线可用）；
2. 点「生成 TTL 命令 + 启动 TFTP」——工具自动探测本机 IP、启动 TFTP 服务
   （端口 69），给出逐行粘贴的命令清单（含旧环境修正版）；
3. 电脑网卡设为同网段静态 IP，串口终端（115200）上电打断进原厂 U-Boot，
   逐行粘贴命令：`tftpboot` → `flash erase 0x600000 0x100000` →
   `flash write` → `reset`。当前双入口镜像**无需改 bootcmd**。

**② 刷固件（全自动）**

设备进恢复模式（10G 口 LED 闪烁时长按 reset，状态灯变跑马灯；电脑网卡保持
DHCP，恢复页地址 `192.168.255.1`）。页面点「开始」后工具自动完成：

- **自动判定 UBI 布局**：解析 sysupgrade.itb 内嵌设备树的 ubi 分区大小，
  自动选 2.0 / 1.5 / 1.0——这是该路线最大的坑（选错会导致内核能起但 Linux
  卡在等 `/dev/fit0`），工具替你排掉；解析不了才要求手选；
- 上传固件（POST `/upload/firmware?layout=…`）、轮询 `/status` 进度到完成、
  自动 reboot。出厂 EEPROM 与 MAC 由恢复页从厂商 DSD 区自动恢复，无需手动备份。

固件同样可一键在线下载（naoki66/ImmortalWrt-for-Gemtek-XR1710G Releases，
约 46 MB，直连失败自动走镜像）。

> 注意：pbs05 路线的 BL2+FIP 与本路线的 chainloader 是两套引导体系，
> **不可交叉使用**；本工具按机型自动隔离，选错路线的文件会被拦下。

## 使用前准备

1. **an758x-stock2ubi**：exe 版已内置，开箱即用；源码运行需从
   <https://github.com/pbs05/an758x-stock2ubi> 的 Releases 下载放入 `files/`。
2. **BL2 + U-Boot FIP**：页面点「在线拉取引导」自动下载；或手动从
   <https://github.com/pbs05/uboot-an758x> Releases 下载对应机型的
   `*-firstblock.bin`（或 `*-preloader.bin`）和 `*-bl31-u-boot.fip`。
3. **系统固件**：PonWrt（<https://github.com/pbs05/ponwrt>，需自行编译）或社区
   编译的 `*-sysupgrade.itb`，profile 必须与机型一致。

## 两种典型流程

### 一键刷机（full）

网线插光猫**千兆口**（2.5G 口在 U-Boot 下无驱动）→ 页面选机型/模式 → 开始。
工具会依次：

1. 探测 stock2ubi；未运行则 telnet 登录（密码按机型规则推导，可手工覆盖）
2. 光猫从本机 wget 并启动 stock2ubi（:3333）
3. 逐分区备份到 `backups/<机型>_<时间戳>/`（含 SHA256 清单 manifest.json）
4. 烽火机型：解析 factory 分区（fhdata 卷）→ 生成 1M 板级数据镜像
5. 刷入 BL2 + U-Boot（需确认），设备自动重启
6. 等 U-Boot 恢复页（192.168.0.1）就绪并校验机型
7. 重建 UBI → 刷板级数据 → 刷 U-Boot → 刷系统 → 启动（每步均有确认弹窗）

### 从备份还原（restore）

进入 U-Boot 恢复模式后选择备份目录，工具把 manifest 里各分区按物理偏移拼成
整片 NAND 镜像，走 `flash-all` 整片写入并校验（约 10–30 分钟）。

## ⚠ 必读风险

- **factory 分区（PON/WiFi 校准、MAC、SN）是全机唯一不可再生数据**。备份完成后
  务必把 `backups/` 目录另行复制一份。
- 板级数据必须用**自己这台猫**的备份生成；刷入他人身份数据 PON 无法注册，也
  不符合合规要求。
- 刷 BL2 与整片还原期间断电 = 变砖，需编程器/TTL 救砖。
- 刷机后将失去 TR-069 远程管理，运营商报修可能要求恢复原厂。
- 使用的 BL2/FIP/固件 profile 必须与机型一致（工具会按文件名校验并拦下明显错配）。

## 文件结构

```
flasher.py     主程序：Web 服务 + 流程引擎 + stock2ubi/U-Boot/telnet 客户端
chainroute.py  XR1710G chainloader 路线：TFTP 服务 / FIT 布局解析 / 恢复页客户端
test_chain_mock.py  XR1710G 路线离线端到端自测
devices.py     机型档案（新增机型只需在此加一条）
ui.py          前端单页
fhfactory.py   烽火 factory 3M→1M 转换（fiberhome-factory 的 Python 移植，GPL-2.0）
test_mock.py   离线端到端自测（模拟两端 API）：python test_mock.py
dist/          打包产物 AN758x自动刷机工具.exe（免 Python 环境，内置 stock2ubi + AN758x 全系引导包）
bundled_fw/    内置引导包（uboot-an758x v2026.07-4 全 23 个资产 + XR1710G chainloader）
files/         an758x-stock2ubi 存放处
firmware/      在线/内置引导包的落地目录
backups/       备份输出
```

## 许可

- 本仓库整体 **AGPL-3.0-only**（见 `LICENSE`）。
- `fhfactory.py` 为 [fiberhome-factory](https://github.com/22p/fiberhome-factory)
  （作者 22p）的 Python 移植，按上游 **GPL-2.0-only** 分发。
- `bundled_fw/` 内的引导包二进制来自
  [pbs05/uboot-an758x](https://github.com/pbs05/uboot-an758x) Releases，
  `files/an758x-stock2ubi` 来自
  [pbs05/an758x-stock2ubi](https://github.com/pbs05/an758x-stock2ubi) Releases，
  均遵循各自上游许可，本工具仅作分发打包、未作修改。

## 已知限制

- 贝尔机型的 `su user_ftp` 提权路径（需先开 FTP）未自动化，请先自行开好 telnet。
- U-Boot API 的响应字段（`upload_id` / `state` / `code`）按公开源码实现，做了宽松
  解析；若上游改版式，日志会透传原始响应便于排查。
- `flash-board-data` 的 target 名称以 `/api/device` 报告的 board_data 列表为准。

## 致谢

本工具站在以下开源项目的肩膀上（请去点 Star）：
[pbs05/an758x-stock2ubi](https://github.com/pbs05/an758x-stock2ubi) ·
[pbs05/uboot-an758x](https://github.com/pbs05/uboot-an758x) ·
[pbs05/ponwrt](https://github.com/pbs05/ponwrt) ·
[22p/fiberhome-factory](https://github.com/22p/fiberhome-factory)（fhfactory.py 移植来源）
