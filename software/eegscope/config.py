"""读取并校验 config.yaml。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import yaml

DEFAULT_CHANNELS = [
    "Fp1", "Fp2", "F3", "F4", "F7", "F8", "Fz", "Cz",
    "C3", "C4", "T7", "T8", "Pz", "O1", "O2", "Oz",
]

# 国际 10-20 扩展（10-10）布局，按用户实际脑电帽（63 个信号位：无 AFz；CPz 位置是 REF 参考不算信号；含耳下枕底 CB1/CB2）。
# 注意：板子 4×16=64 通道，帽 63 信号位 → 第 64 通道空（config64.yaml 用 NC 占位）。
# channels 列表顺序必须匹配「电极物理接到 4 块板 64 个通道」的顺序。
DEFAULT_CHANNELS_64 = [
    # 额极
    "Fp1", "Fpz", "Fp2",
    # 额前
    "AF7", "AF3", "AF4", "AF8",
    # 额
    "F7", "F5", "F3", "F1", "Fz", "F2", "F4", "F6", "F8",
    # 额颞
    "FT7", "FC5", "FC3", "FC1", "FCz", "FC2", "FC4", "FC6", "FT8",
    # 颞 / 中央
    "T7", "C5", "C3", "C1", "Cz", "C2", "C4", "C6", "T8",
    # 颞顶
    "TP7", "CP5", "CP3", "CP1", "CP2", "CP4", "CP6", "TP8",
    # 顶
    "P7", "P5", "P3", "P1", "Pz", "P2", "P4", "P6", "P8",
    # 顶枕
    "PO7", "PO5", "PO3", "POz", "PO4", "PO6", "PO8",
    # 枕 + 耳下枕底
    "O1", "Oz", "O2", "CB1", "CB2",
]


@dataclass
class SyntheticConfig:
    seed: int = 42
    alpha_level: float = 0.25
    blink_rate: float = 0.25
    alpha_uv: float = 30.0
    noise_uv: float = 8.0
    mains_uv: float = 12.0
    harmonic_uv: float = 4.0
    drift_uv: float = 20.0


@dataclass
class OpenBCIConfig:
    port: str = ""
    ports: List[str] = field(default_factory=list)  # 多板：多个串口（4 × 16ch = 64ch）；单板仍用 port
    baudrate: int = 115200
    n_channels: int = 0       # 每块板通道数（8 或 16）；0 = 自动取 channels 列表长度 / 板数
    gain: float = 24.0
    scale_uv: float = 0.0     # 0 = 按 gain 自动算；>0 = 直接覆盖 µV/count
    gain_code: int = -1       # 增益指令参数（0=1x..6=24x）；-1=不发送增益指令（保持板子默认）


@dataclass
class SourceConfig:
    type: str = "synthetic"
    synthetic: SyntheticConfig = field(default_factory=SyntheticConfig)
    openbci: OpenBCIConfig = field(default_factory=OpenBCIConfig)


@dataclass
class DSPConfig:
    bandpass_hz: List[float] = field(default_factory=lambda: [0.5, 45.0])
    notch_hz: float = 50.0
    notch_q: float = 30.0


@dataclass
class AnalysisConfig:
    window_seconds: float = 2.0
    bands: Dict[str, List[float]] = field(default_factory=lambda: {
        "delta": [0.5, 4.0],
        "theta": [4.0, 8.0],
        "alpha": [8.0, 13.0],
        "beta": [13.0, 30.0],
        "gamma": [30.0, 45.0],
    })
    faa_channels: List[str] = field(default_factory=lambda: ["F3", "F4"])


@dataclass
class VizConfig:
    window_seconds: float = 5.0
    refresh_ms: int = 50
    channel_spacing_uv: float = 80.0


@dataclass
class AppConfig:
    sample_rate: float = 250.0
    channels: List[str] = field(default_factory=lambda: list(DEFAULT_CHANNELS))
    source: SourceConfig = field(default_factory=SourceConfig)
    dsp: DSPConfig = field(default_factory=DSPConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    viz: VizConfig = field(default_factory=VizConfig)


def load_config(path: str) -> AppConfig:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    cfg = AppConfig()

    if "sample_rate" in raw:
        cfg.sample_rate = float(raw["sample_rate"])
    if "channels" in raw:
        cfg.channels = [str(c) for c in raw["channels"]]

    s = raw.get("source") or {}
    if "type" in s:
        cfg.source.type = str(s["type"])
    syn = s.get("synthetic") or {}
    for k in ("seed", "alpha_level", "blink_rate", "alpha_uv",
              "noise_uv", "mains_uv", "harmonic_uv", "drift_uv"):
        if k in syn:
            if k == "seed":
                setattr(cfg.source.synthetic, k, int(syn[k]))
            else:
                setattr(cfg.source.synthetic, k, float(syn[k]))

    obc = s.get("openbci") or {}
    if "port" in obc:
        cfg.source.openbci.port = str(obc["port"])
    if "ports" in obc:
        cfg.source.openbci.ports = [str(p) for p in (obc["ports"] or [])]
    if "baudrate" in obc:
        cfg.source.openbci.baudrate = int(obc["baudrate"])
    if "n_channels" in obc:
        cfg.source.openbci.n_channels = int(obc["n_channels"])
    if "gain" in obc:
        cfg.source.openbci.gain = float(obc["gain"])
    if "scale_uv" in obc:
        cfg.source.openbci.scale_uv = float(obc["scale_uv"])
    if "gain_code" in obc:
        cfg.source.openbci.gain_code = int(obc["gain_code"])

    d = raw.get("dsp") or {}
    if "bandpass_hz" in d:
        cfg.dsp.bandpass_hz = [float(x) for x in d["bandpass_hz"]]
    if "notch_hz" in d:
        cfg.dsp.notch_hz = float(d["notch_hz"])
    if "notch_q" in d:
        cfg.dsp.notch_q = float(d["notch_q"])

    a = raw.get("analysis") or {}
    if "window_seconds" in a:
        cfg.analysis.window_seconds = float(a["window_seconds"])
    if "bands" in a:
        cfg.analysis.bands = {k: [float(x) for x in v] for k, v in a["bands"].items()}
    if "faa_channels" in a:
        cfg.analysis.faa_channels = [str(c) for c in a["faa_channels"]]

    v = raw.get("viz") or {}
    if "window_seconds" in v:
        cfg.viz.window_seconds = float(v["window_seconds"])
    if "refresh_ms" in v:
        cfg.viz.refresh_ms = int(v["refresh_ms"])
    if "channel_spacing_uv" in v:
        cfg.viz.channel_spacing_uv = float(v["channel_spacing_uv"])

    return cfg
