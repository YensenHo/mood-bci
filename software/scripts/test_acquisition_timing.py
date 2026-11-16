"""4 个模拟板独立送数，界面周期阻塞后验证录制样本未因刷新慢而丢失。"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from eegscope import app as appmod
from eegscope.config import load_config
from eegscope.sources.multiboard import MultiBoardSource

app = QApplication.instance() or QApplication([])
cfg = load_config(str(Path(__file__).resolve().parents[1] / 'config64.yaml'))
source = MultiBoardSource(['a', 'b', 'c', 'd'], n_channels_per_board=16,
                          channel_names=cfg.channels)
cfg.sample_rate = source.sample_rate  # 与 main.py 的真实入口一致
window = appmod.MainWindow(cfg, source)
count = 375  # 3 秒、每秒 125 点
done = threading.Event()

def produce():
    begin = time.monotonic()
    for i in range(count):
        delay = begin + i / 125 - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        for board in source._boards:
            board._queue.put(np.full(16, i, dtype=float))
    done.set()

def block_gui():
    time.sleep(.20)

with tempfile.TemporaryDirectory() as tmp:
    appmod._RECORDINGS_DIR = tmp
    window._start_recording()
    thread = threading.Thread(target=produce)
    thread.start()
    blocker = QTimer()
    blocker.timeout.connect(block_gui)
    blocker.start(250)
    QTimer.singleShot(3500, app.quit)
    app.exec()
    blocker.stop()
    thread.join(timeout=2)
    assert done.is_set()
    window._stop_recording()
    saved = next(Path(tmp).iterdir())
    data = np.load(saved/'session.npz')['raw']
    meta = json.loads((saved/'meta.json').read_text(encoding='utf-8'))
    assert data.shape == (64, count), data.shape
    np.testing.assert_array_equal(data[0], np.arange(count))
    assert meta['wall_duration_seconds'] >= 3.0
    assert abs(meta['duration_seconds'] - 3.0) < 1e-6
    assert all(meta['export_status'].values())
    print(f'GUI stall: saved {data.shape[1]}/{count}; wall={meta["wall_duration_seconds"]:.2f}s; sample=3s')
window.close()

# 停止时若界面已有超过单次 5 秒读取上限的积压，应多批消费。
source2 = MultiBoardSource(['a', 'b', 'c', 'd'], n_channels_per_board=16,
                           channel_names=cfg.channels)
window2 = appmod.MainWindow(cfg, source2)
window2.timer.stop()
with tempfile.TemporaryDirectory() as tmp:
    appmod._RECORDINGS_DIR = tmp
    window2._start_recording()
    for board in source2._boards:
        for i in range(1300):
            board._queue.put(np.full(16, i, dtype=float))
    window2._stop_recording()
    saved = next(Path(tmp).iterdir())
    data = np.load(saved/'session.npz')['raw']
    assert data.shape == (64, 1300), data.shape
    np.testing.assert_array_equal(data[0], np.arange(1300))
    print('stop backlog: saved 1300/1300 without truncation')
window2.close()
