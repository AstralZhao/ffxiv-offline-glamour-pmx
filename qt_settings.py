"""One-directory first-run wizard and the same in-app Settings page."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from qt_runtime import enable
enable()
from PySide6 import QtCore as C,QtWidgets as W
from qt_theme import label,button,vertical,horizontal,LoadingSpinner
from settings import validate_game


class SettingsDialog(W.QDialog):
    def __init__(self,store,game=None,*,first=False,error='',parent=None):
        super().__init__(parent);self.store=store;self.saved_game=None;self.future=None
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='WardrobeConfig')
        self.setWindowTitle('首次配置'if first else'设置');self.resize(580,310)
        outer=vertical(self,24,16);outer.addWidget(label('开始使用幻衣间'if first else'软件设置','Title'))
        hint=label('选择本机国服游戏目录，用于只读加载模型、纹理与染色数据。配置后会自动记住。','Muted');hint.setWordWrap(True);outer.addWidget(hint)
        outer.addWidget(label('国服游戏目录','Section'));row=horizontal();self.path=W.QLineEdit(str(game)if game else'')
        self.path.setPlaceholderText('包含 game 文件夹的游戏安装目录');row.addWidget(self.path,1)
        self.browse=button('选择目录',self.choose);row.addWidget(self.browse);outer.addLayout(row)
        note=label('运行环境已随软件提供。预设、收藏和搭配保存在软件目录，无需额外配置。','Muted');note.setWordWrap(True);outer.addWidget(note)
        progress=horizontal();self.spinner=LoadingSpinner(18);progress.addWidget(self.spinner)
        self.status=label(error,'Warning');self.status.setWordWrap(True);progress.addWidget(self.status,1);outer.addLayout(progress);outer.addStretch()
        actions=horizontal();self.cancel=button('退出'if first else'取消',self.reject,ghost=True);actions.addWidget(self.cancel);actions.addStretch()
        self.save_button=button('保存并开始'if first else'保存并应用',self.save,primary=True);actions.addWidget(self.save_button);outer.addLayout(actions)
        self.timer=C.QTimer(self);self.timer.setInterval(30);self.timer.timeout.connect(self.poll)
        if parent and parent.testAttribute(C.Qt.WA_DontShowOnScreen):self.setAttribute(C.Qt.WA_DontShowOnScreen)
        self.finished.connect(lambda _:self.pool.shutdown(wait=False,cancel_futures=True))

    def choose(self):
        path=W.QFileDialog.getExistingDirectory(self,'选择国服游戏目录',self.path.text()or str(Path.home()))
        if path:self.path.setText(path)

    def save(self):
        if self.future:return
        self.path.setEnabled(False);self.browse.setEnabled(False);self.save_button.setEnabled(False)
        self.spinner.set_running(True);self.status.setText('正在检查游戏目录与版本…')
        self.future=self.pool.submit(validate_game,self.path.text(),self.store.root);self.timer.start()

    def poll(self):
        if not self.future or not self.future.done():return
        self.timer.stop();self.spinner.set_running(False);future=self.future;self.future=None
        try:self.saved_game=self.store.save(future.result())
        except Exception as error:
            self.status.setText(str(error));self.path.setEnabled(True);self.browse.setEnabled(True);self.save_button.setEnabled(True);return
        self.accept()

    def reject(self):
        self.timer.stop();self.spinner.set_running(False);super().reject()
