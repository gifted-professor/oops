#!/usr/bin/env python3
"""Fetch only the HK voiceprint registry and run a frozen local acceptance.

No meeting audio is transferred. A pending/insufficient report does not deploy
or enable any switch. Use the voice venv; stdout never contains profile vectors.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
SIGNER_ID = 'oops-acceptance'
SIGNATURE_NAMESPACE = 'oops-speaker-acceptance'
ALLOWED_SIGNERS = Path('/etc/oops/acceptance_allowed_signers')
DEFAULT_SIGNING_KEY = Path.home() / '.config/oops/acceptance_ed25519'


def verify_receipt_signature(path, allowed_signers=ALLOWED_SIGNERS):
    """Use an administrator-pinned trust file; never trust a key supplied by a bundle."""
    try:
        path, allowed = Path(path).resolve(strict=True), Path(allowed_signers)
        info = allowed.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022
                or allowed == ALLOWED_SIGNERS and info.st_uid != 0):
            return False
        if path.stat().st_size > 2_000_000:
            return False
        signature = Path(str(path) + '.sig')
        if not signature.is_file() or signature.stat().st_size > 65536:
            return False
        result = subprocess.run(['ssh-keygen', '-Y', 'verify', '-f', str(allowed), '-I', SIGNER_ID,
                                 '-n', SIGNATURE_NAMESPACE, '-s', str(signature)],
                                input=path.read_bytes(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=15, check=False)
        return result.returncode == 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def init_signing_key(path):
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.stat().st_mode & 0o077:
            raise ValueError('Existing acceptance signing key must be a private regular file (0600)')
        subprocess.run(['ssh-keygen', '-l', '-f', str(path) + '.pub'], check=True,
                       stdout=subprocess.DEVNULL, timeout=10)
    else:
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(path),
                        '-C', SIGNER_ID], check=True, stdin=subprocess.DEVNULL, timeout=20)
        path.chmod(0o600)
    print(json.dumps({'signing_key': str(path), 'public_key': str(path) + '.pub',
                      'private_key_transferred': False, 'switches_enabled': False}))


def export_acceptance(report_path, destination, snapshot, signing_key, enable=()):
    """Verify original local evidence, then sign a receipt with no recordings or labels."""
    from local_backend import Speakers
    from speaker_validation import verify_validation, digest, calibrator_hash, gate_statuses
    report_path = Path(report_path).expanduser().resolve(strict=True)
    report = json.loads(report_path.read_text())
    dataset_files = report.get('dataset_files') or {}
    relative = Path(dataset_files.get('frozen', ''))
    if not relative.name or relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Original report must reference its local frozen evidence')
    original_frozen = json.loads((report_path.parent / relative).read_text())
    snapshot = Path(snapshot or original_frozen.get('registry_snapshot') or '').expanduser().resolve()
    if not snapshot.is_file():
        raise ValueError('The accepted voiceprint snapshot is required; never fall back to local profiles')
    speaker = Speakers(.5, .05, registry_path=str(snapshot), require_registry=True)
    if not speaker.ready:
        raise ValueError('Voiceprint model could not load: ' + str(speaker.error))
    if verify_validation(report, report_path, speaker, include_disabled=True) is None:
        raise ValueError('Original local acceptance evidence did not verify; nothing exported')
    statuses = gate_statuses(report['final'], report['baseline'])
    switches = {name: name in enable for name in ('display', 'strict_a', 'self_owner', 'named_owner')}
    if any(switches.values()) and report.get('acceptance_scope') != 'actual-chain':
        raise ValueError('Only actual-chain acceptance can request live switches; DJI is report-only')
    if switches['strict_a'] and not switches['display'] or (switches['self_owner'] or switches['named_owner']) and not switches['strict_a']:
        raise ValueError('Explicitly request display before strict_a, and strict_a before either owner gate')
    if any(on and statuses[name] != 'pass' for name, on in switches.items()):
        raise ValueError('A requested switch lacks passing independent acceptance evidence')
    fields = ('dataset', 'version', 'finalized', 'fingerprints', 'manifest_hash', 'truth_hash', 'frozen_hash',
              'holdout_count', 'holdout_ids', 'holdout_recordings', 'holdout_consumed',
              'holdout_consumption_ids', 'holdout_policy', 'source', 'sampling', 'limitations',
              'final', 'baseline', 'gate_statuses', 'calibrator_hash', 'acceptance_scope')
    receipt = {name: report[name] for name in fields if name in report}
    config = report['config']
    calibrators = {key: config.get(key) for key in ('calibrator', 'owner_calibrator', 'named_calibrator')}
    frozen = {'schema': 2, 'kind': 'deployment-freeze', 'dataset': report['dataset'], 'version': report['version'],
              'source_frozen_hash': report['frozen_hash'], 'manifest_hash': report['manifest_hash'],
              'fingerprints': report['fingerprints'], 'config': config,
              'holdout_policy': report['holdout_policy']}
    receipt.update(schema=2, kind='signed-deployment-receipt', config=config, switches=switches,
                   calibrator_hash=calibrator_hash(config), compact_frozen_hash=digest(frozen),
                   calibrators_hash=digest(calibrators), frozen_file='frozen.json', calibrators_file='calibrators.json')
    destination = Path(destination).expanduser().resolve()
    signing_key = Path(signing_key).expanduser().resolve(strict=True)
    if signing_key.stat().st_mode & 0o077:
        raise ValueError('Signing key permissions must be 0600')
    if destination.exists():
        raise ValueError('Use a new package directory; existing files will not be overwritten')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.acceptance-export-', dir=destination.parent))
    try:
        for name, value in (('frozen.json', frozen), ('calibrators.json', calibrators)):
            (temporary / name).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2) + '\n')
            (temporary / name).chmod(0o600)
        receipt['compact_frozen_hash'] = hashlib.sha256((temporary / 'frozen.json').read_bytes()).hexdigest()
        receipt['calibrators_file_hash'] = hashlib.sha256((temporary / 'calibrators.json').read_bytes()).hexdigest()
        (temporary / 'report.json').write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2) + '\n')
        (temporary / 'report.json').chmod(0o600)
        subprocess.run(['ssh-keygen', '-Y', 'sign', '-f', str(signing_key), '-n', SIGNATURE_NAMESPACE,
                        str(temporary / 'report.json')], stdin=subprocess.DEVNULL, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20)
        os.replace(temporary, destination)
    finally:
        if temporary.exists(): shutil.rmtree(temporary)
    print(json.dumps({'package': str(destination), 'files': sorted(p.name for p in destination.iterdir()),
                      'switches': switches, 'audio_or_truth_or_vectors_transferred': False}))
    return destination


def fetch_registry(host, destination):
    code = 'from pathlib import Path; import sys; sys.stdout.buffer.write(Path("/var/lib/oops/backend/voiceprints.json").read_bytes())'
    raw = subprocess.check_output(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        '-o', 'StrictHostKeyChecking=yes', host, 'python3 -c ' + shlex.quote(code)], timeout=20)
    registry = json.loads(raw)
    if not isinstance(registry.get('people'), dict) or registry.get('dimension', 192) != 192:
        raise ValueError('Remote registry schema mismatch')
    from voiceprint_registry import person_profiles
    for person in registry['people'].values():
        for profile in person_profiles(person).values():
            vector = profile.get('vector')
            if (not isinstance(vector, list) or len(vector) != 192 or any(
                    type(x) not in (int, float) or not math.isfinite(x) for x in vector)):
                raise ValueError('Remote registry contains an invalid vector')
    destination = Path(destination).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix='.registry-')
    try:
        with os.fdopen(fd, 'wb') as target:
            target.write(raw)
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    print(json.dumps(dict(snapshot=str(destination), people=len(registry['people']),
                          sha256=hashlib.sha256(raw).hexdigest(), audio_transferred=False)))
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='root@103.110.81.78')
    parser.add_argument('--snapshot', type=Path, default=ROOT / 'models/voiceprint/hk-registry-snapshot.json')
    parser.add_argument('--signing-key', type=Path, default=DEFAULT_SIGNING_KEY)
    parser.add_argument('--init-signing-key', action='store_true')
    parser.add_argument('--export-acceptance', type=Path, metavar='REPORT')
    parser.add_argument('--package', type=Path)
    parser.add_argument('--enable', nargs='+', choices=('display', 'strict_a', 'self_owner', 'named_owner'), default=[])
    parser.add_argument('--dataset', default='dji')
    parser.add_argument('--dataset-root')
    parser.add_argument('--version')
    parser.add_argument('--tune', action='store_true')
    parser.add_argument('--snapshot-only', action='store_true')
    parser.add_argument('--no-jev', action='store_true')
    parser.add_argument('--holdout-case-count', type=int)
    parser.add_argument('--holdout-self-count', type=int)
    parser.add_argument('--holdout-named-count', type=int)
    args = parser.parse_args()
    if args.init_signing_key:
        init_signing_key(args.signing_key)
        if not args.export_acceptance:
            return
    if args.export_acceptance:
        if not args.package:
            parser.error('--export-acceptance requires --package')
        return export_acceptance(args.export_acceptance, args.package, args.snapshot, args.signing_key, args.enable)
    if args.package or args.enable:
        parser.error('--package / --enable require --export-acceptance')
    snapshot = fetch_registry(args.host, args.snapshot)
    if args.snapshot_only: return
    command = [sys.executable, str(ROOT / 'scripts/voiceprint.py'), 'split-eval',
               '--dataset', args.dataset, '--registry-snapshot', str(snapshot)]
    for name in ('dataset_root', 'version', 'holdout_case_count', 'holdout_self_count', 'holdout_named_count'):
        value = getattr(args, name)
        if value is not None: command.extend(['--' + name.replace('_', '-'), str(value)])
    if args.tune: command.append('--tune')
    if args.no_jev: command.append('--no-jev')
    subprocess.run(command, cwd=ROOT, check=True)


if __name__ == '__main__': main()
