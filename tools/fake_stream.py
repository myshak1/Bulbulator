#!/usr/bin/env python3
# Bulbulator - https://github.com/myshak1/Bulbulator
# Copyright (c) 2026 Kris SP2KMO
# SPDX-License-Identifier: MIT
"""Fake MJPEG camera with two bubbling airlocks - for trying Bulbulator without a camera.

    python3 tools/fake_stream.py            # http://127.0.0.1:8090/stream
    BULBULATOR_STREAM=http://127.0.0.1:8090/stream BULBULATOR_HTTP=127.0.0.1:8088 \
    BULBULATOR_DATA=./data BULBULATOR_STATE=./data python3 bulbulator.py

Airlock 1 bubbles every 1.5 s, airlock 2 every 4 s.  Mark the two bright tubes
on the page and compare the counts.
"""
import http.server
import sys
import time

import cv2
import numpy as np

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
FPS = 15
RURKI = [((420, 200), 1.5), ((860, 200), 4.0)]   # (x, y) of the tube, seconds between bubbles
rng = np.random.default_rng(0)


def klatka(t):
    img = np.full((720, 1280), 60, np.float32) + rng.normal(0, 3, (720, 1280))
    for (x, y), okres in RURKI:
        cv2.rectangle(img, (x - 40, y), (x + 40, y + 320), 150, -1)
        faza = t % okres
        if faza < 0.3:
            cv2.circle(img, (x, int(y + 300 - faza * 900)), 22, 235, -1)
    return cv2.imencode('.jpg', np.clip(img, 0, 255).astype(np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith('/snapshot'):
            j = klatka(time.time())
            self.send_response(200); self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(len(j))); self.end_headers(); self.wfile.write(j)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'multipart/x-mixed-replace;boundary=f')
        self.end_headers()
        while True:
            j = klatka(time.time())
            try:
                self.wfile.write(b'--f\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n' % len(j) + j + b'\r\n')
            except Exception:
                return
            time.sleep(1 / FPS)


print(f'fake camera: http://127.0.0.1:{PORT}/stream')
http.server.ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
