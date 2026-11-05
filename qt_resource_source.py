"""Shared resource controls and asynchronous validation, not a settings window."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from qt_runtime import enable
enable()
from PySide6 import QtCore as C,QtWidgets as W
from qt_theme import label,button,vertical,horizontal,LoadingSpinner
from settings import validate_game


class ResourceSourcePanel(W.QWidget):
    def __init__(self,game=None,*,export=False,parent=None):
        super().__init__(parent);outer=vertical(self,0,14)
        hint=label('使用本机国服游戏目录，或导入独立资源包。配置会自动保存。','Muted');hint.setWordWrap(True);outer.addWidget(hint)
        modes=horizontal();self.game_mode=W.QRadioButton('游戏目录');self.pack_mode=W.QRadioButton('导入资源包')
        modes.addWidget(self.game_mode);modes.addWidget(self.pack_mode);modes.addStretch();outer.addLayout(modes)
        packed=bool(game and(Path(game)/'resource-pack.json').is_file());self._mode=packed
        self._paths={False:''if packed else str(game or''),True:str(game or'')if packed else''}
        self.game_mode.setChecked(not packed);self.pack_mode.setChecked(packed)
        self.path_label=label('','Section');outer.addWidget(self.path_label);row=horizontal()
        self.path=W.QLineEdit(str(game or''));self.path.setClearButtonEnabled(True);row.addWidget(self.path,1)
        self.browse=button('',self.choose);row.addWidget(self.browse);outer.addLayout(row)
        self.note=label('','Muted');self.note.setWordWrap(True);outer.addWidget(self.note)
        self.export_button=None
        if export:
            self.export_button=button('导出精简资源包…',ghost=True);line=horizontal();line.addWidget(self.export_button);line.addStretch();outer.addLayout(line)
        self.game_mode.toggled.connect(self.mode_changed);self.mode_changed()

    def choose(self):
        if self.pack_mode.isChecked():path,_=W.QFileDialog.getOpenFileName(self,'导入幻化柜资源包','','幻化柜资源包 (*.ff14resources *.zip)')
        else:path=W.QFileDialog.getExistingDirectory(self,'选择国服游戏目录',self.path.text()or str(Path.home()))
        if path:self.path.setText(path)

    def mode_changed(self):
        packed=self.pack_mode.isChecked()
        if packed!=self._mode:
            self._paths[self._mode]=self.path.text();self.path.setText(self._paths[packed]);self._mode=packed
        self.path_label.setText('幻化柜资源包'if packed else'国服游戏目录')
        self.path.setPlaceholderText('选择 .ff14resources 文件'if packed else'包含 game 文件夹的安装目录')
        self.browse.setText('选择资源包'if packed else'选择目录')
        self.note.setText('导入后保存在软件 resources 目录，无需安装游戏。'if packed else'直接读取本机游戏文件，无需复制资源。')
        if self.export_button:self.export_button.setVisible(not packed)


class ResourceDialog(W.QDialog):
    def __init__(self,store,parent=None):
        super().__init__(parent);self.store=store;self.saved_game=None;self.saved_studio=store.studio();self.future=None
        self.cancel_event=threading.Event();self.progress_record=None;self.operation=None
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='WardrobeConfig')
        self.timer=C.QTimer(self);self.timer.setInterval(30);self.timer.timeout.connect(self.poll)
        self.finished.connect(lambda _:self.pool.shutdown(wait=False,cancel_futures=True))
        if parent and parent.testAttribute(C.Qt.WA_DontShowOnScreen):self.setAttribute(C.Qt.WA_DontShowOnScreen,True)

    def bind_source(self,panel):
        self.source_panel=panel
        for name in('path','path_label','game_mode','pack_mode','browse','export_button'):setattr(self,name,getattr(panel,name))
        if self.export_button:self.export_button.clicked.connect(lambda:self.export())

    def add_footer(self,outer,*,first=False,error=''):
        progress=horizontal();self.spinner=LoadingSpinner(18);progress.addWidget(self.spinner)
        self.status=label(error,'Warning');self.status.setWordWrap(True);progress.addWidget(self.status,1);outer.addLayout(progress)
        actions=horizontal();self.cancel=button('退出'if first else'取消',self.reject,ghost=True);actions.addWidget(self.cancel);actions.addStretch()
        self.save_button=button('保存并开始'if first else'保存并应用',self.save,primary=True);actions.addWidget(self.save_button);outer.addLayout(actions)

    def progress(self,record):self.progress_record=record
    def extra_busy(self,value):pass
    def studio_value(self):return self.store.studio()

    def busy(self,value):
        self.source_panel.setEnabled(not value);self.save_button.setEnabled(not value);self.spinner.set_running(value);self.extra_busy(value)

    def export(self):
        if self.future:return
        path,_=W.QFileDialog.getSaveFileName(self,'导出幻化柜资源包',str(self.store.root/'幻化柜资源包-全种族精简.ff14resources'),'幻化柜资源包 (*.ff14resources)')
        if not path:return
        from resource_pack import export_resource_pack
        self.operation='export';self.cancel_event.clear();self.progress_record=None;self.busy(True)
        self.status.setText('正在扫描全部种族与装备依赖…')
        self.future=self.pool.submit(export_resource_pack,self.path.text(),path,root=self.store.root,callback=self.progress,cancel=self.cancel_event,compact=True);self.timer.start()

    def save(self):
        if self.future:return
        self.busy(True);self.cancel_event.clear();self.progress_record=None;self.operation='save';value=self.path.text()
        if self.pack_mode.isChecked()and Path(value).is_file():
            from resource_pack import import_resource_pack
            self.status.setText('正在检查并导入资源包…')
            self.future=self.pool.submit(import_resource_pack,value,self.store.root,callback=self.progress,cancel=self.cancel_event)
        else:
            if self.pack_mode.isChecked()and not(Path(value)/'resource-pack.json').is_file():
                self.status.setText('请选择有效的幻化柜资源包文件');self.busy(False);return
            self.status.setText('正在检查目录与版本…');self.future=self.pool.submit(validate_game,value,self.store.root)
        self.timer.start()

    def poll(self):
        if self.progress_record:
            record=self.progress_record;total=max(1,record['total'])
            self.status.setText(f"{record['phase']} · {record['done']/total:.0%}")
        if not self.future or not self.future.done():return
        self.timer.stop();self.spinner.set_running(False);future=self.future;self.future=None
        try:
            result=future.result()
            if self.operation=='export':
                self.status.setText('资源包已导出：'+str(result));self.progress_record=None;self.busy(False);return
            self.saved_studio=self.studio_value();self.saved_game=self.store.save(result,studio=self.saved_studio)
        except Exception as error:
            self.status.setText(str(error));self.progress_record=None;self.busy(False);return
        self.accept()

    def reject(self):
        self.cancel_event.set();self.timer.stop();self.spinner.set_running(False);super().reject()
