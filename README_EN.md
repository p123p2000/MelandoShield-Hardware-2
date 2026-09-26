# MelandoShield Hardware 2

This project uses a CanMV K230 camera to detect insects on a sticky board. It sends insect counts and detection images to a computer over Wi-Fi. The computer dashboard displays the field heatmap, monitoring-point details, recent images, and attractant-light status.

## Hardware

- CanMV K230 with GC2093 camera
- 12-pixel WS2812 ring light
- Computer running the Python service
- One shared Wi-Fi network or mobile hotspot

WS2812 wiring: connect the data line to GPIO42. Connect power and ground according to the LED strip's rated voltage. The K230 and the LED strip must share a common ground.

## Quick start

1. Connect the K230 and the computer to the same Wi-Fi network or mobile hotspot.
2. On the computer, double-click `Start_monitoring_service.bat`. The browser will open `http://127.0.0.1:8000`.
3. Run `ipconfig` in a computer terminal and find the IPv4 address of the `WLAN` adapter.
4. Open `main.py` and update these three settings at the top:

   ```python
   WIFI_SSID = "your Wi-Fi name"
   WIFI_PASSWORD = "your Wi-Fi password"
   SERVER_IP = "computer WLAN IPv4 address"
   ```

5. Upload `main.py` to `/sdcard/main.py` on the K230, then restart and run it.
6. When the connection is working, the dashboard will show the live count from device 1 and a new detection image about every 10 seconds.

Do not use `127.0.0.1`, `198.18.0.1`, the K230's own IP address, or the hotspot gateway as `SERVER_IP`. Use the IPv4 address of the computer's `WLAN` adapter on the shared network.

## Normal log output

After the K230 joins Wi-Fi, it prints its network information. A successful computer connection produces messages similar to:

```text
Connected to PC data server: (..., 10000)
Detection image uploaded: ... bytes
```

## Main functions

- The K230 continuously captures images and detects dark targets on the white sticky board.
- The stable insect count is calculated as the median of the most recent seven valid detections.
- Count data is sent about every 0.5 seconds, and a detection image is uploaded every 10 seconds.
- The WS2812 ring light turns on at startup. The dashboard can send light on/off, brightness, and target-band settings.
- Real K230 images are stored in `data/images/device-1`. The service keeps the latest 30 images.

## Ports

| Port | Purpose |
| --- | --- |
| TCP 10000 | K230 count data and light-control commands |
| TCP 10002 | K230 detection-image upload |
| HTTP 8000 | Dashboard and status API |

## Troubleshooting

`PC data server not ready: ETIMEDOUT` or `Detection image upload failed: ETIMEDOUT`: make sure the computer service is running, both devices use the same hotspot, `SERVER_IP` is the computer's WLAN address, and Windows Firewall allows Python to receive TCP connections on ports 10000 and 10002.

Dashboard shows the device as offline: check that the K230 log repeatedly shows `Connected to PC data server`, and check the computer service window for device-connection messages.

Counts are visible but no new images appear: check whether TCP port 10002 is blocked by the firewall and confirm that the latest `main.py` has been uploaded to the K230.

## Stop the service

Double-click `Start_monitoring_service.bat`, or press `Ctrl+C` in the terminal running the service.

## Before uploading to GitHub

Replace or remove the real Wi-Fi name, Wi-Fi password, and computer IP address in `main.py`. Do not publish personal network information in a public repository.
