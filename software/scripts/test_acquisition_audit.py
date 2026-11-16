"""离体模拟：物理映射未知、丢帧、串口故障和录制覆盖保护。"""
import json
import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PySide6.QtWidgets import QApplication
from eegscope import app as appmod
from eegscope.config import load_config
from eegscope.sources import make_source

app = QApplication.instance() or QApplication([])
cfg = load_config('config64.yaml')
cfg.source.openbci.ports = ['A', 'B']  # 64 通道配置仅发现两块板
source = make_source(cfg)
assert source.channel_names == [f'ch{i+1}' for i in range(32)]
# 第二块板启动失败时，已启动的串口必须关闭。
from eegscope.sources.multiboard import MultiBoardSource
cleanup_source = MultiBoardSource(['a', 'b'])
events = []
class FakeBoard:
    def __init__(self, fail=False): self.fail = fail
    def start(self):
        if self.fail: raise OSError('simulated failure')
        events.append('start')
    def stop(self): events.append('stop')
cleanup_source._boards = [FakeBoard(), FakeBoard(True)]
try:
    cleanup_source.start()
    raise AssertionError('failure not propagated')
except OSError:
    pass
assert events == ['start', 'stop']
cfg.sample_rate = source.sample_rate
win = appmod.MainWindow(cfg, source)
win.timer.stop()
with tempfile.TemporaryDirectory() as tmp:
    appmod._RECORDINGS_DIR = tmp
    win._start_recording()
    # 模拟计数 1 后跳到 3：会话应留下无法恢复的完整性警示。
    source._boards[0]._update_frame_stats(1, 10)
    source._boards[0]._update_frame_stats(3, 10)
    for board in source._boards:
        for _ in range(250):
            board._queue.put(np.ones(16))
    win._tick()
    assert win._integrity_warning is not None
    assert not win.calibrator.calibrated
    win._stop_recording()
    out = next(Path(tmp).iterdir())
    meta = json.loads((out/'meta.json').read_text(encoding='utf8'))
    assert meta['acquisition']['ports_in_channel_order'] == ['A', 'B']
    assert meta['acquisition']['mapping_verified'] is False
    assert meta['acquisition']['integrity_warnings'][0]['board'] == 1
    before = (out/'session.npz').read_bytes()
    from eegscope.recorder import SessionRecorder
    rec = SessionRecorder(125, ['x'])
    rec.start()
    rec.append(np.ones((1, 5)), np.ones((1, 5)))
    try:
        rec.save(str(out))
        raise AssertionError('existing session overwritten')
    except FileExistsError:
        pass
    assert (out/'session.npz').read_bytes() == before
    # 串口后台读取异常也必须保留到会话，并暂停指标。
    win._start_recording()
    source._boards[1]._reader_error = 'simulated disconnect'
    win._record_sync()
    assert win._integrity_warning is not None
    assert win.recorder.acquisition['integrity_warnings'][0]['events'] == ['reader_stopped']
    source._boards[1]._reader_error = None
    win._stop_recording()  # 无数据，不生成第二份录制
    rec._started_at = __import__('datetime').datetime(2026, 1, 1)
    rec.recordings_dir = tmp
    Path(tmp, '20260101_000000').mkdir()
    assert rec._default_dir().endswith('20260101_000000_1')
win.close()
print('PASS: unknown mapping, frame loss warning, original files protected')
