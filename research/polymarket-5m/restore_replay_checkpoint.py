"""Restore a data-only embedded replay checkpoint; never execute stored code.

Offline. Exact member hashes, paths and sizes are checked before publishing a
new cache directory. Repeated restoration verifies existing bytes; no overwrite.
"""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import zipfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def restore(state_path, key, destination):
    state = json.loads(Path(state_path).read_text())
    checkpoint = state['embedded_replay_checkpoints'][key]
    if checkpoint['encoding'] != 'zip+base64':
        raise ValueError('unsupported checkpoint encoding')
    payload = base64.b64decode(checkpoint['data_base64'], validate=True)
    if len(payload) != checkpoint['bytes'] or sha(payload) != checkpoint['sha256']:
        raise ValueError('checkpoint hash or size mismatch')
    destination = Path(destination)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('duplicate archive names')
        manifest = json.loads(archive.read('CHECKPOINT_MANIFEST.json'))
        if manifest != checkpoint['manifest']:
            raise ValueError('manifest mismatch')
        members = manifest['members']
        if set(names) != set(members) | {'CHECKPOINT_MANIFEST.json'}:
            raise ValueError('unexpected or missing archive members')
        if sum(i.file_size for i in archive.infolist()) > 512 * 1024**2:
            raise ValueError('expanded checkpoint too large')
        for name, meta in members.items():
            path = PurePosixPath(name)
            if (not name or path.is_absolute() or '..' in path.parts
                    or '\\' in name or str(path) != name):
                raise ValueError('unsafe member path')
            info = archive.getinfo(name)
            if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('unsupported member type')
            data = archive.read(name)
            if len(data) != meta['bytes'] or sha(data) != meta['sha256']:
                raise ValueError('member hash or size mismatch')
        if destination.exists():
            if destination.is_symlink():
                raise ValueError('destination is a symlink')
            actual = {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()}
            if actual != set(names):
                raise ValueError('existing cache member set differs')
            for name in names:
                path = destination / name
                if any(p.is_symlink() for p in [path, *path.parents]):
                    raise ValueError('symlink in existing cache')
                if path.read_bytes() != archive.read(name):
                    raise ValueError('existing cache corruption')
            reused = True
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix='replay-restore-', dir=destination.parent))
            try:
                for name in names:
                    path = stage / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.read(name))
                if destination.exists():
                    raise ValueError('destination appeared during restore')
                stage.rename(destination)
            except BaseException:
                shutil.rmtree(stage)
                raise
            reused = False
    return {'checkpoint_key': key, 'sha256': checkpoint['sha256'],
            'verified_members': len(members), 'cache': str(destination.resolve()),
            'reused': reused, 'research_git_head': checkpoint['git_head'],
            'new_backtests': 0, 'pnl': None}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True)
    parser.add_argument('--key', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    print(json.dumps(restore(args.state, args.key, args.out), indent=2))
