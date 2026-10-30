#!/usr/bin/env python3
"""GUI 无头启动冒烟测试：offscreen 平台下建窗、跑 2 秒数据流、不崩则 exit 0。

用法（在 software/ 下）：
    QT_QPA_PLATFORM=offscreen env -i HOME=$HOME PATH=... PYTHONPATH= \
        .venv/bin/python scripts/gui_smoke.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from eegscope.app import MainWindow
from eegscope.config import load_config
from eegscope.sources import make_source
from eegscope.viz import setup_theme


def main() -> int:
    cfg = load_config(os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))), "config.yaml"))
    source = make_source(cfg)
    source.start()

    app = QApplication.instance() or QApplication(sys.argv)
    setup_theme()
    win = MainWindow(cfg, source)
    win.show()

    # 模拟 3 秒实时数据流（覆盖 source→dsp→viz→analysis；analysis 需攒满 2s 窗）
    n_ticks = int(3.0 * 1000 / cfg.viz.refresh_ms)
    for _ in range(n_ticks):
        win._tick()
    app.processEvents()  # 让 Qt 处理重绘/信号

    # 校验情绪面板已被更新过（FAA / 频带功率有值）
    faa_text = win.emotion.faa_label.text()
    print(f"[GUI smoke] 建窗成功，跑 {n_ticks} 次 tick OK")
    print(f"[GUI smoke] FAA 面板文本: {faa_text!r}")
    print(f"[GUI smoke] 波形缓冲区: {win.waveform.hist.shape}")

    # 校验脑地形图 / 信号质量面板已建窗并随分析节奏更新（不崩）
    assert hasattr(win, "topography") and hasattr(win, "quality"), \
        "主窗口缺少脑地形图/信号质量面板"
    assert win.topography._powers is not None, "脑地形图面板未被喂入频带功率"
    topo_img = win.topography.img.image
    assert topo_img is not None and topo_img.ndim == 3, \
        f"脑地形图应已渲染 RGBA 热力图，实际 {None if topo_img is None else topo_img.shape}"
    q_labels = win.quality._labels
    assert len(q_labels) == win.source.n_channels, "质量面板标签数应=通道数"
    assert "background-color" in q_labels[0].styleSheet(), "质量标签应有状态色样式"
    print(f"[GUI smoke] 脑地形图热力图 shape={topo_img.shape}，"
          f"质量面板标签 {len(q_labels)} 个 OK")

    # 模拟闭眼按钮再跑 2 秒，验证 alpha 上升后 FAA 面板仍正常刷新
    win.eyes_btn.setChecked(True)
    for _ in range(int(2.0 * 1000 / cfg.viz.refresh_ms)):
        win._tick()
    app.processEvents()
    print(f"[GUI smoke] 模拟闭眼后 FAA 面板文本: {win.emotion.faa_label.text()!r}")

    win.close()
    print("[GUI smoke] 退出 OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
