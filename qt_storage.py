"""Background storage accounting with explicit safe-cache cleanup."""
from concurrent.futures import ThreadPoolExecutor
import threading
from qt_runtime import enable
enable()
from PySide6 import QtCore as C,QtWidgets as W
from qt_theme import label,button,vertical,horizontal,LoadingSpinner
from storage_management import scan,clear_cache,human_size,CATEGORIES


class StoragePanel(W.QWidget):
    cleaningChanged=C.Signal(bool)

    def __init__(self,root,context=None,parent=None):
        super().__init__(parent);self.root=root;self.context=context or(lambda:None);self.report=None;self.future=None;self.closed=False
        self.cancel=threading.Event();self.progress_record=None;self.operation=None;self.last_result=None
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='WardrobeStorage')
        outer=vertical(self,12,10);scroll=W.QScrollArea();scroll.setWidgetResizable(True);scroll.setHorizontalScrollBarPolicy(C.Qt.ScrollBarAlwaysOff)
        content=W.QWidget();body=vertical(content,0,8);scroll.setWidget(content);outer.addWidget(scroll,1)
        heading=horizontal();heading.addWidget(label('空间使用情况','Section'));heading.addStretch();self.total=label('尚未统计','Badge');heading.addWidget(self.total);body.addLayout(heading)
        path=label(str(root),'Muted');path.setWordWrap(True);path.setTextInteractionFlags(C.Qt.TextSelectableByMouse);body.addWidget(path)
        self.table=W.QTableWidget(len(CATEGORIES),3);self.table.setHorizontalHeaderLabels(['内容','占用空间','可清理'])
        self.table.verticalHeader().hide();self.table.setEditTriggers(W.QAbstractItemView.NoEditTriggers);self.table.setSelectionMode(W.QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(C.Qt.NoFocus);self.table.setShowGrid(False);self.table.setFixedHeight(205)
        self.table.setStyleSheet('QTableWidget {background:#191e27;border:1px solid #2b3443;border-radius:7px;} QHeaderView::section {background:#222936;color:#aab7c8;border:0;padding:7px;} QTableWidget::item {padding:4px;border:0;}')
        self.table.horizontalHeader().setSectionResizeMode(0,W.QHeaderView.Stretch)
        for column in(1,2):self.table.horizontalHeader().setSectionResizeMode(column,W.QHeaderView.ResizeToContents)
        for row,(_,name)in enumerate(CATEGORIES):
            self.table.setRowHeight(row,28)
            for col,text in enumerate((name,'—','—')):self.table.setItem(row,col,W.QTableWidgetItem(text))
        body.addWidget(self.table)
        hint=label('仅清理旧版本角色缓存和可重建的字节码；当前角色缓存保留。资源、预设和模版不会作为缓存清除。','Muted');hint.setWordWrap(True);body.addWidget(hint)
        self.scope=label('统计软件目录内的文件，不计游戏安装目录。','Muted');self.scope.setWordWrap(True);body.addWidget(self.scope);body.addStretch()
        progress=horizontal();self.spinner=LoadingSpinner(16);progress.addWidget(self.spinner);self.status=label('切换到本页后自动统计。','Muted');self.status.setWordWrap(True);progress.addWidget(self.status,1);outer.addLayout(progress)
        actions=horizontal();self.refresh_button=button('重新统计',self.refresh,ghost=True);actions.addWidget(self.refresh_button);actions.addStretch()
        self.clear_button=button('清除可清理缓存',self.confirm_clear);self.clear_button.setMinimumWidth(230);self.clear_button.setEnabled(False);actions.addWidget(self.clear_button);outer.addLayout(actions)
        self.timer=C.QTimer(self);self.timer.setInterval(40);self.timer.timeout.connect(self.poll)

    def showEvent(self,event):
        super().showEvent(event)
        if self.report is None and self.future is None and not self.closed:C.QTimer.singleShot(0,self.refresh)

    def identity(self):
        try:return self.context()
        except Exception:return None

    def progress(self,record):self.progress_record=record

    def start(self,operation,fn,*args):
        if self.future is not None or self.closed:return
        self.operation=operation;self.cancel.clear();self.progress_record=None;self.spinner.set_running(True)
        self.refresh_button.setEnabled(False);self.clear_button.setEnabled(False)
        if operation=='clear':self.cleaningChanged.emit(True)
        self.future=self.pool.submit(fn,*args,cancel=self.cancel,callback=self.progress);self.timer.start()

    def refresh(self):
        if self.future is not None or self.closed:return
        self.status.setText('正在统计软件占用…');self.start('scan',scan,self.root,self.identity())

    def confirm_clear(self):
        if self.future is not None or not self.report or not self.report['candidates']:return
        message='可清理 '+human_size(self.report['reclaimable'])+'。\n将保留当前角色缓存、预设、模版、收藏与资源文件。'
        if W.QMessageBox.question(self,'清理缓存',message,W.QMessageBox.Yes|W.QMessageBox.No,W.QMessageBox.No)==W.QMessageBox.Yes:
            self.status.setText('正在清理可重建缓存…');self.start('clear',clear_cache,self.root,self.report['candidates'],self.identity())

    def poll(self):
        if self.progress_record:
            p=self.progress_record
            self.status.setText(f"正在统计…已检查 {p['files']:,} 个文件"if self.operation=='scan'else f"正在清理…{p['done']:,} / {p['total']:,}")
        if self.future is None or not self.future.done():return
        self.timer.stop();future=self.future;self.future=None;self.spinner.set_running(False);self.refresh_button.setEnabled(True)
        operation=self.operation
        if operation=='clear':self.cleaningChanged.emit(False)
        try:result=future.result()
        except Exception as error:self.status.setText('操作未完成：'+str(error));self.clear_button.setEnabled(False);return
        if operation=='clear':self.last_result=result;self.refresh();return
        self.report=result;self.total.setText(human_size(result['total']));amount={key:0 for key,_ in CATEGORIES}
        from storage_management import _category
        for entry in result['candidates']:amount[_category(entry['path'])]+=entry['stamp'][0]
        for row,(key,_)in enumerate(CATEGORIES):
            self.table.item(row,1).setText(human_size(result['totals'][key]));self.table.item(row,2).setText(human_size(amount[key])if amount[key]else'—')
        self.clear_button.setText('清除可清理缓存 · '+human_size(result['reclaimable']));self.clear_button.setEnabled(bool(result['candidates']))
        text=f"已统计 {result['files']:,} 个文件"
        if self.last_result:
            text='已清理 '+human_size(self.last_result['bytes'])
            if self.last_result['skipped']:text+=f"，保留 {self.last_result['skipped']} 个已变化或无法删除的文件。"
        if result['unreadable']:text+=f" · {result['unreadable']} 项无法读取"
        self.status.setText(text)

    def shutdown(self):
        self.closed=True;self.cancel.set();self.timer.stop();self.spinner.set_running(False);self.pool.shutdown(wait=False,cancel_futures=True)
