# 🍷 Bulbulator

**Fermentation airlock bubble counter for a webcam** — counts the bubbles in your wine or beer airlocks and shows live stats, history and trends on a web page.

[🇵🇱 Polska wersja](README.pl.md)

![Two carboys with S-shaped airlocks seen by the camera, the marked airlock areas drawn on the image](docs/airlocks.jpg)

*Two carboys of wine watched by Bulbulator — the coloured rectangles are the airlock areas marked on the page.*

![Bulbulator web page](docs/screenshot.png)

## Features

- Counts bubbles in **1–4 airlocks** at once, each with its own **fixed threshold** that only you change
- Live view per airlock: bubbles/s, /min, /hour, instant rate, last bubble, total
- Live detection signal with the threshold line — click or drag on it to set the threshold
- History chart (6 h / 24 h / 48 h / 7 days / all) and CSV download
- Fermentation statistics: averages, busiest hour, trend (speeding up / slowing down)
- Live camera image at the top of the page; the airlock frame flashes for every counted bubble
- Mark the airlocks with the mouse directly on the camera image
- **Pause** button for all airlocks (e.g. at night) and an **on/off** switch for each jar; paused time does not pull the charts and averages down
- **New fermentation** button: all counters and the history start from zero, the old data is archived as CSV
- Polish and English interface (flags in the top-right corner)
- Light on resources: ~15% of one core on a Raspberry Pi 4 / RK3399 board

## How it works

The camera image is analysed ~15 times per second at half resolution. Inside each marked rectangle the program measures the change between consecutive frames, subtracts the change of the whole picture (light flicker, camera noise) and looks for short peaks. A bubble is counted when this signal crosses the airlock's threshold upwards (and the *minimum time between bubbles* has passed). The threshold is fixed: the program never adjusts it. Only once, when an airlock has no threshold yet, it measures ~10 s of noise and saves a starting value, which you then tune on the page.

## Requirements

- Linux (Debian, Ubuntu, Raspberry Pi OS, Armbian…) with Python 3 and OpenCV
- A camera:
  - **MJPEG stream** (recommended): e.g. [ustreamer](https://github.com/pikvm/ustreamer), mjpg-streamer, or an IP camera with an MJPEG URL — the camera can then be used by other programs at the same time
  - or **a USB camera** read directly (`/dev/video0`)

## Quick start (try it without a camera)

```sh
sudo apt install python3-opencv python3-numpy
git clone https://github.com/myshak1/bulbulator.git
cd bulbulator
python3 tools/fake_stream.py 8090 &          # fake camera with two bubbling airlocks
BULBULATOR_STREAM=http://127.0.0.1:8090/stream \
BULBULATOR_HTTP=127.0.0.1:8088 \
BULBULATOR_DATA=./data BULBULATOR_STATE=./data \
python3 bulbulator.py
```

Open <http://127.0.0.1:8088/>, click **Mark airlock** on each card and drag a rectangle around each bright tube.

## Installation (runs as a service)

```sh
git clone https://github.com/myshak1/bulbulator.git
cd bulbulator
sudo ./install.sh
```

Then set your camera in `/etc/systemd/system/bulbulator.service` and restart:

```sh
sudo nano /etc/systemd/system/bulbulator.service
sudo systemctl daemon-reload && sudo systemctl restart bulbulator
```

The page is at `http://<your-device-ip>:8088/`.

### Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `BULBULATOR_STREAM` | `http://127.0.0.1:8090/stream` | MJPEG URL, or a camera: `0` / `/dev/video0` |
| `BULBULATOR_JARS` | `2` | number of airlocks (1–4) |
| `BULBULATOR_HTTP` | *(off)* | built-in web server address, e.g. `0.0.0.0:8088` |
| `BULBULATOR_AUTH` | *(none)* | login for the built-in server, `user:password` |
| `BULBULATOR_DATA` | `/var/lib/bulbulator` | config, history and counters |
| `BULBULATOR_STATE` | `/run/bulbulator` | live state and snapshot (best in RAM) |
| `BULBULATOR_WEB` | `./web` | folder with `index.html` |
| `BULBULATOR_FPS` | `15` | analysed frames per second |

### Security

The built-in server has **no login by default** — set `BULBULATOR_AUTH` or bind it to `127.0.0.1` if your network is not trusted. For access from the internet put it behind nginx with HTTPS: see [`deploy/nginx-bulbulator.conf`](deploy/nginx-bulbulator.conf) (password-protected, served at `/bulbulator/`).

## Tips for accurate counting

- Keep the camera and jars **still**, use **steady light** (an LED lamp, no sunlight moving across the tubes).
- A **dark background** behind the airlock and the rectangle covering **only the airlock with water** give the clearest peaks.
- Watch the small chart under each counter: every bubble should be a peak above the dashed line and the noise should stay below it. Bubbles missed → lower that airlock's threshold. Counting too many → raise it.
- Count by hand for one minute and compare with *Bubbles/min*.
- Three-piece airlocks (with a floating cap) are the easiest to detect.

## Files

| Path | Content |
|---|---|
| `DATA/config.json` | airlock areas, names, thresholds (written by the page) |
| `DATA/historia_min.csv` | `unix_time,count1,count2,…` per minute (last 48 h) |
| `DATA/historia_godz.csv` | the same per hour (whole history) |
| `DATA/liczniki.json` | total counters |
| `DATA/historia_*-YYYYmmdd-HHMM.csv` | history archived by *New fermentation* |
| `STATE/stan.json`, `STATE/snapshot.jpg` | live state and camera image for the page |

File and JSON key names are Polish (*gąsior* = carboy, *bąbel* = bubble).

## About

Bulbulator started with two carboys of homemade wine on a shelf and a USB camera already watching them. Instead of counting bubbles with a stopwatch, the camera now does it around the clock — the bubble rate is a simple way to follow how fermentation starts, peaks and slows down, and to tell when it is close to finished.

It runs on a small home server (a Radxa ROCK Pi 4B+ with a cheap USB camera), but any Linux computer with a camera will do. Made by **Kris, SP2KMO** — radio amateur and home winemaker. Ideas, bug reports and pull requests are welcome.

## License

Copyright (c) 2026 Kris SP2KMO — released under the [MIT License](LICENSE).
