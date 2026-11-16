"""无硬件端到端：四板 Daisy 串口分片、计数回绕、通道顺序和频谱。"""
import sys
from pathlib import Path
import numpy as np
from scipy.signal import welch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eegscope.sources.openbci import OpenBCISource
from test_openbci_parser import make_frame

fs = 125
n = 4 * fs
class FragmentedSerial:
    def __init__(self, source, payload):
        self.source, self.payload, self.i = source, payload, 0
    @property
    def in_waiting(self):
        return min(17, len(self.payload) - self.i)
    def read(self, nbytes):
        b = self.payload[self.i:self.i+nbytes]
        self.i += len(b)
        if self.i >= len(self.payload):
            self.source._stop_event.set()
        return b

for board_index in range(4):
    source = OpenBCISource('sim', n_channels=16, scale_uv=1.0)
    samples = np.array([[board_index * 10000 + ch * 100 + round(20*np.sin(2*np.pi*10*j/fs))
                         for j in range(n)] for ch in range(16)])
    frames = []
    for j in range(n):
        frames.append(make_frame(samples[:8, j], counter=(2*j+1) % 256))
        frames.append(make_frame(samples[8:, j], counter=(2*j+2) % 256))
    source._ser = FragmentedSerial(source, b''.join(frames))
    source._reader_loop()
    got = source.read_available(n+1)
    np.testing.assert_array_equal(got, samples)
    assert source.frame_stats()['n_dropped_est'] == 0
    f, p = welch(got[0], fs=fs, nperseg=fs*2)
    assert p[np.argmin(abs(f-25))] < p[np.argmin(abs(f-10))] * 1e-4
print('PASS: 4 simulated boards, 16-channel order, fragmentation, wrap, no fabricated 25 Hz')
