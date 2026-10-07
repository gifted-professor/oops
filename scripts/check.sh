#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
if [ -z "${JAVA_HOME:-}" ] && [ -d /opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home ]; then
  export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
fi
./android/gradlew -p android testDebugUnitTest assembleDebug "$@"
node --check android/app/src/main/assets/web/app.js
node --check android/app/src/main/assets/web/bridge-mock.js
python3 -m py_compile scripts/prepare_test_audio.py scripts/device_wav_recall.py scripts/device_stability.py scripts/jev_probe.py scripts/analyze_stability.py scripts/local_api_fixture.py scripts/mac_audio_bridge.py scripts/local_backend.py scripts/voiceprint.py scripts/asr_words_probe.py scripts/voiceprint_split.py
python3 scripts/local_backend.py --self-test
python3 scripts/test_voiceprint_split.py
