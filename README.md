# MelandoShield Hardware 2

本项目使用 CanMV K230 摄像头检测粘虫板上的昆虫数量，并通过 Wi-Fi 将计数数据和检测图片发送到电脑网页。电脑端可查看农田热力图、监测点详情、最近图片和诱虫灯状态。

## 硬件

- CanMV K230 与 GC2093 摄像头
- WS2812 环形灯带 12 颗
- 电脑端 Python 服务
- 同一 Wi-Fi 或手机热点

WS2812 接线：数据线接 GPIO42，电源线和地线按灯带额定电压连接，K230 与灯带必须共地。

## 快速开始

1. 将 K230 和电脑连接到同一个 Wi-Fi 或手机热点。
2. 在电脑上双击 `Start_monitoring_service.bat`，浏览器打开 `http://127.0.0.1:8000`。
3. 在电脑终端运行 `ipconfig`，记录 `WLAN` 的 IPv4 地址。
4. 打开 `main.py`，修改顶部三项配置：

   ```python
   WIFI_SSID = "你的 Wi-Fi 名称"
   WIFI_PASSWORD = "你的 Wi-Fi 密码"
   SERVER_IP = "电脑 WLAN IPv4 地址"
   ```

5. 将 `main.py` 上传到 K230 的 `/sdcard/main.py`，重启并运行程序。
6. 正常连接后，在网页查看设备 1 的实时数量和每 10 秒上传的检测图片。

不要将 `127.0.0.1`、`198.18.0.1`、K230 自己的 IP 或热点网关填入 `SERVER_IP`。应填写电脑连接热点后 `WLAN` 网卡的 IPv4 地址。

## 正常日志

K230 连上 Wi-Fi 后会输出网络信息。成功连到电脑时，应出现：

```text
Connected to PC data server: (..., 10000)
Detection image uploaded: ... bytes
```

## 功能说明

- K230 持续采集图像并检测白色粘虫板上的深色目标。
- 最近 7 次有效检测结果取中位数，作为稳定虫子数量。
- 计数数据约每 0.5 秒发送一次，检测图片每 10 秒上传一次。
- WS2812 灯带启动后默认点亮；网页可以下发开关、亮度和目标波段设定。
- 真实检测图片保存到电脑端 `data/images/device-1`，最多保留最近 30 张。

## 端口

| 端口 | 用途 |
| --- | --- |
| TCP 10000 | K230 计数数据与灯光控制指令 |
| TCP 10002 | K230 检测图片上传 |
| HTTP 8000 | 电脑网页和状态接口 |

## 常见问题

`PC data server not ready: ETIMEDOUT` 或 `Detection image upload failed: ETIMEDOUT`：确认电脑服务已启动；两台设备在同一热点；`SERVER_IP` 是电脑 WLAN 地址；Windows 防火墙允许 Python 接收 TCP 10000 和 10002。

网页显示离线：确认 K230 日志持续出现 `Connected to PC data server`，并检查电脑服务窗口是否收到设备连接日志。

能看到数量但没有新图片：检查 TCP 10002 是否被防火墙拦截，并确认 K230 上传的是最新 `main.py`。

## 停止服务

双击 `Start_monitoring_service.bat`，或在启动服务的命令行按 `Ctrl+C`。

## 上传 GitHub 前

请删除或替换 `main.py` 中真实的 Wi-Fi 名称、Wi-Fi 密码和电脑 IP，避免将个人网络信息上传到公开仓库。
