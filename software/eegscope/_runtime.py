"""Frozen（PyInstaller 打包）感知的路径助手。

开发模式（python launcher.py / main.py）与打包后的单文件 exe 共用这套路径逻辑，
保证「录制的数据、要改的 config」在打包后依然能写到用户看得见的地方。

三种目录，含义不同：
- app_root()     —— 可写根目录。frozen = exe 所在目录（用户解压下载的文件夹）；
                     dev = software/ 目录。录音、拷贝出来的 config 放这里。
- resource_root()—— 只读资源根目录。frozen = sys._MEIPASS（PyInstaller 解压的临时目录）；
                     dev = software/ 目录。打包进去的 config / drivers / scripts 从这里读。
- resource_path(rel) —— 读资源：frozen 时优先 app_root()（用户可能改过的 config），
                     兜底 resource_root()（打包进去的原版）。

frozen 判定：PyInstaller 运行时会设 sys.frozen=True 并给 sys._MEIPASS。
"""
from __future__ import annotations

import os
import shutil
import sys

IS_FROZEN = bool(getattr(sys, "frozen", False))

# 本模块在 software/eegscope/ 下，向上两级 = software/ 目录（dev 模式的根）
_DEV_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 打包时随 exe 一起分发、且允许用户原地改名的配置文件（首次运行拷到 exe 目录）
_USER_CONFIGS = ("config.yaml", "config64.yaml", "config_remote.yaml")


def app_root() -> str:
    """可写根目录：frozen = exe 所在目录；dev = software/ 目录。"""
    if IS_FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))
    return _DEV_ROOT


def resource_root() -> str:
    """只读资源根目录：frozen = _MEIPASS；dev = software/ 目录。"""
    if IS_FROZEN:
        return getattr(sys, "_MEIPASS", _DEV_ROOT)
    return _DEV_ROOT


def resource_path(rel: str) -> str:
    """读资源绝对路径：frozen 优先 app_root()（用户改过的），兜底 resource_root()。"""
    user = os.path.join(app_root(), rel)
    if os.path.exists(user):
        return user
    bundled = os.path.join(resource_root(), rel)
    if os.path.exists(bundled):
        return bundled
    return user  # 都不存在时给 user 路径，让调用方抛友好错误


def data_dir(name: str) -> str:
    """可写数据目录（如 recordings）：frozen = exe 目录下；dev = software/ 目录下。"""
    return os.path.join(app_root(), name)


def drivers_dirs() -> list:
    """候选驱动目录（按优先级）：exe 目录/drivers、打包资源/drivers。"""
    dirs = [os.path.join(app_root(), "drivers")]
    bundled = os.path.join(resource_root(), "drivers")
    if bundled not in dirs:
        dirs.append(bundled)
    return dirs


def ensure_user_configs() -> None:
    """frozen 首次运行时，把打包进去的 config 拷到 exe 目录，方便用户改 ports。

    dev 模式无需做（config 本来就在 software/ 目录）。只拷贝缺失的文件，
    不覆盖用户已改过的 config（升级 exe 时保留用户配置）。
    """
    if not IS_FROZEN:
        return
    for name in _USER_CONFIGS:
        src = os.path.join(resource_root(), name)
        dst = os.path.join(app_root(), name)
        if os.path.exists(src) and not os.path.exists(dst):
            try:
                shutil.copy2(src, dst)
            except OSError:
                pass  # 拷贝失败不致命，resource_path 会兜底读 _MEIPASS 里的原版
