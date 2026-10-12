#!/usr/bin/env python3
"""Stream a Mac audio input (e.g. the Hollyland wireless mic on the Mac mini) to the Oops app.

The phone connects to 127.0.0.1:PORT, which `adb reverse` maps back to this server over the USB
debugging cable; the app's RemoteAudioSource ("Mac 收音") reads it. Frames are
1-byte type | 4-byte big-endian length | payload:
  0x01 audio  16 kHz mono s16le PCM, 100 ms per frame
  0x02 mark   JSON {"kind":"double_tap"}  (= the 录音豆 recall gesture)
  0x03 hello  JSON device info, sent once per connection

  python3 scripts/mac_audio_bridge.py --list
  python3 scripts/mac_audio_bridge.py --adb-reverse --serial PHONE_SERIAL  # default: "Wireless microphone"
  python3 scripts/mac_audio_bridge.py --device 1 --adb-reverse --serial PHONE_SERIAL
  python3 scripts/mac_audio_bridge.py --wav android/app/src/main/assets/test/dialog.wav --adb-reverse --serial PHONE_SERIAL

With --adb-reverse, a background guard restores the fixed phone's missing mapping after USB
reconnection. Without --serial, exactly one online phone must be present at startup; its serial
is then fixed for the lifetime of the bridge. A different mapping on the same port is preserved;
on exit, only a mapping created by this guard and still pointing here is removed.

The mic is captured continuously and forwarded to whichever phone is connected (newest wins);
a WAV restarts from the beginning for every connection and is followed by silence.
While streaming, press Enter (or `kill -USR1 <pid>`) to send a double-tap mark.
macOS asks once for microphone access for the app running this script (Terminal / Claude).
"""
import argparse
import datetime as dt
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
import uuid
import wave
from pathlib import Path

AUDIO, MARK, HELLO = 0x01, 0x02, 0x03
RATE, FRAME_MS = 16000, 100
FRAME_BYTES = RATE * 2 * FRAME_MS // 1000
DEFAULT_DEVICE = "Wireless microphone"
SEND_TIMEOUT_S = 2.0
ADB_COMMAND_TIMEOUT_S = 2.0
ADB_REVERSE_RETRY_S = 5.0


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


def resolve_adb_serial(adb, serial=None, run=subprocess.run, timeout_s=ADB_COMMAND_TIMEOUT_S):
    """Choose once at startup; never migrate a reconnecting bridge to another phone."""
    if serial is not None:
        if not serial.strip():
            raise ValueError("--serial 不能为空")
        return serial.strip()
    try:
        result = run([adb, "devices"], capture_output=True, text=True, timeout=timeout_s)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("无法查询 adb 设备，请明确指定 --serial") from error
    if result.returncode:
        raise ValueError("无法查询 adb 设备，请明确指定 --serial")
    online = [fields[0] for line in result.stdout.splitlines()
              if len(fields := line.split()) >= 2 and fields[1] == "device"]
    if len(online) != 1:
        raise ValueError("--adb-reverse 未指定 --serial 时，启动时必须恰好有一台在线手机")
    return online[0]


class AdbReverseGuard:
    """Restore only a fixed phone's missing reverse mapping, away from the audio threads.

    Every adb command has a timeout. stop() interrupts the retry wait and joins this worker;
    it deliberately leaves the mapping alone so an existing audio bridge can use this guard.
    The bridge's main() owns its normal mapping cleanup on exit.
    """

    def __init__(self, adb, serial, port, run=subprocess.run, log_fn=log,
                 interval_s=ADB_REVERSE_RETRY_S, timeout_s=ADB_COMMAND_TIMEOUT_S):
        if not serial or not serial.strip():
            raise ValueError("ADB 映射守护需要固定 --serial")
        if interval_s <= 0 or timeout_s <= 0:
            raise ValueError("ADB 重试间隔和超时必须大于零")
        self.command = [adb, "-s", serial.strip()]
        self.endpoint = f"tcp:{port}"
        self.run, self.log_fn = run, log_fn
        self.interval_s, self.timeout_s = interval_s, timeout_s
        self._stop = threading.Event()
        self._thread = None
        self._status = None
        self.created_mapping = False

    def _report(self, status, message):
        if status != self._status:
            self._status = status
            self.log_fn(message)

    def _run(self, *arguments):
        return self.run(self.command + list(arguments), capture_output=True, text=True,
                        timeout=self.timeout_s)

    def check_once(self):
        if self._stop.is_set():
            return False
        try:
            result = self._run("reverse", "--list")
            if result.returncode:
                self.created_mapping = False  # reconnects destroy USB mappings; retain no stale ownership
                self._report("waiting", "\n目标手机暂不可连接，等待 USB 重连后恢复 adb reverse")
                return False
            mappings = [fields[-2:] for line in result.stdout.splitlines()
                        if len(fields := line.split()) >= 2]
            existing = [remote for local, remote in mappings if local == self.endpoint]
            if self.endpoint in existing:
                self._report("ready", "\n目标手机 adb reverse 映射已就绪")
                return True
            if existing:
                self.created_mapping = False
                self._report("conflict", "\n目标手机端口已有不同 adb reverse 映射，保留原映射")
                return False
            self.created_mapping = False
            if self._stop.is_set():
                return False
            # Do not replace another mapping created between the check and this command.
            result = self._run("reverse", "--no-rebind", self.endpoint, self.endpoint)
            if result.returncode:
                self._report("waiting", "\n目标手机映射暂未恢复，稍后重试")
                return False
            self.created_mapping = True
            self._status = "ready"
            self.log_fn("\n已恢复目标手机 adb reverse 映射")
            return True
        except (OSError, subprocess.TimeoutExpired):
            self.created_mapping = False  # uncertain mapping lifetime: prefer leaving it on exit
            self._report("waiting", "\n目标手机暂不可连接，等待 USB 重连后恢复 adb reverse")
            return False

    def _loop(self):
        while not self._stop.is_set():
            self.check_once()
            if self._stop.wait(self.interval_s):
                break

    def start(self):
        if self._thread is not None:
            raise RuntimeError("ADB 映射守护已启动")
        self._thread = threading.Thread(target=self._loop, name="oops-adb-reverse", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2 * self.timeout_s + 1)
            return not self._thread.is_alive()
        return True

    def cleanup_created_mapping(self):
        """Explicit bridge-exit cleanup after stop(); never remove a pre-existing/conflicting map."""
        if not self.created_mapping or (self._thread is not None and self._thread.is_alive()):
            return False
        try:
            result = self._run("reverse", "--list")
            mappings = [fields[-2:] for line in result.stdout.splitlines()
                        if len(fields := line.split()) >= 2]
            if result.returncode or [self.endpoint, self.endpoint] not in mappings:
                self.created_mapping = False
                return False
            result = self._run("reverse", "--remove", self.endpoint)
            self.created_mapping = False
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            self.created_mapping = False
            return False


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

    def __init__(self, conn, label, mode, record_dir=None, capture=None):
        self.conn = conn
        self.alive = True
        self.lock = threading.RLock()
        self.recorder = SessionRecorder(record_dir, label, mode, capture) if record_dir else None
        conn.settimeout(SEND_TIMEOUT_S)  # a stuck phone must not stall capture
        try:
            self.send(HELLO, json.dumps({"device": label, "rate": RATE, "channels": 1,
                                         "format": "s16le", "mode": mode}, ensure_ascii=False).encode())
        except OSError:
            self.close()
            raise

    def send(self, kind, payload):
        with self.lock:
            if not self.alive:
                raise OSError("closed phone connection")
            self.conn.sendall(struct.pack(">BI", kind, len(payload)) + payload)
            if kind == AUDIO and self.recorder:
                try:
                    self.recorder.write(payload)
                except OSError as error:
                    # Keep the existing live audio path running, but never hide lost evidence.
                    log(f"\n本地录制失败（{type(error).__name__}），停止录制；手机推流继续")
                    try:
                        self.recorder.close(error=type(error).__name__)
                    except OSError:
                        log("\n本地录制文件未能关闭，请检查磁盘；手机推流继续")
                    self.recorder = None

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
        with self.lock:
            self.alive = False
            try:
                self.conn.close()
            except OSError:
                pass
            if self.recorder:
                try:
                    self.recorder.close()
                except OSError:
                    log("\n本地录制文件未能完整关闭，请检查磁盘")
                self.recorder = None


class SessionRecorder:
    """Local opt-in evidence of successfully sent PCM, one WAV per connection.

    No frame is recorded while disconnected. Transport success is not an
    acknowledgement that the phone consumed a frame; the sidecar says so.
    """

    def __init__(self, folder, label, mode, capture=None):
        folder = Path(folder).expanduser().resolve()
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        now = dt.datetime.now(dt.timezone.utc)
        self.path = folder / ("remote-" + now.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12] + ".wav")
        self.file = os.fdopen(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")
        self.audio = wave.open(self.file, "wb")
        self.audio.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        self.bytes, self.frames, self.closed = 0, 0, False
        self.metadata = {"schema": 1, "recording": self.path.name,
                         "started_at": now.isoformat(), "device": label, "mode": mode,
                         "sample_rate": RATE, "channels": 1, "format": "s16le",
                         "capture": capture or {}, "clock": "connection-relative successfully sent samples",
                         "receiver_consumption_verified": False, "audio_uploaded_by_recorder": False}
        self.metadata['purpose'] = (capture or {}).get('purpose', 'unspecified')
        self._metadata()
        log(f"\n本地录制：{self.path}（每次连接独立，不上传录制文件）")

    def _metadata(self):
        self.metadata.update(frames=self.frames, samples=self.bytes // 2,
                             duration_ms=self.bytes * 1000 // (RATE * 2), finalized=self.closed)
        sidecar = self.path.with_suffix(".json")
        with os.fdopen(os.open(sidecar, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as target:
            json.dump(self.metadata, target, ensure_ascii=False, indent=2)
            target.write("\n")

    def write(self, payload):
        if self.closed or len(payload) % 2:
            raise ValueError("invalid recording frame")
        self.audio.writeframes(payload)  # Patch the header so interrupted files remain readable.
        self.frames += 1
        self.bytes += len(payload)
        if self.frames % 10 == 0:
            self.file.flush()
            self._metadata()

    def close(self, error=None):
        if self.closed:
            return
        self.closed = True
        self.metadata["ended_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        if error:
            self.metadata["error"] = error
        try:
            self.audio.close()
        finally:
            self.file.close()
        try:
            self._metadata()
        except OSError:
            log("\n本地录制元数据未能保存，请检查磁盘空间")


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



def listening_socket(host, port):
    """TCP listener. An IPv6 host (e.g. `::`) also accepts IPv4: a phone resolving `<mac>.local` often gets
    the Mac's IPv6 link-local address first, and an IPv4-only listener answers that with 'connection refused'."""
    v6 = ":" in host
    server = socket.socket(socket.AF_INET6 if v6 else socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if v6:
        server.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    server.bind((host, port))
    server.listen(2)
    return server


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--list", action="store_true", help="列出可用的音频输入设备")
    p.add_argument("--device", help=f"设备名片段或序号（默认 {DEFAULT_DEVICE!r}）")
    p.add_argument("--channel", choices=["mix", "left", "right"], default="mix",
                   help="双声道接收器：mix 混合（默认），或只取左/右（两个发射器各占一路时）")
    p.add_argument("--gain-db", type=float, default=0.0, help="增益（dB），声音太小时用")
    p.add_argument("--wav", help="用音频文件代替麦克风（任意格式，按实时速度发送）")
    p.add_argument("--record", nargs="?", const=str(Path(__file__).resolve().parent.parent / "evidence" / "remote-recordings"),
                   metavar="DIRECTORY", help="本人同意后启用本地 WAV 录制；每次手机连接另存一份，默认 evidence/remote-recordings")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7790)
    p.add_argument("--adb-reverse", action="store_true", help="守护固定手机的 adb reverse，USB 重连后补回缺失映射；退出只撤除本次创建的映射")
    p.add_argument("--adb", help="adb 路径")
    p.add_argument("--serial", help="adb reverse 固定的手机序列号；省略时仅允许启动时恰好一台在线手机")
    p.add_argument("--purpose", choices=['unspecified', 'voiceprint-enrollment', 'meeting-development', 'meeting-holdout'],
                   default='unspecified', help="录制用途写入本机元数据；声纹注册录音不能用作独立验收")
    args = p.parse_args()

    if args.list:
        for index, name in list_devices():
            print(f"[{index}] {name}")
        return
    if args.wav and not os.path.exists(args.wav):
        sys.exit(f"找不到文件：{args.wav}")
    adb = adb_path(args.adb) if args.adb_reverse else None
    if adb:
        try:
            args.serial = resolve_adb_serial(adb, args.serial)
        except ValueError as error:
            p.error(str(error))
    guard = AdbReverseGuard(adb, args.serial, args.port) if adb else None
    device = None if args.wav else resolve_device(args.device)

    server = listening_socket(args.host, args.port)
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
    log("手机 App：调试设置 → 音源选「Mac 收音」→ 开始收音。回车 = 双击召回，Ctrl+C 退出。")
    try:
        if guard:
            guard.start()
        if not args.wav:
            MicCapture(args, device, current).start()
        while True:
            conn, _ = server.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            label, mode = (os.path.basename(args.wav), "wav") if args.wav else (device[1], "mic")
            try:
                session = Session(conn, label, mode, args.record,
                                  {"channel": args.channel, "gain_db": args.gain_db, "purpose": args.purpose})
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
        guard_stopped = guard.stop() if guard else True
        if current["session"]:
            current["session"].close()
        server.close()
        if guard and guard_stopped:
            guard.cleanup_created_mapping()
        log("\n已停止")


if __name__ == "__main__":
    main()
