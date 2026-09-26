"""九监测点小菜蛾虫害局域网监测服务。"""

from __future__ import annotations

import argparse
import atexit
import html
import ipaddress
import json
import os
import random
import socket
import subprocess
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


DEFAULT_TCP_PORT = 10000
DEFAULT_IMAGE_PORT = 10002
DEFAULT_DISCOVERY_PORT = 10001
DEFAULT_HTTP_PORT = 8000
DISCOVERY_MAGIC = "K230_INSECT_SERVER"
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_SAVED_IMAGES = 30
REAL_DEVICE_TIMEOUT_SECONDS = 30.0

BASE_DIR = Path(__file__).resolve().parent
DASHBOARD_PATH = BASE_DIR / "dashboard.html"
REAL_IMAGE_DIR = BASE_DIR / "data" / "images" / "device-1"
INSTANCE_LOCK_PATH = BASE_DIR / ".pc_server.lock"


def acquire_instance_lock() -> None:
    """Prevent two server processes from splitting the three TCP ports."""
    current_pid = os.getpid()
    while True:
        try:
            descriptor = os.open(INSTANCE_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(descriptor, "w", encoding="ascii") as lock_file:
                lock_file.write(str(current_pid))
            break
        except FileExistsError:
            try:
                existing_pid = int(INSTANCE_LOCK_PATH.read_text(encoding="ascii").strip())
                os.kill(existing_pid, 0)
            except (OSError, ValueError):
                try:
                    INSTANCE_LOCK_PATH.unlink()
                except FileNotFoundError:
                    pass
                continue
            raise RuntimeError(
                f"监测服务已经在运行（进程 {existing_pid}）。请关闭旧服务后再启动。"
            )

    def release_lock() -> None:
        try:
            if INSTANCE_LOCK_PATH.read_text(encoding="ascii").strip() == str(current_pid):
                INSTANCE_LOCK_PATH.unlink()
        except (FileNotFoundError, OSError):
            pass

    atexit.register(release_lock)

DEVICE_LAYOUT = (
    ("device-1", "1号监测点", 17, 18, "real", 0),
    ("device-2", "2号监测点", 50, 18, "simulated", 5),
    ("device-3", "3号监测点", 83, 18, "simulated", 9),
    ("device-4", "4号监测点", 17, 50, "simulated", 14),
    ("device-5", "5号监测点", 50, 50, "simulated", 22),
    ("device-6", "6号监测点", 83, 50, "simulated", 31),
    ("device-7", "7号监测点", 17, 82, "simulated", 8),
    ("device-8", "8号监测点", 50, 82, "simulated", 18),
    ("device-9", "9号监测点", 83, 82, "simulated", 38),
)


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class FarmState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._control_version = 1
        self._devices: dict[str, dict[str, Any]] = {}
        self._history: dict[str, deque[dict[str, Any]]] = {}
        self._images: dict[str, deque[dict[str, Any]]] = {}
        current = time.time()
        for device_id, name, x, y, kind, baseline in DEVICE_LAYOUT:
            is_simulated = kind == "simulated"
            self._devices[device_id] = {
                "id": device_id,
                "name": name,
                "kind": kind,
                "x": x,
                "y": y,
                "baseline": baseline,
                "count": baseline if is_simulated else 0,
                "raw_count": baseline if is_simulated else 0,
                "fps": 25.0 if is_simulated else 0.0,
                "background_l": 52 if is_simulated else None,
                "threshold_l": 40 if is_simulated else None,
                "lamp_on": True if not is_simulated else int(device_id.split("-")[1]) % 2 == 0,
                "light_enabled": True if not is_simulated else int(device_id.split("-")[1]) % 2 == 0,
                "light_brightness": 75 if not is_simulated else 55,
                "wavelength_nm": 400,
                "control_enabled": True if not is_simulated else int(device_id.split("-")[1]) % 2 == 0,
                "control_brightness": 75 if not is_simulated else 55,
                "control_wavelength_nm": 400,
                "last_seen": current if is_simulated else 0.0,
                "received_at": now_text() if is_simulated else "--",
                "client_ip": "电脑模拟" if is_simulated else "--",
                "has_data": is_simulated,
            }
            self._history[device_id] = deque(maxlen=600)
            self._images[device_id] = deque(maxlen=MAX_SAVED_IMAGES)

    def update_real(self, payload: dict[str, Any], client_ip: str) -> None:
        current = time.time()
        with self._lock:
            device = self._devices["device-1"]
            for key in (
                "count", "raw_count", "fps", "background_l", "threshold_l",
                "lamp_on", "light_brightness", "wavelength_nm", "uptime_ms",
            ):
                if key in payload:
                    device[key] = payload[key]
            device.update({
                "last_seen": current,
                "received_at": now_text(),
                "client_ip": client_ip,
                "has_data": True,
            })
            self._append_history_locked("device-1", current, int(device.get("count", 0)))

    def set_light_control(self, enabled: bool, brightness: int, wavelength_nm: int) -> dict[str, Any]:
        brightness = max(0, min(100, int(brightness)))
        wavelength_nm = max(350, min(450, int(wavelength_nm)))
        with self._lock:
            self._control_version += 1
            device = self._devices["device-1"]
            device["control_enabled"] = bool(enabled)
            device["control_brightness"] = brightness
            device["control_wavelength_nm"] = wavelength_nm
            return self._light_command_locked()

    def get_light_command(self) -> dict[str, Any]:
        with self._lock:
            return self._light_command_locked()

    def _light_command_locked(self) -> dict[str, Any]:
        device = self._devices["device-1"]
        return {
            "type": "light_control",
            "version": self._control_version,
            "enabled": bool(device["control_enabled"]),
            "brightness": int(device["control_brightness"]),
            "wavelength_nm": int(device["control_wavelength_nm"]),
        }

    def update_simulated(self, device_id: str, values: dict[str, Any]) -> None:
        current = time.time()
        with self._lock:
            device = self._devices[device_id]
            device.update(values)
            device.update({"last_seen": current, "received_at": now_text(), "has_data": True})
            self._append_history_locked(device_id, current, int(device["count"]))

    def _append_history_locked(self, device_id: str, timestamp: float, count: int) -> None:
        history = self._history[device_id]
        if history and timestamp - history[-1]["time"] < 1.0:
            history[-1] = {"time": timestamp, "count": count}
        else:
            history.append({"time": timestamp, "count": count})

    def add_image(
        self,
        device_id: str,
        url: str,
        count: int,
        timestamp: float | None = None,
        client_ip: str | None = None,
    ) -> None:
        timestamp = timestamp or time.time()
        record = {
            "url": url,
            "count": int(count),
            "time": timestamp,
            "captured_at": datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S"),
        }
        with self._lock:
            self._images[device_id].appendleft(record)
            device = self._devices[device_id]
            device.update({
                "count": int(count),
                "last_seen": timestamp,
                "received_at": datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S"),
                "has_data": True,
            })
            if client_ip:
                device["client_ip"] = client_ip
            self._append_history_locked(device_id, timestamp, int(count))

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        with self._lock:
            device = self._devices.get(device_id)
            return dict(device) if device else None

    def snapshot(self) -> dict[str, Any]:
        current = time.time()
        devices: list[dict[str, Any]] = []
        with self._lock:
            for device_id, source in self._devices.items():
                device = {key: value for key, value in source.items() if key != "baseline"}
                device["online"] = True if device["kind"] == "simulated" else bool(
                    device["has_data"]
                    and current - float(device["last_seen"]) < REAL_DEVICE_TIMEOUT_SECONDS
                )
                device["history"] = list(self._history[device_id])[-120:]
                device["images"] = list(self._images[device_id])[:12]
                devices.append(device)

        total = sum(int(device.get("count", 0)) for device in devices)
        online = sum(1 for device in devices if device["online"])
        hottest = max(devices, key=lambda item: int(item.get("count", 0)))
        return {
            "devices": devices,
            "summary": {
                "total_count": total,
                "online_count": online,
                "device_count": len(devices),
                "hottest_device": hottest["name"],
                "hottest_count": int(hottest.get("count", 0)),
                "updated_at": now_text(),
            },
        }


class DataServer:
    def __init__(self, host: str, port: int, state: FarmState) -> None:
        self.host = host
        self.port = port
        self.state = state

    def serve(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(5)
        print(f"[计数] 正在监听 TCP {self.host}:{self.port}")
        while True:
            client, address = server.accept()
            print(f"[设备1] 计数连接成功：{address[0]}:{address[1]}")
            threading.Thread(target=self._handle_client, args=(client, address[0]), daemon=True).start()

    def _handle_client(self, client: socket.socket, client_ip: str) -> None:
        buffer = b""
        last_control_version = -1
        try:
            with client:
                while True:
                    chunk = client.recv(2048)
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        if not line.strip():
                            continue
                        try:
                            payload = json.loads(line.decode("utf-8"))
                            if isinstance(payload, dict):
                                self.state.update_real(payload, client_ip)
                                command = self.state.get_light_command()
                                reported_enabled = bool(payload.get("lamp_on", True))
                                reported_brightness = int(payload.get("light_brightness", 75))
                                reported_wavelength = int(payload.get("wavelength_nm", 400))
                                needs_sync = (
                                    reported_enabled != bool(command["enabled"])
                                    or reported_brightness != int(command["brightness"])
                                    or reported_wavelength != int(command["wavelength_nm"])
                                )
                                if command["version"] != last_control_version or needs_sync:
                                    client.sendall((json.dumps(command, separators=(",", ":")) + "\n").encode("utf-8"))
                                    last_control_version = command["version"]
                        except (UnicodeDecodeError, json.JSONDecodeError) as error:
                            print(f"[警告] 忽略无法解析的计数数据：{error}")
        except (ConnectionError, OSError):
            pass
        finally:
            print(f"[设备1] 计数连接断开：{client_ip}")


class ImageServer:
    def __init__(self, host: str, port: int, state: FarmState) -> None:
        self.host = host
        self.port = port
        self.state = state
        REAL_IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    def serve(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(3)
        print(f"[图片] 正在监听 TCP {self.host}:{self.port}")
        while True:
            client, address = server.accept()
            threading.Thread(target=self._handle_client, args=(client, address[0]), daemon=True).start()

    def _handle_client(self, client: socket.socket, client_ip: str) -> None:
        try:
            client.settimeout(8)
            with client:
                header_line = self._read_line(client, 4096)
                header = json.loads(header_line.decode("utf-8"))
                size = int(header.get("length", 0))
                if size <= 0 or size > MAX_IMAGE_BYTES:
                    raise ValueError(f"图片大小不合法：{size}")
                image_data = self._read_exact(client, size)
                if not image_data.startswith(b"\xff\xd8"):
                    raise ValueError("收到的内容不是 JPEG 图片")

                timestamp = time.time()
                filename = datetime.now().strftime("%Y%m%d-%H%M%S-") + f"{int(timestamp * 1000) % 1000:03d}.jpg"
                target = REAL_IMAGE_DIR / filename
                target.write_bytes(image_data)
                self._prune_images()
                count = int(header.get("count", self.state.get_device("device-1").get("count", 0)))
                self.state.add_image(
                    "device-1",
                    f"/images/device-1/{filename}",
                    count,
                    timestamp,
                    client_ip,
                )
                print(f"[图片] 已接收设备1检测图：{filename}，{size} bytes，来源 {client_ip}")
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            print(f"[图片] 接收失败：{error}")

    @staticmethod
    def _read_line(client: socket.socket, maximum: int) -> bytes:
        data = bytearray()
        while len(data) < maximum:
            chunk = client.recv(1)
            if not chunk:
                break
            if chunk == b"\n":
                return bytes(data)
            data.extend(chunk)
        raise ValueError("图片头信息不完整")

    @staticmethod
    def _read_exact(client: socket.socket, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = client.recv(min(8192, size - len(data)))
            if not chunk:
                raise ValueError("图片数据提前中断")
            data.extend(chunk)
        return bytes(data)

    @staticmethod
    def _prune_images() -> None:
        files = sorted(REAL_IMAGE_DIR.glob("*.jpg"), key=lambda path: path.stat().st_mtime, reverse=True)
        for old_file in files[MAX_SAVED_IMAGES:]:
            old_file.unlink(missing_ok=True)


def simulation_loop(state: FarmState) -> None:
    rng = random.Random(230)
    last_image_time: dict[str, float] = {}
    while True:
        current = time.time()
        for device_id, _, _, _, kind, baseline in DEVICE_LAYOUT:
            if kind != "simulated":
                continue
            device = state.get_device(device_id)
            count = int(device["count"])
            pull_to_baseline = 1 if count < baseline else -1 if count > baseline else 0
            movement = rng.choice((-1, 0, 0, 0, 1, pull_to_baseline))
            count = max(0, min(60, count + movement))
            state.update_simulated(device_id, {
                "count": count,
                "raw_count": max(0, count + rng.choice((-1, 0, 0, 1, 2))),
                "fps": round(rng.uniform(23.0, 29.5), 1),
                "background_l": rng.randint(46, 64),
                "threshold_l": rng.randint(34, 50),
            })
            if current - last_image_time.get(device_id, 0) >= 10:
                stamp = int(current)
                image_url = f"/api/sim-image?device={device_id}&stamp={stamp}&count={count}"
                state.add_image(device_id, image_url, count, current)
                last_image_time[device_id] = current
        time.sleep(2)


def simulated_image_svg(device_id: str, stamp: int, count: int, state: FarmState) -> bytes:
    device = state.get_device(device_id)
    if not device or device["kind"] != "simulated":
        raise ValueError("未知模拟设备")
    rng = random.Random(f"{device_id}-{stamp}-{count}")
    insects = []
    for index in range(min(count, 24)):
        x = rng.randint(58, 582)
        y = rng.randint(78, 410)
        w = rng.randint(7, 16)
        h = rng.randint(10, 23)
        insects.append(
            f'<ellipse cx="{x}" cy="{y}" rx="{w // 2}" ry="{h // 2}" fill="#33291c" transform="rotate({rng.randint(-45,45)} {x} {y})"/>'
            f'<rect x="{x-w}" y="{y-h}" width="{w*2}" height="{h*2}" rx="3" fill="none" stroke="#22c55e" stroke-width="2"/>'
            f'<text x="{x-w}" y="{y-h-4}" fill="#0b3b23" font-size="12">{index + 1}</text>'
        )
    captured = datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M:%S")
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" viewBox="0 0 640 480">
      <defs><linearGradient id="board" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#f4e7a4"/><stop offset="1" stop-color="#d9c870"/></linearGradient></defs>
      <rect width="640" height="480" fill="#20372a"/><rect x="30" y="54" width="580" height="390" rx="10" fill="url(#board)" stroke="#f8f0bd" stroke-width="3"/>
      <rect width="640" height="54" fill="#10261b"/><text x="22" y="35" fill="#e8fff4" font-size="22" font-family="Microsoft YaHei, sans-serif">{html.escape(device['name'])} · 模拟检测图</text>
      {''.join(insects)}
      <rect x="30" y="414" width="580" height="30" fill="#10261bcc"/><text x="46" y="435" fill="#e8fff4" font-size="16" font-family="Microsoft YaHei, sans-serif">检测 {count} 只 · {captured}</text>
    </svg>'''
    return svg.encode("utf-8")


def make_handler(state: FarmState) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "MothFarmMonitor/2.0"
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_bytes(DASHBOARD_PATH.read_bytes(), "text/html; charset=utf-8")
            elif parsed.path in ("/api/status", "/api/farm"):
                self._send_json(state.snapshot())
            elif parsed.path == "/events":
                self._serve_events()
            elif parsed.path == "/api/sim-image":
                self._serve_simulated_image(parse_qs(parsed.query))
            elif parsed.path.startswith("/images/device-1/"):
                self._serve_real_image(parsed.path)
            elif parsed.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/api/light":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 4096:
                    raise ValueError("请求大小不合法")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                command = state.set_light_control(
                    bool(payload.get("enabled", True)),
                    int(payload.get("brightness", 75)),
                    int(payload.get("wavelength_nm", 400)),
                )
                self._send_json({"ok": True, "control": command})
            except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
                body = json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def _send_json(self, value: Any) -> None:
            self._send_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _send_bytes(self, body: bytes, content_type: str, cache: str = "no-store") -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def _serve_events(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while True:
                    body = json.dumps(state.snapshot(), ensure_ascii=False)
                    self.wfile.write(f"data: {body}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(1)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def _serve_simulated_image(self, query: dict[str, list[str]]) -> None:
            try:
                device_id = query.get("device", [""])[0]
                stamp = int(query.get("stamp", [str(int(time.time()))])[0])
                count = int(query.get("count", ["0"])[0])
                body = simulated_image_svg(device_id, stamp, count, state)
                self._send_bytes(body, "image/svg+xml; charset=utf-8", "public, max-age=3600")
            except (ValueError, TypeError):
                self.send_error(404)

        def _serve_real_image(self, request_path: str) -> None:
            filename = Path(request_path).name
            target = REAL_IMAGE_DIR / filename
            if target.exists() and target.suffix.lower() in (".jpg", ".jpeg"):
                self._send_bytes(target.read_bytes(), "image/jpeg", "public, max-age=86400")
            else:
                self.send_error(404)

        def log_message(self, format: str, *args: object) -> None:
            return

    return DashboardHandler


def lan_interfaces() -> list[tuple[str, str]]:
    """返回真实网卡的地址及各自子网广播地址，绕开代理默认路由。"""
    if os.name == "nt":
        command = (
            "@(Get-NetAdapter -Physical | Where-Object Status -eq 'Up' | "
            "Get-NetIPAddress -AddressFamily IPv4 | "
            "Select-Object IPAddress,PrefixLength) | ConvertTo-Json -Compress"
        )
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", command],
                capture_output=True, text=True, timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode != 0:
                raise ValueError("network enumeration failed")
            rows = json.loads(result.stdout or "[]")
            if isinstance(rows, dict):
                rows = [rows]
            addresses = []
            for row in rows:
                interface = ipaddress.ip_interface(f"{row['IPAddress']}/{row['PrefixLength']}")
                if interface.ip.is_loopback or interface.ip.is_link_local:
                    continue
                addresses.append((str(interface.ip), str(interface.network.broadcast_address)))
            return addresses
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
            return []
    return []


def find_lan_ip() -> str:
    interfaces = lan_interfaces()
    if interfaces:
        return interfaces[0][0]
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        try:
            addresses = socket.gethostbyname_ex(socket.gethostname())[2]
            return next(ip for ip in addresses if not ip.startswith("127."))
        except (OSError, StopIteration):
            return "127.0.0.1"
    finally:
        probe.close()


def broadcast_discovery(lan_ip: str, data_port: int, discovery_port: int) -> None:
    interfaces = []
    next_refresh = 0.0
    while True:
        if time.monotonic() >= next_refresh:
            interfaces = lan_interfaces()
            if not interfaces and os.name != "nt":
                interfaces = [(find_lan_ip(), "255.255.255.255")]
            if not interfaces:
                print("[自动发现] 未找到有效物理网卡，请检查 Wi-Fi 连接。")
            next_refresh = time.monotonic() + 10
        for address, broadcast in interfaces:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as broadcaster:
                    broadcaster.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    broadcaster.bind((address, 0))
                    message = f"{DISCOVERY_MAGIC}|{address}|{data_port}".encode("ascii")
                    broadcaster.sendto(message, (broadcast, discovery_port))
            except OSError:
                pass
        time.sleep(1.5)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="九监测点小菜蛾虫害局域网监测服务")
    parser.add_argument("--tcp-port", type=int, default=DEFAULT_TCP_PORT, help="K230 计数端口")
    parser.add_argument("--image-port", type=int, default=DEFAULT_IMAGE_PORT, help="K230 图片端口")
    parser.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT, help="浏览器页面端口")
    parser.add_argument("--discovery-port", type=int, default=DEFAULT_DISCOVERY_PORT, help="自动发现端口")
    parser.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    acquire_instance_lock()
    state = FarmState()
    lan_ip = find_lan_ip()

    threading.Thread(target=DataServer("0.0.0.0", args.tcp_port, state).serve, daemon=True).start()
    threading.Thread(target=ImageServer("0.0.0.0", args.image_port, state).serve, daemon=True).start()
    threading.Thread(target=simulation_loop, args=(state,), daemon=True).start()
    threading.Thread(
        target=broadcast_discovery,
        args=(lan_ip, args.tcp_port, args.discovery_port),
        daemon=True,
    ).start()

    http_server = ThreadingHTTPServer(("0.0.0.0", args.http_port), make_handler(state))
    local_url = f"http://127.0.0.1:{args.http_port}"
    lan_url = f"http://{lan_ip}:{args.http_port}"
    print("\n小菜蛾九设备监测服务已启动")
    print(f"本机浏览器：{local_url}")
    print(f"局域网访问：{lan_url}")
    print(f"K230 计数端口：TCP {args.tcp_port}")
    print(f"K230 图片端口：TCP {args.image_port}")
    print(f"自动发现：UDP {args.discovery_port}，每 10 秒刷新物理网卡地址")
    print("按 Ctrl+C 停止服务。\n")

    if not args.no_browser:
        threading.Timer(0.8, webbrowser.open, args=(local_url,)).start()
    try:
        http_server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        http_server.server_close()


if __name__ == "__main__":
    main()
