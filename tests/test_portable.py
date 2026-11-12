"""Portable edition identity, clean package inputs and reproducible archives."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from app_paths import AppPaths
import app_version
from scripts import build_portable
import updates


class PortableUpdateTests(unittest.TestCase):
    def test_custom_edition_cannot_download_an_upstream_replacement(self):
        with tempfile.TemporaryDirectory()as directory:
            program=Path(directory)/'程序';(program/'artifacts').mkdir(parents=True);(program/'docs').mkdir()
            (program/'artifacts/source-runtime.json').write_text('{}')
            version=program/'docs/client-version.json'
            version.write_text(json.dumps({'version':'v0.0.9','update_policy':'manual','edition_label':'PMX 便携版 r1'}),encoding='utf8')
            with mock.patch.object(app_version,'paths',return_value=AppPaths(program)),mock.patch.object(updates,'_open')as opening:
                self.assertFalse(app_version.can_install())
                self.assertEqual(app_version.current_version(),'v0.0.9')
                self.assertEqual(app_version.distribution_label(),'PMX 便携版 r1')
                with self.assertRaisesRegex(ValueError,'手动更新'):updates.prepare({})
                opening.assert_not_called()
                version.write_text(json.dumps({'version':'v0.0.9'}))
                self.assertTrue(app_version.can_install())


class PortablePackageTests(unittest.TestCase):
    def archive(self,folder,files,*,bad_checksum=False):
        target=folder/'official.zip';prefix='official/'
        manifest={'version':build_portable.TAG,'format':'source-py-v1',
                  'files':{name:hashlib.sha256(data).hexdigest()for name,data in files.items()}}
        if bad_checksum:manifest['files'][next(iter(files))]='0'*64
        with zipfile.ZipFile(target,'w')as archive:
            for name,data in files.items():archive.writestr(prefix+name,data)
            archive.writestr(prefix+build_portable.MANIFEST,json.dumps(manifest))
        return target

    def test_manifest_rejects_personal_files_before_extracting_anything(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);destination=root/'unpacked'
            archive=self.archive(root,{'幻化柜.exe':b'app','用户数据/presets/private.dat':b'private'})
            with self.assertRaisesRegex(ValueError,'Non-application'):build_portable.unpack_verified(archive,destination)
            self.assertFalse(destination.exists())

    def test_checksum_failure_does_not_leave_a_partial_installation(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);destination=root/'unpacked'
            archive=self.archive(root,{'幻化柜.exe':b'app','程序/demo/launch.py':b'code'},bad_checksum=True)
            with self.assertRaisesRegex(ValueError,'checksum'):build_portable.unpack_verified(archive,destination)
            self.assertFalse(destination.exists())

    def test_path_traversal_and_windows_aliases_are_rejected(self):
        for name in ('../outside','程序/demo/../secret','C:/file','程序//demo/test.py','程序/demo/hidden.py:stream','程序/demo/test.py.','程序\\demo\\test.py'):
            with self.subTest(name=name),self.assertRaises(ValueError):build_portable.relative_path(name)

    def test_valid_manifest_extracts_only_listed_application_files(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);destination=root/'unpacked'
            archive=self.archive(root,{'幻化柜.exe':b'app','程序/demo/launch.py':b'code'})
            with zipfile.ZipFile(archive,'a')as package:package.writestr('official/用户数据/secret.txt',b'not a build input')
            build_portable.unpack_verified(archive,destination)
            self.assertEqual((destination/'程序/demo/launch.py').read_bytes(),b'code')
            self.assertFalse((destination/'用户数据').exists())

    def test_zip_is_reproducible_and_keeps_unicode_paths(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);folder=root/'PMX 便携版';(folder/'程序').mkdir(parents=True)
            (folder/'程序/a.txt').write_text('内容',encoding='utf8')
            first=root/'one.zip';second=root/'two.zip'
            build_portable.zip_tree(folder,first,1794513600)
            build_portable.zip_tree(folder,second,1794513600)
            self.assertEqual(first.read_bytes(),second.read_bytes())
            with zipfile.ZipFile(first)as archive:self.assertEqual(archive.read('PMX 便携版/程序/a.txt').decode(),'内容')


if __name__=='__main__':unittest.main()
