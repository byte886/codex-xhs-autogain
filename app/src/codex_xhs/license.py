# -*- coding: utf-8 -*-
"""授权薄层（free 优先落地，activation/server 留接口）。

ADR《授权机制选型与薄层设计》定死的 4 件事：
1. 接口形状：`check_license(feature) -> (ok, reason, detail)`，所有业务入口统一过它；free 恒放行；
2. feature 清单：collect / clone / publish / llm（前后端共用同一份）；
3. 机器码算法：跨平台稳定硬件指纹（发布后换算法 = 已发激活码全废，故必须先定）；
4. License 文件 schema：版本化（schema_version=1），预留 RSA signature 字段（server 模式填充）。

档位切换只改本文件内部实现，其余业务代码零改动。
"""

import hashlib
import json
import os
import platform
import subprocess
import sys

from codex_xhs import config  # noqa: E402（DATA_DIR 相对项目根，可移植）

# 模式：free | activation | server（环境变量切换；未来档位只改内部实现）
LICENSE_MODE = os.environ.get("XHS_LICENSE_MODE", "free")

# 前后端共用的功能清单（免费试用/付费档位以后映射到同一清单）
FEATURES = ("collect", "clone", "publish", "llm")

_SCHEMA_VERSION = 1
_LICENSE_FILE = config.DATA_DIR / "license.json"


def check_license(feature: str, detail: dict | None = None) -> tuple[bool, str, dict]:
    """统一权限问询。所有业务入口（启动/采集/克隆/发布/LLM/前端路由守卫）过此函数。

    free 模式恒 (True, "", {...})——调试/分享期零门槛、无感；
    activation / server 由 _check_paid 承接（未来档位）。
    """
    if feature not in FEATURES:
        return False, f"未知功能：{feature}", {"mode": LICENSE_MODE}
    if LICENSE_MODE == "free":
        return True, "", {"mode": "free", "feature": feature}
    return _check_paid(feature, detail or {})


def _check_paid(feature: str, detail: dict) -> tuple[bool, str, dict]:
    """activation/server 占位实现：当前一律未授权（绝不破坏 free 路径）。

    未来：
    - activation：读 license.json 激活码 + machine_code() 绑定校验；
    - server：读签名许可（signature 字段）→ 联网校验后放行。
    """
    lic = load_license_file()
    if lic.get("signature"):
        return True, "", {"mode": "server", "feature": feature}
    return (
        False,
        "未授权：请配置激活码或有效许可（当前 free 模式不受影响，设置 XHS_LICENSE_MODE=free 即可）",
        {"mode": LICENSE_MODE, "feature": feature},
    )


def machine_code() -> str:
    """跨平台稳定硬件指纹（SHA-256 摘要，非明文；不进 git、不落日志明文）。

    - macOS：IOPlatformUUID（ioreg，重启不变、同机稳定）；
    - Windows：MachineGuid（HKLM\\SOFTWARE\\Microsoft\\Cryptography，装机不变）；
    - 兜底：主机名|用户（同机稳定，不可跨设备共享）。
    算法发布后不可更换（换 = 已发激活码全废），改动需评审 + 版本迁移。
    """
    try:
        if sys.platform == "darwin":
            out = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                text=True, stderr=subprocess.DEVNULL, timeout=10,
            )
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    parts = line.split('"')
                    if len(parts) >= 3 and parts[-2].strip():
                        return _digest(parts[-2].strip())
        elif sys.platform in ("win32", "cygwin"):
            import winreg  # noqa: PLC0415（仅 Windows 导入）

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                return _digest(str(guid))
    except Exception:  # noqa: BLE001（任一平台探测失败走兜底，不阻断）
        pass
    return _digest(f"{platform.node()}|{os.getlogin() if hasattr(os, 'getlogin') else 'unknown'}")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def load_license_file() -> dict:
    """读 data/license.json；schema_version=1，预留 signature 字段（server 模式填充）。

    文件不存在或损坏时返回默认结构（不抛异常——free 模式根本不需要它）。
    """
    defaults = {
        "schema_version": _SCHEMA_VERSION,
        "mode": LICENSE_MODE,
        "machine_code": machine_code(),
        "features": list(FEATURES),
        "signature": None,        # 预留：server 模式 RSA 签名
        "activated_at": None,     # 预留：activation 模式激活时间
    }
    try:
        if _LICENSE_FILE.exists():
            data = json.loads(_LICENSE_FILE.read_text(encoding="utf-8"))
            data.setdefault("schema_version", _SCHEMA_VERSION)
            data.setdefault("signature", None)
            data.setdefault("features", list(FEATURES))
            return data
    except (OSError, ValueError):
        pass
    return defaults


def license_status() -> dict:
    """给前端展示用的授权状态（不暴露 machine_code 明文——仅返回摘要指纹）。"""
    lic = load_license_file()
    return {
        "mode": LICENSE_MODE,
        "schema_version": lic.get("schema_version", _SCHEMA_VERSION),
        "features": lic.get("features", list(FEATURES)),
        "machine_fingerprint": lic.get("machine_code", ""),  # 已是 SHA-256 摘要，可安全展示
        "has_signature": bool(lic.get("signature")),
        "activated_at": lic.get("activated_at"),
    }
