"""采集链关键回归；EDF 往返使用独立 edfio 读取器。"""
from pathlib import Path
import sys
import tempfile
import time

import numpy as np
from scipy.signal import welch
import edfio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eegscope.dsp import SignalChain
from eegscope.edfio import write_edf
from eegscope.recorder import SessionRecorder
from eegscope.sources.multiboard import MultiBoardSource


def test_filter_chunk_independent():
    fs = 125
    t = np.arange(fs * 30) / fs
    x = np.stack([10*np.sin(2*np.pi*10*t), 10*np.sin(2*np.pi*25*t)])
    full = SignalChain(fs).process(x)
    chain = SignalChain(fs)
    parts, i, k = [], 0, 0
    while i < x.shape[1]:
        n = 7 if k % 4 == 3 else 6
        parts.append(chain.process(x[:, i:i+n]))
        i += n
        k += 1
    chunked = np.concatenate(parts, axis=1)
    np.testing.assert_allclose(full, chunked, atol=1e-10)
    f, psd = welch(chunked[1, fs*5:], fs=fs, nperseg=fs*4)
    assert psd[np.argmin(abs(f-15))] < 1e-8
    assert np.std(chunked[0, fs*5:]) > 6.0  # 10 Hz 信号不能被短块去趋势破坏


def test_edf_roundtrip():
    fs = 125
    t = np.arange(8825) / fs
    x = np.stack([30*np.sin(2*np.pi*10*t), 120*np.cos(2*np.pi*15*t)])
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / 'record.edf')
        assert write_edf(path, x, fs, ['F3', 'F4'])
        edf = edfio.read_edf(path, lazy_load_data=False)
        assert edf.labels == ('F3', 'F4'), edf.labels
        assert abs(edf.duration - len(t)/fs) < 1e-9
        for i, signal in enumerate(edf.signals):
            assert signal.sampling_frequency == fs
            assert len(signal.data) == len(t)
            np.testing.assert_allclose(signal.data, x[i], atol=0.01)


def test_nonblocking_four_boards():
    source = MultiBoardSource(['COM1', 'COM2', 'COM3', 'COM4'], n_channels_per_board=8)
    for i, board in enumerate(source._boards):
        for j in range(i+1):
            board._queue.put(np.full(8, 10*i+j))
    out = source.read_available(20)
    assert out.shape == (32, 1)
    assert source.read_gap_info()['per_board_buffered'] == [0, 1, 2, 3]
    assert source.read_available(20).shape == (32, 0)  # no fake zeros
    for board in source._boards:
        board._queue.put(np.ones(8))
    assert source.read_available(20).shape == (32, 1)
    assert source.read_gap_info()['per_board_buffered'] == [0, 1, 2, 3]


def test_wall_duration():
    rec = SessionRecorder(125, ['F3'])
    rec.start()
    rec.append(np.ones((1, 5)), np.ones((1, 5)))
    time.sleep(.03)
    with tempfile.TemporaryDirectory() as tmp:
        rec._stop_monotonic = time.monotonic()
        rec._recording = False
        rec.save(tmp)
        import json
        meta = json.loads((Path(tmp)/'meta.json').read_text(encoding='utf-8'))
        assert meta['wall_duration_seconds'] >= .02
        assert meta['duration_seconds'] == .04
        assert all(meta['export_status'].values())


def test_recording_after_gui_stall():
    """后台样本持续到达，界面暂停刷新后应完整消费积压。"""
    source = MultiBoardSource(['COM1', 'COM2', 'COM3', 'COM4'], n_channels_per_board=8)
    # 3 秒 × 125 Hz；数据到达期间故意不调用界面读取。
    for i, board in enumerate(source._boards):
        for j in range(375):
            board._queue.put(np.full(8, i * 1000 + j, dtype=float))
    data = source.read_available(625)
    assert data.shape == (32, 375)
    for i in range(4):
        np.testing.assert_array_equal(data[i*8], i*1000 + np.arange(375))
    assert source.read_available(625).shape == (32, 0)


if __name__ == '__main__':
    test_filter_chunk_independent()
    test_edf_roundtrip()
    test_nonblocking_four_boards()
    test_wall_duration()
    test_recording_after_gui_stall()
    print('recording integrity: 5 passed')
