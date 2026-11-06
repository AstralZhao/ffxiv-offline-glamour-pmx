"""One portable layout for source development and compiled distributions.

Program files are immutable update inputs. Personal data and disposable caches
belong to the installation, never to the Python/module directory.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import uuid
from contextlib import closing

PROGRAM = Path(__file__).resolve().parents[1]


def is_release(program):
    program=Path(program)
    return any((program/'artifacts'/name).is_file() for name in ('source-runtime.json','compiled-runtime.json'))


def verify_source_release(program=PROGRAM):
    program=Path(program)
    marker=program/'artifacts/source-runtime.json'
    if not marker.is_file():return
    record=json.loads(marker.read_bytes())
    if record.get('version')!=1 or record.get('format')!='source-py-v1' or not record.get('modules'):
        raise ValueError('Unknown source release identity')
    for relative,expected in record['modules'].items():
        path=Path(relative)
        if path.parent.as_posix() not in ('demo','tools') or path.suffix!='.py':
            raise ValueError('Invalid source module path')
        target=no_links(program/path,program)
        if _sha(target)!=expected:raise ValueError('Source module identity differs: '+relative)
        if list(target.parent.glob(target.stem+'.*.pyd')) or target.with_suffix('.pyd').exists():
            raise ValueError('Source release contains a shadowing extension: '+relative)


def program_root(root):
    root = Path(root).resolve()
    return root/'程序' if is_release(root/'程序') else root


class AppPaths:
    def __init__(self, program=PROGRAM):
        self.program = Path(program).resolve()
        self.root = self.program.parent if self.program.name == '程序' and is_release(self.program) else self.program
        self.user = self.root/'用户数据'
        self.cache = self.root/'缓存'
        self.presets = self.user/'presets'
        self.config = self.user/'config.json'
        self.resources = self.root/'resources'

    def derived(self, name):
        if name not in ('character-cache','prepared-presets','static-hair-cache','network-resources','logs','updates','appearance-previews'):
            raise ValueError('Unknown managed cache')
        return self.cache/name


def paths():
    return AppPaths()


def no_links(path, root):
    """Check existing ancestors, including the root; do not follow junctions."""
    path, root = Path(path).absolute(), Path(root).absolute()
    if not path.is_relative_to(root):
        raise ValueError('Path is outside the installation')
    for p in (root, *[root.joinpath(*path.relative_to(root).parts[:i]) for i in range(1,len(path.relative_to(root).parts)+1)]):
        if p.exists() or p.is_symlink():
            info = p.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                raise ValueError('Managed paths cannot contain links: '+str(p))
    return path


def _sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def migrate(layout=None):
    """Copy legacy personal files once, verify before commit, retain originals.

    An interrupted copy resumes without overwriting an existing destination.
    Resources and large research/derived trees stay in place; legacy resource
    config paths are explicitly accepted until the user selects a new source.
    """
    p = layout or paths()
    marker = p.user/'layout-v1.json'
    no_links(marker,p.root)
    if marker.is_file():
        if json.loads(marker.read_bytes()).get('version') != 1:
            raise ValueError('Unknown personal-data layout')
        return
    pairs = []
    old = p.program
    for name in ('config.json','outfits.json','native-session.json','equipment-library.sqlite'):
        source = old/name if name=='config.json' else old/'artifacts/demo'/name
        if source.is_file(): pairs.append((source,p.user/name))
    legacy = old/'presets'
    if legacy.is_dir():
        pairs.extend((f,p.presets/f.name) for f in legacy.iterdir() if f.is_file() and (f.suffix.lower()=='.dat' or f.name=='library.json'))
    # Preserve a user's explicit deletion of the default seed.
    deleted_default=False
    if (legacy/'library.json').is_file():
        metadata=json.loads((legacy/'library.json').read_bytes())
        deleted_default=any(name=='test.dat' and origin in metadata.get('deleted_seeds',[]) for origin,name in metadata.get('seeded',{}).items())
    # A packaged default is an immutable seed. Never replace an edited test.
    default = old/'defaults/test.dat'
    if not pairs and default.is_file() and not deleted_default:
        pairs.append((default,p.presets/'test.dat'))
    if not deleted_default and not any(dst==p.presets/'test.dat' for _,dst in pairs) and default.is_file() and not (p.presets/'test.dat').exists():
        pairs.append((default,p.presets/'test.dat'))
    records = []
    for source,target in pairs:
        no_links(source,p.root); no_links(target,p.root)
        if target.exists():
            # Existing new-layout files are authoritative, including after a
            # partial migration. Do not resurrect deleted/edited old records.
            continue
        target.parent.mkdir(parents=True,exist_ok=True)
        temporary = target.with_name('.migrate-'+uuid.uuid4().hex)
        try:
            if source.suffix=='.sqlite':
                with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as db, closing(sqlite3.connect(temporary)) as out:
                    with out:
                        db.backup(out)
                        if out.execute('PRAGMA quick_check').fetchone()[0]!='ok': raise ValueError('Personal database verification failed')
            else:
                before = _sha(source);shutil.copy2(source,temporary)
                if _sha(source)!=before or _sha(temporary)!=before: raise ValueError('Legacy file changed during migration')
            if target.exists(): raise FileExistsError('Migration target appeared: '+str(target))
            if source.name=='config.json' and p.root!=p.program:
                data=json.loads(temporary.read_bytes())
                if isinstance(data.get('resource_directory'),str):data['resource_directory']='程序/'+data['resource_directory']
                for value in data.get('resource_inputs',{}).values():
                    if isinstance(value,dict) and isinstance(value.get('software_relative'),str):value['software_relative']='程序/'+value['software_relative']
                temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
            temporary.rename(target)
            records.append({'source':str(source.relative_to(p.root)),'target':str(target.relative_to(p.root)),'sha256':_sha(target)})
        finally:
            temporary.unlink(missing_ok=True)
    # Preserve IDs derived from old absolute paths, including libraries which
    # had no index. Session restoration must still select the same character.
    if p.presets.is_dir():
        index = p.presets/'library.json'
        data = json.loads(index.read_bytes()) if index.exists() else {'version':1,'files':{},'seeded':{}}
        changed = not index.exists()
        for f in p.presets.glob('*.dat'):
            if f.name not in data['files']:
                data['files'][f.name]={'id':hashlib.sha256(str((legacy/f.name).resolve()).encode()).hexdigest()[:24]};changed=True
        if deleted_default and str(default.resolve()) not in data.get('deleted_seeds',[]):
            data.setdefault('deleted_seeds',[]).append(str(default.resolve()));changed=True
        if changed: _atomic_json(index,data)
    p.user.mkdir(parents=True,exist_ok=True)
    _atomic_json(marker,{'version':1,'copied':records,'legacy_originals_retained':True})


def _atomic_json(path,data):
    temporary=path.with_name('.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf8') as out:
            json.dump(data,out,ensure_ascii=False,indent=2);out.flush();os.fsync(out.fileno())
        temporary.replace(path)
    finally: temporary.unlink(missing_ok=True)
