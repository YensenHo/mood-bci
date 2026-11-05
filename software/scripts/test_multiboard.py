"""MultiBoardSource 测试：验证 4 板合并逻辑 + 64 通道布局（无硬件，mock 板子）。

用法（隔离环境）：
    env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \\
        .venv/bin/python scripts/test_multiboard.py
"""
import os
import sys
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from eegscope.config import DEFAULT_CHANNELS_64  # noqa: E402
from eegscope.sources.multiboard import MultiBoardSource  # noqa: E402


class _MockBoard:
    """模拟单块 OpenBCISource：read 返回填了板编号的 (16, n)。"""

    def __init__(self, port, n_channels=16, **_kw):
        self.port = port
        self.n_ch = n_channels
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    @property
    def sample_rate(self):
        return 125.0

    def read(self, n):
        return np.full((self.n_ch, n), float(self.port), dtype=np.float64)


def test_layout():
    assert len(DEFAULT_CHANNELS_64) == 64, f"布局 {len(DEFAULT_CHANNELS_64)} 个，应为 64"
    assert len(set(DEFAULT_CHANNELS_64)) == 64, "布局有重复通道名"
    print(f"[1] DEFAULT_CHANNELS_64 = 64 通道，无重复 ✓")


def test_merge():
    with mock.patch("eegscope.sources.multiboard.OpenBCISource", _MockBoard):
        src = MultiBoardSource(ports=[0, 1, 2, 3], n_channels_per_board=16,
                               channel_names=DEFAULT_CHANNELS_64)
        assert src.n_channels == 64
        assert src.sample_rate == 125.0
        assert src.channel_names == DEFAULT_CHANNELS_64

        out = src.read(100)
        assert out.shape == (64, 100), f"read 返回 {out.shape}，应为 (64, 100)"
        for b in range(4):
            block = out[b * 16:(b + 1) * 16]
            assert (block == float(b)).all(), f"板{b} 通道值不对（拼接顺序错）"
        print(f"[2] read() 合并 4×16ch → {out.shape}，板顺序拼接正确 ✓")


def test_start_stop():
    with mock.patch("eegscope.sources.multiboard.OpenBCISource", _MockBoard):
        src = MultiBoardSource(ports=[0, 1, 2, 3], n_channels_per_board=16)
        src.start()
        assert all(b.started for b in src._boards)
        src.stop()
        assert not any(b.started for b in src._boards)
        print("[3] start/stop 转发到 4 块板 ✓")


def test_make_source():
    from eegscope.config import AppConfig, OpenBCIConfig
    from eegscope.sources import make_source

    # 多板：ports 4 个 + channels 64 个 → 应路由到 MultiBoardSource
    cfg = AppConfig()
    cfg.source.type = "openbci"
    cfg.source.openbci = OpenBCIConfig(ports=["p0", "p1", "p2", "p3"], n_channels=16)
    cfg.channels = list(DEFAULT_CHANNELS_64)

    with mock.patch("eegscope.sources.multiboard.OpenBCISource", _MockBoard):
        src = make_source(cfg)
        from eegscope.sources.multiboard import MultiBoardSource
        assert isinstance(src, MultiBoardSource), f"期望 MultiBoardSource，得到 {type(src).__name__}"
        assert src.n_channels == 64
        assert src.channel_names == list(DEFAULT_CHANNELS_64)
        print("[4] make_source 多板分支 → MultiBoardSource(64ch) ✓")


if __name__ == "__main__":
    test_layout()
    test_merge()
    test_start_stop()
    test_make_source()
    print("\n全部 MultiBoardSource 测试通过 ✓")
