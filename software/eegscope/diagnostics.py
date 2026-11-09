"""64 通道调试的共用逻辑：扫描 / 校准 / 电极接触判定 / 摸电极识板 / 写 config。

命令行脚本（scripts/detect_boards.py、scripts/identify_boards.py）和图形向导
（eegscope/wizard.py）共用这一套，避免两处各写一份、改了这处忘了那处。
"""
from __future__ import annotations

import os
import re
import time

import numpy as np

from .sources.openbci import OpenBCISource
from .sources.ports import detect_stm32_ports

FULL_SCALE_UV = 187500.0            # gain 24x 满量程 ±187.5 mV
RAILED_RMS_UV = 100_000.0           # rms 超过这个 = 电极悬空「打满」
FLAT_RMS_UV = 1.0                   # rms 低于这个 = 通道没流「平线」
TOUCH_RMS_UV = 30_000.0             # 识板时：捏住电极后 rms 掉到这个以下算「有响应」

# 每块板挑一个容易找到的代表电极（ISC 核心，额/颞/颞/枕各一个）
REP_ELECTRODES = ["F3", "T7", "T8", "O1"]


def scan_ports():
    """返回 STM32 串口的 ListPortInfo 列表（跨平台）。"""
    return detect_stm32_ports()


def calibrate_board(port: str, seconds: float = 2.0):
    """对一块板握手 + 读数据。返回 (16, n) µV；失败返回 None。"""
    src = OpenBCISource(port, n_channels=16)
    try:
        src.start()
        time.sleep(0.8)  # 等握手完成 + 开始流
        data = src.read(max(1, int(seconds * src.sample_rate)))
        src.stop()
        if data.shape[1] <= 1:
            return None  # 只拿到兜底 1 样本 = 实际没流
        return data
    except Exception:
        try:
            src.stop()
        except Exception:
            pass
        return None


def channel_states(data):
    """逐通道判定状态。返回 list[dict]（ch/rms/peak/state），state ∈ {平线, 打满, OK}。"""
    data = np.asarray(data, dtype=np.float64)
    states = []
    for i in range(data.shape[0]):
        x = data[i]
        rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
        peak = float(np.abs(x).max()) if x.size else 0.0
        if rms < FLAT_RMS_UV:
            state = "平线"
        elif rms > RAILED_RMS_UV:
            state = "打满"
        else:
            state = "OK"
        states.append({"ch": i + 1, "rms": rms, "peak": peak, "state": state})
    return states


def check_all_boards(seconds: float = 2.0):
    """扫描 + 逐板校准，返回结构化结果。

    返回 dict:
      {"n_found": int, "boards": [
         {"port", "ok": bool, "error": str|None,
          "streaming": int, "n_ok": int, "channels": [states]},
         ...]}
    """
    ports = detect_stm32_ports()
    boards = []
    for p in ports:
        data = calibrate_board(p.device, seconds)
        if data is None:
            boards.append({"port": p.device, "ok": False,
                           "error": "握手失败 / 没数据", "streaming": 0,
                           "n_ok": 0, "channels": []})
            continue
        states = channel_states(data)
        streaming = sum(1 for s in states if s["state"] != "平线")
        n_ok = sum(1 for s in states if s["state"] == "OK")
        boards.append({"port": p.device, "ok": True, "error": None,
                       "streaming": streaming, "n_ok": n_ok,
                       "channels": states})
    return {"n_found": len(ports), "boards": boards}


def _channel_rms(data):
    data = np.asarray(data, dtype=np.float64)
    if data.ndim == 1:
        data = data[None, :]
    return (np.sqrt(np.mean(data * data, axis=1))
            if data.shape[1] else np.zeros(data.shape[0]))


def identify_boards(channel_names=None, touch_cb=None, progress_cb=None,
                    per_electrode_timeout: float = 20.0):
    """摸电极识板：确定 4 个串口分别对应哪块物理板。

    channel_names：config64.yaml 的 64 通道名（用来算每个代表电极在哪块板）。
    touch_cb(name, board)：在每个电极开始时回调，GUI 用它显示「请捏住板X的F3」。
    progress_cb(board, device)：每认出一个电极后回调，便于打印中间结果。
    返回 {"ordered": [板1..板4 的设备名，识别不到的为 None],
          "mapping": {板号: 设备名}}。全程阻塞，务必放后台线程跑。
    """
    ports = detect_stm32_ports()
    names = list(channel_names) if channel_names else [f"ch{i + 1}" for i in range(64)]

    reps = []  # (电极名, 板内本地通道 1-based, 板号 1-based)
    for want in REP_ELECTRODES:
        try:
            gidx = names.index(want)
        except ValueError:
            continue
        reps.append((want, (gidx % 16) + 1, (gidx // 16) + 1))

    mapping = {}  # 板号 -> 串口下标
    if not ports or len(ports) < 4:
        return {"ordered": [None] * 4, "mapping": mapping, "error": f"只找到 {len(ports)} 块板"}

    # 打开全部板，读基线
    srcs = [OpenBCISource(p.device, n_channels=16) for p in ports]
    for s in srcs:
        s.start()
    time.sleep(1.5)
    base = []
    for s in srcs:
        d = s.read(250)
        base.append(_channel_rms(d) if d.shape[1] else np.zeros(16))

    try:
        for name, local_ch, board in reps:
            if touch_cb:
                touch_cb(name, board)
            found = None
            t0 = time.time()
            while time.time() - t0 < per_electrode_timeout:
                for bi in range(len(srcs)):
                    if bi in mapping.values():
                        continue  # 已经认过的板，跳过（防止 F3/T7 同通道误判）
                    if base[bi][local_ch - 1] <= 60000:
                        continue  # 该通道本来不是浮空态，跳过
                    hit = 0
                    for _ in range(4):  # 连续 4 次(~0.6s)都掉下来才算真触摸
                        d = srcs[bi].read(15)
                        if d.shape[1] < 4:
                            break
                        if _channel_rms(d)[local_ch - 1] < TOUCH_RMS_UV:
                            hit += 1
                    if hit == 4:
                        found = bi
                        break
                if found is not None:
                    break
                time.sleep(0.1)
            if found is not None:
                mapping[board] = found
                if progress_cb:
                    progress_cb(board, ports[found].device)
            time.sleep(0.4)
    finally:
        for s in srcs:
            try:
                s.stop()
            except Exception:
                pass

    ordered = [ports[mapping[b]].device if b in mapping else None for b in range(1, 5)]
    return {"ordered": ordered, "mapping": {b: ports[i].device for b, i in mapping.items()},
            "error": None}


def write_ports_to_config(ports, config_path: str):
    """把 4 个串口名按板1~板4顺序写进 config64.yaml 的 ports:。

    返回 (ok, msg)。用逐行替换，保留原文件注释和格式。
    """
    if not os.path.isfile(config_path):
        return False, f"找不到配置文件：{config_path}"
    with open(config_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    out = []
    done = False
    for line in lines:
        if not done and "AUTO" in line and line.lstrip().startswith("-"):
            indent = line[: len(line) - len(line.lstrip())]
            for i, p in enumerate(ports, 1):
                out.append(f'{indent}- "{p}"   # 板{i}\n')
            done = True
        else:
            out.append(line)
    if not done:
        return False, "没找到 ports: 里的 AUTO（config 结构变了？）"
    with open(config_path, "w", encoding="utf-8") as f:
        f.writelines(out)
    return True, f"已把 4 个串口按板序写进 {os.path.basename(config_path)}"
