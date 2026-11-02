"""First-run and in-app resource source selection, import and export."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from qt_runtime import enable
enable()
from PySide6 import QtCore as C,QtWidgets as W
from qt_theme import label,button,vertical,horizontal,LoadingSpinner
from settings import validate_game


class SettingsDialog(W.QDialog):
    def __init__(self,store,game=None,*,first=False,error='',parent=None):
        super().__init__(parent);self.store=store;self.saved_game=None;self.future=None
        self.cancel_event=threading.Event();self.progress_record=None;self.operation=None
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='WardrobeConfig')
        self.setWindowTitle('首次配置'if first else'设置');self.resize(620,400)
        outer=vertical(self,24,16);outer.addWidget(label('开始使用幻衣间'if first else'软件设置','Title'))
        hint=label('选择资源来源：使用本机国服游戏目录，或导入独立资源包。导入后无需安装游戏，配置会自动记住。','Muted');hint.setWordWrap(True);outer.addWidget(hint)
        modes=horizontal();self.game_mode=W.QRadioButton('使用游戏目录');self.pack_mode=W.QRadioButton('导入资源包')
        modes.addWidget(self.game_mode);modes.addWidget(self.pack_mode);modes.addStretch();outer.addLayout(modes)
        selected=bool(game and(Path(game)/'resource-pack.json').is_file())
        self.game_mode.setChecked(not selected);self.pack_mode.setChecked(selected)
        self.path_label=label('国服游戏目录','Section');outer.addWidget(self.path_label);row=horizontal();self.path=W.QLineEdit(str(game)if game else'')
        self.path.setPlaceholderText('包含 game 文件夹的游戏安装目录');row.addWidget(self.path,1)
        self.browse=button('选择目录',self.choose);row.addWidget(self.browse);outer.addLayout(row)
        exports=horizontal();self.export_button=button('从游戏目录导出资源包',self.export,ghost=True);exports.addWidget(self.export_button);exports.addStretch();outer.addLayout(exports)
        self.game_mode.toggled.connect(self.mode_changed);self.mode_changed()
        note=label('运行环境已随软件提供。预设、收藏和搭配保存在软件目录，无需额外配置。','Muted');note.setWordWrap(True);outer.addWidget(note)
        progress=horizontal();self.spinner=LoadingSpinner(18);progress.addWidget(self.spinner)
        self.status=label(error,'Warning');self.status.setWordWrap(True);progress.addWidget(self.status,1);outer.addLayout(progress);outer.addStretch()
        actions=horizontal();self.cancel=button('退出'if first else'取消',self.reject,ghost=True);actions.addWidget(self.cancel);actions.addStretch()
        self.save_button=button('保存并开始'if first else'保存并应用',self.save,primary=True);actions.addWidget(self.save_button);outer.addLayout(actions)
        self.timer=C.QTimer(self);self.timer.setInterval(30);self.timer.timeout.connect(self.poll)
        if parent and parent.testAttribute(C.Qt.WA_DontShowOnScreen):self.setAttribute(C.Qt.WA_DontShowOnScreen)
        self.finished.connect(lambda _:self.pool.shutdown(wait=False,cancel_futures=True))

    def choose(self):
        if self.pack_mode.isChecked():path,_=W.QFileDialog.getOpenFileName(self,'导入幻衣间资源包','','幻衣间资源包 (*.ff14resources *.zip)')
        else:path=W.QFileDialog.getExistingDirectory(self,'选择国服游戏目录',self.path.text()or str(Path.home()))
        if path:self.path.setText(path)

    def mode_changed(self):
        packed=self.pack_mode.isChecked();self.path_label.setText('幻衣间资源包'if packed else'国服游戏目录')
        self.path.setPlaceholderText('选择 .ff14resources 文件，导入后保存在软件 resources 目录'if packed else'包含 game 文件夹的游戏安装目录')
        self.browse.setText('选择资源包'if packed else'选择目录');self.export_button.setVisible(not packed)

    def progress(self,record):self.progress_record=record

    def busy(self,value):
        for widget in(self.path,self.browse,self.save_button,self.game_mode,self.pack_mode,self.export_button):widget.setEnabled(not value)
        self.spinner.set_running(value)

    def export(self):
        if self.future:return
        path,_=W.QFileDialog.getSaveFileName(self,'导出幻衣间资源包',str(self.store.root/'幻衣间资源包.ff14resources'),'幻衣间资源包 (*.ff14resources)')
        if not path:return
        from resource_pack import export_resource_pack
        self.operation='export';self.cancel_event.clear();self.progress_record=None;self.busy(True)
        self.status.setText('正在准备导出，全部装备资源可能需要数十 GB…')
        self.future=self.pool.submit(export_resource_pack,self.path.text(),path,root=self.store.root,callback=self.progress,cancel=self.cancel_event);self.timer.start()

    def save(self):
        if self.future:return
        self.busy(True);self.cancel_event.clear();self.progress_record=None;self.operation='save'
        value=self.path.text()
        if self.pack_mode.isChecked()and Path(value).is_file():
            from resource_pack import import_resource_pack
            self.status.setText('正在检查并导入资源包…')
            self.future=self.pool.submit(import_resource_pack,value,self.store.root,callback=self.progress,cancel=self.cancel_event)
        else:
            if self.pack_mode.isChecked()and not(Path(value)/'resource-pack.json').is_file():
                self.status.setText('请选择有效的幻衣间资源包文件');self.busy(False);return
            self.status.setText('正在检查资源目录与版本…');self.future=self.pool.submit(validate_game,value,self.store.root)
        self.timer.start()

    def poll(self):
        if self.progress_record:
            record=self.progress_record;total=max(1,record['total'])
            self.status.setText(f"{record['phase']} · {record['done']/total:.0%} · {record['done']/1024**3:.2f} / {record['total']/1024**3:.2f} GB")
        if not self.future or not self.future.done():return
        self.timer.stop();self.spinner.set_running(False);future=self.future;self.future=None
        try:
            result=future.result()
            if self.operation=='export':
                self.status.setText('资源包已导出：'+str(result));self.progress_record=None;self.busy(False);return
            self.saved_game=self.store.save(result)
        except Exception as error:
            self.status.setText(str(error));self.progress_record=None;self.busy(False);return
        self.accept()

    def reject(self):
        self.cancel_event.set();self.timer.stop();self.spinner.set_running(False);super().reject()
