"""多板旧积压必须在录制开始前清理，录制中不得暂停采集。"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PySide6.QtWidgets import QApplication
from eegscope import app as appmod
from eegscope.config import load_config
from eegscope.sources.multiboard import MultiBoardSource

app = QApplication.instance() or QApplication([])
cfg = load_config("config64.yaml")
source = MultiBoardSource(["a", "b", "c", "d"], n_channels_per_board=16,
                          channel_names=cfg.channels)
cfg.sample_rate = source.sample_rate
window = appmod.MainWindow(cfg, source)
window.timer.stop()

# 模拟四板依次启动：旧数据只可能出现在第一、二、三板。
old_counts = [750, 500, 250, 0]
for board, count in zip(source._boards, old_counts):
    for _ in range(count):
        board._queue.put(np.full(16, -99.0))

with tempfile.TemporaryDirectory() as tmp:
    appmod._RECORDINGS_DIR = tmp
    window._start_recording()
    assert window.timer.isActive()
    assert window.recorder.alignment["discarded_queued_samples"] == old_counts
    for board in source._boards:
        for i in range(20):
            board._queue.put(np.full(16, float(i)))
    window._tick()
    window.recorder.add_marker("刺激开始")
    window.start_btn.setChecked(True)
    assert not window.start_btn.isChecked() and window.timer.isActive()
    window._stop_recording()
    out = next(Path(tmp).iterdir())
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    data = np.load(out / "session.npz")["raw"]
    assert data.shape == (64, 20), data.shape
    for channel in (0, 16, 32, 48):
        np.testing.assert_array_equal(data[channel], np.arange(20))
    assert meta["alignment"]["discarded_queued_samples"] == old_counts
    assert meta["alignment"]["hardware_synchronized"] is False
    assert "t_monotonic" in meta["markers"][0]
window.close()
print("PASS: pre-recording backlog removed; new samples retained and boundary logged")
