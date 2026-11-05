"""Source identities in development; verified build identities in extensions.

Compiled releases contain hashes, not hidden copies of Python source. This is
an integrity/cache contract, not a signature or protection against a person
who can replace both the program and its manifest.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_manifest = None


def _load_manifest(root):
    record = json.loads((root/'artifacts/compiled-runtime.json').read_bytes())
    if record.get('version') != 1 or not isinstance(record.get('modules'), dict):
        raise ValueError('Unknown compiled runtime identity')
    for logical, item in record['modules'].items():
        source = Path(logical)
        binary = Path(item['file'])
        if (source.parent.as_posix() not in ('demo', 'tools') or source.suffix != '.py'
                or binary.parent != source.parent or binary.name != source.stem+'.cp311-win_amd64.pyd'):
            raise ValueError('Compiled module path differs')
        target = (root/binary).resolve(strict=True)
        if not target.is_relative_to(root.resolve()):
            raise ValueError('Compiled module escapes program directory')
        if hashlib.sha256(target.read_bytes()).hexdigest() != item['binary_sha256']:
            raise ValueError('Compiled module identity differs: '+logical)
        value = item['source_sha256']
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('Invalid compiled source identity')
    if not record['modules']:
        raise ValueError('Compiled runtime has no modules')
    for relative in record['profiles']:
        path = Path(relative)
        if path.parent.as_posix() != 'docs' or path.suffix != '.json':
            raise ValueError('Invalid compiled profile path')
        if not (root/path).resolve(strict=True).is_relative_to(root.resolve()):
            raise ValueError('Compiled profile escapes program directory')
    return record


def compiled_manifest():
    global _manifest
    # A stray manifest never changes source development semantics.
    if not __file__.endswith('.pyd'):
        return None
    if _manifest is None:
        _manifest = _load_manifest(ROOT)
    return _manifest


def logical_source(path):
    path = Path(path)
    if path.name.endswith('.cp311-win_amd64.pyd'):
        path = path.with_name(path.name.removesuffix('.cp311-win_amd64.pyd')+'.py')
    return path


def implementation_digest(path):
    """Hash exact source bytes, or the checked release's original source hash."""
    path = logical_source(path)
    record = compiled_manifest()
    if record is not None and path.suffix == '.py':
        relative = path.resolve().relative_to(ROOT).as_posix()
        if relative not in record['modules']:
            raise ValueError('Module absent from compiled runtime: '+relative)
        return record['modules'][relative]['source_sha256']
    return hashlib.sha256(path.read_bytes()).hexdigest()
