"""Release download recovery without network access or executing an updater."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
import urllib.request
import zipfile

from app_paths import AppPaths
from scripts import run_dev
import updates


class Response(io.BytesIO):
    def __init__(self,raw,url='https://example.org/release.zip',declared=True):
        super().__init__(raw);self.url=url
        self.headers={'Content-Length':str(len(raw))}if declared else{}
    def geturl(self):return self.url


class UpdatePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)/'install';program=self.root/'程序'
        self.original={'幻化柜.exe':b'launcher','程序/demo/launch.py':b'old source',
            '程序/docs/client-version.json':json.dumps({'version':'v1.0.0'}).encode(),
            '程序/artifacts/source-runtime.json':b'{}','程序/tools/UpdateHelper.exe':b'helper',
            '程序/artifacts/runtime/python/python.exe':b'interpreter'}
        for name,raw in self.original.items():
            target=self.root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        (program/'artifacts/package-manifest.json').write_text(json.dumps({'files':{n:hashlib.sha256(r).hexdigest()for n,r in self.original.items()}}))
        self.layout=AppPaths(program);self.parent=self.layout.derived('updates')
        self.older=self.parent/'previous-job';self.older.mkdir(parents=True);(self.older/'keep.txt').write_text('keep')

    def info(self,raw):
        return {'status':'available','asset':'幻化柜-v1.0.1.zip','current':'v1.0.0','latest':'v1.0.1',
                'bytes':len(raw),'url':'https://example.org/release.zip'}

    def assert_originals_preserved(self):
        for name,raw in self.original.items():self.assertEqual((self.root/name).read_bytes(),raw)
        self.assertEqual((self.older/'keep.txt').read_text(),'keep')

    def test_network_failure_removes_only_the_new_attempt(self):
        with mock.patch.object(updates,'_open',side_effect=TimeoutError('offline')):
            with self.assertRaises(TimeoutError):updates.prepare(self.info(b'archive'),layout=self.layout)
        self.assertEqual(set(self.parent.iterdir()),{self.older});self.assert_originals_preserved()

    def test_cancelled_partial_download_does_not_accumulate_files(self):
        raw=b'x'*150000;cancel=threading.Event()
        with mock.patch.object(updates,'_open',return_value=Response(raw)):
            with self.assertRaises(InterruptedError):
                updates.prepare(self.info(raw),layout=self.layout,cancel=cancel,
                    progress=lambda done,total:cancel.set()if done else None)
        self.assertEqual(set(self.parent.iterdir()),{self.older});self.assert_originals_preserved()

    def test_invalid_archive_removes_partial_stage_and_preserves_installation(self):
        raw=b'not a ZIP'
        with mock.patch.object(updates,'_open',return_value=Response(raw)):
            with self.assertRaises(zipfile.BadZipFile):updates.prepare(self.info(raw),layout=self.layout)
        self.assertEqual(set(self.parent.iterdir()),{self.older});self.assert_originals_preserved()

    def test_successful_verified_job_is_retained_for_the_native_helper(self):
        files={**self.original,'程序/demo/launch.py':b'new source',
               '程序/docs/client-version.json':json.dumps({'version':'v1.0.1'}).encode()}
        manifest={'format':'source-py-v1','version':'v1.0.1','files':{n:hashlib.sha256(r).hexdigest()for n,r in files.items()}}
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w')as archive:
            for name,data in files.items():archive.writestr(name,data)
            archive.writestr('程序/artifacts/package-manifest.json',json.dumps(manifest))
        content=raw.getvalue()
        with mock.patch.object(updates,'_open',return_value=Response(content)):
            folder=updates.prepare(self.info(content),layout=self.layout)
        self.assertEqual(folder.parent,self.parent)
        self.assertEqual((folder/'stage/程序/demo/launch.py').read_bytes(),b'new source')
        self.assertEqual(json.loads((folder/'job.json').read_bytes())['version'],'v1.0.1')
        self.assert_originals_preserved()


class RuntimeDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.archive=Path(self.temporary.name)/'runtime.zip';self.payload=b'pinned archive content'
        self.digest=hashlib.sha256(self.payload).hexdigest()
        for key,value in (('ARCHIVE_BYTES',len(self.payload)),('ARCHIVE_SHA256',self.digest)):
            patch=mock.patch.object(run_dev,key,value);patch.start();self.addCleanup(patch.stop)
        self.output=redirect_stdout(io.StringIO());self.output.__enter__();self.addCleanup(self.output.__exit__,None,None,None)

    def responses(self,payload,declared=True):
        url='https://gitee.com/kilislan-wei/ffxiv_offline_glamour/releases/download/'+run_dev.TAG+'/runtime.zip'
        metadata=[{'tag_name':run_dev.TAG,'assets':[{'name':run_dev.ASSET,'browser_download_url':url}]}]
        return [Response(json.dumps(metadata).encode()),Response(payload,url,declared)]

    def test_verified_cache_needs_no_network(self):
        self.archive.write_bytes(self.payload)
        with mock.patch.object(run_dev,'_open')as opening:run_dev.download_archive(self.archive)
        opening.assert_not_called()

    def test_corrupt_cache_is_replaced_only_by_verified_download(self):
        self.archive.write_bytes(b'x'*len(self.payload))
        with mock.patch.object(run_dev,'_open',side_effect=self.responses(self.payload)):
            run_dev.download_archive(self.archive)
        self.assertEqual(self.archive.read_bytes(),self.payload)
        self.assertEqual(list(self.archive.parent.iterdir()),[self.archive])

    def test_checksum_failure_preserves_previous_cache_and_removes_partial_file(self):
        self.archive.write_bytes(b'old cache')
        with mock.patch.object(run_dev,'_open',side_effect=self.responses(b'x'*len(self.payload))):
            with self.assertRaisesRegex(ValueError,'checksum'):run_dev.download_archive(self.archive)
        self.assertEqual(self.archive.read_bytes(),b'old cache')
        self.assertEqual(list(self.archive.parent.iterdir()),[self.archive])

    def test_unbounded_response_stops_at_the_pinned_size(self):
        with mock.patch.object(run_dev,'_open',side_effect=self.responses(self.payload+b'x',False)):
            with self.assertRaisesRegex(ValueError,'exceeds'):run_dev.download_archive(self.archive)
        self.assertEqual(list(self.archive.parent.iterdir()),[])

    def test_redirect_cannot_downgrade_to_http(self):
        with self.assertRaisesRegex(ValueError,'HTTPS'):
            run_dev._Redirect().redirect_request(urllib.request.Request('https://example.org'),None,302,'redirect',{},'http://example.org/archive.zip')


if __name__=='__main__':unittest.main()
