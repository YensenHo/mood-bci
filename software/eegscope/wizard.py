"""调试向导（图形版）—— 让新手自己把 64 通道调通，不用找技术。

三步：检测板子 → 摸电极识板（自动写 config）→ 启动采集。
全程点按钮，说人话，不给命令行。

用法：从启动器点「🔧 调试向导」，或直接 .venv/bin/python -m eegscope.wizard
"""
from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from . import _runtime
from .config import load_config
from .diagnostics import (REP_ELECTRODES, check_all_boards, identify_boards,
                          write_ports_to_config)

_STYLE = """
QWidget { background: #1b1b22; color: #e0e0e0; font-size: 14px; }
QLabel#title { font-size: 20px; font-weight: bold; color: #ffffff; }
QLabel#subtitle { color: #9a9aa5; font-size: 13px; }
QLabel#step { font-size: 15px; font-weight: bold; color: #7fb7ff; }
QLabel#prompt { font-size: 17px; font-weight: bold; color: #ffd479; }
QFrame#card { background: #232330; border: 1px solid #32323f; border-radius: 10px; }
QFrame#board { background: #1f1f2b; border: 1px solid #32323f; border-radius: 8px; }
QLabel#ok { color: #7bc67e; }
QLabel#warn { color: #e0a860; }
QLabel#bad { color: #e07a7a; }
QPushButton { background: #2f2f3d; border: 1px solid #3d3d4d; border-radius: 8px;
              padding: 10px 18px; color: #e0e0e0; }
QPushButton:hover { background: #3a3a4a; }
QPushButton:disabled { background: #2a2a35; color: #666; border-color: #333; }
QPushButton#primary { background: #2e7d32; border: 1px solid #388e3c;
                      font-size: 16px; font-weight: bold; padding: 12px 24px; }
QPushButton#primary:hover { background: #388e3c; }
QPlainTextEdit { background: #14141a; border: 1px solid #32323f; border-radius: 8px;
                 font-family: Menlo, Consolas, monospace; font-size: 12px; color: #b8b8c2; }
"""


class _CheckWorker(QThread):
    done = Signal(object)

    def run(self):
        self.done.emit(check_all_boards())


class _IdentifyWorker(QThread):
    touch = Signal(str, int)   # (电极名, 板号)
    done = Signal(object)

    def __init__(self, channel_names, parent=None):
        super().__init__(parent)
        self._names = channel_names

    def run(self):
        res = identify_boards(self._names,
                              touch_cb=lambda n, b: self.touch.emit(n, b))
        self.done.emit(res)


class DiagnosticWizard(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("eegscope 调试向导 · 64 通道")
        self._check_worker = None
        self._ident_worker = None
        self._build_ui()
        self._do_check()

    # ---- UI ----
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        title = QLabel("调试向导")
        title.setObjectName("title")
        root.addWidget(title)
        sub = QLabel("跟着三步走，64 通道就能自己调通。哪里不对，它会直接告诉你调哪个。")
        sub.setObjectName("subtitle")
        sub.setWordWrap(True)
        root.addWidget(sub)

        # ---- 第 1 步：检测板子 ----
        step1 = QLabel("第 1 步 · 检测板子")
        step1.setObjectName("step")
        root.addWidget(step1)

        card1 = QFrame()
        card1.setObjectName("card")
        c1 = QVBoxLayout(card1)
        c1.setContentsMargins(14, 12, 14, 12)
        c1.setSpacing(8)
        self.check_btn = QPushButton("🔍 检测 4 块板（自动扫描 + 读数据）")
        self.check_btn.clicked.connect(self._do_check)
        c1.addWidget(self.check_btn)
        self.board_area = QVBoxLayout()
        self.board_area.setSpacing(6)
        c1.addLayout(self.board_area)
        root.addWidget(card1)

        # ---- 第 2 步：识板 ----
        step2 = QLabel("第 2 步 · 摸电极识板（让软件知道哪块板是板1/2/3/4）")
        step2.setObjectName("step")
        root.addWidget(step2)

        card2 = QFrame()
        card2.setObjectName("card")
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(14, 12, 14, 12)
        c2.setSpacing(8)
        hint2 = QLabel("按提示一个电极一个电极地捏，软件自动认，认完自动把串口填进 config64.yaml。")
        hint2.setObjectName("subtitle")
        hint2.setWordWrap(True)
        c2.addWidget(hint2)
        self.ident_btn = QPushButton("✋ 开始识板（需要你捏 4 个电极）")
        self.ident_btn.clicked.connect(self._do_identify)
        c2.addWidget(self.ident_btn)
        self.prompt_label = QLabel("")
        self.prompt_label.setObjectName("prompt")
        self.prompt_label.setWordWrap(True)
        self.prompt_label.setMinimumHeight(28)
        c2.addWidget(self.prompt_label)
        self.ident_result = QLabel("")
        self.ident_result.setWordWrap(True)
        c2.addWidget(self.ident_result)
        root.addWidget(card2)

        # ---- 第 3 步：启动 ----
        step3 = QLabel("第 3 步 · 启动采集")
        step3.setObjectName("step")
        root.addWidget(step3)
        self.launch_btn = QPushButton("▶  启动 64 通道采集软件")
        self.launch_btn.setObjectName("primary")
        self.launch_btn.clicked.connect(self._launch)
        root.addWidget(self.launch_btn)

        # ---- 日志 ----
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFixedHeight(110)
        root.addWidget(self.log_box)

    def _clear_board_area(self):
        while self.board_area.count():
            item = self.board_area.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    # ---- 第 1 步：检测 ----
    def _do_check(self):
        self._clear_board_area()
        self.check_btn.setEnabled(False)
        self.log("正在检测板子…（每块板要握手 + 读 2 秒，共约十几秒）")
        self._check_worker = _CheckWorker(self)
        self._check_worker.done.connect(self._on_check_done)
        self._check_worker.start()

    def _on_check_done(self, result):
        self.check_btn.setEnabled(True)
        n = result["n_found"]
        boards = result["boards"]
        if n == 0:
            self.log("❌ 没检测到任何板子。检查：都插好、都按蓝键开机。")
            self._add_board_label("没检测到板子。请检查 USB 线、每块板都按蓝键开机（绿灯常亮）。", "bad")
            return
        self.log(f"✅ 找到 {n} 块板。")
        total_streaming = 0
        total_ok = 0
        for i, b in enumerate(boards, 1):
            if not b["ok"]:
                self._add_board_label(f"板{i}（{b['port']}）：❌ {b['error']}", "bad")
                continue
            total_streaming += b["streaming"]
            total_ok += b["n_ok"]
            problems = [s for s in b["channels"] if s["state"] != "OK"]
            if problems:
                parts = [f"第{s['ch']}通道{s['state']}" for s in problems]
                self._add_board_label(
                    f"板{i}（{b['port']}）：在流 {b['streaming']}/16，"
                    f"⚠️ {', '.join(parts)}", "warn")
            else:
                self._add_board_label(
                    f"板{i}（{b['port']}）：✅ 在流 {b['streaming']}/16，全 OK", "ok")
        self.log(f"结果：{total_streaming}/64 通道在流，{total_ok}/64 全好。")
        if total_ok < 64:
            self.log("⚠️ 有通道「打满/平线」。打满=电极没贴好（重贴）；平线=这条线没接电极。")

    def _add_board_label(self, text, kind):
        lbl = QLabel(text)
        lbl.setObjectName(kind)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("padding: 6px 8px;")
        self.board_area.addWidget(lbl)

    # ---- 第 2 步：识板 ----
    def _do_identify(self):
        names = None
        try:
            cfg = load_config(_runtime.resource_path("config64.yaml"))
            names = list(cfg.channels)
        except Exception:
            pass
        self.ident_btn.setEnabled(False)
        self.ident_result.setText("")
        self.prompt_label.setText("正在打开 4 块板，读基线…")
        self.log("开始识板。请按下方提示，一个一个捏电极。")
        self._ident_worker = _IdentifyWorker(names, self)
        self._ident_worker.touch.connect(self._on_touch)
        self._ident_worker.done.connect(self._on_identify_done)
        self._ident_worker.start()

    def _on_touch(self, name, board):
        self.prompt_label.setText(f"👉 请捏住【板{board}】上的 {name} 电极，捏实、保持 2 秒再松开…")

    def _on_identify_done(self, result):
        self.ident_btn.setEnabled(True)
        self.prompt_label.setText("")
        if result.get("error"):
            self.ident_result.setText(f"⚠️ {result['error']}")
            self.log(f"识板失败：{result['error']}")
            return
        ordered = result["ordered"]
        lines = []
        missing = []
        for i, dev in enumerate(ordered, 1):
            if dev is None:
                missing.append(str(i))
                lines.append(f"板{i} = 没识别到")
            else:
                lines.append(f"板{i} = {dev}")
        self.ident_result.setText("认完了：\n" + "\n".join(lines))
        self.log("识板结果：\n" + "\n".join(lines))
        if missing:
            self.log(f"⚠️ 板 {', '.join(missing)} 没识别到，重新点「开始识板」，捏久一点。")
            return
        # 自动写 config64.yaml
        ok, msg = write_ports_to_config([d for d in ordered], _runtime.resource_path("config64.yaml"))
        self.log(msg if ok else f"❌ {msg}")
        if ok:
            self.ident_result.setText(
                self.ident_result.text() + f"\n\n✅ {msg}。可以直接点第 3 步启动。")

    # ---- 第 3 步：启动 ----
    def _launch(self):
        config = "config64.yaml"
        cfg_path = _runtime.resource_path(config)
        self.log(f"启动采集软件（{config}）…")
        if _runtime.IS_FROZEN:
            subprocess.Popen([sys.executable, "--app", cfg_path],
                             cwd=_runtime.app_root())
        else:
            subprocess.Popen([sys.executable, os.path.join(_runtime.app_root(), "main.py"),
                              config], cwd=_runtime.app_root())
        self.log("采集窗口会单独弹出。看波形在动、质量面板大多绿色，就成了。")

    def log(self, text):
        self.log_box.appendPlainText(text)
        self.log_box.verticalScrollBar().setValue(
            self.log_box.verticalScrollBar().maximum())


def open_wizard(parent=None):
    """从启动器打开向导（返回窗口对象，保持引用防止被回收）。"""
    win = DiagnosticWizard(parent)
    win.resize(620, 760)
    win.show()
    return win


def main():
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(sys.argv)
    app.setStyleSheet(_STYLE)
    win = open_wizard()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
