"""离线不依赖 Qt，旧硬件映射未验证时不输出电极定位或情绪结论。"""
import json
from pathlib import Path
import tempfile
import numpy as np
from analyze_session import run

fs = 125
t = np.arange(fs * 4) / fs
raw = np.array([np.sin(2*np.pi*10*t)*10,
                np.sin(2*np.pi*10*t)*8,
                np.full(len(t), 187500.),
                np.sin(2*np.pi*10*t)*12])
with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp)
    np.savez_compressed(p/'session.npz', raw=raw, filtered=raw, fs=fs,
                        channel_names=np.array(['F3','F4','F7','O1']))
    (p/'meta.json').write_text(json.dumps({'source_type': 'MultiBoardSource',
        'config': {'source': {'openbci': {'gain': 24, 'gain_code': -1}}}}), encoding='utf8')
    assert run(tmp, 2, 1) == 0
    with np.load(p/'trajectories.npz') as z:
        assert not np.isfinite(z['faa']).any()
        assert not np.isfinite(z['valence']).any()
        assert not np.isfinite(z['arousal']).any()
        assert (z['excluded_channels'] >= 1).all()
    assert '映射或记录版本未验证' in (p/'report.md').read_text(encoding='utf8')
print('PASS: offline raw quality, mapping warning and no uncalibrated emotion output')
