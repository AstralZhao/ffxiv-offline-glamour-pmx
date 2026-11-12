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
import urllib.parse
import urllib.request
import uuid
import zipfile

ROOT=Path(__file__).resolve().parents[1]
TAG='v0.0.9'  # Application sources in this branch are migrated from this release.
API='https://gitee.com/api/v5/repos/kilislan-wei/ffxiv_offline_glamour/releases'
ASSET='幻化柜-v0.0.9-20261112-233631.zip'
# Pin the entire official archive, including Python, Qt and native binaries.
ARCHIVE_BYTES=49437152
ARCHIVE_SHA256='80f32aef0d0b3f81c8292473c24c5c39d6bdc03b28b60ae6d4f229daeec16637'
MAX_JSON_BYTES=4*1024**2


def _https(url):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Runtime downloads and redirects must use HTTPS')
    return url


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        _https(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def _open(url,timeout=30):
    request=urllib.request.Request(_https(url),headers={'User-Agent':'HuanHuaGui-DevRuntime/1','Accept-Encoding':'identity'})
    return urllib.request.build_opener(_Redirect()).open(request,timeout=timeout)


def download_archive(archive):
    """Reuse only a verified cache; a failed retry leaves its previous file intact."""
    archive=Path(archive)
    if archive.is_file() and archive.stat().st_size==ARCHIVE_BYTES:
        with archive.open('rb')as source:
            if hashlib.file_digest(source,'sha256').hexdigest()==ARCHIVE_SHA256:return
    print('Downloading the matching official application package…',flush=True)
    with _open(API+'?per_page=100')as response:
        _https(response.geturl());raw=response.read(MAX_JSON_BYTES+1)
    if len(raw)>MAX_JSON_BYTES:raise ValueError('Release metadata exceeds the size limit')
    releases=json.loads(raw)
    if not isinstance(releases,list):raise ValueError('Invalid release metadata')
    release=next((row for row in releases if isinstance(row,dict)and row.get('tag_name')==TAG),None)
    assets=release.get('assets',[])if release else []
    asset=next((row for row in assets if isinstance(row,dict)and row.get('name')==ASSET),None)
    if asset is None:raise ValueError('The pinned runtime release asset is unavailable: '+ASSET)
    url=asset.get('browser_download_url','')
    if not isinstance(url,str)or not url.startswith('https://gitee.com/kilislan-wei/ffxiv_offline_glamour/releases/download/'+TAG+'/'):
        raise ValueError('Unexpected release download location')
    temporary=archive.with_name(archive.name+'.'+uuid.uuid4().hex+'.download')
    try:
        digest=hashlib.sha256();received=0
        with _open(url,timeout=60)as response,temporary.open('xb')as out:
            _https(response.geturl());length=response.headers.get('Content-Length')
            if length is not None and int(length)!=ARCHIVE_BYTES:raise ValueError('Unexpected runtime archive size')
            while True:
                block=response.read(65536)
                if not block:break
                received+=len(block)
                if received>ARCHIVE_BYTES:raise ValueError('Runtime download exceeds the pinned size')
                digest.update(block);out.write(block)
        if received!=ARCHIVE_BYTES or digest.hexdigest()!=ARCHIVE_SHA256:
            raise ValueError('Runtime archive checksum differs; no downloaded code was extracted')
        temporary.replace(archive)
    finally:temporary.unlink(missing_ok=True)


def prepare():
    local=ROOT/'local_runtime';local.mkdir(exist_ok=True)
    # A new baseline must never reuse older tools, tables or Python binaries.
    archive=local/(TAG+'.zip');runtime=local/('dev-'+TAG)
    if not (runtime/'artifacts/source-runtime.downloaded.json').is_file():
        download_archive(archive)
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
    if json.loads((runtime/'docs/client-version.json').read_bytes()).get('version')!=TAG:
        raise ValueError('The development runtime does not match the pinned release: '+str(runtime))
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
