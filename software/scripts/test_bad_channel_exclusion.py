"""验证坏通道剔除、关键通道降级及实时界面接入。"""
import os
import sys
import json
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PySide6.QtWidgets import QApplication
from eegscope.analysis import EmotionAnalyzer, detect_25hz_concentration
from eegscope.viz import compute_channel_quality

fs = 125
t = np.arange(fs * 2) / fs
names = ["F3", "F4", "F7", "CB2", "NC", "O1"]
data = np.array([np.sin(2*np.pi*10*t)*k for k in [2, 3, 50, 100, 1000, 4]])
analyzer = EmotionAnalyzer(fs, names)
mask = [True, True, False, False, True, True]
actual = analyzer.analyze(data, mask)
reference = EmotionAnalyzer(fs, ["F3", "F4", "O1"]).analyze(data[[0, 1, 5]])
for band in actual["bands"]:
    np.testing.assert_allclose(actual["bands"][band], reference["bands"][band])
assert actual["valid_channels"] == [True, True, False, False, False, True]
assert actual["faa_ok"]
bad_f3 = analyzer.analyze(data, [False, True, False, False, True, True])
assert bad_f3["faa"] is None and bad_f3["valence"] is None and bad_f3["theta_asymmetry"] is None
all_bad = analyzer.analyze(data, [False] * len(names))
assert all_bad["arousal"] is None and all(v is None for v in all_bad["bands"].values())
quality = compute_channel_quality(np.array([np.ones(fs*2)*187500, np.ones(fs*2)*500,
                                             np.sin(2*np.pi*10*t)*10]), fs,
                                  full_scale_uv=187500)
assert [q["status"] for q in quality] == ["bad", "bad", "good"]
# 自定义量程时 RMS 判定应按量程变化，而非固定 150000 µV。
large_scale = compute_channel_quality(np.array([150001 + np.sin(2*np.pi*10*t)*10]), fs,
                                      full_scale_uv=4500000)
assert large_scale[0]['status'] == 'good'
clean = np.tile(np.sin(2*np.pi*10*t), (8, 1))
noise = clean + 20*np.sin(2*np.pi*25*t)
assert not detect_25hz_concentration(clean, fs, [True]*8)["warning"]
assert detect_25hz_concentration(noise, fs, [True]*8)["warning"]
assert not detect_25hz_concentration(noise, fs, [True]*3+[False]*5)["available"]

# 模拟真实界面窗口：原始质量决定过滤数据的掩码，且数据本身不被修改。
from eegscope import app as appmod
from eegscope.config import load_config
from eegscope.sources.multiboard import MultiBoardSource
app = QApplication.instance() or QApplication([])
cfg = load_config("config64.yaml")
source = MultiBoardSource(["a", "b", "c", "d"], channel_names=cfg.channels)
cfg.sample_rate = source.sample_rate
window = appmod.MainWindow(cfg, source)
window.timer.stop()
raw = np.tile(np.sin(2*np.pi*10*t)*10, (64, 1))
raw[7] = 187500
raw[62] = -187500
window._raw_hist = raw.copy()
window.waveform.hist = raw.copy()
window._run_analysis()
assert window._last_valid_channels[7] is False and window._last_valid_channels[62] is False
assert window._last_valid_channels[6] is False and window._last_valid_channels[63] is False
np.testing.assert_array_equal(window._raw_hist, raw)
window._raw_hist[9] = 187500  # F3 饱和时 FAA 应停用
window._run_analysis()
assert window._last_valid_channels[9] is False
assert window.emotion.faa_label.text() == "需 F3/F4"
# 25 Hz 共同成分明显时不使用该窗口累积基线，也不显示情绪二维点。
window._raw_hist = np.tile(noise[0], (64, 1)) * 10
window.waveform.hist = window._raw_hist.copy()
window._baseline_mode = True
window.calibrator.reset()
window._run_analysis()
assert window.calibrator.n_samples == 0
assert "25 Hz" in window.info_label.text()
window._baseline_mode = False
window._raw_hist = raw.copy()
with tempfile.TemporaryDirectory() as tmp:
    appmod._RECORDINGS_DIR = tmp
    window._start_recording()
    window.recorder.append(raw, raw)
    window._run_analysis()
    window._stop_recording()
    folder = next(Path(tmp).iterdir())
    saved = np.load(folder / "session.npz")["raw"]
    np.testing.assert_array_equal(saved, raw.astype(np.float32))
    sync = json.loads((folder / "sync.json").read_text(encoding="utf8"))
    entry = sync["channel_quality"][-1]
    assert not entry["valid"][7] and not entry["valid"][62]
    assert entry["reasons"][6] == ["预留或参考通道"]
window.close()
print("PASS: exclusion, no valid channels, invalid F3, and GUI integration")
