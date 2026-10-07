#!/usr/bin/env python3
"""Stream a Mac audio input (e.g. the Hollyland wireless mic on the Mac mini) to the Oops app.

The phone connects to 127.0.0.1:PORT, which `adb reverse` maps back to this server over the USB
debugging cable; the app's RemoteAudioSource ("Mac 收音") reads it. Frames are
1-byte type | 4-byte big-endian length | payload:
  0x01 audio  16 kHz mono s16le PCM, 100 ms per frame
  0x02 mark   JSON {"kind":"double_tap"}  (= the 录音豆 recall gesture)
  0x03 hello  JSON device info, sent once per connection

  python3 scripts/mac_audio_bridge.py --list
  python3 scripts/mac_audio_bridge.py --adb-reverse                     # default: "Wireless microphone"
  python3 scripts/mac_audio_bridge.py --device 1 --adb-reverse          # by index from --list
  python3 scripts/mac_audio_bridge.py --wav android/app/src/main/assets/test/dialog.wav --adb-reverse

The mic is captured continuously and forwarded to whichever phone is connected (newest wins);
a WAV restarts from the beginning for every connection and is followed by silence.
While streaming, press Enter (or `kill -USR1 <pid>`) to send a double-tap mark.
macOS asks once for microphone access for the app running this script (Terminal / Claude).
"""
import argparse
import json
import math
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

AUDIO, MARK, HELLO = 0x01, 0x02, 0x03
RATE, FRAME_MS = 16000, 100
FRAME_BYTES = RATE * 2 * FRAME_MS // 1000
DEFAULT_DEVICE = "Wireless microphone"
SEND_TIMEOUT_S = 2.0


def log(message):
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


def ffmpeg_path():
    path = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    if not os.path.exists(path):
        sys.exit("需要 ffmpeg：brew install ffmpeg")
    return path


def list_devices():
    """AVFoundation audio inputs as [(index, name)]."""
    out = subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
        capture_output=True, text=True,
    ).stderr
    devices, in_audio = [], False
    for line in out.splitlines():
        if "AVFoundation audio devices" in line:
            in_audio = True
        elif "AVFoundation video devices" in line:
            in_audio = False
        elif in_audio:
            m = re.search(r"\[(\d+)\] (.+)$", line)
            if m:
                devices.append((int(m.group(1)), m.group(2).strip()))
    return devices


def resolve_device(spec):
    devices = list_devices()
    if not devices:
        sys.exit("没有找到音频输入设备（ffmpeg avfoundation 列表为空）")
    if spec is not None and spec.isdigit():
        match = [d for d in devices if d[0] == int(spec)]
    else:
        wanted = (spec or DEFAULT_DEVICE).lower()
        match = [d for d in devices if wanted in d[1].lower()]
    if not match:
        names = ", ".join(f"[{i}] {n}" for i, n in devices)
        sys.exit(f"找不到设备 {spec or DEFAULT_DEVICE!r}；可用：{names}")
    return match[0]


def adb_path(explicit):
    for candidate in (explicit, shutil.which("adb"),
                      os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")):
        if candidate and os.path.exists(candidate):
            return candidate
    sys.exit("找不到 adb；用 --adb 指定路径")


def ffmpeg_command(args, device_index):
    """ffmpeg emitting 16 kHz mono s16le on stdout."""
    filters = []
    if args.channel == "left":
        filters.append("pan=mono|c0=c0")
    elif args.channel == "right":
        filters.append("pan=mono|c0=c1")
    if args.gain_db:
        filters.append(f"volume={args.gain_db}dB")
    cmd = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-nostdin"]
    if args.wav:
        cmd += ["-re", "-i", args.wav]  # -re paces the file at real time, like a live mic
    else:
        cmd += ["-f", "avfoundation", "-i", f":{device_index}"]
    if filters:
        cmd += ["-af", ",".join(filters)]
    return cmd + ["-ac", "1", "-ar", str(RATE), "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"]


def read_frame(stream):
    """Exactly one 100 ms frame, or the short remainder at end of stream."""
    data = b""
    while len(data) < FRAME_BYTES:
        chunk = stream.read(FRAME_BYTES - len(data))
        if not chunk:
            break
        data += chunk
    return data


def dbfs(frame):
    n = len(frame) // 2
    if n == 0:
        return -120.0
    samples = struct.unpack(f"<{n}h", frame[: n * 2])
    rms = math.sqrt(sum(s * s for s in samples) / n)
    return 20 * math.log10(rms / 32768) if rms > 0 else -120.0


class Meter:
    """One-line level display plus a hint when the mic stays silent."""

    def __init__(self, label, warn_silence):
        self.label, self.warn_silence = label, warn_silence
        self.frames, self.last_draw, self.silent_since = 0, 0.0, time.monotonic()

    def update(self, frame, connected):
        self.frames += 1
        level, now = dbfs(frame), time.monotonic()
        if level > -60:
            self.silent_since = now
        elif self.warn_silence and now - self.silent_since > 8:
            log("\n连续 8 秒几乎没有声音：确认无线麦已开机、接收器插好，或检查麦克风权限")
            self.silent_since = now
        if now - self.last_draw >= 0.5:
            self.last_draw = now
            bar = "▮" * max(0, min(20, int((level + 60) / 3)))
            state = "→ 手机" if connected else "（等待手机连接）"
            sys.stderr.write(f"\r{self.label} {level:6.1f} dBFS {bar:<20} {state}   ")
            sys.stderr.flush()


class Session:
    """One phone connection."""

    def __init__(self, conn, label, mode):
        self.conn = conn
        self.alive = True
        self.lock = threading.Lock()
        conn.settimeout(SEND_TIMEOUT_S)  # a stuck phone must not stall capture
        self.send(HELLO, json.dumps({"device": label, "rate": RATE, "channels": 1,
                                     "format": "s16le", "mode": mode}, ensure_ascii=False).encode())

    def send(self, kind, payload):
        with self.lock:
            self.conn.sendall(struct.pack(">BI", kind, len(payload)) + payload)

    def try_send(self, kind, payload):
        if not self.alive:
            return False
        try:
            self.send(kind, payload)
            return True
        except OSError:
            self.close()
            log("\n手机已断开，等待重新连接…")
            return False

    def close(self):
        self.alive = False
        try:
            self.conn.close()
        except OSError:
            pass


class MicCapture(threading.Thread):
    """Keeps ffmpeg capturing and forwards frames to the current session."""

    def __init__(self, args, device, current):
        super().__init__(daemon=True)
        self.args, self.device, self.current = args, device, current
        self.meter = Meter(device[1], warn_silence=True)

    def run(self):
        while True:
            proc = subprocess.Popen(ffmpeg_command(self.args, self.device[0]),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            while True:
                frame = read_frame(proc.stdout)
                if len(frame) < FRAME_BYTES:
                    break
                session = self.current["session"]
                connected = bool(session and session.try_send(AUDIO, frame))
                self.meter.update(frame, connected)
            err = proc.stderr.read().decode(errors="replace").strip()
            proc.kill()
            log(f"\nffmpeg 停止采集：{err or '没有输出'}；2 秒后重试")
            log("若是权限问题：系统设置 → 隐私与安全性 → 麦克风，允许运行本脚本的应用")
            time.sleep(2)


def stream_wav(args, session):
    """Plays the file in real time for this connection, then sends silence until it closes."""
    meter = Meter(os.path.basename(args.wav), warn_silence=False)
    proc = subprocess.Popen(ffmpeg_command(args, -1), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while session.alive:
            frame = read_frame(proc.stdout)
            if len(frame) < FRAME_BYTES:
                if frame:
                    session.try_send(AUDIO, frame.ljust(FRAME_BYTES, b"\0"))
                break
            if not session.try_send(AUDIO, frame):
                return
            meter.update(frame, True)
        log("\n测试音频播完，继续发送静音（保持会话时钟）")
        silence, next_at = bytes(FRAME_BYTES), time.monotonic()
        while session.try_send(AUDIO, silence):
            next_at += FRAME_MS / 1000
            time.sleep(max(0.0, next_at - time.monotonic()))
    finally:
        proc.kill()


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--list", action="store_true", help="列出可用的音频输入设备")
    p.add_argument("--device", help=f"设备名片段或序号（默认 {DEFAULT_DEVICE!r}）")
    p.add_argument("--channel", choices=["mix", "left", "right"], default="mix",
                   help="双声道接收器：mix 混合（默认），或只取左/右（两个发射器各占一路时）")
    p.add_argument("--gain-db", type=float, default=0.0, help="增益（dB），声音太小时用")
    p.add_argument("--wav", help="用音频文件代替麦克风（任意格式，按实时速度发送）")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7790)
    p.add_argument("--adb-reverse", action="store_true", help="自动执行并在退出时撤除 adb reverse")
    p.add_argument("--adb", help="adb 路径")
    args = p.parse_args()

    if args.list:
        for index, name in list_devices():
            print(f"[{index}] {name}")
        return
    if args.wav and not os.path.exists(args.wav):
        sys.exit(f"找不到文件：{args.wav}")
    device = None if args.wav else resolve_device(args.device)

    adb = adb_path(args.adb) if args.adb_reverse else None
    if adb:
        subprocess.run([adb, "reverse", f"tcp:{args.port}", f"tcp:{args.port}"], check=True)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.host, args.port))
    server.listen(2)
    current = {"session": None}

    def send_mark(*_):
        session = current["session"]
        if session and session.try_send(MARK, json.dumps({"kind": "double_tap"}).encode()):
            log("\n→ 已发送双击标记（触发召回）")
        else:
            log("\n还没有手机连接，标记未发送")

    signal.signal(signal.SIGUSR1, send_mark)
    if sys.stdin.isatty():
        threading.Thread(target=lambda: [send_mark() for _ in sys.stdin], daemon=True).start()

    if args.wav:
        log(f"Mac 收音（测试音频）：{args.wav} → {args.host}:{args.port}（pid {os.getpid()}）")
    else:
        log(f"Mac 收音：[{device[0]}] {device[1]} → {args.host}:{args.port}（pid {os.getpid()}）")
        MicCapture(args, device, current).start()
    log("手机 App：调试设置 → 音源选「Mac 收音」→ 开始收音。回车 = 双击召回，Ctrl+C 退出。")
    try:
        while True:
            conn, _ = server.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            label, mode = (os.path.basename(args.wav), "wav") if args.wav else (device[1], "mic")
            try:
                session = Session(conn, label, mode)
            except OSError:
                continue
            if current["session"]:
                current["session"].close()  # one phone at a time; the newest wins
            current["session"] = session
            log("\n手机已连接，开始推流")
            if args.wav:
                threading.Thread(target=stream_wav, args=(args, session), daemon=True).start()
    except KeyboardInterrupt:
        pass
    finally:
        if current["session"]:
            current["session"].close()
        server.close()
        if adb:
            subprocess.run([adb, "reverse", "--remove", f"tcp:{args.port}"], capture_output=True)
        log("\n已停止")


if __name__ == "__main__":
    main()
