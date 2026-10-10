#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
if [ -z "${JAVA_HOME:-}" ] && [ -d /opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home ]; then
  export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
fi
./android/gradlew -p android testDebugUnitTest assembleDebug "$@"
node --check android/app/src/main/assets/web/app.js
node --check android/app/src/main/assets/web/draft-state.js
node android/app/src/test/js/draft-state.test.js
node android/app/src/test/js/voiceprint-ui.test.js
node --check android/app/src/main/assets/web/bridge-mock.js
node --check web/voiceprint/enroll.js
node scripts/test_voiceprint_audio.js
python3 -m py_compile scripts/prepare_test_audio.py scripts/device_wav_recall.py scripts/device_stability.py scripts/jev_probe.py scripts/analyze_stability.py scripts/local_api_fixture.py scripts/mac_audio_bridge.py scripts/local_backend.py scripts/voiceprint.py scripts/asr_words_probe.py scripts/voiceprint_split.py
python3 scripts/local_backend.py --self-test
python3 scripts/test_voiceprint_split.py
python3 scripts/test_backend_http.py
python3 scripts/test_voiceprint_registry.py
python3 scripts/test_speaker_preview.py

python3 scripts/test_notices_meetings.py
python3 scripts/test_attribution_io.py
python3 scripts/test_mac_audio_bridge.py
python3 scripts/test_task_resolver.py
python3 scripts/test_recordings.py
python3 scripts/test_feishu_actions.py
python3 -m py_compile scripts/feishu_actions.py scripts/oops_mac_agent.py
python3 -m unittest discover -s scripts -p 'test_speaker_attribution.py'
python3 -m unittest discover -s scripts -p 'test_speaker_latency.py'
python3 scripts/test_latency_report.py
python3 scripts/test_task_eval.py
python3 scripts/test_archive_to_eval.py
python3 scripts/test_filler_report.py
python3 scripts/test_jev_budget.py
python3 scripts/test_meetings_overview.py
python3 scripts/test_label_meeting.py
python3 scripts/test_review_truth.py
VOICE_PYTHON=${OOPS_VOICE_PYTHON:-"$ROOT/.venv-voice/bin/python"}
if [ ! -x "$VOICE_PYTHON" ]; then VOICE_PYTHON=python3; fi
"$VOICE_PYTHON" -m unittest discover -s scripts -p 'test_speaker_validation.py'
python3 -m unittest discover -s scripts -p 'test_speaker_acceptance.py'
python3 -m py_compile scripts/meeting_features.py scripts/notice_probe.py scripts/recordings.py scripts/task_resolver.py
python3 -m py_compile scripts/latency_report.py scripts/task_eval.py scripts/archive_to_eval.py scripts/filler_report.py scripts/meetings_overview.py scripts/label_meeting.py scripts/asr_words_probe.py deploy/live-speed-upgrade.py
sh -n deploy/mac/oops-archive-pull.sh
python3 -m py_compile scripts/speaker_attribution.py scripts/speaker_validation.py scripts/speaker_acceptance.py deploy/speaker-attribution-upgrade.py
