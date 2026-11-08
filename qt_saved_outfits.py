"""Paged saved-outfit browser. Applying clothes retains the active character."""
from copy import deepcopy
import math,sqlite3,unicodedata
from pathlib import Path
from qt_runtime import enable
enable()
from PySide6 import QtCore as C,QtGui as G,QtWidgets as W
from qt_theme import label,button,vertical,horizontal,ComboBox
from app import ROOT,SLOTS


def normalized(text):return unicodedata.normalize('NFKC',str(text)).casefold()


class SavedOutfitsDialog(W.QDialog):
    page_size=12

    def __init__(self,controller):
        super().__init__(controller.window);self.c=controller;self.setWindowTitle('投影模版')
        self.resize(960,730);self.setMinimumSize(780,580);self.page_index=0;self.selected_id=None
        self.list_icons=[];self.detail_icons=[];self.records=[];self.matches=[];self.metadata={}
        outer=vertical(self,22,14);heading=horizontal();heading.addWidget(label('投影模版','Title'));heading.addStretch()
        self.total=label('','Badge');heading.addWidget(self.total)
        self.batch_export_button=button('批量导出',self.export_batch);heading.addWidget(self.batch_export_button)
        self.batch_import_button=button('批量导入',self.import_batch);heading.addWidget(self.batch_import_button);outer.addLayout(heading)
        outer.addWidget(label('应用装备、染色和面部配饰，保留当前角色与观察视角。','Muted'))
        tools=horizontal();self.search=W.QLineEdit();self.search.setPlaceholderText('搜索模版名称、装备名称或编号');self.search.setClearButtonEnabled(True)
        tools.addWidget(self.search,1);self.sort=ComboBox();self.sort.addItem('最近保存','recent');self.sort.addItem('最早保存','oldest');self.sort.addItem('名称排序','name');tools.addWidget(self.sort)
        tools.addWidget(button('刷新',self.reload,ghost=True));self.import_code_button=button('导入模版码',self.import_code);tools.addWidget(self.import_code_button);outer.addLayout(tools)
        splitter=W.QSplitter(C.Qt.Horizontal);outer.addWidget(splitter,1)
        left=W.QWidget();left_layout=vertical(left,0,10);self.list=W.QListWidget();self.list.setSpacing(4)
        self.list.setVerticalScrollMode(W.QAbstractItemView.ScrollPerPixel)
        self.list.setStyleSheet('QListWidget::item { border:1px solid #303a49; border-radius:7px; } QListWidget::item:selected { background:#294344; border-color:#79b9af; }')
        left_layout.addWidget(self.list,1);self.empty=label('','Muted');self.empty.setWordWrap(True);left_layout.addWidget(self.empty)
        pager=horizontal();self.previous=button('‹',lambda:self.change_page(self.page_index-1));self.next=button('›',lambda:self.change_page(self.page_index+1))
        pager.addWidget(self.previous);self.page=W.QSpinBox();self.page.setPrefix('第 ');self.page.setSuffix(' 页');self.page.setMinimumWidth(100)
        pager.addWidget(self.page);self.pages=label('','Muted');pager.addWidget(self.pages);pager.addStretch();pager.addWidget(self.next);left_layout.addLayout(pager)
        splitter.addWidget(left)
        self.detail_panel=W.QFrame();self.detail_panel.setObjectName('Panel');detail=vertical(self.detail_panel,18,12)
        self.title=label('选择一套模版','Title');self.title.setWordWrap(True);detail.addWidget(self.title)
        self.info=label('在左侧查看保存的模版。','Muted');self.info.setWordWrap(True);detail.addWidget(self.info)
        scroll=W.QScrollArea();scroll.setWidgetResizable(True);self.gear_content=W.QWidget();self.gear_layout=vertical(self.gear_content,0,8)
        self.gear_layout.setSizeConstraint(W.QLayout.SetMinAndMaxSize);scroll.setWidget(self.gear_content);detail.addWidget(scroll,1)
        self.notice=label('','Warning');self.notice.setWordWrap(True);detail.addWidget(self.notice)
        actions=horizontal();self.rename_button=button('重命名',self.rename);self.delete_button=button('删除',self.delete,ghost=True)
        actions.addWidget(self.rename_button);actions.addWidget(self.delete_button);actions.addStretch();self.export_code_button=button('生成导出码',self.export_code);actions.addWidget(self.export_code_button);detail.addLayout(actions)
        self.apply_button=button('应用到当前角色',self.apply,primary=True);detail.addWidget(self.apply_button)
        splitter.addWidget(self.detail_panel);splitter.setSizes([440,440]);splitter.setChildrenCollapsible(False)
        bottom=horizontal();self.count_note=label('','Muted');bottom.addWidget(self.count_note,1);bottom.addWidget(button('关闭',self.reject,ghost=True));outer.addLayout(bottom)
        self.search_timer=C.QTimer(self);self.search_timer.setSingleShot(True);self.search_timer.setInterval(160);self.search_timer.timeout.connect(self.filter)
        self.search.textChanged.connect(lambda:self.search_timer.start());self.sort.activated.connect(lambda:self.filter())
        self.page.valueChanged.connect(lambda value:self.change_page(value-1));self.list.currentItemChanged.connect(self.select)
        self.list.itemDoubleClicked.connect(lambda _:self.apply());self.reload()

    def reload(self):
        self.records=self.c.engine.saved();ids={choice['item_id']for record in self.records for choice in record.get('state',{}).get('equipment',{}).values()if choice.get('item_id')}
        self.metadata={}
        with sqlite3.connect((ROOT/'artifacts/cn-data/catalog.sqlite').as_uri()+'?mode=ro',uri=True)as db:
            values=sorted(ids)
            for start in range(0,len(values),500):
                chunk=values[start:start+500]
                for identifier,name,icon in db.execute('SELECT item_id,name,icon_id FROM equipment WHERE item_id IN ('+','.join('?'for _ in chunk)+')',chunk):
                    self.metadata[identifier]={'name':name,'icon_id':icon}
        self.search_text={r['id']:normalized(r['name']+' '+r.get('time','')+' '+' '.join(str(c['item_id'])+' '+self.metadata.get(c['item_id'],{}).get('name','')for c in r['state']['equipment'].values()if c.get('item_id')))for r in self.records}
        from facewear import catalogue
        facewear=catalogue()[2]
        for r in self.records:self.search_text[r['id']]+=' '+normalized(' '.join(facewear.get(i,{}).get('name','')for i in r['state'].get('facewear',[0,0])))
        self.total.setText(f'{len(self.records):,} 套');self.batch_export_button.setEnabled(bool(self.records));self.filter(reset=False)

    def filter(self,*,reset=True):
        terms=normalized(self.search.text()).split();self.matches=[r for r in self.records if all(term in self.search_text[r['id']]for term in terms)]
        order=self.sort.currentData()
        if order=='oldest':self.matches.reverse()
        elif order=='name':self.matches.sort(key=lambda r:normalized(r['name']))
        if reset:self.page_index=0
        self.change_page(self.page_index)

    def change_page(self,index):
        pages=max(1,math.ceil(len(self.matches)/self.page_size));self.page_index=max(0,min(index,pages-1))
        self.page.blockSignals(True);self.page.setRange(1,pages);self.page.setValue(self.page_index+1);self.page.blockSignals(False)
        self.pages.setText(f'/ {pages:,}');self.previous.setEnabled(self.page_index>0);self.next.setEnabled(self.page_index+1<pages)
        self.list.blockSignals(True);self.list.clear();self.list_icons=[]
        start=self.page_index*self.page_size;shown=self.matches[start:start+self.page_size]
        for record in shown:
            choices=record['state']['equipment'];nonempty=[s for s in SLOTS if choices.get(s,{}).get('item_id')]
            item=W.QListWidgetItem();item.setData(C.Qt.UserRole,record['id']);item.setSizeHint(C.QSize(260,72));self.list.addItem(item)
            widget=W.QWidget();row=horizontal(widget,10,10);image=label();image.setFixedSize(44,44)
            slot=next((s for s in('Body','Head','Legs','Feet',*SLOTS)if choices.get(s,{}).get('item_id')),None)
            identifier=self.metadata.get(choices[slot]['item_id'],{}).get('icon_id')if slot else None
            self.list_icons.append((image,identifier));row.addWidget(image)
            text=vertical(spacing=5);name=label(record['name']);name.setToolTip(record['name']);name.setStyleSheet('font-weight:600;');text.addWidget(name)
            faces=sum(bool(i)for i in record['state'].get('facewear',[0,0]))
            text.addWidget(label(f"{record.get('time','')} · {len(nonempty)} 件装备"+(f' · {faces} 件面饰'if faces else''),'Muted'));row.addLayout(text,1)
            self.list.setItemWidget(item,widget)
        self.list.blockSignals(False)
        selected=next((i for i,r in enumerate(shown)if r['id']==self.selected_id),0)
        if shown:self.list.setCurrentRow(selected)
        else:self.select(None)
        self.empty.setText('还没有保存模版。试穿后点击主界面的“保存模版”。'if not self.records else'没有符合搜索条件的模版。')
        self.empty.setVisible(not shown);self.list.setVisible(bool(shown));self.count_note.setText(f"找到 {len(self.matches):,} / 共 {len(self.records):,} 套 · 每页 {self.page_size} 套")
        self.refresh_icons()

    def selected_record(self):return next((r for r in self.records if r['id']==self.selected_id),None)

    def select(self,item,*_):
        self.selected_id=item.data(C.Qt.UserRole)if item else None;record=self.selected_record();self.detail_icons=[]
        while self.gear_layout.count():
            child=self.gear_layout.takeAt(0)
            if child.widget():child.widget().deleteLater()
        for control in(self.rename_button,self.delete_button,self.apply_button,self.export_code_button):control.setEnabled(record is not None)
        self.notice.clear()
        if record is None:
            self.title.setText('选择一套模版');self.info.setText('在左侧查看保存的模版。');return
        self.title.setText(record['name']);self.info.setText('保存于 '+record.get('time','')+'\n空部位会清空当前对应装备。')
        equipment=record['state']['equipment']
        for slot,slot_name in SLOTS.items():
            choice=equipment.get(slot,{});identifier=choice.get('item_id');meta=self.metadata.get(identifier,{})
            card=W.QFrame();card.setObjectName('OutfitCard');card.setMinimumHeight(58);card.setSizePolicy(W.QSizePolicy.Expanding,W.QSizePolicy.Fixed)
            row=horizontal(card,8,10);icon=label();icon.setFixedSize(36,36);row.addWidget(icon)
            self.detail_icons.append((icon,meta.get('icon_id')));text=vertical(spacing=3)
            title=label(slot_name+' · '+(meta.get('name',f'装备 #{identifier}')if identifier else'未设置'));title.setWordWrap(True);text.addWidget(title)
            if identifier:
                dyes=[]
                for index,key in enumerate(('first','second'),1):
                    stain=choice.get(key,0)
                    if stain:dyes.append(f'{index}区：'+self.c.dye_by_id.get(stain,{}).get('name',str(stain)))
                if dyes:text.addWidget(label(' · '.join(dyes),'Muted'))
            row.addLayout(text,1);self.gear_layout.addWidget(card)
        from qt_facewear import add_template_facewear
        add_template_facewear(self,record)
        self.gear_layout.addStretch();self.refresh_icons()

    def refresh_icons(self,changed=None):
        for widget,identifier in self.list_icons+self.detail_icons:
            if identifier and(changed is None or identifier in changed):widget.setPixmap(self.c.icon(identifier).pixmap(widget.size()))

    def apply(self):
        record=self.selected_record()
        if record is None:return
        if self.c.job is not None:self.notice.setText('当前预览正在更新，请稍后再试。');return
        state=deepcopy(self.c.good)
        if self.c.submit(self.c.engine.run,self.c.accept_result,'outfit',{'id':record['id'],'state':state}):self.accept()

    def rename(self):
        record=self.selected_record()
        if record is None:return
        name,ok=W.QInputDialog.getText(self,'重命名模版','模版名称',text=record['name'])
        if ok:
            try:self.c.engine.rename_saved_outfit(record['id'],name);self.reload()
            except Exception as error:self.notice.setText(str(error))

    def delete(self):
        record=self.selected_record()
        if record is None:return
        answer=W.QMessageBox.question(self,'删除模版','删除“'+record['name']+'”？此操作无法撤销。',W.QMessageBox.Yes|W.QMessageBox.No,W.QMessageBox.No)
        if answer==W.QMessageBox.Yes:
            try:self.c.engine.delete_saved_outfit(record['id']);self.reload()
            except Exception as error:self.notice.setText(str(error))

    def export_code(self):
        record=self.selected_record()
        if record is None:return
        try:code=self.c.engine.export_template_code(record['id'])
        except Exception as error:self.notice.setText(str(error));return
        return self.open_code_dialog(TemplateExportDialog(self,record['name'],code))

    def import_code(self):return self.open_code_dialog(TemplateImportDialog(self))

    def export_batch(self):
        if not self.records:return
        return self.open_code_dialog(TemplateBatchDialog(self,self.records))

    def import_batch(self):
        path,_=W.QFileDialog.getOpenFileName(self,'批量导入模版','','幻化柜模版文件 (*.hggtemplates);;所有文件 (*)')
        if not path:return
        try:
            from template_code import MAX_BATCH_BYTES
            with Path(path).open('rb')as file:raw=file.read(MAX_BATCH_BYTES+1)
            records=self.c.engine.inspect_template_batch(raw)
        except Exception as error:self.notice.setText(str(error));return
        return self.open_code_dialog(TemplateBatchDialog(self,records,raw=raw))

    def open_code_dialog(self,dialog):
        dialog.setWindowModality(C.Qt.WindowModal)
        if self.testAttribute(C.Qt.WA_DontShowOnScreen):dialog.setAttribute(C.Qt.WA_DontShowOnScreen,True)
        dialog.finished.connect(dialog.deleteLater);dialog.show();return dialog


class TemplateExportDialog(W.QDialog):
    def __init__(self,browser,name,code):
        super().__init__(browser);self.setWindowTitle('模版导出码');self.resize(570,330)
        outer=vertical(self,22,14);outer.addWidget(label('模版导出码','Title'));name_label=label(name,'Muted');name_label.setWordWrap(True);outer.addWidget(name_label)
        self.code=W.QPlainTextEdit();self.code.setReadOnly(True);self.code.setPlainText(code);outer.addWidget(self.code,1)
        note=label('包含装备、双染色和面部配饰；对方可在“投影模版 → 导入模版码”新增模版。','Muted');note.setWordWrap(True);outer.addWidget(note)
        actions=horizontal();self.status=label('','Muted');actions.addWidget(self.status,1)
        actions.addWidget(button('关闭',self.reject,ghost=True));self.copy_button=button('复制导出码',self.copy,primary=True);actions.addWidget(self.copy_button);outer.addLayout(actions)

    def copy(self):W.QApplication.clipboard().setText(self.code.toPlainText());self.status.setText('已复制')


class TemplateImportDialog(W.QDialog):
    def __init__(self,browser):
        super().__init__(browser);self.browser=browser;self.setWindowTitle('导入模版码');self.resize(570,430);self.record=None
        outer=vertical(self,22,14);outer.addWidget(label('导入模版码','Title'))
        hint=label('粘贴完整导出码，校验后新增一份模版。','Muted');hint.setWordWrap(True);outer.addWidget(hint)
        self.code=W.QPlainTextEdit();self.code.setPlaceholderText('HGG1.… / HGG2.…');outer.addWidget(self.code,1)
        self.name=W.QLineEdit();self.name.setMaxLength(32);self.name.setPlaceholderText('模版名称');outer.addWidget(self.name)
        self.info=label('','Muted');self.info.setWordWrap(True);outer.addWidget(self.info)
        self.error=label('','Warning');self.error.setWordWrap(True);outer.addWidget(self.error)
        actions=horizontal();actions.addWidget(button('取消',self.reject,ghost=True));actions.addStretch();self.add_button=button('新增模版',self.add,primary=True);self.add_button.setEnabled(False);actions.addWidget(self.add_button);outer.addLayout(actions)
        self.timer=C.QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(180);self.timer.timeout.connect(self.parse)
        self.code.textChanged.connect(self.changed);self.name.textChanged.connect(lambda:self.add_button.setEnabled(self.record is not None and bool(self.name.text().strip())))

    def changed(self):self.record=None;self.add_button.setEnabled(False);self.timer.start()

    def parse(self):
        self.timer.stop();self.record=None;self.add_button.setEnabled(False);self.info.clear();self.error.clear()
        if not self.code.toPlainText().strip():return
        try:self.record=self.browser.c.engine.inspect_template_code(self.code.toPlainText())
        except Exception as error:self.error.setText(str(error));return
        self.name.setText(self.record['name']);count=sum(c['item_id']is not None for c in self.record['state']['equipment'].values())
        faces=sum(bool(i)for i in self.record['state'].get('facewear',[0,0]))
        self.info.setText(f'{count} 件装备 · {faces} 件面饰 · 含两个染色区 · 新增后可在列表中选择应用到当前角色。');self.add_button.setEnabled(True)

    def add(self):
        try:record=self.browser.c.engine.import_template_code(self.code.toPlainText(),self.name.text())
        except Exception as error:self.error.setText(str(error));return
        b=self.browser;b.search.clear();b.search_timer.stop();b.sort.setCurrentIndex(0);b.page_index=0;b.selected_id=record['id'];b.reload()
        b.notice.setText('模版已新增，尚未应用到角色。');self.accept()


class TemplateBatchDialog(W.QDialog):
    """Checkable selection persists across name filtering; no preview/render work."""
    def __init__(self,browser,records,*,raw=None):
        super().__init__(browser);self.browser=browser;self.records=records;self.raw=raw;self.importing=raw is not None
        title='批量导入模版'if self.importing else'批量导出模版';self.setWindowTitle(title);self.resize(620,520);self.setMinimumSize(460,370)
        outer=vertical(self,22,12);outer.addWidget(label(title,'Title'))
        hint=label('勾选需要导入的模版。同名模版也会新增，不覆盖已有记录，不立即换装。'if self.importing else'勾选需要导出的模版，保存为一个文件；包含名称、装备、双染色与面部配饰。','Muted');hint.setWordWrap(True);outer.addWidget(hint)
        tools=horizontal();self.search=W.QLineEdit();self.search.setPlaceholderText('搜索模版名称');self.search.setClearButtonEnabled(True);tools.addWidget(self.search,1)
        self.all_button=button('全选结果',lambda:self.check_visible(True));tools.addWidget(self.all_button)
        self.none_button=button('取消勾选',lambda:self.check_visible(False));tools.addWidget(self.none_button);outer.addLayout(tools)
        self.list=W.QListWidget();self.list.setSpacing(3);self.list.setSelectionMode(W.QAbstractItemView.NoSelection)
        self.list.setVerticalScrollMode(W.QAbstractItemView.ScrollPerPixel);outer.addWidget(self.list,1)
        for i,record in enumerate(records):
            count=sum(bool(c.get('item_id'))for c in record['state']['equipment'].values())
            faces=sum(bool(v)for v in record['state'].get('facewear',[0,0]))
            item=W.QListWidgetItem(record['name']+f'  ·  {count} 件装备'+(f' · {faces} 件面饰'if faces else''));item.setToolTip(record['name']);item.setData(C.Qt.UserRole,i)
            item.setFlags(C.Qt.ItemIsEnabled|C.Qt.ItemIsUserCheckable);item.setCheckState(C.Qt.Checked);item.setSizeHint(C.QSize(100,36));self.list.addItem(item)
        self.count=label('','Muted');outer.addWidget(self.count);self.error=label('','Warning');self.error.setWordWrap(True);outer.addWidget(self.error)
        actions=horizontal();actions.addStretch();actions.addWidget(button('取消',self.reject,ghost=True))
        self.action_button=button('导入所选'if self.importing else'导出所选',self.execute,primary=True);actions.addWidget(self.action_button);outer.addLayout(actions)
        self.search.textChanged.connect(self.filter);self.list.itemChanged.connect(self.update_count);self.update_count()

    def selected_indices(self):
        return [self.list.item(i).data(C.Qt.UserRole)for i in range(self.list.count())if self.list.item(i).checkState()==C.Qt.Checked]

    def update_count(self,*_):
        count=len(self.selected_indices());visible=sum(not self.list.item(i).isHidden()for i in range(self.list.count()))
        self.count.setText(f'已选择 {count:,} / 共 {len(self.records):,} 套 · 搜索结果 {visible:,} 套');self.action_button.setEnabled(count>0)

    def filter(self,text):
        terms=normalized(text).split()
        for i,record in enumerate(self.records):self.list.item(i).setHidden(not all(t in normalized(record['name'])for t in terms))
        self.update_count()

    def check_visible(self,checked):
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            item=self.list.item(i)
            if not item.isHidden():item.setCheckState(C.Qt.Checked if checked else C.Qt.Unchecked)
        self.list.blockSignals(False);self.update_count()

    def execute(self):
        indices=self.selected_indices()
        if not indices:return
        self.error.clear();b=self.browser
        try:
            if self.importing:
                added=b.c.engine.import_template_batch(self.raw,indices)
                b.search.clear();b.search_timer.stop();b.sort.setCurrentIndex(0);b.page_index=0;b.selected_id=added[0]['id'];b.reload()
                b.notice.setText(f'已新增 {len(added)} 套模版，尚未应用到角色。')
            else:
                raw=b.c.engine.export_template_batch([self.records[i]['id']for i in indices])
                name='幻化柜-投影模版-'+C.QDate.currentDate().toString('yyyyMMdd')+'.hggtemplates'
                path,_=W.QFileDialog.getSaveFileName(self,'保存模版文件',name,'幻化柜模版文件 (*.hggtemplates)')
                if not path:return
                if not Path(path).suffix:path+='.hggtemplates'
                file=C.QSaveFile(path);file.setDirectWriteFallback(False)
                if not file.open(C.QIODevice.WriteOnly):raise OSError(file.errorString())
                if file.write(raw)!=len(raw):
                    error=file.errorString();file.cancelWriting();raise OSError(error)
                if not file.commit():raise OSError(file.errorString())
                b.notice.setText(f'已导出 {len(indices)} 套模版。')
        except Exception as error:self.error.setText(str(error));return
        self.accept()
