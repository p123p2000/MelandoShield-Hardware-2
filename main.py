'''
实验名称：白色背景昆虫检测与计数 + 小菜蛾诱虫灯
实验平台：01Studio CanMV K230 mini + 2.4寸mipi屏
说明：白色粘虫板背景昆虫检测（自适应亮度阈值）
      灯光由标志位控制开关（固定紫色，低亮度）
      核心算法参考：韦老师黄色粘虫板方案（已适配白色背景）
接线：WS2812环形灯带（12颗）→ GPIO42（绿线DATA）+ 3.3V（红线）+ GND（黑线）
教程：wiki.01studio.cc
'''

import gc
import time
import os
import network
import socket
import json

from media.sensor import *
from media.display import *
from media.media import *
from machine import Pin
from neopixel import NeoPixel


# ----------------------- 局域网配置 -----------------------
WIFI_SSID = "YOUR_WIFI_SSID"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"

# 固定电脑 Wi-Fi 的 IPv4 地址；连接超时且电脑 IP 变化时，修改后重新上传。
SERVER_IP = "192.168.1.100"
DATA_PORT = 10000
IMAGE_PORT = 10002
DISCOVERY_PORT = 10001
DISCOVERY_MAGIC = b"K230_INSECT_SERVER"
SEND_INTERVAL_MS = 500
IMAGE_INTERVAL_MS = 10000
IMAGE_QUALITY = 60
RECONNECT_INTERVAL_MS = 3000
WIFI_RETRY_INTERVAL_MS = 10000


# ----------------------- 屏幕与显示 -----------------------
#IMAGE_WIDTH  = 640
#IMAGE_HEIGHT = 480
IMAGE_WIDTH  = 1920
IMAGE_HEIGHT = 1080

# ----------------------- 小菜蛾诱虫灯 -----------------------
LED_PIN  = 42       # GPIO42 = PWM0
LED_COUNT = 12

# 基准紫色。网页的目标波长会映射为邻近的紫/蓝色输出。
PURPLE = (90, 25, 128)

np = NeoPixel(Pin(LED_PIN), LED_COUNT)

# 默认开灯，亮度和目标诱虫波段可以从网页实时调整。
lamp_on = True
light_brightness = 20
target_wavelength_nm = 400
last_ring_color = None

def set_ring(color):
    for i in range(LED_COUNT):
        np[i] = color
    np.write()

def get_lamp_color():
    """将 350-450nm 目标波段映射为邻近 RGB 输出，再应用亮度。"""
    wavelength = clamp(target_wavelength_nm, 350, 450)
    offset = wavelength - 400
    red = 80 - (offset * 50 // 100)
    green = 24 + (offset * 16 // 100)
    blue = 145 + (offset * 20 // 100)
    brightness = clamp(light_brightness, 0, 100)
    return (
        clamp(red * brightness // 100, 0, 255),
        clamp(green * brightness // 100, 0, 255),
        clamp(blue * brightness // 100, 0, 255),
    )

def update_lamp():
    """只在颜色发生变化时写 WS2812，避免每帧重复刷新。"""
    global last_ring_color
    target_color = get_lamp_color() if lamp_on else (0, 0, 0)
    if target_color != last_ring_color:
        set_ring(target_color)
        last_ring_color = target_color


# ----------------------- 检测区域 -----------------------
ROI_MARGIN = 16
DETECT_ROI = (
    ROI_MARGIN,
    ROI_MARGIN,
    IMAGE_WIDTH  - ROI_MARGIN * 2,
    IMAGE_HEIGHT - ROI_MARGIN * 2,
)


# ----------------------- 亮度自适应（适配白色背景）---------
L_CONTRAST = 14
MIN_DARK_L = 30
MAX_DARK_L = 72


# ----------------------- Blob 过滤 -----------------------
MIN_PIXELS       = 18
MIN_BOX_AREA    = 24
MAX_BOX_AREA    = 6000
MIN_WIDTH       = 3
MIN_HEIGHT      = 3
MAX_WIDTH       = 100
MAX_HEIGHT      = 100
MIN_DENSITY     = 0.12
MAX_ASPECT_RATIO = 8
MERGE_MARGIN    = 2
EDGE_GUARD      = 2


# ----------------------- 计数稳定 -----------------------
COUNT_HISTORY_SIZE = 7


# ----------------------- 全局状态 -----------------------
latest_stable_count = 0
latest_raw_count    = 0

wlan = None
discovery_socket = None
data_socket = None
active_server_ip = SERVER_IP
last_send_ms = 0
last_connect_attempt_ms = 0
last_wifi_attempt_ms = 0
last_discovery_poll_ms = 0
last_image_attempt_ms = 0
command_buffer = b""


# ----------------------- 辅助函数 -----------------------

def clamp(value, low, high):
    if value < low:
        return low
    if value > high:
        return high
    return value

def median_count(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2]

def blob_is_insect(blob):
    width  = blob.w()
    height = blob.h()
    area   = blob.area()

    if width < MIN_WIDTH or height < MIN_HEIGHT:
        return False
    if width > MAX_WIDTH or height > MAX_HEIGHT:
        return False
    if area > MAX_BOX_AREA:
        return False
    if blob.density() < MIN_DENSITY:
        return False

    short_side = min(width, height)
    long_side  = max(width, height)
    if (long_side / short_side) > MAX_ASPECT_RATIO:
        return False

    roi_x, roi_y, roi_w, roi_h = DETECT_ROI
    if blob.x() <= roi_x + EDGE_GUARD:
        return False
    if blob.y() <= roi_y + EDGE_GUARD:
        return False
    if (blob.x() + width) >= (roi_x + roi_w - EDGE_GUARD):
        return False
    if (blob.y() + height) >= (roi_y + roi_h - EDGE_GUARD):
        return False

    return True


# ----------------------- 局域网通信 -----------------------

def close_socket(sock):
    if sock is not None:
        try:
            sock.close()
        except Exception:
            pass

def connect_wifi(wait_for_result=True):
    global wlan, last_wifi_attempt_ms

    last_wifi_attempt_ms = time.ticks_ms()
    if wlan is None:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)

    if wlan.isconnected():
        print("Wi-Fi already connected, network information:", wlan.ifconfig())
        return True

    print("Wi-Fi connecting to %s ..." % WIFI_SSID)
    try:
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    except Exception as error:
        print("Wi-Fi connect error:", error)
        return False

    if not wait_for_result:
        return False

    start_ms = time.ticks_ms()
    while not wlan.isconnected():
        if time.ticks_diff(time.ticks_ms(), start_ms) > 12000:
            print("Wi-Fi connect timeout; insect detection will continue.")
            return False
        time.sleep_ms(200)

    print("Wi-Fi connected, network information:", wlan.ifconfig())
    return True

def open_discovery_socket():
    global discovery_socket

    if discovery_socket is not None:
        return
    try:
        discovery_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        discovery_socket.bind(("0.0.0.0", DISCOVERY_PORT))
        discovery_socket.settimeout(0)
        print("Waiting for PC server auto-discovery on UDP %d ..." % DISCOVERY_PORT)
    except Exception as error:
        close_socket(discovery_socket)
        discovery_socket = None
        print("Discovery socket error:", error)

def poll_server_discovery():
    global active_server_ip, discovery_socket, last_discovery_poll_ms

    if active_server_ip:
        return
    now_ms = time.ticks_ms()
    if time.ticks_diff(now_ms, last_discovery_poll_ms) < 250:
        return
    last_discovery_poll_ms = now_ms
    open_discovery_socket()
    if discovery_socket is None:
        return
    try:
        message, sender = discovery_socket.recvfrom(128)
        if message.split(b"|")[0] == DISCOVERY_MAGIC:
            active_server_ip = sender[0]
            print("PC server discovered:", active_server_ip)
            close_socket(discovery_socket)
            discovery_socket = None
    except Exception:
        pass

def connect_data_server(now_ms):
    global data_socket, active_server_ip, command_buffer, last_connect_attempt_ms

    if data_socket is not None or not active_server_ip:
        return
    if time.ticks_diff(now_ms, last_connect_attempt_ms) < RECONNECT_INTERVAL_MS:
        return

    last_connect_attempt_ms = now_ms
    candidate = None
    try:
        address = socket.getaddrinfo(active_server_ip, DATA_PORT)[0][-1]
        candidate = socket.socket()
        candidate.settimeout(2)
        candidate.connect(address)
        candidate.settimeout(0)
        data_socket = candidate
        command_buffer = b""
        print("Connected to PC data server:", address)
    except Exception as error:
        close_socket(candidate)
        print("PC data server not ready:", error)
        if not SERVER_IP:
            active_server_ip = ""

def apply_light_command(command):
    global lamp_on, light_brightness, target_wavelength_nm

    if command.get("type") != "light_control":
        return
    lamp_on = bool(command.get("enabled", lamp_on))
    light_brightness = clamp(int(command.get("brightness", light_brightness)), 0, 100)
    target_wavelength_nm = clamp(
        int(command.get("wavelength_nm", target_wavelength_nm)),
        350,
        450,
    )
    update_lamp()
    print("Light control: enabled=%s brightness=%d wavelength=%dnm color=%s" % (
        "ON" if lamp_on else "OFF",
        light_brightness,
        target_wavelength_nm,
        str(get_lamp_color()),
    ))

def poll_server_commands():
    global data_socket, command_buffer

    if data_socket is None:
        return
    try:
        received = data_socket.recv(512)
        if not received:
            # CanMV 的非阻塞 socket 在暂时没有数据时可能返回 b""，
            # 不能据此判断服务器已断开；真正断线会在后续 send() 时抛出异常。
            return
        command_buffer += received
        while b"\n" in command_buffer:
            line, command_buffer = command_buffer.split(b"\n", 1)
            if line:
                try:
                    apply_light_command(json.loads(line.decode("utf-8")))
                except Exception as error:
                    print("Light command parse failed:", error)
    except Exception:
        pass

def network_tick(stable_count, raw_count, board_l, threshold_l, fps):
    global data_socket, active_server_ip, command_buffer, last_send_ms

    now_ms = time.ticks_ms()
    if wlan is None or not wlan.isconnected():
        close_socket(data_socket)
        data_socket = None
        command_buffer = b""
        if not SERVER_IP:
            active_server_ip = ""
        if time.ticks_diff(now_ms, last_wifi_attempt_ms) >= WIFI_RETRY_INTERVAL_MS:
            connect_wifi(wait_for_result=False)
        return

    poll_server_discovery()
    connect_data_server(now_ms)

    if data_socket is None:
        return
    poll_server_commands()
    if data_socket is None:
        return
    if time.ticks_diff(now_ms, last_send_ms) < SEND_INTERVAL_MS:
        return

    lamp_text = "true" if lamp_on else "false"
    message = (
        '{"device":"k230","count":%d,"raw_count":%d,'
        '"background_l":%d,"threshold_l":%d,"fps":%.1f,'
        '"lamp_on":%s,"light_brightness":%d,"wavelength_nm":%d,'
        '"uptime_ms":%d}\n'
    ) % (
        stable_count, raw_count, board_l, threshold_l, fps,
        lamp_text, light_brightness, target_wavelength_nm, now_ms,
    )

    try:
        data_socket.send(message.encode("utf-8"))
        last_send_ms = now_ms
        poll_server_commands()
    except Exception as error:
        print("Data send failed; reconnecting:", error)
        close_socket(data_socket)
        data_socket = None
        command_buffer = b""
        if not SERVER_IP:
            active_server_ip = ""

def send_all(sock, data):
    view = memoryview(data)
    sent_total = 0
    while sent_total < len(view):
        sent = sock.send(view[sent_total:])
        if sent is None or sent <= 0:
            raise OSError("socket send interrupted")
        sent_total += sent

def image_tick(img, stable_count):
    """每 10 秒压缩并上传一张带检测框的 JPEG 图片。"""
    global last_image_attempt_ms

    now_ms = time.ticks_ms()
    if wlan is None or not wlan.isconnected() or not active_server_ip:
        return
    if time.ticks_diff(now_ms, last_image_attempt_ms) < IMAGE_INTERVAL_MS:
        return

    last_image_attempt_ms = now_ms
    image_socket = None
    compressed_image = None
    try:
        # compressed() 返回新的 JPEG 图像，不改变当前屏幕显示的 RGB565 图像。
        compressed_image = img.compressed(quality=IMAGE_QUALITY)
        image_bytes = compressed_image.bytearray()
        header = (
            '{"device":"device-1","length":%d,"count":%d,'
            '"width":%d,"height":%d,"uptime_ms":%d}\n'
        ) % (
            len(image_bytes), stable_count,
            IMAGE_WIDTH, IMAGE_HEIGHT, now_ms,
        )

        address = socket.getaddrinfo(active_server_ip, IMAGE_PORT)[0][-1]
        image_socket = socket.socket()
        image_socket.settimeout(5)
        image_socket.connect(address)
        send_all(image_socket, header.encode("utf-8"))
        send_all(image_socket, image_bytes)
        print("Detection image uploaded: %d bytes" % len(image_bytes))
    except Exception as error:
        print("Detection image upload failed:", error)
    finally:
        close_socket(image_socket)
        if compressed_image is not None:
            del compressed_image


# ----------------------- 启动：先开灯，再初始化显示、摄像头及网络 -----------------------

update_lamp()
print("Startup: WS2812 ON, brightness=%d%%" % light_brightness)

Display.init(Display.VIRT, IMAGE_WIDTH, IMAGE_WIDTH, to_ide=True)

sensor = Sensor(width=1920, height=1080)
sensor.reset()
sensor.set_framesize(width=IMAGE_WIDTH, height=IMAGE_HEIGHT)
sensor.set_pixformat(Sensor.RGB565)

MediaManager.init()
sensor.run()

connect_wifi(wait_for_result=True)
if wlan is not None and wlan.isconnected() and not SERVER_IP:
    open_discovery_socket()

time.sleep_ms(800)
for _ in range(5):
    sensor.snapshot()

clock         = time.clock()
count_history = []
dark_l        = None


# ----------------------- 主循环 -----------------------

while True:
    os.exitpoint()
    clock.tick()

    img = sensor.snapshot()

    board_stats = img.get_statistics(roi=DETECT_ROI)
    board_l     = board_stats.l_median()
    candidate_l = clamp(board_l - L_CONTRAST, MIN_DARK_L, MAX_DARK_L)

    if dark_l is None:
        dark_l = candidate_l
    else:
        dark_l = (dark_l * 3 + candidate_l) // 4

    dark_threshold = [(0, dark_l, -128, 127, -128, 127)]

    blobs = img.find_blobs(
        dark_threshold,
        roi=DETECT_ROI,
        pixels_threshold=MIN_PIXELS,
        area_threshold=MIN_BOX_AREA,
        merge=True,
        margin=MERGE_MARGIN,
    )

    insects = []
    for blob in blobs:
        if blob_is_insect(blob):
            insects.append(blob)

    latest_raw_count    = len(insects)
    count_history.append(latest_raw_count)
    if len(count_history) > COUNT_HISTORY_SIZE:
        count_history.pop(0)
    latest_stable_count = median_count(count_history)
    current_fps         = clock.fps()

    for index, blob in enumerate(insects):
        img.draw_rectangle(blob.rect(), color=(0, 255, 0), thickness=2)
        img.draw_cross(blob.cx(), blob.cy(), color=(255, 0, 0), thickness=2)
        label_y = max(blob.y() - 18, ROI_MARGIN)
        img.draw_string_advanced(blob.x(), label_y, 16, str(index + 1), color=(0, 255, 0))

    img.draw_rectangle(DETECT_ROI, color=(0, 128, 255), thickness=1)

    img.draw_string_advanced(
        4, 4, 24,
        "INSECTS:%d  RAW:%d" % (latest_stable_count, latest_raw_count),
        color=(255, 255, 255),
    )
    img.draw_string_advanced(
        4, 32, 18,
        "BG-L:%d  DARK-L:%d  FPS:%.1f" % (board_l, dark_l, current_fps),
        color=(255, 255, 255),
    )
    img.draw_string_advanced(
        4, 54, 16,
        "LAMP:%s B:%d W:%dnm" % (
            "ON" if lamp_on else "OFF",
            light_brightness,
            target_wavelength_nm,
        ),
        color=(255, 255, 255),
    )

    update_lamp()
    Display.show_image(img)
    print("insects=%d raw=%d bg_l=%d lamp=%s" % (
        latest_stable_count, latest_raw_count, board_l,
        "ON" if lamp_on else "OFF"))

    network_tick(
        latest_stable_count,
        latest_raw_count,
        board_l,
        dark_l,
        current_fps,
    )
    image_tick(img, latest_stable_count)

    gc.collect()
