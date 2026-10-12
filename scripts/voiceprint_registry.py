"""Private, atomic voiceprint storage and basic recording checks (not identity verification)."""
import copy
import datetime as dt
import hashlib
import json
import math
import os
import struct
import threading


def audio_quality(pcm):
    if len(pcm) % 2:
        raise ValueError("音频格式无效")
    values = [v[0] / 32768 for v in struct.iter_unpack("<h", pcm)]
    active = sum(len(values[i:i + 1600]) for i in range(0, len(values), 1600)
                 if math.sqrt(sum(v * v for v in values[i:i + 1600]) / len(values[i:i + 1600])) >= .006)
    return {"seconds": round(len(values) / 16000, 2), "active_seconds": round(active / 16000, 2),
            "clipped_ratio": round(sum(abs(v) >= .998 for v in values) / max(1, len(values)), 4)}


def person_profiles(person):
    """One voiceprint per capture source (phone mic, remote bridge, DJI...). Old records hold a single top-level vector."""
    if person.get("profiles"):
        return person["profiles"]
    return {person.get("source") or "DJI 历史录音": person}


class VoiceprintRegistry:
    def __init__(self, path, seed_path, team):
        self.path, self.team = path, team
        self.lock = threading.RLock()
        with open(path if os.path.isfile(path) else seed_path, encoding="utf-8") as f:
            self.data = json.load(f)
        self.data.setdefault("people", {})
        dimension = len(next(iter(self.data["people"].values()))["vector"]) if self.data["people"] else 192
        self.dimension = self.data.setdefault("dimension", dimension)

    def revision(self):
        return hashlib.sha256(json.dumps(self.data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def profile_revision(self, name):
        return hashlib.sha256(json.dumps(self.data["people"].get(name), ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def catalog(self):
        with self.lock:
            rows = []
            for name in self.team:
                p = self.data["people"].get(name)
                rows.append({"name": name, "registered": p is not None,
                    "seconds": p.get("seconds", 0) if p else 0,
                    "source": p.get("source", "DJI 历史录音") if p else None,
                    "sources": [{"source": k, "seconds": v.get("seconds", 0), "updated_at": v.get("updated_at")}
                                for k, v in person_profiles(p).items()] if p else [],
                    "updated_at": p.get("updated_at") if p else None,
                    "sample_id": p.get("sample_id") if p else None,
                    "profile_revision": self.profile_revision(name)})
            return {"people": rows, "revision": self.revision()}

    def _write(self, data):
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        temp = self.path + ".tmp"
        fd = os.open(temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, self.path)
        self.data = data

    def save(self, name, vector, quality, source, sample_id, expected_revision, expected_profile_revision=None):
        with self.lock:
            if name not in self.team:
                raise ValueError("请选择组织中的成员")
            old = self.data["people"].get(name, {})
            if old and any(v.get("sample_id") == sample_id for v in person_profiles(old).values()):
                return self.catalog()  # same frozen recording after a lost response
            current = self.profile_revision(name) if expected_profile_revision is not None else self.revision()
            expected = expected_profile_revision if expected_profile_revision is not None else expected_revision
            if expected != current:
                raise ValueError("声纹已被更新，请刷新列表后重新录入")
            norm = math.sqrt(sum(x * x for x in vector))
            if len(vector) != self.dimension or not all(math.isfinite(x) for x in vector) or norm < 1e-8:
                raise ValueError("声纹特征无效，请重新录音")
            data = copy.deepcopy(self.data)
            entry = {"vector": [x / norm for x in vector], "seconds": quality["seconds"],
                "source": source, "sample_id": sample_id,
                "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
            profiles = {k: dict(v) for k, v in person_profiles(old).items()} if old else {}
            profiles[source] = entry  # re-enrolling on the same source replaces it; other sources are kept
            data["people"][name] = dict(entry, profiles=profiles)
            self._write(data)
            return self.catalog()

    def delete(self, name, expected_revision):
        with self.lock:
            if name not in self.team:
                raise ValueError("请选择组织中的成员")
            if expected_revision != self.revision():
                raise ValueError("声纹已被更新，请刷新列表后再删除")
            data = copy.deepcopy(self.data)
            data["people"].pop(name, None)
            self._write(data)  # even an empty registry never silently restores deleted seed profiles
            return self.catalog()
