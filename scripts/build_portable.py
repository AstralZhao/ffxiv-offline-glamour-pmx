"""Build a clean, self-contained Windows PMX ZIP from the pinned release."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.run_dev import TAG,ARCHIVE_SHA256,download_archive

MANIFEST='程序/artifacts/package-manifest.json'
PMX_MODULES={'demo/pmx_export.py','demo/pmx_material.py','demo/pmx_rig.py','demo/qt_pmx_export.py'}


def sha(path):
    with Path(path).open('rb')as source:return hashlib.file_digest(source,'sha256').hexdigest()


def relative_path(name):
    if not isinstance(name,str)or not name or '\\'in name:
        raise ValueError('Invalid package path: '+str(name))
    path=PurePosixPath(name)
    if path.is_absolute()or path.as_posix()!=name or any(
            part in('.','..')or ':'in part or part.endswith((' ','.'))or any(ord(c)<32 for c in part)
            for part in path.parts):
        raise ValueError('Invalid package path: '+name)
    return path


def allowed_file(name):
    path=relative_path(name)
    return name in('幻化柜.exe','使用说明.txt')or (
        len(path.parts)>=3 and path.parts[0]=='程序'and
        path.parts[1]in('artifacts','defaults','demo','docs','tools'))


def clean_environment():
    env=os.environ.copy()
    for key in ('PYTHONHOME','PYTHONPATH','PYTHONUSERBASE','QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH','QML2_IMPORT_PATH'):
        env.pop(key,None)
    env['PYTHONNOUSERSITE']='1'
    return env


def unpack_verified(archive,destination):
    """Extract only the official manifest's checked application files."""
    destination=Path(destination).resolve()
    with zipfile.ZipFile(archive)as package:
        names=package.namelist()
        if len(names)!=len(set(names)):raise ValueError('Duplicate ZIP member')
        markers=[name for name in names if name.endswith('/'+MANIFEST)]
        if len(markers)!=1:raise ValueError('Ambiguous release manifest')
        relative_path(markers[0]);prefix=markers[0][:-len(MANIFEST)]
        manifest=json.loads(package.read(markers[0]))
        if manifest.get('version')!=TAG or manifest.get('format')!='source-py-v1':
            raise ValueError('The archive is not the pinned source release')
        files=manifest.get('files')
        if not isinstance(files,dict)or not files:raise ValueError('Empty release manifest')
        seen=set();total=0
        for name,digest in files.items():
            if not allowed_file(name):raise ValueError('Non-application file in release manifest: '+name)
            if name.casefold()in seen:raise ValueError('Case-colliding release paths')
            seen.add(name.casefold());item=package.getinfo(prefix+name)
            total+=item.file_size
            if total>1024**3:raise ValueError('Unexpectedly large release')
            with package.open(item)as source:actual=hashlib.file_digest(source,'sha256').hexdigest()
            if actual!=digest:raise ValueError('Release checksum differs: '+name)
        for name in files:
            path=relative_path(name);target=destination.joinpath(*path.parts)
            if not target.resolve().is_relative_to(destination):raise ValueError('Package path escapes destination')
            target.parent.mkdir(parents=True,exist_ok=True)
            with package.open(prefix+name)as source,target.open('xb')as out:shutil.copyfileobj(source,out)
    return manifest


def write_json(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf8')


def source_file(path):
    path=Path(path)
    if path.is_symlink()or getattr(path.lstat(),'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError('Build inputs cannot be links: '+str(path))
    if not path.resolve().is_relative_to(ROOT):raise ValueError('Build input escapes checkout')
    return path


def assemble(archive,destination,revision):
    inherited=unpack_verified(archive,destination);program=destination/'程序'
    # Candidate source modules are pruned to the actual dependency closure below.
    for source in sorted(ROOT.glob('*.py')):
        shutil.copyfile(source_file(source),program/'demo'/source.name)
    for target in (program/'demo/assets').rglob('*'):
        if target.is_file():
            source=ROOT/'assets'/target.relative_to(program/'demo/assets')
            if source.is_file():shutil.copyfile(source_file(source),target)
    shutil.copyfile(source_file(ROOT/'packaging/使用说明.txt'),destination/'使用说明.txt')
    write_json(program/'docs/client-version.json',{'version':TAG,'base_version':TAG,
        'edition':'pmx-portable','edition_label':f'PMX 便携版 r{revision}',
        'build_revision':revision,'update_policy':'manual'})
    python=program/'artifacts/runtime/python/python.exe'
    code="import json,runtime_files; m,p=runtime_files._application_files(); print(json.dumps({'modules':[str(x.relative_to(runtime_files.ROOT)).replace(chr(92),'/') for x in m],'profiles':[str(x.relative_to(runtime_files.ROOT)).replace(chr(92),'/') for x in p]}))"
    result=subprocess.run([str(python),'-B','-X','utf8','-c',code],cwd=program,env=clean_environment(),
                          check=True,capture_output=True,encoding='utf8')
    closure=json.loads(result.stdout)
    selected=set(closure['modules']);profiles=sorted(closure['profiles'])
    if not PMX_MODULES<=selected:raise ValueError('The runtime closure omitted a PMX module')
    for folder in ('demo','tools'):
        for path in (program/folder).glob('*.py'):
            if path.relative_to(program).as_posix()not in selected:path.unlink()
    modules={}
    for relative in sorted(selected):
        path=relative_path(relative)
        if path.parent.as_posix()not in('demo','tools')or path.suffix!='.py':raise ValueError('Invalid source module')
        modules[relative]=sha(program.joinpath(*path.parts))
    write_json(program/'artifacts/source-runtime.json',{'version':1,'format':'source-py-v1','modules':modules,'profiles':profiles})
    revision_id=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
    dirty=subprocess.run(['git','-C',str(ROOT),'-c','core.safecrlf=false','diff','--quiet','HEAD'],check=False).returncode!=0
    epoch=int(os.environ.get('SOURCE_DATE_EPOCH')or subprocess.check_output(['git','-C',str(ROOT),'show','-s','--format=%ct','HEAD'],text=True))
    files={}
    for path in sorted(destination.rglob('*')):
        if not path.is_file():continue
        relative=path.relative_to(destination).as_posix()
        if not allowed_file(relative):raise ValueError('Unexpected build output: '+relative)
        if relative!=MANIFEST:files[relative]=sha(path)
    snapshot=hashlib.sha256(json.dumps(modules,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    manifest={**inherited,'created':datetime.fromtimestamp(epoch,timezone.utc).isoformat(),
        'edition':'pmx-portable','build_revision':revision,'update_policy':'manual',
        'source_revision':revision_id,'source_dirty':dirty,'source_snapshot_sha256':snapshot,
        'base_archive_sha256':ARCHIVE_SHA256,'code_modules':len(modules),'runtime_profiles':len(profiles),
        'user_data_included':False,'files':files,
        'bytes':sum((destination/name).stat().st_size for name in files)}
    write_json(destination/MANIFEST,manifest)
    return manifest,epoch


def zip_tree(folder,destination,epoch):
    timestamp=datetime.fromtimestamp(max(epoch,315532800),timezone.utc)
    date=(timestamp.year,timestamp.month,timestamp.day,timestamp.hour,timestamp.minute,timestamp.second)
    with zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9)as package:
        for path in sorted(folder.rglob('*')):
            if not path.is_file():continue
            name=folder.name+'/'+path.relative_to(folder).as_posix()
            info=zipfile.ZipInfo(name,date);info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o100644<<16;info.create_system=3
            package.writestr(info,path.read_bytes(),compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    with zipfile.ZipFile(destination)as package:
        bad=package.testzip()
        if bad is not None:raise ValueError('Output ZIP failed CRC verification: '+bad)


def build(output,revision=1,overwrite=False):
    if type(revision)is not int or not 1<=revision<=9999:raise ValueError('Invalid build revision')
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    name=f'幻化柜-PMX-{TAG}-r{revision}-便携版';target=output/(name+'.zip')
    if target.exists()and not overwrite:raise FileExistsError(str(target)+' already exists; use --overwrite')
    archive=ROOT/'local_runtime'/(TAG+'.zip');archive.parent.mkdir(exist_ok=True)
    download_archive(archive)
    workspace=Path(tempfile.mkdtemp(prefix='.pmx-build-',dir=output))
    try:
        folder=workspace/name;folder.mkdir()
        manifest,epoch=assemble(archive,folder,revision)
        temporary=workspace/(name+'.zip');zip_tree(folder,temporary,epoch)
        digest=sha(temporary);size=temporary.stat().st_size
        temporary.replace(target)
        target.with_suffix('.zip.sha256').write_text(digest+'  '+target.name+'\n',encoding='utf8')
        print(json.dumps({'archive':str(target),'bytes':size,'sha256':digest,'modules':manifest['code_modules'],
                          'profiles':manifest['runtime_profiles'],'files':len(manifest['files'])},ensure_ascii=False),flush=True)
        return target
    finally:
        # This is our own fresh staging directory, never a user's installation.
        if workspace.resolve().parent!=output or workspace.is_symlink():raise ValueError('Unsafe build cleanup target')
        if getattr(workspace.lstat(),'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT:raise ValueError('Linked build workspace')
        shutil.rmtree(workspace)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'dist')
    parser.add_argument('--revision',type=int,default=1)
    parser.add_argument('--overwrite',action='store_true')
    args=parser.parse_args();build(args.output,args.revision,args.overwrite)


if __name__=='__main__':main()
