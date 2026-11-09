"""远程实时采集：参与者端上传 + 监控端接收（WebSocket 直连）。

数据流（内网穿透 / 公网中转均可，只认一个 ws:// 或 wss:// 地址）：

    参与者电脑                       我的电脑（监控端）
    ──────────                     ──────────────────
    读脑电帽串口 → StreamUploader ──► StreamServer ──► NetworkSource ──► 现有 GUI 管线

传输格式：
  - 第一条消息：JSON 文本 hello = {type, participant, fs, channels:[...]}
  - 之后每条：二进制 float32 数组字节 (n_channels × n_samples, C 顺序)

带宽：8ch@250Hz 的 float32 ≈ 8 KB/s；64ch@125Hz ≈ 32 KB/s，很小。

用 websockets 的 sync API（各自线程跑，不碰 asyncio，避免和 Qt 事件循环打架）。
"""
from __future__ import annotations

import json
import queue
import threading

import numpy as np
from websockets.sync.client import connect as _ws_connect
from websockets.sync.server import serve as _ws_serve


class StreamServer:
    """监控端：WebSocket 服务器，收参与者的 hello + 数据块，按参与者分队列。

    用法：
        srv = StreamServer(host="0.0.0.0", port=8765)
        srv.start()
        while ...:
            for p in srv.participants:
                chunk = srv.get(p)      # 该参与者的数据块；None 表示暂时没数据
        srv.stop()

    多人支持（ISC / inter-brain 同步）：每个参与者一个队列，get(participant) 取各自数据。
    v1 的单人用法（get() 不带参数）仍兼容：从任意有数据的参与者取一块。
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8765,
                 n_channels: int | None = None):
        self.host = host
        self.port = port
        # 多人：participant -> queue / meta；_order 记录到达顺序（用于"最新 meta"）
        self._queues: dict = {}
        self._metas: dict = {}
        self._order: list = []
        self._last_error: str | None = None
        # 显式指定 n_channels 时（监控端 config 给了通道数），解码用它，不被 hello 覆盖
        self._n_ch = int(n_channels) if n_channels else 0
        self._n_ch_explicit = n_channels is not None
        self._server = None
        self._thread: threading.Thread | None = None
        self._connections: set = set()
        self._stop = threading.Event()
        self._lock = threading.Lock()

    @property
    def meta(self) -> dict | None:
        """最新一条 hello 元数据（channels / fs / participant），没连上时为 None。"""
        with self._lock:
            return self._metas.get(self._order[-1]) if self._order else None

    @property
    def participants(self) -> list:
        """已连接（发过 hello）的参与者名单，按到达顺序。"""
        with self._lock:
            return list(self._order)

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def sample_rate(self) -> float:
        return float((self.meta or {}).get("fs", 0.0))

    @property
    def channels(self) -> list:
        return list((self.meta or {}).get("channels", []))

    def _queue_for(self, participant: str) -> queue.Queue:
        with self._lock:
            q = self._queues.get(participant)
            if q is None:
                q = queue.Queue()
                self._queues[participant] = q
                self._order.append(participant)
            return q

    def get(self, participant: str | None = None,
            timeout: float = 0.05) -> np.ndarray | None:
        """取一块 (n_ch, n) µV 数据；暂时没有返回 None。

        participant 给定时只取该参与者的数据（ISC/多人用）；
        不给定（None）时从任意有数据的参与者取（保持 v1 单人行为兼容）。
        """
        if participant is not None:
            q = self._queues.get(participant)
            if q is None:
                return None
            try:
                return q.get(timeout=timeout)
            except queue.Empty:
                return None
        for p in self._order:
            q = self._queues.get(p)
            if q is None:
                continue
            try:
                return q.get(timeout=0.0)
            except queue.Empty:
                continue
        return None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self._server = _ws_serve(self._handler, self.host, self.port)
            self._server.serve_forever()
        except Exception as e:  # noqa: BLE001 —— 端口被占等
            self._last_error = str(e)

    def _handler(self, ws) -> None:
        self._connections.add(ws)
        participant = "未知"
        try:
            # 第一条必须是 hello JSON
            try:
                first = ws.recv(timeout=15.0)
            except Exception:  # noqa: BLE001 —— 连接后不发 hello 直接走
                return
            try:
                meta = json.loads(first) if isinstance(first, str) else {}
                if isinstance(meta, dict) and "channels" in meta:
                    participant = str(meta.get("participant", "未知")) or "未知"
                    with self._lock:
                        self._metas[participant] = meta
                        if not self._n_ch_explicit:
                            self._n_ch = len(meta.get("channels") or [])
            except (ValueError, TypeError):
                pass

            while not self._stop.is_set():
                try:
                    msg = ws.recv(timeout=1.0)
                except TimeoutError:
                    continue
                except Exception:  # noqa: BLE001 —— 连接断开
                    break
                if msg is None:
                    break
                if isinstance(msg, (bytes, bytearray)):
                    self._ingest(bytes(msg), participant)
        finally:
            self._connections.discard(ws)

    def _ingest(self, payload: bytes, participant: str) -> None:
        if self._n_ch <= 0:
            return
        try:
            arr = np.frombuffer(payload, dtype=np.float32)
        except Exception:  # noqa: BLE001
            return
        if arr.size == 0 or arr.size % self._n_ch != 0:
            return
        q = self._queue_for(participant)
        q.put(arr.reshape(self._n_ch, -1).astype(np.float64))

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self._server = None
        for ws in list(self._connections):
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._connections.clear()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None


class StreamUploader:
    """参与者端：WebSocket 客户端，把数据块异步发到监控端。

    用法：
        up = StreamUploader("ws://127.0.0.1:8765", participant="被试A")
        up.set_meta(fs=250, channels=[...])
        up.start()
        ... 每来一块数据：up.send(chunk) ...   # 非阻塞，内部队列 + 后台线程发
        up.close()

    send() 不阻塞 GUI：数据进有界队列，后台线程发；网络慢时丢最旧，保证实时性优先。
    """

    def __init__(self, url: str, participant: str = "被试"):
        self.url = url
        self.participant = participant
        self._meta: dict = {"type": "hello", "participant": participant,
                            "fs": 250.0, "channels": []}
        self._out: queue.Queue = queue.Queue(maxsize=4000)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._error: str | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    @property
    def error(self) -> str | None:
        return self._error

    def set_meta(self, fs: float, channels: list) -> None:
        self._meta = {"type": "hello", "participant": self.participant,
                      "fs": float(fs), "channels": [str(c) for c in channels]}

    def start(self) -> None:
        self._stop.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def send(self, chunk) -> None:
        """非阻塞入队。chunk 为 (n_ch, n) 的 µV 数组。"""
        arr = np.asarray(chunk, dtype=np.float32)
        if arr.ndim == 1:
            arr = arr[None, :]
        if arr.size == 0:
            return
        payload = arr.tobytes()
        try:
            self._out.put_nowait(payload)
        except queue.Full:
            # 网络跟不上：丢最旧的一块，保证延迟不越积越多
            try:
                self._out.get_nowait()
            except queue.Empty:
                pass
            try:
                self._out.put_nowait(payload)
            except queue.Full:
                pass

    def _run(self) -> None:
        try:
            # proxy=None 强制直连：本场景是连本机/LAN/Tailscale-IP/隧道地址，
            # 必须绕过系统代理。默认 proxy=True 会读 macOS 系统级 SOCKS 代理
            # (urllib.getproxies)，把 127.0.0.1 也往代理里送，直接报错。
            with _ws_connect(self.url, open_timeout=15.0, proxy=None) as ws:
                ws.send(json.dumps(self._meta))
                self._connected.set()
                while not self._stop.is_set():
                    try:
                        payload = self._out.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    try:
                        ws.send(payload)
                    except Exception:  # noqa: BLE001 —— 连接断了，退出去重连由上层决定
                        break
        except Exception as e:  # noqa: BLE001 —— 连不上等
            self._error = str(e)
        finally:
            self._connected.clear()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
