#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机型档案：所有 AN758x 光猫的差异集中在这里。

数据来源：
  - pbs05/uboot-an758x 支持表（BL2 / FIP 文件命名、存储类型）
  - pbs05/ponwrt 支持表（profile、板级数据分区类型）
  - 恩山 thread-8494648 Q&A（telnet/su 密码规则）
  - xe5700/xg-040g-md-auto-flash-uboot 的机型注册表（贝尔账号）

新增机型只需要在 DEVICES 里加一条，UI / 流程引擎自动跟进。
"""

from dataclasses import dataclass, field


@dataclass
class TelnetSpec:
    """telnet 登录与提权规则。

    password_from_mac=True 时，实际密码 = password_prefix + MAC 后 6 位大写。
    密码错误时按 fallbacks 里的固定值再试（最多消耗 2 次认证机会）。
    """
    user: str = ""
    password: str = ""                 # 固定密码（静态机型）
    password_prefix: str = ""          # 前缀 + MAC 后 6 位（动态机型）
    password_from_mac: bool = False
    password_fallbacks: tuple = ()     # 备选固定密码
    su_user: str = "root"
    su_password: str = ""
    su_password_prefix: str = ""
    su_password_from_mac: bool = False
    port: int = 23


@dataclass
class Device:
    key: str                    # 机型短名（UI / 配置用）
    name: str                   # 界面显示名
    vendor: str
    uboot_target: str           # uboot-an758x 的 target，决定 BL2/FIP 文件名
    ponwrt_profile: str         # ponwrt 的 profile，决定 sysupgrade 文件名
    board_data: str             # 板级数据卷类型：factory / dsd / bosa+ri / reservearea
    board_convert: str = "none" # 板级数据转换：none=直接写 / fiberhome=fhfactory 3M→1M
    stock_ip: str = "192.168.1.1"
    uboot_ip: str = "192.168.0.1"
    telnet: TelnetSpec = field(default_factory=TelnetSpec)
    telnet_enable: str = "manual"    # fhtool / manual（用户自行开好）
    fhtool_port: int = 8080
    note: str = ""
    # 上游 uboot-an758x 的 GitHub Releases 是否提供预编译 BL2/FIP。
    # 源码里 12 个 defconfig 全有，但 Releases 只发了 10 个机型；
    # xr1710g / hm2004-du 仅存在于 CI 构建的 Actions artifacts（需登录下载），
    # 故标为 False：工具不硬拉，改为提示用户自备或自行编译。
    bootloader_release: bool = True

    # --- 文件名匹配模式（glob，{} 里填 target / profile）---
    @property
    def bl2_patterns(self) -> tuple:
        # an7581-fiberhome-hg5585f-cu-firstblock.bin / *-preloader.bin
        return ("*{0}*firstblock*.bin".format(self.uboot_target),
                "*{0}*preloader*.bin".format(self.uboot_target))

    @property
    def fip_patterns(self) -> tuple:
        return ("*{0}*bl31*uboot*.fip".format(self.uboot_target),
                "*{0}*.fip".format(self.uboot_target),
                "*{0}*bl31*.bin".format(self.uboot_target))

    @property
    def sysupgrade_patterns(self) -> tuple:
        return ("*{0}*sysupgrade*.itb".format(self.ponwrt_profile),
                "*{0}*sysupgrade*.bin".format(self.ponwrt_profile))

    def telnet_password_for(self, mac_suffix: str) -> str:
        if self.telnet.password_from_mac:
            return self.telnet.password_prefix + (mac_suffix or "")
        return self.telnet.password

    def su_password_for(self, mac_suffix: str) -> str:
        if self.telnet.su_password_from_mac:
            return self.telnet.su_password_prefix + (mac_suffix or "")
        return self.telnet.su_password

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "vendor": self.vendor,
            "board_data": self.board_data,
            "board_convert": self.board_convert,
            "stock_ip": self.stock_ip,
            "uboot_ip": self.uboot_ip,
            "telnet_enable": self.telnet_enable,
            "note": self.note,
            "bl2_patterns": list(self.bl2_patterns),
            "fip_patterns": list(self.fip_patterns),
            "sysupgrade_patterns": list(self.sysupgrade_patterns),
            "bootloader_release": self.bootloader_release,
            "uboot_defconfig": self.uboot_defconfig,
        }

    @property
    def uboot_defconfig(self) -> str:
        """上游 uboot-an758x 里对应的 defconfig 名，用于自备编译时参考。"""
        soc = "an7583" if self.uboot_target.endswith("-mf") else "an7581"
        # "Nokia / Bell" -> nokia ; "ZNXT" -> znxt ; "UnionMan" -> unionman
        vendor = self.vendor.split("/")[0].strip().lower()
        return "%s_%s_%s_defconfig" % (soc, vendor, self.uboot_target)


def mac_suffix(mac: str) -> str:
    """取 MAC 后 6 位大写；接受 3 种写法。"""
    digits = "".join(ch for ch in (mac or "") if ch.isalnum()).upper()
    return digits[-6:] if len(digits) >= 6 else ""


def normalize_mac(mac: str) -> str:
    digits = "".join(ch for ch in (mac or "") if ch.isalnum()).upper()
    return digits


DEVICES = [
    Device(
        key="hg5585f-cu",
        name="烽火 HG5585F（联通版 CU）",
        vendor="FiberHome",
        uboot_target="hg5585f-cu",
        ponwrt_profile="fiberhome_hg5585f-cu",
        board_data="factory",
        board_convert="fiberhome",
        telnet=TelnetSpec(
            user="admin",
            password_prefix="Fh@", password_from_mac=True,
            password_fallbacks=("Fh@",),
            su_user="root",
            su_password_prefix="hg2x0", su_password_from_mac=True,
        ),
        telnet_enable="fhtool",
        note="XGPON 万兆（10G/2.5G），2.5G 网口，WiFi 6。联通定制。",
    ),
    Device(
        key="hg5585f-ct",
        name="烽火 HG5585F（电信版 CT）",
        vendor="FiberHome",
        uboot_target="hg5585f-ct",
        ponwrt_profile="fiberhome_hg5585f-ct",
        board_data="factory",
        board_convert="fiberhome",
        telnet=TelnetSpec(
            user="telnetadmin",
            password="FH-nE7jA%5m",          # 固件证据支持的固定值
            password_fallbacks=("nE7jA%5m",),
            su_user="root",
            su_password_prefix="Fh@", su_password_from_mac=True,
        ),
        telnet_enable="fhtool",
        note="电信定制，2.5G 口 PHY 与联通版不同（MaxLinear）。",
    ),
    Device(
        key="hg5382a",
        name="烽火 HG5382A",
        vendor="FiberHome",
        uboot_target="hg5382a",
        ponwrt_profile="fiberhome_hg5382a",
        board_data="factory",
        board_convert="fiberhome",
        telnet=TelnetSpec(
            user="admin",
            password_prefix="Fh@", password_from_mac=True,
            su_user="root",
            su_password_prefix="hg2x0", su_password_from_mac=True,
        ),
        telnet_enable="fhtool",
        note="千兆 XGPON 入门款。",
    ),
    Device(
        key="ung00a",
        name="九联 UNG00A",
        vendor="UnionMan",
        uboot_target="ung00a",
        ponwrt_profile="unionman_ung00a",
        board_data="reservearea",
        board_convert="none",
        telnet=TelnetSpec(
            user="admin",
            password="admin",
            su_user="root",
            su_password="admin",
        ),
        telnet_enable="manual",
        note="板级数据在 reservearea 卷，备份后直接写回，无需转换。",
    ),
    Device(
        key="zn504xg-d",
        name="兆能 ZN504XG-D",
        vendor="ZNXT",
        uboot_target="zn504xg-d",
        ponwrt_profile="znxt_zn504xg-d",
        board_data="reservearea",
        board_convert="none",
        telnet=TelnetSpec(
            user="admin",
            password="admin",
            su_user="root",
            su_password="admin",
        ),
        telnet_enable="manual",
        note="约 30 元的入门 10G PON 猫，刷机热门机型。",
    ),
    Device(
        key="zn515xg-d",
        name="兆能 ZN515XG-D",
        vendor="ZNXT",
        uboot_target="zn515xg-d",
        ponwrt_profile="znxt_zn515xg-d",
        board_data="reservearea",
        board_convert="none",
        telnet=TelnetSpec(
            user="admin",
            password="admin",
            su_user="root",
            su_password="admin",
        ),
        telnet_enable="manual",
        note="带 WiFi 6 的 ZN504 升级款。",
    ),
    Device(
        key="xg-040g-md",
        name="诺基亚贝尔 XG-040G-MD",
        vendor="Nokia / Bell",
        uboot_target="xg-040g-md",
        ponwrt_profile="nokia_xg-040g-md-ubi",
        board_data="bosa+ri",
        board_convert="none",
        telnet=TelnetSpec(
            user="user",
            password="j3ba73@8",
            su_user="root",
            su_password="j3ba73@8",
        ),
        telnet_enable="manual",
        note="板级数据为 bosa 与 ri 两个卷。移动版超管 CMCCAdmin / aDm8H%MdA。",
    ),
    Device(
        key="xg-040g-tf",
        name="诺基亚贝尔 XG-040G-TF",
        vendor="Nokia / Bell",
        uboot_target="xg-040g-tf",
        ponwrt_profile="nokia_xg-040g-tf-ubi",
        board_data="bosa+ri",
        board_convert="none",
        telnet=TelnetSpec(
            user="user",
            password="j3ba73@8",
            su_user="root",
            su_password="j3ba73@8",
        ),
        telnet_enable="manual",
        note="XG-040G-MD 的姊妹型号，板级数据同为 bosa + ri。",
    ),
    Device(
        key="xg2010g",
        name="Gemtek XG2010G",
        vendor="Gemtek",
        uboot_target="xg2010g",
        ponwrt_profile="gemtek_xg2010g",
        board_data="dsd",
        board_convert="none",
        telnet=TelnetSpec(
            user="admin",
            password="admin",
            su_user="root",
            su_password="admin",
        ),
        telnet_enable="manual",
        note="板级数据在 dsd 分区。",
    ),
    Device(
        key="xr1710g",
        name="Gemtek XR1710G",
        vendor="Gemtek",
        uboot_target="xr1710g",
        ponwrt_profile="gemtek_xr1710g",
        board_data="dsd",
        board_convert="none",
        telnet=TelnetSpec(
            user="admin",
            password="admin",
            su_user="root",
            su_password="admin",
        ),
        telnet_enable="manual",
        note="XG2010G 姊妹型号。上游有 defconfig 但 Releases 未发布预编译包，BL2/FIP 需自备。\n"
             "替代路线：社区 naoki66/XR1710G-http-uboot 发布的是 chainloader.itb（走原厂 web "
             "升级的链式加载），与本工具的 BL2+FIP 路径不同，两者不可混用。",
        bootloader_release=False,
    ),
    Device(
        key="hm2004-du",
        name="H3C HM2004-DU",
        vendor="H3C",
        uboot_target="hm2004-du",
        ponwrt_profile="h3c_hm2004-du",
        board_data="factory",
        board_convert="none",   # factory 卷直写；若备份为 3M 烽火格式可切换 fiberhome 转换
        telnet=TelnetSpec(
            user="admin",
            password="admin",
            su_user="root",
            su_password="admin",
        ),
        telnet_enable="manual",
        note="板级数据在 factory 卷，一般可直接写回。上游有 defconfig 但 Releases 未发布预编译包，BL2/FIP 需自备。",
        bootloader_release=False,
    ),
    Device(
        key="xg-040g-mf",
        name="诺基亚贝尔 XG-040G-MF（AN7583）",
        vendor="Nokia / Bell",
        uboot_target="xg-040g-mf",
        ponwrt_profile="nokia_xg-040g-mf",
        board_data="bosa+ri",
        board_convert="none",
        telnet=TelnetSpec(
            user="user",
            password="j3ba73@8",
            su_user="root",
            su_password="j3ba73@8",
        ),
        telnet_enable="manual",
        note="AN7583 平台，双 SFP。profile 含 UBI 与非 UBI 两个变体。",
    ),
]

BY_KEY = {d.key: d for d in DEVICES}


def get(key: str) -> Device:
    return BY_KEY.get(key) or BY_KEY["hg5585f-cu"]


def all_dicts() -> list:
    return [d.as_dict() for d in DEVICES]
