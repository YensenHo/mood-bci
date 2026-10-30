# eegscope — 实时脑电可视化 + 情绪分析桌面应用（硬件无关 MVP）

本项目是「情绪脑电采集」的软件核心。当前版本**不需要任何真实硬件**——用合成脑电
数据源驱动，实时显示多通道滚动波形、频谱和情绪指标（FAA 额叶偏侧化 + 分频带功率）。
架构上预留了清晰的 **DataSource 插件接口**，后续接入真实 ADS1299 串口驱动时，
只需实现一个类并注册进工厂，其余管线（DSP / 分析 / 可视化）完全不用改。

---

## 目录结构

```
software/
  README.md              # 本文件
  requirements.txt       # 依赖清单
  config.yaml            # 采样率 / 通道名 / 数据源 / 滤波 / 分析 / 可视化参数
  main.py                # 入口：读 config → 造 source → 启动 GUI
  scripts/
    smoke_test.py        # headless 冒烟测试（无 GUI）
    gui_smoke.py         # GUI 无头启动冒烟测试（offscreen 平台）
  eegscope/
    __init__.py
    config.py            # 读并校验 config.yaml（dataclass）
    sources/
      __init__.py        # make_source() 工厂 —— 真实数据源在此注册
      base.py            # DataSource 抽象基类（start/stop/read + sample_rate/n_channels）
      synthetic.py       # SyntheticSource 合成真实感脑电
    dsp.py               # 信号链：带通 0.5-45Hz + 50Hz 陷波 + 去直流/去趋势（在线带状态）
    analysis.py          # FAA + 分频带功率（Welch），含 F3/F4 缺失降级
    viz.py               # pyqtgraph 面板：堆叠波形 / 频谱 / 情绪指标（暗色主题）
    app.py               # Qt 主窗口 + 定时器，把 source→dsp→analysis→viz 串起来
```

## 数据流

```
DataSource(合成/真实) --read(n)--> SignalChain(带通+陷波+去趋势) --已滤波--> 波形滚动
                                                    └--> EmotionAnalyzer(FAA+频带功率) --> 情绪面板
                                                    └--> Welch PSD --> 频谱面板
```

## 环境安装（国内网络，务必用清华镜像 + 隔离环境）

```bash
cd ~/mood_BCI/software

# 1) 建干净 venv（用 miniconda 的 3.13；绝不用 openmmlab 的 3.8、不用系统 python3）
/opt/miniconda3/bin/python3 -m venv .venv

# 2) 清华镜像装依赖
.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
    pyqtgraph PySide6 numpy scipy PyYAML
```

> 运行任何 python 时**必须清空 PYTHONPATH**（否则 Hermes 的 venv 会污染 import）：

```bash
env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \
    .venv/bin/python main.py
```

## 运行

```bash
cd ~/mood_BCI/software
env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \
    .venv/bin/python main.py
```

默认用合成数据源启动，16 通道滚动波形 + 频谱 + 情绪指标面板。UI 上：

- **alpha 幅度旋钮**：0=睁眼（alpha 弱）↔ 100=闭眼（alpha 强）。
- **「模拟闭眼」按钮**：一键让 alpha 突然增大，用来验证整条链路是通的
  （项目 HARNESS 的链路自检指标：**闭眼 alpha 上升**）。
- **PSD 通道**：下拉选择频谱面板看哪个通道的功率谱。
- **暂停/继续**：暂停定时器（数据流停）。

## config.yaml 说明

| 键 | 含义 | 默认 |
|---|---|---|
| `sample_rate` | 采样率（Hz） | 250（ADS1299 典型值） |
| `channels` | 通道名列表，**通道数 = 列表长度**，改这里即可增减通道 | 16ch（见下） |
| `source.type` | 数据源类型 | `synthetic` |
| `source.synthetic.*` | 合成源参数（seed/alpha 幅度/眨眼率/噪声/工频等） | — |
| `dsp.bandpass_hz` | 带通范围 | [0.5, 45] |
| `dsp.notch_hz` | 陷波频率 | 50（中国工频） |
| `analysis.bands` | 分频带定义（Hz） | delta/theta/alpha/beta/gamma |
| `analysis.faa_channels` | FAA 所需两通道 | [F3, F4] |
| `viz.*` | 波形窗长 / 刷新周期 / 通道显示间距 | 5s / 50ms / 80µV |

**默认 16 通道布局（10-20）**：
- 8ch 核心（项目定）：`F3 F4 F7 F8 T7 T8 O1 O2`
- 中线补充：`Fz Cz Pz`
- 对称补充：`Fp1 Fp2`、`C3 C4`、`Oz`

**弹针 3 电极场景**（2 EEG + 1 参考）：把 `channels` 改成 `["F3", "C3", "REF"]`，
应用照常运行；因缺 F4，FAA 自动降级显示「需 F3/F4」，其余频带功率正常。

## 信号链约束（对应 HARNESS M2）

- ✅ 带通 0.5–45 Hz（`scipy.signal.butter` 4 阶 sos）
- ✅ 50 Hz 陷波（中国工频，`scipy.signal.iirnotch`）
- ✅ 去直流 / 去趋势（逐通道线性 detrend）
- 在线滤波用带状态的 `sosfilt` / `lfilter`，避免分块处理边界跳变。

## 情绪指标（单被试辅助指标）

- **FAA = ln(P_F4) − ln(P_F3)**：效价辅助指标（项目约定：正=偏正性、负=偏负性）。
  P_F4 / P_F3 为 F4 / F3 通道在 alpha 频带的功率。仅当 F3、F4 同时存在才计算，
  缺失时优雅降级（不报错，显示「需 F3/F4」）。
- **分频带功率** delta(0.5-4)/theta(4-8)/alpha(8-13)/beta(13-30)/gamma(30-45)，
  以及 beta/alpha 比值、枕区 alpha，作为唤醒度/投入度辅助显示。
- 效价/唤醒度二维指示点：效价 ← FAA（tanh 映射到 [-1,1]），唤醒度 ← beta/alpha 比值。

> 注意：合成数据没有真实情绪内容，FAA 在 alpha 较弱（睁眼）时会受 1/f 噪声影响而
> 抖动（这正是真实 EEG 的已知现象——额叶 alpha 弱时 FAA 不可靠）；点「模拟闭眼」让
> alpha 增强后，FAA 会回落到接近 0 的稳定中性值。情绪面板在攒满一个分析窗（默认 2s）
> 后才开始出数。

## 冒烟验证（无 GUI）

```bash
cd ~/mood_BCI/software
env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \
    .venv/bin/python scripts/smoke_test.py
```

覆盖：5 秒数据形状/无 NaN、DSP 滤波、50Hz 陷波自检、FAA + 分频带功率、
闭眼 alpha 上升、F3/F4 缺失降级。全部通过 exit 0。

GUI 无头启动冒烟（offscreen 平台，跑 3 秒数据流 + 模拟闭眼，验证不崩）：

```bash
cd ~/mood_BCI/software
env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin PYTHONPATH= \
    QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/gui_smoke.py
```

## 接入真实 ADS1299 串口驱动（接口说明）

实现一个继承 `eegscope.sources.base.DataSource` 的类：

```python
class ADS1299Source(DataSource):
    @property
    def sample_rate(self): ...          # 例如 250.0
    @property
    def n_channels(self): ...           # 例如 16
    @property
    def channel_names(self): ...        # 可选，覆盖后波形面板显示真实通道名
    def start(self): ...                # 打开串口、发 START/初始化命令
    def stop(self): ...                 # 关闭串口
    def read(self, n_samples): ...      # 从串口解析 n 个样本，返回 (n_ch, n) µV 数组
```

然后在 `eegscope/sources/__init__.py` 的 `make_source()` 里加一个分支：

```python
if stype == "ads1299":
    from .ads1299 import ADS1299Source
    return ADS1299Source(cfg)
```

最后把 `config.yaml` 的 `source.type` 改成 `ads1299` 即可。DSP / 分析 / 可视化
一行都不用改。当前版本**只写接口 + 合成数据源，不写真实串口驱动**。
