#!/usr/bin/env python3
import os
import select
import sys
import threading
import time

from evdev import InputDevice, list_devices, ecodes
from PyQt5 import QtCore, QtGui, QtWidgets

# ---------- settings ----------
TEST_MODE = False         # True = generated placeholder keys, False = your PNGs
KEYS_DIR = "/home/yuki/Desktop/Keys"
KEY_SIZE = 90            # each key cell is KEY_SIZE x KEY_SIZE pixels
SPACING = 4              # gap between keys
SHOW_TIMER = True        # hold timer inside the key
TIMER_FONT_SIZE = 11     # timer text size
TIMER_BOTTOM_MARGIN = 6  # pixels above the bottom edge of the key image
TIMER_DECIMALS = 2       # 1 -> 0.4s, 2 -> 0.43s
CLICK_THROUGH = False    # True = mouse clicks pass through (can't drag it)
KILL_KEY = ecodes.KEY_KP9  # press this key to close the overlay (numpad 9)

KEY_OPACITY_OFF = 0.45   # idle key opacity   (0.0 = invisible ... 1.0 = solid)
KEY_OPACITY_ON = 0.85    # pressed key opacity

BG_IMAGE = "background.png"  # file inside KEYS_DIR, "" = no background
BG_OPACITY = 0.35        # background opacity (0.0 = invisible ... 1.0 = solid)
BG_PADDING = 8           # border between the keys and the edge of the background
BG_RADIUS = 10           # rounded corners of the background, 0 = square
# ------------------------------

# (slot name, row, column). The slot name is also the image file prefix,
# so "W" loads W_off.png / W_on.png even though it is now the Up arrow.
LAYOUT = [
    ("W", 0, 1),
    ("A", 1, 0), ("S", 1, 1), ("D", 1, 2),
    ("Z", 2, 0), ("X", 2, 1), ("C", 2, 2),
]

# physical key -> slot name
KEYMAP = {
    ecodes.KEY_UP: "W",
    ecodes.KEY_LEFT: "A",
    ecodes.KEY_DOWN: "S",
    ecodes.KEY_RIGHT: "D",
    ecodes.KEY_Z: "Z",
    ecodes.KEY_X: "X",
    ecodes.KEY_C: "C",
}

ARROWS = {"W": "↑", "A": "←", "S": "↓", "D": "→"}  # test mode labels only


class Bridge(QtCore.QObject):
    changed = QtCore.pyqtSignal(str, bool)  # slot name, is_down
    kill = QtCore.pyqtSignal()              # killswitch pressed


def find_keyboards():
    devs = []
    for path in list_devices():
        try:
            dev = InputDevice(path)
        except OSError:
            continue
        keys = dev.capabilities().get(ecodes.EV_KEY, [])
        if ecodes.KEY_UP in keys and ecodes.KEY_Z in keys:
            devs.append(dev)
    return devs


def reader(devs, bridge):
    fds = {d.fd: d for d in devs}
    while fds:
        r, _, _ = select.select(list(fds), [], [])
        for fd in r:
            try:
                for ev in fds[fd].read():
                    if ev.type == ecodes.EV_KEY and ev.code == KILL_KEY and ev.value == 1:
                        bridge.kill.emit()
                    elif ev.type == ecodes.EV_KEY and ev.code in KEYMAP:
                        if ev.value == 1:
                            bridge.changed.emit(KEYMAP[ev.code], True)
                        elif ev.value == 0:
                            bridge.changed.emit(KEYMAP[ev.code], False)
            except OSError:
                fds.pop(fd, None)


def load_pix(name):
    path = os.path.join(KEYS_DIR, name)
    pix = QtGui.QPixmap(path)
    if pix.isNull():
        raise SystemExit(f"Could not load image: {path}")
    return pix.scaled(KEY_SIZE, KEY_SIZE, QtCore.Qt.KeepAspectRatio,
                      QtCore.Qt.SmoothTransformation)


def make_test_pix(key, on):
    s = KEY_SIZE
    pix = QtGui.QPixmap(s, s)
    pix.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pix)
    p.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing)

    skew = s * 0.18
    shape = QtGui.QPolygonF([
        QtCore.QPointF(skew, s * 0.1), QtCore.QPointF(s - 2, s * 0.1),
        QtCore.QPointF(s - 2 - skew, s * 0.9), QtCore.QPointF(2, s * 0.9),
    ])
    p.setPen(QtGui.QPen(QtGui.QColor("#888888"), 2))
    p.setBrush(QtGui.QColor("#fdfb8a") if on else QtGui.QColor(235, 235, 235, 190))
    p.drawPolygon(shape)

    font = QtGui.QFont("Sans", int(s * 0.28), QtGui.QFont.Bold)
    font.setItalic(True)
    p.setFont(font)
    p.setPen(QtGui.QColor("#333333"))
    label = ARROWS.get(key, key)
    p.drawText(QtCore.QRectF(0, s * 0.1, s, s * 0.62), QtCore.Qt.AlignCenter, label)
    p.end()
    return pix


class KeyWidget(QtWidgets.QWidget):
    def __init__(self, key):
        super().__init__()
        if TEST_MODE:
            self.off = make_test_pix(key, False)
            self.on = make_test_pix(key, True)
        else:
            self.off = load_pix(f"{key}_off.png")
            self.on = load_pix(f"{key}_on.png")
        self.down = False
        self.t0 = 0.0
        self.setFixedSize(KEY_SIZE, KEY_SIZE)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.timer_font = QtGui.QFont("Sans", TIMER_FONT_SIZE, QtGui.QFont.Bold)

    def set_down(self, down):
        if down == self.down:
            return
        self.down = down
        if down:
            self.t0 = time.monotonic()
        self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHints(QtGui.QPainter.SmoothPixmapTransform
                         | QtGui.QPainter.Antialiasing
                         | QtGui.QPainter.TextAntialiasing)
        pix = self.on if self.down else self.off
        x = (self.width() - pix.width()) // 2
        y = (self.height() - pix.height()) // 2
        p.setOpacity(KEY_OPACITY_ON if self.down else KEY_OPACITY_OFF)
        p.drawPixmap(x, y, pix)
        p.setOpacity(1.0)  # timer text stays fully readable

        if SHOW_TIMER and self.down:
            text = f"{time.monotonic() - self.t0:.{TIMER_DECIMALS}f}s"
            fm = QtGui.QFontMetrics(self.timer_font)
            tx = (self.width() - fm.horizontalAdvance(text)) / 2
            ty = y + pix.height() - TIMER_BOTTOM_MARGIN
            path = QtGui.QPainterPath()
            path.addText(tx, ty, self.timer_font, text)
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 230), 3,
                                QtCore.Qt.SolidLine, QtCore.Qt.RoundCap,
                                QtCore.Qt.RoundJoin))
            p.drawPath(path)                      # white outline
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor("#333333"))
            p.drawPath(path)                      # dark fill


class Overlay(QtWidgets.QWidget):
    def __init__(self, bridge):
        super().__init__()
        self.bg_src = None    # original background image
        self.bg_cache = None  # background scaled + cropped to the window size
        if BG_IMAGE:
            bg_path = os.path.join(KEYS_DIR, BG_IMAGE)
            pix = QtGui.QPixmap(bg_path)
            if pix.isNull():
                print(f"Background not found, running without it: {bg_path}")
            else:
                self.bg_src = pix

        flags = (QtCore.Qt.FramelessWindowHint
                 | QtCore.Qt.WindowStaysOnTopHint
                 | QtCore.Qt.Tool)
        if CLICK_THROUGH:
            flags |= QtCore.Qt.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)

        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(BG_PADDING, BG_PADDING, BG_PADDING, BG_PADDING)
        grid.setSpacing(SPACING)

        self.keys = {}
        for k, row, col in LAYOUT:
            w = KeyWidget(k)
            grid.addWidget(w, row, col)
            self.keys[k] = w

        bridge.changed.connect(self.on_change)

        # redraw held keys so the timer ticks
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(33)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.bg_cache = None
        if self.bg_src is not None and self.width() > 0 and self.height() > 0:
            # scale until the image covers the window, then crop the centre
            scaled = self.bg_src.scaled(self.size(),
                                        QtCore.Qt.KeepAspectRatioByExpanding,
                                        QtCore.Qt.SmoothTransformation)
            x = (scaled.width() - self.width()) // 2
            y = (scaled.height() - self.height()) // 2
            self.bg_cache = scaled.copy(x, y, self.width(), self.height())

    def paintEvent(self, _):
        if self.bg_cache is None:
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        if BG_RADIUS > 0:
            clip = QtGui.QPainterPath()
            clip.addRoundedRect(QtCore.QRectF(self.rect()), BG_RADIUS, BG_RADIUS)
            p.setClipPath(clip)
        p.setOpacity(BG_OPACITY)
        p.drawPixmap(0, 0, self.bg_cache)

    def on_change(self, key, down):
        self.keys[key].set_down(down)

    def tick(self):
        for w in self.keys.values():
            if w.down:
                w.update()

    # drag the overlay with the left mouse button
    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            handle = self.windowHandle()
            if handle and hasattr(handle, "startSystemMove"):
                handle.startSystemMove()
            else:
                self._drag = e.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if hasattr(self, "_drag") and e.buttons() & QtCore.Qt.LeftButton:
            self.move(e.globalPos() - self._drag)


def main():
    devs = find_keyboards()
    if not devs:
        raise SystemExit("No keyboard found. Are you in the 'input' group?")

    app = QtWidgets.QApplication(sys.argv)
    bridge = Bridge()
    bridge.kill.connect(app.quit)
    win = Overlay(bridge)
    win.show()
    threading.Thread(target=reader, args=(devs, bridge), daemon=True).start()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
