"""Raw meeting assets on the team server: original PCM audio, final sentences and the raw ASR stream.

Upload is resumable and verified: the device declares each file's size and SHA-256, appends 1 MB chunks
that are each hash-checked, and `complete` re-hashes every whole file before marking the session
archived. The device only frees its local copy after `complete` succeeds. Kept until the meeting is
deleted; deleted sessions leave a tombstone so a delayed retry cannot re-create them.
"""
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import threading
import wave

FILES = {"audio.pcm": 4_000_000_000, "finals.jsonl": 200_000_000, "asr.jsonl": 1_000_000_000,
         "timing.jsonl": 200_000_000, "meta.json": 100_000}
CHUNK_LIMIT = 1_100_000
CLIP_LIMIT_MS, CLIP_PAD_MS = 30_000, 300
BYTES_PER_MS = 32  # 16 kHz mono s16le; byte 0 of audio.pcm is session time 0
SESSION = re.compile(r"[A-Za-z0-9-]{8,80}")


class RecordingError(ValueError):
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status, self.extra = status, extra


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path, value):
    temp = path + ".tmp"
    fd = os.open(temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


class Recordings:
    def __init__(self, directory, deleted=lambda sid: False):
        self.root, self.deleted = directory, deleted
        os.makedirs(directory, mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
        self.lock = threading.RLock()

    def folder(self, sid):
        if not isinstance(sid, str) or not SESSION.fullmatch(sid):
            raise RecordingError(400, "会话编号无效")
        return os.path.join(self.root, sid)

    def _manifest(self, sid):
        path = os.path.join(self.folder(sid), "manifest.json")
        if not os.path.isfile(path):
            raise RecordingError(409, "请先提交文件清单")
        return json.load(open(path, encoding="utf-8"))

    def meta(self, sid):
        """What the phone said about the recording (strings, as declared in the manifest)."""
        return self._manifest(sid).get("meta") or {}

    def _received(self, folder, name):
        path = os.path.join(folder, name)
        return os.path.getsize(path) if os.path.isfile(path) else 0

    def manifest(self, sid, body):
        files = body.get("files")
        if not isinstance(files, list) or not files or len(files) > len(FILES):
            raise RecordingError(400, "文件清单无效")
        declared = {}
        for item in files:
            name, size, sha = (item or {}).get("name"), (item or {}).get("size"), (item or {}).get("sha256")
            if name not in FILES or name in declared or type(size) is not int or not 0 <= size <= FILES[name] \
                    or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
                raise RecordingError(400, f"文件 {name} 的大小或校验值无效")
            declared[name] = {"size": size, "sha256": sha}
        if "audio.pcm" not in declared or declared["audio.pcm"]["size"] % 2:
            raise RecordingError(400, "缺少 16 kHz 单声道 PCM 录音")
        meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
        with self.lock:
            if self.deleted(sid):
                raise RecordingError(410, "该会议已删除，不再接收录音")
            folder = self.folder(sid)
            os.makedirs(folder, mode=0o700, exist_ok=True)
            done = os.path.join(folder, "complete.json")
            if os.path.isfile(done) and json.load(open(done, encoding="utf-8"))["files"] == declared:
                return {"session_id": sid, "complete": True, "received": {n: f["size"] for n, f in declared.items()}}
            for name in os.listdir(folder):  # a changed manifest restarts any file that no longer fits
                if name in FILES and (name not in declared or self._received(folder, name) > declared[name]["size"]):
                    os.unlink(os.path.join(folder, name))
            if os.path.isfile(done):
                os.unlink(done)
            _write_json(os.path.join(folder, "manifest.json"), {
                "session_id": sid, "files": declared, "format": "pcm_s16le_16000_mono",
                "meta": {k: str(v)[:200] for k, v in list(meta.items())[:20]},
                "declared_at": dt.datetime.now(dt.timezone.utc).isoformat()})
            return {"session_id": sid, "complete": False,
                    "received": {name: self._received(folder, name) for name in declared}}

    def chunk(self, sid, name, offset, data, sha):
        if name not in FILES:
            raise RecordingError(400, "文件名无效")
        if not data or len(data) > CHUNK_LIMIT or hashlib.sha256(data).hexdigest() != sha:
            raise RecordingError(400, "分块为空、过大或校验失败")
        with self.lock:
            if self.deleted(sid):
                raise RecordingError(410, "该会议已删除，不再接收录音")
            declared = self._manifest(sid)["files"].get(name)
            if declared is None:
                raise RecordingError(400, "清单里没有这个文件")
            folder = self.folder(sid)
            current = self._received(folder, name)
            if offset != current:
                raise RecordingError(409, "续传位置不一致", received=current)
            if offset + len(data) > declared["size"]:
                raise RecordingError(400, "超出清单声明的大小")
            fd = os.open(os.path.join(folder, name), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            return {"received": current + len(data)}

    def complete(self, sid):
        with self.lock:
            manifest = self._manifest(sid)
            folder = self.folder(sid)
            for name, declared in manifest["files"].items():
                path = os.path.join(folder, name)
                if self._received(folder, name) != declared["size"]:
                    raise RecordingError(409, f"{name} 尚未传完", received=self._received(folder, name))
                if _sha256(path) != declared["sha256"]:
                    os.unlink(path)  # corrupt: restart this file from zero
                    raise RecordingError(409, f"{name} 整体校验失败，需重传", received=0)
            record = {"session_id": sid, "files": manifest["files"],
                      "completed_at": dt.datetime.now(dt.timezone.utc).isoformat()}
            _write_json(os.path.join(folder, "complete.json"), record)
            return dict(record, complete=True)

    def status(self, sid):
        folder = self.folder(sid)
        if not os.path.isdir(folder):
            raise RecordingError(404, "没有这场会议的录音")
        done = os.path.join(folder, "complete.json")
        if os.path.isfile(done):
            return dict(json.load(open(done, encoding="utf-8")), complete=True)
        manifest = self._manifest(sid)
        return {"session_id": sid, "complete": False,
                "received": {name: self._received(folder, name) for name in manifest["files"]}}

    def clip(self, sid, start_ms, end_ms):
        """WAV of one sentence from the archived audio, for listening back after the phone has moved on."""
        if any(type(n) is not int for n in (start_ms, end_ms)) or not 0 <= start_ms < end_ms \
                or end_ms - start_ms > CLIP_LIMIT_MS:
            raise RecordingError(400, f"片段时间无效或超过 {CLIP_LIMIT_MS // 1000} 秒")
        path = os.path.join(self.folder(sid), "audio.pcm")
        with self.lock:
            if self.deleted(sid) or not os.path.isfile(path):
                raise RecordingError(404, "服务器上还没有这场会议的录音")
            size = os.path.getsize(path)
            if end_ms * BYTES_PER_MS > size:
                raise RecordingError(409, "录音还没有上传到这一段")
            first = max(0, start_ms - CLIP_PAD_MS) * BYTES_PER_MS
            last = min(size - size % 2, (end_ms + CLIP_PAD_MS) * BYTES_PER_MS)
            with open(path, "rb") as source:
                source.seek(first)
                pcm = source.read(last - first)
        out = io.BytesIO()
        with wave.open(out, "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(16000)
            target.writeframes(pcm)
        return out.getvalue()

    def delete(self, sid):
        with self.lock:
            try:
                folder = self.folder(sid)
            except RecordingError:
                return {"deleted": False}  # ids that could never hold audio
            if os.path.isdir(folder):
                shutil.rmtree(folder)
            return {"deleted": True}
