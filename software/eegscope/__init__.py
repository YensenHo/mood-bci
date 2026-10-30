"""eegscope — 实时脑电可视化 + 情绪分析桌面应用（硬件无关 MVP）。

数据源 → 信号链 DSP → 情绪分析 → 可视化，四段解耦；真实 ADS1299 串口驱动
只需实现 `eegscope.sources.base.DataSource` 接口并注册进 `make_source` 工厂。
"""
__version__ = "0.1.0"
