"""Nonblocking update checks, explicit consent, and graceful native shutdown."""
from concurrent.futures import ThreadPoolExecutor
import json,threading
from qt_runtime import enable
enable()
from PySide6 import QtCore as C,QtWidgets as W
from qt_theme import label,button,vertical,horizontal,LoadingSpinner
from app_paths import paths,_atomic_json
from app_version import current_version,can_install,RELEASES,is_beta
import updates


class UpdateController(C.QObject):
    changed=C.Signal()
    def __init__(self,session):
        super().__init__(session);self.session=session;self.version=current_version();self.info=None;self.future=None
        self.operation=None;self.status='';self.fraction=None;self.closed=False;self.automatic=False
        self.cancel=threading.Event();self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='WardrobeUpdates')
        self.preference=paths().user/'updates.json'
        try:preference=json.loads(self.preference.read_bytes())
        except(OSError,ValueError):preference={}
        if not isinstance(preference,dict):preference={}
        self.auto=preference.get('startup_check',True)is not False;self.beta=preference.get('channel')=='beta'
        self.timer=C.QTimer(self);self.timer.setInterval(80);self.timer.timeout.connect(self.poll)
        session.application.aboutToQuit.connect(self.shutdown)
        if self.auto and can_install():C.QTimer.singleShot(3000,lambda:self.check(True))

    def save_preferences(self,auto,beta):
        self.preference.parent.mkdir(parents=True,exist_ok=True)
        _atomic_json(self.preference,{'startup_check':bool(auto),'channel':'beta'if beta else'stable'})
        self.auto=bool(auto);self.beta=bool(beta)

    def set_auto(self,value):
        try:self.save_preferences(value,self.beta)
        except OSError as error:self.status='更新设置未保存：'+str(error)
        self.changed.emit()

    def set_beta(self,value):
        if self.future:return
        try:
            self.save_preferences(self.auto,value);self.info=None
            self.status='已开启测试版通道：同时检查正式版和测试版。'if value else'已切换正式版通道。当前测试版可点击“返回最新正式版”。'if is_beta(self.version)else'已切换正式版通道。'
        except OSError as error:self.status='更新设置未保存：'+str(error)
        self.changed.emit()

    def return_stable(self):self.check(return_to_stable=True)

    def check(self,automatic=False,*,return_to_stable=False):
        if self.future or self.closed:return
        self.automatic=automatic;self.operation='return_stable'if return_to_stable else'check';self.status='正在检查 Gitee 发行版本…';self.fraction=None
        self.future=self.pool.submit(updates.check,self.version,channel='stable'if return_to_stable else'beta'if self.beta else'stable',return_to_stable=return_to_stable);self.timer.start();self.changed.emit()

    def download(self,*,confirmed=False):
        if self.future or self.closed or not self.info:return
        if not can_install():self.status='开发版请通过 Git 更新源码，不覆盖开发目录。';self.changed.emit();return
        returning=self.info.get('return_to_stable',False)
        if returning and not confirmed:
            answer=W.QMessageBox.question(self.session.window,'返回正式版',self.version+' → '+self.info['latest']+'\n\n安装此正式版并保留用户数据、缓存和资源？',W.QMessageBox.Yes|W.QMessageBox.No,W.QMessageBox.No)
            if answer!=W.QMessageBox.Yes:return
        if returning:
            try:self.save_preferences(self.auto,False)
            except OSError as error:self.status='更新设置未保存：'+str(error);self.changed.emit();return
        self.cancel.clear();self.operation='download';self.status='正在下载并校验完整软件包…';self.fraction=0
        self.future=self.pool.submit(updates.prepare,self.info,progress=self.progress,cancel=self.cancel,allow_stable_return=returning);self.timer.start();self.changed.emit()

    def progress(self,done,total):self.fraction=done/max(1,total) if total else None

    def poll(self):
        self.changed.emit()
        if not self.future or not self.future.done():return
        future=self.future;self.future=None;self.timer.stop();self.fraction=None
        if self.closed:return
        try:
            result=future.result()
            if self.operation=='download':
                self.status='发行包已校验，正在保存会话并关闭预览…';self.changed.emit()
                self.session.update_folder=result;self.session.window.setEnabled(False)
                if self.session.settings_dialog:self.session.settings_dialog.reject()
                self.session.controller.close();return
            self.info=result
            if self.operation=='return_stable':
                self.status='已找到正式版 '+result['latest']if result['status']=='available'else'没有可用的正式版发行包。'
                self.changed.emit()
                if result['status']=='available':self.download()
                return
            self.status=('当前通道暂无更新：'+self.version if result['status']=='current' else
                '检测到新版本 '+result['latest']+('，可使用完整软件包更新。' if result['status']=='available' else '，发行页没有唯一可识别的完整软件包。'))
            if self.automatic and result['status']=='available' and not self.session.controller.closing:
                answer=W.QMessageBox.question(self.session.window,'发现新版本',
                    self.version+' → '+result['latest']+'\n\n'+result.get('notes','')[:1500]+
                    '\n\n现在下载完整软件包并更新？更新前会保存会话并退出软件，角色、模版、收藏、标签及资源保留。',
                    W.QMessageBox.Yes|W.QMessageBox.No,W.QMessageBox.No)
                if answer==W.QMessageBox.Yes:self.download(confirmed=True)
        except Exception as error:
            self.status='更新未完成：'+str(error)
            # Background failures are shown in Settings, never block startup.
        self.changed.emit()

    def shutdown(self):
        self.closed=True;self.cancel.set();self.timer.stop();self.pool.shutdown(wait=False,cancel_futures=True)


class UpdatePanel(W.QWidget):
    def __init__(self,controller,parent=None):
        super().__init__(parent);self.controller=controller
        outer=vertical(self,0,16);outer.addWidget(label('软件更新','Section'))
        outer.addWidget(label('当前版本：'+controller.version+(' · 开发模式' if not can_install() else ''),'Badge'))
        self.auto=W.QCheckBox('启动时自动检查更新');self.auto.setChecked(controller.auto);self.auto.toggled.connect(controller.set_auto);outer.addWidget(self.auto)
        self.beta=W.QCheckBox('接收测试版更新');self.beta.setChecked(controller.beta);self.beta.toggled.connect(controller.set_beta);outer.addWidget(self.beta)
        self.return_button=button('返回最新正式版',controller.return_stable,ghost=True);self.return_button.setVisible(is_beta(controller.version));outer.addWidget(self.return_button)
        hint=label('开启测试通道后，正式版和 -beta.数字 测试版均参与检查；关闭后仅检查正式版。返回正式版需确认。下载新版完整发行包，只替换程序文件，保留用户数据、缓存和资源。更新失败时保留原版本。','Muted');hint.setWordWrap(True);outer.addWidget(hint)
        row=horizontal();self.check_button=button('检查更新',lambda:controller.check());self.install_button=button('下载新版并更新',controller.download,primary=True)
        row.addWidget(self.check_button);row.addWidget(self.install_button);row.addStretch();outer.addLayout(row)
        self.spinner=LoadingSpinner(36);outer.addWidget(self.spinner)
        self.status=label('','Muted');self.status.setWordWrap(True);outer.addWidget(self.status)
        self.notes=W.QPlainTextEdit();self.notes.setReadOnly(True);self.notes.setMaximumHeight(160);self.notes.setPlaceholderText('检查更新后显示新版说明');outer.addWidget(self.notes)
        link=label('<a href="'+RELEASES+'">打开发行页面</a>');link.setOpenExternalLinks(True);outer.addWidget(link)
        controller.changed.connect(self.refresh);self.refresh()

    def refresh(self):
        c=self.controller;busy=c.future is not None
        self.beta.blockSignals(True);self.beta.setChecked(c.beta);self.beta.blockSignals(False);self.beta.setEnabled(not busy)
        self.return_button.setEnabled(not busy and can_install());self.auto.blockSignals(True);self.auto.setChecked(c.auto);self.auto.blockSignals(False)
        self.install_button.setText('下载并返回正式版'if c.info and c.info.get('return_to_stable')else'下载新版并更新')
        self.check_button.setEnabled(not busy);self.install_button.setEnabled(not busy and can_install() and bool(c.info and c.info['status']=='available'))
        self.install_button.setToolTip('开发版通过 Git 更新源码' if not can_install() else '下载校验完成后保存会话并重启')
        self.spinner.set_progress(c.fraction);self.spinner.set_running(busy);self.status.setText(c.status or '可手动检查更新。')
        self.notes.setPlainText(c.info.get('notes','') if c.info else '')
