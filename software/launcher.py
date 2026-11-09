#!/usr/bin/env python3
"""eegscope 启动器 —— 暗色欢迎窗口，自动自检环境 + 一键修复 + 一键启动采集。

给「不懂技术的人」用的入口：打开后电脑自己检查环境，缺啥补啥，点一下就启动。

用法（跨平台，推荐用一键脚本）：
    Windows:  双击 start.bat
    macOS/Linux:  bash start.sh
    或手动：  .venv/bin/python launcher.py   (Windows 用 .venv\\Scripts\\python)
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import webbrowser

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout,
                               QLabel, QMainWindow, QPlainTextEdit, QPushButton,
                               QRadioButton, QVBoxLayout, QWidget)

_SW_DIR = os.path.dirname(os.path.abspath(__file__))
if _SW_DIR not in sys.path:
    sys.path.insert(0, _SW_DIR)

from eegscope import _runtime
from eegscope.envcheck import STM32_DRIVER_URL, run_all_checks, run_fix

IS_FROZEN = _runtime.IS_FROZEN

_STYLE = """
QWidget { background: #1b1b22; color: #e0e0e0; font-size: 14px; }
QLabel#title { font-size: 20px; font-weight: bold; color: #ffffff; }
QLabel#subtitle { color: #9a9aa5; font-size: 13px; }
QFrame#card { background: #232330; border: 1px solid #32323f; border-radius: 10px; }
QLabel#check_icon { font-size: 18px; }
QLabel#check_name { font-size: 14px; font-weight: bold; }
QLabel#check_detail { color: #b8b8c2; font-size: 13px; }
QPushButton { background: #2f2f3d; border: 1px solid #3d3d4d; border-radius: 8px;
              padding: 8px 16px; color: #e0e0e0; }
QPushButton:hover { background: #3a3a4a; }
QPushButton#primary { background: #2e7d32; border: 1px solid #388e3c;
                      font-size: 16px; font-weight: bold; padding: 12px 24px; }
QPushButton#primary:hover { background: #388e3c; }
QPushButton#primary:disabled { background: #2a2a35; color: #666; border-color: #333; }
QRadioButton { color: #c8c8d0; }
QPlainTextEdit { background: #14141a; border: 1px solid #32323f; border-radius: 8px;
                 font-family: Menlo, Consolas, monospace; font-size: 12px; color: #b8b8c2; }
QLabel#footer { color: #6a6a78; font-size: 12px; }
"""


class Worker(QThread):
    """后台跑自检 / 修复，避免卡 UI。task: 'check' | 'fix'。"""
    check_done = Signal(object)   # dict
    fix_done = Signal(object)     # dict（修复后再跑一次自检的结果）
    log = Signal(str)

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self._task = task

    def run(self):
        if self._task == "check":
            self.check_done.emit(run_all_checks())
        else:
            res = run_fix(self.log.emit)
            self.fix_done.emit(res)


class ScriptWorker(QThread):
    """打包版里「扫描串口 / 摸电极识板」等脚本：进程内跑，输出流式进日志框。

    PyInstaller 单文件 exe 里没有独立 Python 解释器，脚本不能 subprocess 跑，
    改为 runpy 在后台线程执行，把它的 print 输出经 Qt Signal 送回主线程日志框。
    """
    line = Signal(str)
    done = Signal()

    def __init__(self, script_path, parent=None):
        super().__init__(parent)
        self._path = script_path

    def run(self):
        import runpy

        class _Sink(io.TextIOBase):
            def __init__(self, emit):
                self._emit = emit
                self._buf = ""

            def write(self, s):
                self._buf += s
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    if line.strip():
                        self._emit(line)
                return len(s)

            def flush(self):
                if self._buf.strip():
                    self._emit(self._buf)
                    self._buf = ""

        old = sys.stdout
        sys.stdout = _Sink(self.line.emit)
        try:
            runpy.run_path(self._path, run_name="__main__")
        except SystemExit:
            pass
        except Exception as e:  # noqa: BLE001
            self.line.emit(f"⚠️ 运行出错：{e}")
        finally:
            sys.stdout = old
            self.done.emit()


class Launcher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("eegscope 启动器 · 脑电采集")
        self._worker = None
        self._build_ui()
        self.start_check()

    # ---- UI ----
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(12)

        title = QLabel("eegscope 启动器")
        title.setObjectName("title")
        root.addWidget(title)
        sub = QLabel("打开后电脑自己检查环境，缺啥补啥，点一下就能开始采集。")
        sub.setObjectName("subtitle")
        root.addWidget(sub)

        # 检查结果卡片
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 12, 16, 12)
        card_layout.setSpacing(8)
        self.rows = {}
        for key, name in (("python", "Python 版本"),
                          ("deps", "依赖包"),
                          ("board", "STM32 采集板"),
                          ("driver", "STM32 驱动")):
            icon, detail = self._make_row(card_layout, name)
            self.rows[key] = (icon, detail)
        root.addWidget(card)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.recheck_btn = QPushButton("🔄 重新检测")
        self.recheck_btn.clicked.connect(self.start_check)
        btn_row.addWidget(self.recheck_btn)
        self.fix_btn = QPushButton("🛠 一键修复缺的东西")
        self.fix_btn.clicked.connect(self.start_fix)
        btn_row.addWidget(self.fix_btn)
        self.driver_btn = QPushButton("打开驱动下载页")
        self.driver_btn.clicked.connect(lambda: webbrowser.open(STM32_DRIVER_URL))
        btn_row.addWidget(self.driver_btn)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        # 启动区
        launch_card = QFrame()
        launch_card.setObjectName("card")
        lc = QVBoxLayout(launch_card)
        lc.setContentsMargins(16, 12, 16, 14)
        lc.setSpacing(10)
        ch_row = QHBoxLayout()
        ch_row.addWidget(QLabel("通道数："))
        self.ch16 = QRadioButton("16 通道（单板）")
        self.ch64 = QRadioButton("64 通道（4 板）")
        self.ch16.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.ch16)
        group.addButton(self.ch64)
        ch_row.addWidget(self.ch16)
        ch_row.addWidget(self.ch64)
        ch_row.addStretch(1)
        lc.addLayout(ch_row)

        self.launch_btn = QPushButton("▶  启动采集软件")
        self.launch_btn.setObjectName("primary")
        self.launch_btn.clicked.connect(self._launch)
        lc.addWidget(self.launch_btn)

        self.launch_hint = QLabel("")
        self.launch_hint.setObjectName("subtitle")
        self.launch_hint.setWordWrap(True)
        lc.addWidget(self.launch_hint)
        root.addWidget(launch_card)

        # 调试向导（新手自己调通 64 通道用，不用命令行）
        self.wizard_btn = QPushButton("🔧 调试向导（检测板子 → 识板 → 启动）")
        self.wizard_btn.clicked.connect(self._open_wizard)
        root.addWidget(self.wizard_btn)

        # 日志
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFixedHeight(140)
        root.addWidget(self.log_box)

        footer = QLabel(f"运行环境：{sys.executable}")
        footer.setObjectName("footer")
        root.addWidget(footer)

    def _make_row(self, layout, name):
        row = QHBoxLayout()
        row.setSpacing(8)
        icon = QLabel("⏳")
        icon.setObjectName("check_icon")
        row.addWidget(icon)
        name_lbl = QLabel(name)
        name_lbl.setObjectName("check_name")
        name_lbl.setFixedWidth(120)
        row.addWidget(name_lbl)
        detail = QLabel("正在检查…")
        detail.setObjectName("check_detail")
        row.addWidget(detail, stretch=1)
        layout.addLayout(row)
        return icon, detail

    # ---- 检测 / 修复 ----
    def start_check(self):
        self._set_busy(True)
        self.recheck_btn.setEnabled(False)
        self.log("开始环境自检…")
        self._worker = Worker("check", self)
        self._worker.check_done.connect(self._on_check_done)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def start_fix(self):
        self._set_busy(True)
        self.fix_btn.setEnabled(False)
        self.log("开始一键修复…")
        self._worker = Worker("fix", self)
        self._worker.log.connect(self.log)
        self._worker.fix_done.connect(self._on_check_done)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _set_busy(self, busy):
        self.launch_btn.setEnabled(not busy)

    def _on_worker_finished(self):
        self.recheck_btn.setEnabled(True)
        self.fix_btn.setEnabled(True)
        self._set_busy(False)

    def _on_check_done(self, results):
        self._render(results)
        self.log("自检完成。")

    def _render(self, results):
        py_ok, py_ver = results["python"]
        dep_ok, missing = results["deps"]
        board_ok, ports = results["board"]
        drv_ok, drv_note = results["driver"]

        self._set_row("python", py_ok, f"版本 {py_ver}" + ("" if py_ok else "（要 3.10+）"))
        if dep_ok:
            self._set_row("deps", True, "全部装齐")
        else:
            names = "、".join(pip for _, pip in missing)
            self._set_row("deps", False, f"缺 {len(missing)} 个：{names}")
        if board_ok:
            self._set_row("board", True, f"检测到 {len(ports)} 块板")
        else:
            self._set_row("board", False, "没检测到（可能没插 / 没开机）")
        self._set_row("driver", drv_ok, drv_note)

        # 启动提示
        if board_ok:
            self.launch_hint.setText("")
        else:
            self.launch_hint.setText(
                "提示：还没检测到采集板。请先插好板子并开机，再点「重新检测」；"
                "如果 Windows 上插了还不认，先点「一键修复缺的东西」装驱动。")

    def _set_row(self, key, ok, text):
        icon, detail = self.rows[key]
        if ok:
            icon.setText("✅")
            detail.setStyleSheet("color: #7bc67e;")
        else:
            icon.setText("⚠️")
            detail.setStyleSheet("color: #e0a860;")
        detail.setText(text)

    # ---- 启动 ----
    def _launch(self):
        config = "config64.yaml" if self.ch64.isChecked() else "config.yaml"
        self.log(f"启动采集软件（{config}）…")
        if IS_FROZEN:
            # 打包版：没有 main.py，用 --app <config> 重新拉起自己这个 exe
            cfg_path = _runtime.resource_path(config)
            subprocess.Popen([sys.executable, "--app", cfg_path],
                             cwd=_runtime.app_root())
        else:
            main_py = os.path.join(_SW_DIR, "main.py")
            subprocess.Popen([sys.executable, main_py, config], cwd=_SW_DIR)
        self.launch_hint.setText(
            f"已启动采集软件（{config}）。采集窗口会单独弹出；本窗口可留着，采集完还能再启动。")

    def _open_wizard(self):
        from eegscope import wizard
        self._wizard = wizard.open_wizard(self)
        self.log("已打开调试向导。")

    def _run_script(self, script):
        self.log(f"运行 {script}…")
        if IS_FROZEN:
            path = _runtime.resource_path(os.path.join("scripts", script))
            self._script_worker = ScriptWorker(path, self)
            self._script_worker.line.connect(self.log)
            self._script_worker.done.connect(lambda: self.log("— 结束 —"))
            self._script_worker.start()
        else:
            path = os.path.join(_SW_DIR, "scripts", script)
            subprocess.Popen([sys.executable, path], cwd=_SW_DIR)

    def log(self, text):
        self.log_box.appendPlainText(text)
        self.log_box.verticalScrollBar().setValue(
            self.log_box.verticalScrollBar().maximum())


def main():
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(sys.argv)
    app.setStyleSheet(_STYLE)
    win = Launcher()
    win.resize(560, 640)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
