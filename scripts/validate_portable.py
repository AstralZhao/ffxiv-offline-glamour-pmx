"""Unzip a portable release away from the checkout and exercise its own runtime."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import time
import uuid
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def sha(path):
    with Path(path).open('rb')as source:return hashlib.file_digest(source,'sha256').hexdigest()


def isolated_environment():
    env=os.environ.copy()
    for name in ('PYTHONHOME','PYTHONPATH','PYTHONUSERBASE','VIRTUAL_ENV','QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH','QML2_IMPORT_PATH'):
        env.pop(name,None)
    windows=Path(os.environ['SystemRoot'])
    env['PATH']=str(windows/'System32')+';'+str(windows)
    env['PYTHONNOUSERSITE']='1'
    return env


def run(command,*,cwd,env,log,timeout=180):
    process=subprocess.Popen(command,cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    try:output,_=process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run([str(Path(os.environ['SystemRoot'])/'System32/taskkill.exe'),'/PID',str(process.pid),'/T','/F'],capture_output=True)
        output,_=process.communicate();Path(log).write_bytes(output)
        raise TimeoutError('Portable check timed out; see '+str(log))
    Path(log).write_bytes(output)
    if process.returncode:raise RuntimeError('Portable check failed; see '+str(log)+'\n'+output[-4000:].decode('utf8',errors='replace'))
    return output.decode('utf8',errors='replace')


def inside(install,game,report_path):
    """Only stdlib from this harness; all application imports come from the ZIP."""
    install=install.resolve();program=install/'程序';game=game.resolve();os.chdir(program)
    from app_paths import paths,verify_source_release,migrate
    assert paths().root==install
    verify_source_release();migrate()
    from qt_runtime import enable
    enable()
    import numpy,PIL,PySide6,shiboken6,app,pmx_export,pmx_material,pmx_rig,pmx_morphs,qt_pmx_export
    imports={module.__name__:str(Path(module.__file__).resolve())for module in(numpy,PIL,PySide6,shiboken6,app,pmx_export,pmx_material,pmx_rig,pmx_morphs,qt_pmx_export)}
    assert all(Path(path).is_relative_to(program)for path in imports.values()),imports
    assert Path(sys.executable).resolve().is_relative_to(program)
    from PySide6 import QtWidgets as W,QtCore as C
    from qt_theme import STYLE
    from qt_first_run import FirstRunDialog
    from settings import SettingsStore
    from qt_client import QtClient,WardrobeWindow
    from qt_updates import UpdateController,UpdatePanel
    from app_version import current_version,can_install,manual_updates
    application=W.QApplication([]);application.setStyleSheet(STYLE);application.setQuitOnLastWindowClosed(False)
    window=WardrobeWindow();window.setAttribute(C.Qt.WA_DontShowOnScreen)
    store=SettingsStore();assert not store.path.exists()
    setup=FirstRunDialog(store,parent=window);setup.setAttribute(C.Qt.WA_DontShowOnScreen)
    client=None;updater=None

    def until(test,timeout=180):
        deadline=time.monotonic()+timeout
        while not test():
            application.processEvents();time.sleep(.01)
            if time.monotonic()>deadline:raise TimeoutError('Qt operation timed out')

    try:
        setup.game_mode.setChecked(True);setup.path.setText(str(game));setup.save()
        until(lambda:setup.future is None)
        assert setup.saved_game==game,setup.status.text()
        assert store.read()==(game,'')
        print('FIRST_RUN_CONFIGURATION_OK',flush=True)
        client=QtClient(window,game,program/'defaults/test.dat');window.show()
        until(lambda:client.ready and client.job is None and not client.dirty and client.effect_deferred is None)
        assert client.pmx_export_button.isEnabled()
        session=C.QObject(window);session.application=application;session.window=window;session.controller=client
        updater=UpdateController(session);updater.check()
        assert manual_updates()and not can_install()and not updater.auto and updater.future is None
        panel=UpdatePanel(updater,window)
        assert any('PMX 便携版' in label.text()for label in panel.findChildren(W.QLabel))
        assert not panel.findChildren(W.QPushButton)
        print('PORTABLE_PREVIEW_AND_UPDATE_POLICY_OK',flush=True)
        output=report_path.parent/'导出结果';output.mkdir(parents=True,exist_ok=True)
        destination=output/'便携版 标准角色.pmx'
        client.pmx_export_button.click();application.processEvents()
        dialog=next(d for d in client.dialogs if d.windowTitle()=='导出 MMD 模型')
        picker_type=W.QFileDialog
        class Picker(picker_type):
            def exec(self):return W.QDialog.Accepted
            def selectedFiles(self):return [str(destination)]
        qt_pmx_export.W.QFileDialog=Picker
        ticks=[];timer=C.QTimer();timer.setInterval(20);timer.timeout.connect(lambda:ticks.append(1));timer.start()
        try:
            next(b for b in dialog.findChildren(W.QPushButton)if b.text()=='选择位置并导出').click()
            assert client.job is not None
            assert not client.pmx_export_button.isEnabled()
            until(lambda:client.job is None and client.effect_deferred is None)
        finally:qt_pmx_export.W.QFileDialog=picker_type;timer.stop()
        assert 'PMX 已导出' in client.message.get(),client.message.get()
        assert client.pmx_export_button.isEnabled()and len(ticks)>2
        exports=[json.loads(path.read_bytes())for path in output.glob('pmx_assets_*/export.json')]
        standard=next(record for record in exports if record['model']==destination.name)
        assert standard['ik_bones']==4 and standard['morphs']==13 and standard['physics_joints']>0
        assert standard['expression_source']=='native_facial'and len(standard['morph_sources'])==13
        assert standard['textures']>0 and destination.is_file()
        for item in list(client.dialogs):item.close()
        future=client.engine.pool.submit(client.engine.export_pmx,output/'便携版 原生角色.pmx',{'rig':'native','textures':False,'overwrite':True})
        until(future.done);native=future.result()
        assert native['rig']=='native'and native['ik_bones']==0 and native['morphs']==0
        future=client.engine.pool.submit(client.engine.render,client.good)
        until(future.done);assert future.result()['visible_meshes']>0
        report={'version':current_version(),'interpreter':sys.executable,'module_paths':imports,
                'runtime_versions':{'python':sys.version.split()[0],'numpy':numpy.__version__,
                                    'pillow':PIL.__version__,'pyside6':PySide6.__version__},
                'first_run_configuration':True,'manual_updates':True,'preview_after_export':True,
                'ui_timer_ticks':len(ticks),'standard':standard,'standard_path':str(destination),'native':native}
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print('PORTABLE_GUI_EXPORT_OK',standard['vertices'],standard['bones'],standard['textures'],flush=True)
    finally:
        if updater:updater.shutdown()
        if client:
            for dialog in list(client.dialogs):dialog.close()
            client.close();until(lambda:client.closed,timeout=60)
        setup.close();window.close();application.processEvents()


def validate(archive,game,extract_parent,report_parent):
    archive=archive.resolve();game=game.resolve();identifier=uuid.uuid4().hex[:8]
    job=extract_parent.resolve()/('解压验证 '+identifier);job.mkdir(parents=True)
    report_dir=report_parent.resolve()/identifier;report_dir.mkdir(parents=True)
    with zipfile.ZipFile(archive)as package:
        names=package.namelist();roots=set()
        for name in names:
            path=PurePosixPath(name)
            if path.is_absolute()or '..'in path.parts or any(':'in p or '\\'in p for p in path.parts):raise ValueError('Unsafe ZIP member')
            roots.add(path.parts[0]);target=job.joinpath(*path.parts)
            if not target.resolve().is_relative_to(job):raise ValueError('ZIP member escapes validation folder')
        assert len(roots)==1
        package.extractall(job)
    install=job/roots.pop();program=install/'程序'
    manifest=json.loads((program/'artifacts/package-manifest.json').read_bytes())
    actual={p.relative_to(install).as_posix()for p in install.rglob('*')if p.is_file()}
    assert actual==set(manifest['files'])|{'程序/artifacts/package-manifest.json'}
    for name,digest in manifest['files'].items():assert sha(install/name)==digest,name
    assert not any((install/name).exists()for name in('用户数据','缓存','resources','local_runtime','test_output'))
    env=isolated_environment();foreign=job/'无开发环境的工作目录';foreign.mkdir()
    run([str(install/'幻化柜.exe'),'--startup-check'],cwd=foreign,env=env,log=report_dir/'launcher-process.log',timeout=90)
    launcher_log=(install/'缓存/logs/launcher.log').read_text(encoding='utf8')
    assert 'STARTUP_CHECK_OK'in launcher_log
    python=program/'artifacts/runtime/python/python.exe'
    probe=program/'demo/pmx_export.py';original=probe.read_bytes()
    try:
        probe.write_bytes(original+b'\n# Validation-only integrity probe\n')
        result=subprocess.run([str(python),'-B','-X','utf8','-c','from app_paths import verify_source_release; verify_source_release()'],
                              cwd=foreign,env=env,capture_output=True,timeout=30)
        assert result.returncode!=0 and b'Source module identity differs'in result.stderr
    finally:probe.write_bytes(original)
    gui_report=report_dir/'gui-export.json'
    run([str(python),'-B','-X','utf8',str(Path(__file__).resolve()),'--inside-install',str(install),
         '--game',str(game),'--inside-report',str(gui_report)],cwd=foreign,env=env,log=report_dir/'gui-export.log',timeout=300)
    report={'archive':str(archive),'sha256':sha(archive),'archive_bytes':archive.stat().st_size,'installed_directory':str(install),
            'manifest_files_checked':len(manifest['files']),'clean_archive':True,'launcher_startup_check':True,
            'source_integrity_check':True,'restricted_path':env['PATH'],'gui_report':str(gui_report)}
    (report_dir/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False),flush=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--game',type=Path,required=True)
    parser.add_argument('--extract-parent',type=Path,default=ROOT.parent/'PMX Portable 验收')
    parser.add_argument('--reports',type=Path,default=ROOT/'test_output/portable')
    parser.add_argument('--inside-install',type=Path)
    parser.add_argument('--inside-report',type=Path)
    args=parser.parse_args()
    if args.inside_install:inside(args.inside_install,args.game,args.inside_report)
    elif args.archive:validate(args.archive,args.game,args.extract_parent,args.reports)
    else:parser.error('--archive is required')


if __name__=='__main__':main()
