"""Prepare the missing runtime from the matching official release and run source.

Downloaded game/application data stays in local_runtime (ignored by Git).
Only the dedicated development copy is overlaid; release archives stay intact.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
TAG='v0.0.7-beta.3'  # The release matching this repository's facewear UI.
API='https://gitee.com/api/v5/repos/kilislan-wei/ffxiv_offline_glamour/releases'


def prepare():
    local=ROOT/'local_runtime';local.mkdir(exist_ok=True)
    archive=local/(TAG+'.zip');runtime=local/'dev'
    if not (runtime/'artifacts/source-runtime.downloaded.json').is_file():
        if not archive.is_file():
            print('Downloading the matching official application package…',flush=True)
            with urllib.request.urlopen(API,timeout=30)as response:releases=json.load(response)
            release=next(row for row in releases if row['tag_name']==TAG)
            asset=next(row for row in release['assets']if '/releases/download/'in row['browser_download_url']and row['name'].endswith('.zip'))
            url=asset['browser_download_url']
            if not url.startswith('https://gitee.com/kilislan-wei/ffxiv_offline_glamour/releases/download/'+TAG+'/'):
                raise ValueError('Unexpected release download location')
            temporary=archive.with_suffix('.download')
            try:
                with urllib.request.urlopen(url,timeout=60)as response,temporary.open('wb')as out:
                    shutil.copyfileobj(response,out)
                temporary.replace(archive)
            finally:temporary.unlink(missing_ok=True)
        with zipfile.ZipFile(archive)as package:
            modules=[p for p in package.namelist()if p.endswith('/程序/artifacts/source-runtime.json')]
            if len(modules)!=1:raise ValueError('The package has no unambiguous source runtime')
            prefix=modules[0].removesuffix('artifacts/source-runtime.json')
            manifest=json.loads(package.read(modules[0]))
            if manifest.get('version')!=1 or manifest.get('format')!='source-py-v1':
                raise ValueError('Unsupported source runtime manifest')
            for name,expected in manifest['modules'].items():
                if hashlib.sha256(package.read(prefix+name)).hexdigest()!=expected:
                    raise ValueError('Release source checksum differs: '+name)
            total=0
            for item in package.infolist():
                if not item.filename.startswith(prefix)or item.is_dir():continue
                relative=PurePosixPath(item.filename[len(prefix):])
                if relative.is_absolute()or '..'in relative.parts or any(':'in p or '\\'in p for p in relative.parts):
                    raise ValueError('Unsafe release archive path')
                target=runtime.joinpath(*relative.parts)
                if not target.resolve().is_relative_to(runtime.resolve()):raise ValueError('Archive path escapes runtime')
                total+=item.file_size
                if total>2*1024**3:raise ValueError('Unexpectedly large application package')
                target.parent.mkdir(parents=True,exist_ok=True)
                with package.open(item)as src,target.open('wb')as dst:shutil.copyfileobj(src,dst)
        # This directory is an editable source runtime, not an installed
        # release. Preserve its original manifest as download provenance.
        (runtime/'artifacts/source-runtime.json').rename(runtime/'artifacts/source-runtime.downloaded.json')
    for source in ROOT.iterdir():
        if source.is_file()and source.suffix in('.py','.css','.js','.html'):
            shutil.copy2(source,runtime/'demo'/source.name)
    shutil.copytree(ROOT/'assets',runtime/'demo/assets',dirs_exist_ok=True)
    return runtime


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only',action='store_true')
    options,arguments=parser.parse_known_args()
    runtime=prepare()
    print('Development runtime: '+str(runtime),flush=True)
    if options.prepare_only:return 0
    python=runtime/'artifacts/runtime/python/python.exe'
    return subprocess.call([str(python),'-X','utf8',str(runtime/'demo/launch.py'),*arguments],cwd=runtime)


if __name__=='__main__':raise SystemExit(main())
