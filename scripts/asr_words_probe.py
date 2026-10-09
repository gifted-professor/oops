#!/usr/bin/env python3
"""Probe the app's streaming ASR configuration; credentials never enter evidence/logs.

Run with .venv-voice/bin/python (websocket-client). Raw replies and definite sentences
stay under ignored evidence/asr-words/. --seconds is only for a preliminary smoke probe;
omit it for the two complete non-enrollment recordings.
"""
import argparse
import difflib
import gzip
import json
import os
import re
from pathlib import Path
import struct
import threading
import time
import unicodedata
import uuid
import wave

ROOT = Path(__file__).resolve().parent.parent
ENDPOINT = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"


def credentials(path):
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return {k: values.get(k) or os.environ.get(k, "") for k in
            ("VOLC_API_KEY", "VOLC_APP_ID", "VOLC_ACCESS_TOKEN", "VOLC_RESOURCE_ID")}


# Same hotwords as the app (Settings.HOTWORDS); without them the probe transcribes names differently from live.
HOTWORDS = ["Oops", "欧普斯", "Lydia", "黄丽萍", "江古月", "洪玉麟", "林春源"]


def request_json(hotwords=True, end_window_size=None):
    # Keep in sync with VolcRequest.json; diarization is deliberately disabled.
    request = {"model_name": "bigmodel", "enable_nonstream": True,
               "enable_itn": True, "enable_punc": True,
               "show_utterances": True, "result_type": "single"}
    if hotwords:
        words = [{"word": w} for w in HOTWORDS]
        request["corpus"] = {"context": json.dumps({"hotwords": words}, ensure_ascii=False)}
    if end_window_size is not None:
        # Silence (ms) after which the provider ends a sentence; the app leaves the provider default.
        request["end_window_size"] = end_window_size
    return {"user": {"uid": "oops-words-probe-" + uuid.uuid4().hex},
            "audio": {"format": "pcm", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1},
            "request": request}


def frame(kind, payload, last=False, compressed=False, final_sequence=False):
    return bytes((0x11, (kind << 4) | (3 if last and final_sequence else 2 if last else 0), 0x11 if compressed else 0, 0)) \
        + (struct.pack(">i", -1) if last and final_sequence else b"") \
        + struct.pack(">I", len(payload)) + payload


def decode(data):
    if not isinstance(data, bytes) or len(data) < 8:
        raise ValueError("invalid ASR binary response")
    kind, flags, compression = data[1] >> 4, data[1] & 15, data[2] & 15
    offset = (data[0] & 15) * 4
    if kind == 15:
        code = struct.unpack_from(">I", data, offset)[0]
        size = struct.unpack_from(">I", data, offset + 4)[0]
        payload = data[offset + 8:offset + 8 + size]
        if compression == 1:
            payload = gzip.decompress(payload)
        raise RuntimeError(f"ASR provider error {code}: " + payload.decode(errors="replace")[:300])
    if flags & 1:
        offset += 4
    size = struct.unpack_from(">I", data, offset)[0]
    payload = data[offset + 4:offset + 4 + size]
    if compression == 1:
        payload = gzip.decompress(payload)
    return json.loads(payload), bool(flags & 2)


def clean(text):
    return "".join(c for c in text if not c.isspace() and not unicodedata.category(c).startswith("P"))


def difference_kind(final, words):
    spoken = "".join(w.get("text", w.get("word", "")) for w in words)
    if spoken == final:
        return "none"
    source, target = clean(spoken), clean(final)
    if source == target:
        return "punctuation"
    numeric = re.compile(r"[0-9零〇一二三四五六七八九十百千万亿兆两壹贰叁肆伍陆柒捌玖拾佰仟点负百分之]+")
    changes = [(source[a:b], target[c:d]) for op, a, b, c, d in
               difflib.SequenceMatcher(None, source, target, autojunk=False).get_opcodes() if op != "equal"]
    # Numeric-only spelling changes are ITN candidates, requiring manual confirmation.
    return "itn_candidate" if all(numeric.fullmatch(a) and numeric.fullmatch(b) for a, b in changes) else "revision"


def summarize(utterances):
    rows = []
    for u in utterances:
        words = u.get("words") or []
        text, spoken = clean(u.get("text", "")), clean("".join(w.get("text", w.get("word", "")) for w in words))
        matcher = difflib.SequenceMatcher(None, spoken, text, autojunk=False)
        covered = sum(b.size for b in matcher.get_matching_blocks()) / max(1, len(text))
        # ASR emits whitespace tokens with sentinel -1 timestamps; these are not spoken words.
        timed = [w for w in words if clean(w.get("text", w.get("word", "")))]
        valid = bool(timed) and all("end_time" in w and
                                    u.get("start_time", 0) <= w.get("start_time", 0) <=
                                    w.get("end_time", 0) <= u.get("end_time", 0) for w in timed)
        temporal = max(0, timed[-1].get("end_time", 0) - timed[0].get("start_time", 0)) \
            / max(1, u.get("end_time", 0) - u.get("start_time", 0)) if valid else 0
        rows.append({"start_ms": u.get("start_time", 0), "end_ms": u.get("end_time", 0),
                     "has_word_times": valid, "exact_character_coverage": round(covered, 4),
                     "word_span_fraction": round(temporal, 4),
                     "difference": difference_kind(u.get("text", ""), words)})
    n = len(rows)
    return {"final_count": n, "with_word_times": sum(r["has_word_times"] for r in rows),
            "coverage_ge_90_count": sum(r["has_word_times"] and r["exact_character_coverage"] >= .9 for r in rows),
            "coverage_ge_90_fraction": round(sum(r["has_word_times"] and r["exact_character_coverage"] >= .9 for r in rows) / max(1, n), 4),
            "difference_counts": {d: sum(r["difference"] == d for r in rows)
                                  for d in ("none", "punctuation", "itn_candidate", "revision")},
            "rows": rows}


def probe(path, args):
    import websocket
    creds = credentials(ROOT / "android/local.properties")
    headers = {"X-Api-Resource-Id": creds["VOLC_RESOURCE_ID"] or "volc.seedasr.sauc.duration",
               "X-Api-Connect-Id": str(uuid.uuid4())}
    if creds["VOLC_API_KEY"]:
        headers["X-Api-Key"] = creds["VOLC_API_KEY"]
    elif creds["VOLC_APP_ID"] and creds["VOLC_ACCESS_TOKEN"]:
        headers.update({"X-Api-App-Key": creds["VOLC_APP_ID"], "X-Api-Access-Key": creds["VOLC_ACCESS_TOKEN"]})
    else:
        raise RuntimeError("ASR credentials are not configured")
    dest = Path(args.output) / path.stem
    dest.mkdir(parents=True, exist_ok=True)
    finals, seen, errors, finished, server_last = [], set(), [], threading.Event(), []
    ws = websocket.create_connection(ENDPOINT, header=headers, timeout=30, enable_multithread=True)
    ws.send_binary(frame(1, gzip.compress(json.dumps(request_json(not getattr(args, 'no_hotwords', False), getattr(args, 'end_window_size', None)), ensure_ascii=False).encode()), compressed=True))

    def receive():
        try:
            with (dest / "responses.jsonl").open("w") as out:
                while not finished.is_set():
                    raw = ws.recv()
                    if not raw:
                        break
                    reply, last = decode(raw)
                    out.write(json.dumps(reply, ensure_ascii=False) + "\n")
                    out.flush()
                    for u in (reply.get("result") or {}).get("utterances", []):
                        key = (u.get("start_time", 0), u.get("end_time", 0), u.get("text"))
                        if u.get("definite") and key not in seen:
                            finals.append(u)
                            seen.add(key)
                    if last:
                        server_last.append(True)
                        break
        except Exception as exc:
            detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            for secret in creds.values():
                if secret:
                    detail = detail.replace(secret, "[redacted]")
            errors.append(detail)
        finally:
            finished.set()

    reader = threading.Thread(target=receive, daemon=True)
    reader.start()
    sent_samples = 0
    try:
        with wave.open(str(path)) as audio:
            if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
                raise ValueError("requires 16 kHz mono s16le WAV")
            limit = min(audio.getnframes(), int(args.seconds * 16000)) if args.seconds else audio.getnframes()
            started, progress_at = time.monotonic(), time.monotonic()
            while sent_samples < limit and not finished.is_set():
                data = audio.readframes(min(3200, limit - sent_samples))
                if not data:
                    break
                is_last = sent_samples + len(data) // 2 == limit
                # Very short tail frames can leave the provider waiting for another packet.
                # Pad only the final transport packet with silence; original sample count stays exact.
                payload = data.ljust(6400, b"\0") if is_last else data
                ws.send_binary(frame(2, payload, last=is_last, final_sequence=args.final_sequence))
                sent_samples += len(data) // 2
                wait = started + sent_samples / 16000 / args.speed - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                if time.monotonic() - progress_at >= 30:
                    print(json.dumps({"recording": path.name, "sent_seconds": round(sent_samples / 16000),
                                      "final_count": len(finals)}), flush=True)
                    progress_at = time.monotonic()
            reader.join(30)
    finally:
        finished.set()
        ws.close()
        reader.join(2)
    finals.sort(key=lambda u: (u.get("start_time", 0), u.get("end_time", 0)))
    (dest / "finals.json").write_text(json.dumps(finals, ensure_ascii=False, indent=2) + "\n")
    report = {"recording": path.name, "sent_seconds": round(sent_samples / 16000, 3),
              "complete": not args.seconds and sent_samples == limit and not errors and bool(server_last),
              "server_last_received": bool(server_last),
              "transport_errors": errors, **summarize(finals)}
    (dest / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, ensure_ascii=False), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", nargs="*", type=Path)
    parser.add_argument("--output", default=str(ROOT / "evidence/asr-words"))
    parser.add_argument("--seconds", type=float)
    parser.add_argument("--speed", type=float, default=1.0, help="audio send pace; 1 matches the live app")
    parser.add_argument("--no-hotwords", action="store_true", help="omit the app's hotwords (the pre-2026-10-08 probe behaviour)")
    parser.add_argument("--end-window-size", type=int, help="sentence-ending silence in ms; unset = provider default, as in the app")
    parser.add_argument("--final-sequence", action="store_true", help="final negative sequence packet for transport diagnosis")
    args = parser.parse_args()
    if args.speed <= 0 or (args.seconds is not None and args.seconds <= 0):
        parser.error("speed and seconds must be positive")
    paths = args.wav or [p for p in sorted((ROOT / "test-media").glob("*-16k-mono.wav"))
                         if "_0236_" in p.name or "_0238_" in p.name]
    if not paths:
        parser.error("no evaluation recordings found")
    reports = [probe(p, args) for p in paths]
    if any(r["transport_errors"] or not r["final_count"] or (not args.seconds and not r["complete"]) for r in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
