"""数据源工厂。真实 ADS1299 串口驱动在此注册接入。"""
from __future__ import annotations

from .base import DataSource
from .synthetic import SyntheticSource

__all__ = ["DataSource", "SyntheticSource", "make_source"]


def make_source(cfg):
    """根据 config 的 `source.type` 构造数据源实例。

    这是后续接入真实硬件的唯一入口：实现一个继承 `DataSource` 的
    `ADS1299Source`（start/stop/read + sample_rate/n_channels），然后在这里
    加一个分支即可，其余管线（DSP/分析/可视化）完全不用改。
    """
    stype = (cfg.source.type or "synthetic").lower()

    if stype == "synthetic":
        s = cfg.source.synthetic
        return SyntheticSource(
            sample_rate=cfg.sample_rate,
            channel_names=cfg.channels,
            seed=s.seed,
            alpha_level=s.alpha_level,
            blink_rate=s.blink_rate,
            alpha_uv=s.alpha_uv,
            noise_uv=s.noise_uv,
            mains_uv=s.mains_uv,
            harmonic_uv=s.harmonic_uv,
            drift_uv=s.drift_uv,
        )

    if stype == "openbci":
        from .openbci import OpenBCISource
        o = cfg.source.openbci
        n_ch = o.n_channels or len(cfg.channels)
        names = cfg.channels if len(cfg.channels) == n_ch else None
        return OpenBCISource(
            port=o.port,
            baudrate=o.baudrate,
            n_channels=n_ch,
            gain=o.gain,
            scale_uv=o.scale_uv if o.scale_uv > 0 else None,
            channel_names=names,
            gain_code=None if o.gain_code < 0 else o.gain_code,
        )

    raise ValueError(f"未知数据源类型: {cfg.source.type!r}（当前支持: synthetic, openbci）")
