#!/usr/bin/env python3
# Bulbulator - https://github.com/myshak1/Bulbulator
# Copyright (c) 2026 Kris SP2KMO
# SPDX-License-Identifier: MIT
"""Bulbulator - fermentation airlock bubble counter for a webcam.

Watches one or more wine/beer airlocks through a camera, counts the bubbles
that pass through each of them and keeps per-minute and per-hour statistics
for the web page in web/index.html.

Video source (BULBULATOR_STREAM):
  * an MJPEG stream URL, e.g. ustreamer / mjpg-streamer / many IP cameras
    http://127.0.0.1:8090/stream
  * or a local camera: a device number (0) or path (/dev/video0)

Files:
  STATE/stan.json          live state for the page (every second, keep it in RAM)
  STATE/snapshot.jpg       current camera image for drawing the airlock areas
  DATA/config.json         airlock areas, names and sensitivity (written by the page)
  DATA/historia_min.csv    bubbles per minute  (last 48 h)
  DATA/historia_godz.csv   bubbles per hour    (whole history)
  DATA/liczniki.json       total counters (survive restarts)

Bubble threshold: each airlock has a FIXED threshold ('prog') set on the page;
the program never changes it.  A bubble is counted when the signal rises above
the threshold (and at least przerwa_min seconds passed since the previous one).
Only when an airlock has no threshold yet (new install, upgrade from 1.2) the
program measures its noise for ~10 s once, saves a starting value and keeps it.

"New fermentation" on the page sets reset_stat in config.json: the counters of
all airlocks go to zero and the history files are moved aside as
historia_min-YYYYmmdd-HHMM.csv / historia_godz-YYYYmmdd-HHMM.csv.

Optional built-in web server (BULBULATOR_HTTP=0.0.0.0:8088) serves the page
and the files above, so nginx is not required.  JSON keys and file names are
Polish for compatibility with existing installations.
"""
import base64
import collections
import http.server
import json
import os
import sys
import threading
import time
import urllib.request

import cv2
import numpy as np

VERSION = '1.3'
HERE = os.path.dirname(os.path.abspath(__file__))

STREAM = os.environ.get('BULBULATOR_STREAM', 'http://127.0.0.1:8090/stream')
DATA = os.environ.get('BULBULATOR_DATA', '/var/lib/bulbulator')
STATE = os.environ.get('BULBULATOR_STATE', '/run/bulbulator')
WEB = os.environ.get('BULBULATOR_WEB', os.path.join(HERE, 'web'))
HTTP = os.environ.get('BULBULATOR_HTTP', '')          # e.g. "0.0.0.0:8088", empty = off
AUTH = os.environ.get('BULBULATOR_AUTH', '')          # e.g. "user:password", empty = no login
N = min(max(int(os.environ.get('BULBULATOR_JARS', '2')), 1), 4)
FPS_MAX = float(os.environ.get('BULBULATOR_FPS', '15'))

CONFIG = os.path.join(DATA, 'config.json')
LICZNIKI = os.path.join(DATA, 'liczniki.json')
HIST_MIN = os.path.join(DATA, 'historia_min.csv')
HIST_GODZ = os.path.join(DATA, 'historia_godz.csv')
STAN = os.path.join(STATE, 'stan.json')
SNAPSHOT = os.path.join(STATE, 'snapshot.jpg')
SKALA = 2          # frames are analysed at half resolution (much faster)

DOMYSLNA = {
    'gasiory': [{'nazwa': f'Gąsior {i + 1}', 'roi': None, 'reset': 0, 'prog': None} for i in range(N)],
    'czulosc': 4.0,       # only for the one-time starting threshold (noise widths above the background)
    'przerwa_min': 0.35,  # minimum time between two bubbles in one airlock [s]
    'obrot': False,       # page only: show the camera image rotated by 180 degrees
    'reset_stat': 0,      # time of the last "new fermentation" (all counters and history)
}


PROG_MIN, PROG_MAX = 0.05, 50.0   # allowed fixed threshold range (signal units)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def zapisz_json(sciezka, dane):
    tmp = sciezka + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(dane, f, ensure_ascii=False)
    os.replace(tmp, sciezka)


def wczytaj_json(sciezka, domyslne):
    try:
        with open(sciezka) as f:
            return json.load(f)
    except Exception:
        return domyslne


def liczba_lub(v, domyslna):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else domyslna


def normalizuj_cfg(c):
    """Validate config written by the page; unknown or broken values fall back to defaults."""
    cfg = json.loads(json.dumps(DOMYSLNA))
    if not isinstance(c, dict):
        c = {}
    cfg['czulosc'] = min(max(liczba_lub(c.get('czulosc'), 4.0), 0.5), 20.0)
    cfg['przerwa_min'] = min(max(liczba_lub(c.get('przerwa_min'), 0.35), 0.05), 5.0)
    cfg['obrot'] = bool(c.get('obrot', False))
    cfg['reset_stat'] = liczba_lub(c.get('reset_stat'), 0.0)
    zrodlo = c.get('gasiory') if isinstance(c.get('gasiory'), list) else []
    for i in range(N):
        g = zrodlo[i] if i < len(zrodlo) and isinstance(zrodlo[i], dict) else {}
        d = cfg['gasiory'][i]
        d['nazwa'] = str(g.get('nazwa') or d['nazwa'])[:40]
        roi = g.get('roi')
        if isinstance(roi, list) and len(roi) == 4 and all(isinstance(v, (int, float)) for v in roi):
            d['roi'] = [int(v) for v in roi]
        d['reset'] = liczba_lub(g.get('reset'), 0.0)
        d['czulosc'] = min(max(liczba_lub(g.get('czulosc'), cfg['czulosc']), 0.5), 20.0)
        p = liczba_lub(g.get('prog'), None)
        d['prog'] = None if p is None else round(min(max(p, PROG_MIN), PROG_MAX), 2)
    return cfg


class Detektor:
    """Counts a bubble when the airlock signal rises above a fixed threshold."""

    def __init__(self):
        self.prev = None
        self.pomiar = collections.deque(maxlen=150)    # only for the one-time starting threshold
        self.ponizej = True                            # signal was below the threshold
        self.ostatni = 0.0
        self.zdarzenia = collections.deque()           # bubble times (last hour)
        self.sygnal = collections.deque(maxlen=150)    # for the live chart

    def reset(self):
        self.zdarzenia.clear()
        self.ostatni = 0.0

    def startowy_prog(self, czulosc):
        """Starting threshold from ~10 s of noise (median + czulosc x MAD), used once."""
        h = np.fromiter(self.pomiar, float)
        tlo = float(np.median(h))
        odch = float(np.median(np.abs(h - tlo))) * 1.4826
        return round(min(max(tlo + czulosc * max(odch, 0.15) + 0.1, PROG_MIN), PROG_MAX), 2)

    def krok(self, wycinek, glob, t, prog, przerwa):
        """One frame. prog=None: no threshold yet, only measure the noise."""
        if self.prev is None or self.prev.shape != wycinek.shape:
            self.prev = wycinek
            return False
        d = float(cv2.absdiff(wycinek, self.prev).mean())
        self.prev = wycinek
        s = max(0.0, d - glob)                             # minus whole-frame change (light, noise)
        babel = False
        if prog is None:
            self.pomiar.append(s)
        else:
            self.pomiar.clear()
            if s > prog:
                if self.ponizej and t - self.ostatni >= przerwa:
                    self.ostatni = t
                    self.zdarzenia.append(t)
                    babel = True
                self.ponizej = False
            else:
                self.ponizej = True
        self.sygnal.append([round(t, 2), round(s, 2), prog if prog is not None else 0])
        while self.zdarzenia and t - self.zdarzenia[0] > 3600:
            self.zdarzenia.popleft()
        return babel

    def ile(self, t, okno):
        n = 0
        for z in reversed(self.zdarzenia):
            if t - z > okno:
                break
            n += 1
        return n


def klatki_mjpeg(url):
    """JPEG frames from an MJPEG stream, reconnecting automatically."""
    while True:
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                buf = b''
                while True:
                    kawal = r.read(65536)
                    if not kawal:
                        break
                    buf += kawal
                    while True:
                        a = buf.find(b'\xff\xd8')
                        if a < 0:
                            buf = buf[-1:]
                            break
                        b = buf.find(b'\xff\xd9', a + 2)
                        if b < 0:
                            buf = buf[a:]
                            break
                        yield 'jpg', buf[a:b + 2]
                        buf = buf[b + 2:]
                    if len(buf) > 5_000_000:
                        buf = b''
        except Exception as e:
            log('stream:', e)
        time.sleep(3)


def klatki_kamera(zrodlo):
    """BGR frames from a local camera (OpenCV VideoCapture), reopening it after errors."""
    src = int(zrodlo) if zrodlo.isdigit() else zrodlo
    while True:
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            log('camera: cannot open', zrodlo)
        while cap.isOpened():
            ok, frame = cap.read()
            if not ok:
                log('camera: read failed')
                break
            yield 'img', frame
        cap.release()
        time.sleep(3)


def klatki():
    if STREAM.startswith(('http://', 'https://')):
        return klatki_mjpeg(STREAM)
    return klatki_kamera(STREAM)


def dopisz(sciezka, linia):
    with open(sciezka, 'a') as f:
        f.write(linia + '\n')


def czas_linii(linia):
    """Time stamp of a history line, None for a broken line (e.g. NUL bytes after a power cut)."""
    try:
        return float(linia.split(',')[0])
    except ValueError:
        return None


def przytnij_minuty(teraz):
    try:
        with open(HIST_MIN, errors='replace') as f:
            linie = [l.replace('\0', '') for l in f]
        dobre = [l for l in linie if l.strip() and (czas_linii(l) or 0) > teraz - 48 * 3600]
        zle = sum(1 for l in linie if l.strip() and czas_linii(l) is None)
        if zle:
            log(f'history trim: skipped {zle} broken line(s)')
        with open(HIST_MIN + '.tmp', 'w') as f:
            f.writelines(dobre)
        os.replace(HIST_MIN + '.tmp', HIST_MIN)
    except FileNotFoundError:
        pass
    except Exception as e:
        log('history trim:', e)


def archiwizuj_historie(t):
    """New fermentation: move the history files aside, the page starts with empty charts."""
    znacznik = time.strftime('%Y%m%d-%H%M', time.localtime(t))
    for sciezka in (HIST_MIN, HIST_GODZ):
        if os.path.exists(sciezka):
            cel = sciezka[:-4] + '-' + znacznik + '.csv'
            try:
                os.replace(sciezka, cel)
                log('history archived:', cel)
            except OSError as e:
                log('history archive:', e)


def zapisz_prog(i, prog):
    """Store the one-time starting threshold in config.json, keeping everything else as it is."""
    c = wczytaj_json(CONFIG, {})
    if not isinstance(c, dict):
        c = {}
    gas = c.get('gasiory') if isinstance(c.get('gasiory'), list) else []
    while len(gas) <= i:
        gas.append({})
    if not isinstance(gas[i], dict):
        gas[i] = {}
    if liczba_lub(gas[i].get('prog'), None) is None:   # never overwrite a value set on the page
        gas[i]['prog'] = prog
        c['gasiory'] = gas
        zapisz_json(CONFIG, c)


# ---------------------------------------------------------------- web server (optional)

PLIKI = {   # URL path -> (file, content type); nothing else is served
    '/stan.json': (STAN, 'application/json'),
    '/snapshot.jpg': (SNAPSHOT, 'image/jpeg'),
    '/config.json': (CONFIG, 'application/json'),
    '/historia_min.csv': (HIST_MIN, 'text/csv; charset=utf-8'),
    '/historia_godz.csv': (HIST_GODZ, 'text/csv; charset=utf-8'),
    '/': (os.path.join(WEB, 'index.html'), 'text/html; charset=utf-8'),
    '/index.html': (os.path.join(WEB, 'index.html'), 'text/html; charset=utf-8'),
}


class Serwer(http.server.BaseHTTPRequestHandler):
    server_version = 'Bulbulator/' + VERSION

    def log_message(self, *a):
        pass

    def autoryzacja(self):
        if not AUTH:
            return True
        oczekiwane = 'Basic ' + base64.b64encode(AUTH.encode()).decode()
        if self.headers.get('Authorization') == oczekiwane:
            return True
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="Bulbulator"')
        self.send_header('Content-Length', '0')
        self.end_headers()
        return False

    def odpowiedz(self, kod, typ='text/plain', tresc=b''):
        self.send_response(kod)
        self.send_header('Content-Type', typ)
        self.send_header('Content-Length', str(len(tresc)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(tresc)

    def do_GET(self):
        if not self.autoryzacja():
            return
        sciezka = self.path.split('?', 1)[0]
        if sciezka not in PLIKI:
            return self.odpowiedz(404, tresc=b'not found')
        plik, typ = PLIKI[sciezka]
        try:
            with open(plik, 'rb') as f:
                self.odpowiedz(200, typ, f.read())
        except FileNotFoundError:
            self.odpowiedz(404, tresc=b'not found')

    do_HEAD = do_GET

    def do_PUT(self):
        if not self.autoryzacja():
            return
        if self.path.split('?', 1)[0] != '/config.json':
            return self.odpowiedz(405, tresc=b'method not allowed')
        n = int(self.headers.get('Content-Length') or 0)
        if n <= 0 or n > 16384:
            return self.odpowiedz(413, tresc=b'too large')
        try:
            dane = json.loads(self.rfile.read(n))
            if not isinstance(dane, dict):
                raise ValueError
        except ValueError:
            return self.odpowiedz(400, tresc=b'bad json')
        zapisz_json(CONFIG, dane)
        self.odpowiedz(204)


def uruchom_serwer():
    host, _, port = HTTP.rpartition(':')
    srv = http.server.ThreadingHTTPServer((host or '0.0.0.0', int(port)), Serwer)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f'web page: http://{host or "0.0.0.0"}:{port}/' + (' (login required)' if AUTH else ''))


# ---------------------------------------------------------------- main loop

def main():
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(STATE, exist_ok=True)
    log(f'Bulbulator {VERSION} start, source: {STREAM}, airlocks: {N}')
    if not os.path.exists(CONFIG):
        zapisz_json(CONFIG, DOMYSLNA)
    if HTTP:
        uruchom_serwer()
    cfg = normalizuj_cfg(wczytaj_json(CONFIG, DOMYSLNA))
    cfg_mtime = os.path.getmtime(CONFIG)

    def lista(klucz, domyslna):
        v = licz.get(klucz) if isinstance(licz.get(klucz), list) else []
        return (v + [domyslna] * N)[:N]
    licz = wczytaj_json(LICZNIKI, {})
    lacznie = lista('lacznie', 0)
    od = lista('od', time.time())
    reset_znany = lista('reset', 0)
    reset_stat_znany = liczba_lub(licz.get('reset_stat'), 0.0)

    det = [Detektor() for _ in range(N)]
    teraz = start = time.time()
    minuta = int(teraz // 60) * 60
    godzina = int(teraz // 3600) * 3600
    w_minucie = [0] * N
    w_godzinie = [0] * N
    prev_full = None
    ost_analiza = ost_stan = ost_cfg = ost_licz = ost_foto = 0.0
    klatek = 0
    fps = 0.0
    fps_t = teraz

    for rodzaj, klatka in klatki():
        t = time.time()
        if t - ost_analiza < 1.0 / FPS_MAX:
            continue
        ost_analiza = t

        # reload the config when the page changes it
        if t - ost_cfg > 2:
            ost_cfg = t
            try:
                m = os.path.getmtime(CONFIG)
                if m != cfg_mtime:
                    cfg_mtime = m
                    cfg = normalizuj_cfg(wczytaj_json(CONFIG, cfg))
                    log('new config:', json.dumps(cfg, ensure_ascii=False))
            except OSError:
                pass
            if cfg['reset_stat'] > reset_stat_znany:          # "new fermentation" on the page
                reset_stat_znany = cfg['reset_stat']
                archiwizuj_historie(t)
                for i in range(N):
                    lacznie[i] = 0
                    od[i] = t
                    det[i].reset()
                w_minucie = [0] * N
                w_godzinie = [0] * N
                minuta = int(t // 60) * 60
                godzina = int(t // 3600) * 3600
                ost_licz = 0.0                                # save the zeroed totals right away
                log('statistics reset: new fermentation')
            for i, g in enumerate(cfg['gasiory']):
                if g['reset'] > reset_znany[i]:
                    reset_znany[i] = g['reset']
                    lacznie[i] = 0
                    od[i] = t
                    det[i].reset()

        # current image for the page (every 2 s)
        if t - ost_foto >= 2:
            ost_foto = t
            try:
                jpg = klatka if rodzaj == 'jpg' else cv2.imencode('.jpg', klatka, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
                with open(SNAPSHOT + '.tmp', 'wb') as f:
                    f.write(jpg)
                os.replace(SNAPSHOT + '.tmp', SNAPSHOT)
            except Exception as e:
                log('snapshot:', e)

        if rodzaj == 'jpg':
            img = cv2.imdecode(np.frombuffer(klatka, np.uint8), cv2.IMREAD_REDUCED_GRAYSCALE_2)
        else:
            szary = cv2.cvtColor(klatka, cv2.COLOR_BGR2GRAY)
            img = cv2.resize(szary, (szary.shape[1] // SKALA, szary.shape[0] // SKALA), interpolation=cv2.INTER_AREA)
        if img is None:
            continue
        img = cv2.GaussianBlur(img, (3, 3), 0)
        if prev_full is not None and prev_full.shape == img.shape:
            glob = float(cv2.absdiff(img, prev_full).mean())
        else:
            glob = 0.0
        prev_full = img
        hh, ww = img.shape[:2]

        for i, g in enumerate(cfg['gasiory']):
            roi = g['roi']
            if not roi:
                continue
            x, y, w, h = (int(v / SKALA) for v in roi)
            x, y = max(0, min(x, ww - 1)), max(0, min(y, hh - 1))
            w, h = min(w, ww - x), min(h, hh - y)
            if w < 2 or h < 2:
                continue
            if det[i].krok(img[y:y + h, x:x + w], glob, t, g['prog'], cfg['przerwa_min']):
                lacznie[i] += 1
                w_minucie[i] += 1
                w_godzinie[i] += 1
            if g['prog'] is None and len(det[i].pomiar) >= det[i].pomiar.maxlen:
                g['prog'] = det[i].startowy_prog(g['czulosc'])
                zapisz_prog(i, g['prog'])
                log(f'airlock {i + 1}: starting threshold {g["prog"]} (fixed, change it on the page)')

        klatek += 1
        if t - fps_t >= 5:
            fps = klatek / (t - fps_t)
            klatek, fps_t = 0, t

        # history: every minute and every hour
        if t >= minuta + 60:
            dopisz(HIST_MIN, ','.join([str(minuta)] + [str(v) for v in w_minucie]))
            w_minucie = [0] * N
            minuta = int(t // 60) * 60
        if t >= godzina + 3600:
            dopisz(HIST_GODZ, ','.join([str(godzina)] + [str(v) for v in w_godzinie]))
            w_godzinie = [0] * N
            godzina = int(t // 3600) * 3600
            przytnij_minuty(t)

        # live state for the page (every second)
        if t - ost_stan >= 1:
            ost_stan = t
            gas = []
            for i, g in enumerate(cfg['gasiory']):
                d = det[i]
                zd = list(d.zdarzenia)[-2:]
                gas.append({
                    'nazwa': g['nazwa'],
                    'prog': g['prog'],
                    'aktywny': bool(g['roi']),
                    'bps': round(d.ile(t, 10) / 10, 2),
                    'bpm': d.ile(t, 60),
                    'bph': d.ile(t, 3600),
                    'ostatni': d.ostatni or None,
                    'odstep': round(zd[1] - zd[0], 2) if len(zd) == 2 else None,
                    'lacznie': lacznie[i],
                    'od': od[i],
                    'w_minucie': w_minucie[i],
                    'sygnal': list(d.sygnal),
                })
            zapisz_json(STAN, {'wersja': VERSION, 't': t, 'start': start, 'fps': round(fps, 1),
                               'przerwa_min': cfg['przerwa_min'], 'reset_stat': reset_stat_znany,
                               'gasiory': gas})

        # totals to disk once a minute (spares SD/eMMC)
        if t - ost_licz >= 60:
            ost_licz = t
            zapisz_json(LICZNIKI, {'lacznie': lacznie, 'od': od, 'reset': reset_znany,
                                   'reset_stat': reset_stat_znany})


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
