#!/bin/sh
# Nightly on the Mac (launchd local.oops.archive-pull, 03:30). Installed copy: ~/.local/share/oops-archive/pull.sh
# 1. Second copy of the team's raw meeting assets (audio, finals, raw ASR, timing, meeting text) from the HK server.
#    Mirrors deletions so a meeting deleted in the app is gone here too.
# 2. The other direction: evaluation labels live only in the git-ignored evidence/ folder on this Mac, so push a
#    copy to the server. Never deletes there; a lost laptop must not cost the labelling work.
set -eu
DEST=/Volumes/GPFS/Users/a1234/oops-archive
REPO=/Volumes/GPFS/Users/a1234/Desktop/Coding/oops
HOST=root@103.110.81.78
SSH="/usr/bin/ssh -o BatchMode=yes -o ConnectTimeout=20"
[ -d "$DEST" ] || exit 0   # GPFS volume not mounted yet; launchd retries tomorrow
umask 077
for part in recordings meetings; do
  /usr/bin/rsync -a --delete -e "$SSH" "$HOST:/var/lib/oops/backend/$part/" "$DEST/$part/"
done
date -u +%Y-%m-%dT%H:%M:%SZ > "$DEST/last-pull.txt"

# Labels and meeting texts are small; failures here are reported but must not hide that the mirror succeeded.
push_labels() {
  [ -d "$REPO/evidence" ] || return 0
  $SSH $HOST 'mkdir -p -m 700 /var/lib/oops/backend/evidence-backup/tasks /var/lib/oops/backend/evidence-backup/split-datasets'
  for set in "$REPO"/evidence/task-*; do
    [ -d "$set" ] || continue
    /usr/bin/rsync -a -e "$SSH" "$set/" "$HOST:/var/lib/oops/backend/evidence-backup/tasks/$(basename "$set")/"
  done
  for set in "$REPO"/models/voiceprint/split-datasets/*; do
    [ -f "$set/truth.json" ] || continue
    name=$(basename "$set")
    $SSH $HOST "mkdir -p -m 700 /var/lib/oops/backend/evidence-backup/split-datasets/$name"
    /usr/bin/rsync -a -e "$SSH" --include='truth.json' --include='manifest.json' --include='frozen.json' --exclude='*' \
      "$set/" "$HOST:/var/lib/oops/backend/evidence-backup/split-datasets/$name/"
  done
}
if push_labels; then
  date -u +%Y-%m-%dT%H:%M:%SZ > "$DEST/last-labels-push.txt"
else
  echo "label backup failed" >&2
fi
