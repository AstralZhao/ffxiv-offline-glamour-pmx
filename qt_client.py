"""PySide6 wardrobe UI over the existing native D3D11 rendering controller."""
from __future__ import annotations

import argparse
import base64
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys

from qt_runtime import enable
enable()
from PySide6 import QtCore as C,QtGui as G,QtWidgets as W

from app import DemoEngine,GAME,DEFAULT_PRESET,ROOT,SLOTS,equipment_rows,parse_state
from client import NativeClient,color_text
from qt_theme import STYLE,ACCENT,MUTED,label,button,vertical,horizontal,polish,EquipmentDelegate,ComboBox,slot_icon,swatch_icon,LoadingSpinner

ARMOR=['Head','Body','Gloves','Legs','Feet'];ACCESSORIES=['Ears','Neck','Wrists','FingerR','FingerL']


class Value:
    def __init__(self,value=None,callback=None):self.value=value;self.callback=callback
    def get(self):return self.value
    def set(self,value):
        self.value=value
        if self.callback:self.callback(value)


class TimerHost:
    """GUI-thread timers for the shared controller; no Tk event loop."""
    def __init__(self,window):self.window=window;self.timers={};self.serial=0
    def after(self,delay,callback):
        self.serial+=1;key=self.serial;timer=C.QTimer(self.window);timer.setSingleShot(True);self.timers[key]=timer
        def fire():
            self.timers.pop(key,None);timer.deleteLater();callback()
        timer.timeout.connect(fire);timer.start(delay);return key
    def after_cancel(self,key):
        timer=self.timers.pop(key,None)
        if timer:timer.stop();timer.deleteLater()
    def destroy(self):
        for key in list(self.timers):self.after_cancel(key)
        self.window.allow_close=True;self.window.close()


class NativeView(W.QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.controller=None
        self.setAttribute(C.Qt.WA_NativeWindow);self.setAttribute(C.Qt.WA_PaintOnScreen)
        self.setAttribute(C.Qt.WA_NoSystemBackground);self.setAttribute(C.Qt.WA_OpaquePaintEvent)
        self.setMinimumSize(280,300);self.setMouseTracking(True);self.setCursor(C.Qt.OpenHandCursor)
        self.setSizePolicy(W.QSizePolicy.Expanding,W.QSizePolicy.Expanding)
    def paintEngine(self):return None
    def paintEvent(self,event):
        # Qt repaint requests (including adjacent spinner ticks) are not
        # scene edits. DXGI/DWM retains the last native frame independently.
        pass
    def showEvent(self,event):
        super().showEvent(event)
        if self.controller and self.controller.ready:self.controller.queue_render()
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self.controller:self.controller.queue_render()
    def winfo_width(self):return round(self.width()*self.devicePixelRatioF())
    def winfo_height(self):return round(self.height()*self.devicePixelRatioF())
    def point(self,event):return SimpleNamespace(x=event.position().x(),y=event.position().y())
    def mousePressEvent(self,event):
        if self.controller and event.button()in(C.Qt.LeftButton,C.Qt.RightButton):
            self.controller.begin_drag(self.point(event),3 if event.button()==C.Qt.RightButton else 1)
            self.setCursor(C.Qt.ClosedHandCursor);self.grabMouse()
    def mouseMoveEvent(self,event):
        if self.controller:self.controller.move_camera(self.point(event))
    def mouseReleaseEvent(self,event):
        if self.controller:self.controller.end_drag()
        self.releaseMouse();self.setCursor(C.Qt.OpenHandCursor)
    def wheelEvent(self,event):
        if self.controller:self.controller.zoom(.1 if event.angleDelta().y()>0 else -.1)
        event.accept()


class OutfitCard(W.QFrame):
    selected=C.Signal(str)
    def __init__(self,slot,controller):
        super().__init__();self.slot=slot;self.controller=controller;self.setObjectName('OutfitCard');self.setFixedHeight(78)
        self.setCursor(C.Qt.PointingHandCursor);outer=horizontal(self,8,9)
        self.icon=label();self.icon.setFixedSize(42,42);self.icon.setAlignment(C.Qt.AlignCenter);outer.addWidget(self.icon)
        content=vertical(spacing=2);outer.addLayout(content,1)
        top=horizontal();self.part=label(SLOTS[slot],'Muted');self.part.setStyleSheet('font-size:10px');top.addWidget(self.part);top.addStretch()
        self.clear=button('×',lambda:self.remove());self.clear.setProperty('clear',True);self.clear.setFixedSize(16,16);top.addWidget(self.clear);content.addLayout(top)
        self.name=label('未装备');self.name.setStyleSheet('font-size:12px;font-weight:500');content.addWidget(self.name)
        dyes=horizontal(spacing=5);self.dyes=[]
        for channel in(1,2):
            b=button(f'● {channel}区',lambda ch=channel:self.controller.open_dyes(self.slot,ch));b.setProperty('dye',True)
            b.setMinimumWidth(62);b.setFixedHeight(20);dyes.addWidget(b);self.dyes.append(b)
        dyes.addStretch();content.addLayout(dyes)
        self.icon.setPixmap(slot_icon(slot));self.clear.hide()
        for b in self.dyes:b.setEnabled(False)
    def remove(self):
        self.controller.state['equipment'][self.slot]={'item_id':None,'first':0,'second':0}
        self.controller.update_outfit();self.controller.queue_render()
    def mousePressEvent(self,event):
        if event.button()==C.Qt.LeftButton:self.selected.emit(self.slot)
        super().mousePressEvent(event)
    def display(self,choice,item,selected):
        key=(choice['item_id'],choice['first'],choice['second'],item.get('icon_id'),item.get('name'),item.get('dye_count'),selected,self.width())
        if getattr(self,'display_key',None)==key:return
        self.display_key=key
        self.setProperty('selected',selected);polish(self)
        text=item.get('name','未装备');self.name.setToolTip(text)
        self.name.setText(G.QFontMetrics(self.name.font()).elidedText(text,C.Qt.ElideRight,max(70,self.width()-82)))
        self.name.setStyleSheet('color:'+('#edf1f7'if item else'#748296')+';font-size:12px;font-weight:500')
        self.icon.setPixmap(self.controller.icon(item.get('icon_id')).pixmap(40,40)if item else slot_icon(self.slot))
        self.clear.setVisible(bool(item));count=item.get('dye_count',0)
        for ch,b in enumerate(self.dyes,1):
            stain=choice['first'if ch==1 else'second'];dye=self.controller.dye_by_id.get(stain)
            b.setEnabled(count>=ch);name=dye['name']if dye and stain else'原色'
            color='#%06x'%(dye['color']&0xffffff)if dye and stain else'#91a0b4'
            b.setText(f'{ch}区');b.setIcon(swatch_icon(color));b.setIconSize(C.QSize(10,10))
            b.setToolTip(f'第{ch}染色区 · '+(name if count>=ch else'不可染色'))


class WardrobeWindow(W.QMainWindow):
    def __init__(self):
        super().__init__();self.controller=None;self.allow_close=False;self.setWindowTitle('幻衣间')
        available=W.QApplication.primaryScreen().availableGeometry()
        self.resize(min(1480,available.width()-36),min(940,available.height()-55));self.setMinimumSize(1020,680)
        self.setAttribute(C.Qt.WA_ShowWithoutActivating)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self.controller and hasattr(self.controller,'browser'):self.controller.adjust_density()
    def closeEvent(self,event):
        if self.allow_close:event.accept()
        elif self.controller:event.ignore();self.controller.close()
        else:event.accept()


class QtClient(NativeClient):
    def __init__(self,window,game=GAME,preset=DEFAULT_PRESET,*,preset_directory=None,session_path=None):
        self.window=window;window.controller=self;self.root=TimerHost(window)
        self.closed=False;self.closing=False;self.job=None;self.dirty=False;self.timer=None;self.generation=0
        self.session_timer=None;self.accept_baseline=None;self.boot=None;self.ready=False
        self.state=parse_state({});self.good=deepcopy(self.state);self.items={};self.icons=OrderedDict()
        self.rows=[];self.folder=None;self.outer=None;self.offset=0;self.more=False
        self.editor=None;self.editor_record=None;self.draft=None;self.drag=None;self.search_timer=None;self.group=ARMOR
        self.session_path=Path(session_path)if session_path else ROOT/'artifacts/demo/native-session.json'
        self.message=Value('正在准备角色与装备…');self.slot=Value(SLOTS['Head']);self.query=Value('');self.show_head=Value(True)
        self.preset_name=Value('');self.folder_name=Value('全部同模目录');self.appearance_note=Value('');self.selected_note=Value('')
        self.dye_by_id={};self.raw_status='';self.dialogs=[];self.build_ui()
        self.message.callback=self.display_status;self.display_status(self.message.get())
        self.engine=None;self.icon_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='WardrobeIcons');self.pending_icons={};self.icon_timer=None
        self.loading_kind='正在加载角色…';self.sync_loading()
        # Let Qt paint the complete shell before starting asset preparation.
        self.root.after(50,lambda:self.start_engine(game,preset,preset_directory))

    def start_engine(self,game,preset,preset_directory):
        if self.closing:return
        try:
            self.engine=DemoEngine(Path(game),Path(preset),preset_directory=preset_directory,native_hwnd=int(self.viewport.winId()))
        except Exception as error:
            self.message.set('角色未能加载：'+str(error));self.loading_spinner.set_running(False);self.footer_spinner.set_running(False);self.loading_panel.hide();return
        self.poll_startup()

    def build_ui(self):
        central=W.QWidget();self.window.setCentralWidget(central);outer=vertical(central,0,0)
        header=W.QFrame();header.setObjectName('Header');header.setFixedHeight(86);h=horizontal(header,22,14)
        brand=vertical(spacing=0);brand.addWidget(label('GLAMOUR ATELIER','Eyebrow'));brand.addWidget(label('幻衣间','Brand'));h.addLayout(brand)
        h.addSpacing(15);self.tagline=label('自由搭配，每一种可能。','Muted');h.addWidget(self.tagline);h.addStretch()
        self.preset_combo=ComboBox();self.preset_combo.setMinimumWidth(190);self.preset_combo.setMaximumWidth(255)
        self.preset_combo.setToolTip('角色外观预设');self.preset_combo.activated.connect(self.select_preset);h.addWidget(self.preset_combo)
        h.addWidget(button('编辑外观',self.open_editor));h.addWidget(button('导入',self.import_preset,ghost=True))
        h.addWidget(button('我的搭配',self.saved_dialog,ghost=True));h.addWidget(button('收藏这套',self.save_outfit,primary=True));outer.addWidget(header)
        body=W.QWidget();body_layout=horizontal(body,18,16);outer.addWidget(body,1)
        self.browser=W.QFrame();self.browser.setObjectName('Panel');self.browser.setFixedWidth(340);left=vertical(self.browser,16,12)
        left.addWidget(label('挑选装备','Title'));left.addWidget(label('寻找属于你的下一套搭配','Muted'))
        groups=horizontal(spacing=6);self.browse_groups=[]
        for text,parts in(('防具',ARMOR),('饰品',ACCESSORIES)):
            b=button(text,lambda p=parts:self.switch_group(p));b.setCheckable(True);b.setChecked(parts==ARMOR);groups.addWidget(b);self.browse_groups.append(b)
        left.addLayout(groups);self.part_bar=horizontal(spacing=5);self.slot_buttons={};left.addLayout(self.part_bar);self.rebuild_part_tabs()
        self.search=W.QLineEdit();self.search.setClearButtonEnabled(True);self.search.setPlaceholderText('搜索装备名称或编号')
        self.search.textChanged.connect(self.search_changed);left.addWidget(self.search)
        self.query.callback=lambda text:self.search.setText(text)if self.search.text()!=text else None
        nav=horizontal(spacing=4);self.back_button=button('‹',self.back_folder,ghost=True);self.back_button.setFixedWidth(29);nav.addWidget(self.back_button)
        self.breadcrumb=label('全部同模目录','Muted');nav.addWidget(self.breadcrumb,1);left.addLayout(nav)
        self.catalog=W.QListWidget();self.catalog.setMouseTracking(True);self.catalog.setItemDelegate(EquipmentDelegate(self.catalog))
        self.catalog.setVerticalScrollMode(W.QAbstractItemView.ScrollPerPixel);self.catalog.setHorizontalScrollBarPolicy(C.Qt.ScrollBarAlwaysOff)
        self.catalog.itemClicked.connect(self.activate_item)
        enter=G.QShortcut(G.QKeySequence('Return'),self.catalog);enter.activated.connect(lambda:self.activate_item(self.catalog.currentItem())if self.catalog.currentItem()else None)
        left.addWidget(self.catalog,1)
        self.load_more=button('加载更多',lambda:self.load_catalog(True));left.addWidget(self.load_more)
        self.load_more.hide();self.back_button.hide()
        self.count_note=label('正在读取装备目录…','Muted');self.count_note.setStyleSheet('font-size:10px');left.addWidget(self.count_note);body_layout.addWidget(self.browser)
        scene=W.QFrame();scene.setObjectName('Panel');middle=vertical(scene,1,0)
        title=horizontal(margin=16);title.addWidget(label('试衣预览','Section'));title.addStretch()
        self.loading_panel=W.QWidget();loading=horizontal(self.loading_panel,0,6)
        self.loading_spinner=LoadingSpinner();loading.addWidget(self.loading_spinner)
        self.loading_label=label('正在加载角色…','Muted');loading.addWidget(self.loading_label)
        policy=self.loading_panel.sizePolicy();policy.setRetainSizeWhenHidden(True);self.loading_panel.setSizePolicy(policy)
        title.addWidget(self.loading_panel);title.addWidget(label('默认站姿','Badge'));middle.addLayout(title)
        self.viewport=NativeView();self.viewport.controller=self;middle.addWidget(self.viewport,1)
        tools=W.QWidget();self.camera_grid=W.QGridLayout(tools);self.camera_grid.setContentsMargins(12,10,12,8);self.camera_grid.setSpacing(6)
        directions=W.QWidget();camera=horizontal(directions,0,6);self.camera_buttons=[]
        for text,yaw in(('正面',0),('侧面',90),('背面',180)):
            b=button(text,lambda y=yaw:self.camera_yaw(y));b.setCheckable(True);camera.addWidget(b);self.camera_buttons.append((yaw,b))
        self.zoom_group=W.QWidget();zoom=horizontal(self.zoom_group,0,4);zoom.addWidget(button('−',lambda:self.zoom(-.15),ghost=True))
        self.zoom_label=label('100%','Muted');self.zoom_label.setFixedWidth(42);self.zoom_label.setAlignment(C.Qt.AlignCenter);zoom.addWidget(self.zoom_label)
        zoom.addWidget(button('+',lambda:self.zoom(.15),ghost=True))
        self.camera_grid.addWidget(directions,0,0,C.Qt.AlignLeft);self.camera_grid.addWidget(self.zoom_group,0,1)
        self.camera_grid.addWidget(button('还原',self.reset_camera,ghost=True),0,2);self.camera_grid.setColumnStretch(0,1);middle.addWidget(tools)
        hint=label('拖动旋转 · 右键平移 · 滚轮缩放','Muted');hint.setAlignment(C.Qt.AlignCenter);hint.setStyleSheet('font-size:10px;padding:0 0 12px 0');middle.addWidget(hint)
        body_layout.addWidget(scene,1)
        self.dressing=W.QFrame();self.dressing.setObjectName('Panel');self.dressing.setFixedWidth(290);right=vertical(self.dressing,14,8)
        title=horizontal();title.addWidget(label('当前搭配','Title'));title.addStretch();self.worn_count=label('0 件','Badge');title.addWidget(self.worn_count)
        title.addWidget(button('清空',self.clear_outfit,ghost=True));right.addLayout(title)
        self.appearance_label=label('软件预设库','Muted');self.appearance_label.setWordWrap(True);right.addWidget(self.appearance_label)
        tabs=horizontal(spacing=6);self.outfit_tabs=[]
        for text,parts in(('防具',ARMOR),('饰品',ACCESSORIES)):
            b=button(text,lambda p=parts:self.switch_group(p));b.setCheckable(True);b.setChecked(parts==ARMOR);tabs.addWidget(b);self.outfit_tabs.append(b)
        right.addLayout(tabs);self.outfit_cards={}
        card_scroll=W.QScrollArea();card_scroll.setWidgetResizable(True);card_scroll.setHorizontalScrollBarPolicy(C.Qt.ScrollBarAlwaysOff)
        card_content=W.QWidget();cards=vertical(card_content,0,8);card_scroll.setWidget(card_content);right.addWidget(card_scroll,1)
        for slot in SLOTS:
            card=OutfitCard(slot,self);card.selected.connect(self.select_slot);cards.addWidget(card);self.outfit_cards[slot]=card;card.setVisible(slot in ARMOR)
        cards.addStretch()
        self.head_check=W.QCheckBox('显示头饰');self.head_check.setChecked(True);self.head_check.toggled.connect(self.head_toggled);right.addWidget(self.head_check)
        body_layout.addWidget(self.dressing)
        footer=W.QFrame();footer.setObjectName('Footer');f=horizontal(footer,12,8)
        self.status_dot=label('●');self.status_dot.setStyleSheet('color:#89d5c4;font-size:9px');f.addWidget(self.status_dot)
        policy=self.status_dot.sizePolicy();policy.setRetainSizeWhenHidden(True);self.status_dot.setSizePolicy(policy)
        self.footer_spinner=LoadingSpinner(16);f.addWidget(self.footer_spinner)
        self.status_label=label('正在准备角色…','Muted');self.status_label.setSizePolicy(W.QSizePolicy.Expanding,W.QSizePolicy.Preferred);f.addWidget(self.status_label,1)
        self.detail_button=button('查看原因',self.show_details,ghost=True);self.detail_button.hide();f.addWidget(self.detail_button)
        f.addWidget(label('本地资源 · 离线试衣','Muted'));outer.addWidget(footer)
        self.compact=None;self.adjust_density()

    def adjust_density(self):
        compact=self.window.width()<1300
        if compact==getattr(self,'compact',None):return
        self.compact=compact;self.browser.setFixedWidth(300 if compact else 340);self.dressing.setFixedWidth(264 if compact else 290)
        self.tagline.setVisible(not compact);self.preset_combo.setMinimumWidth(160 if compact else 190)
        self.camera_grid.removeWidget(self.zoom_group)
        if compact:self.camera_grid.addWidget(self.zoom_group,1,0,1,3,C.Qt.AlignCenter)
        else:self.camera_grid.addWidget(self.zoom_group,0,1)

    def display_status(self,text):
        self.raw_status=text;error=any(word in text for word in('未完成','未能','无法加载','未能保存'))
        self.detail_button.setVisible(error);self.status_dot.setStyleSheet('color:'+('#e0b381'if error else ACCENT)+';font-size:9px')
        if text.startswith('D3D11 直接显示'):text='预览已更新'+(' · '+text.split(' · ',3)[3]if len(text.split(' · ',3))>3 else'')
        if error:text='本次操作未完成，已保留已有模型。'
        self.status_label.setText(G.QFontMetrics(self.status_label.font()).elidedText(text,C.Qt.ElideRight,max(300,self.window.width()-320)))
        self.status_label.setToolTip(self.raw_status)

    def show_details(self):W.QMessageBox.information(self.window,'操作说明',self.raw_status)

    def submit(self,fn,callback,*arguments):
        if self.engine is None or not self.engine.ready.done():
            self.message.set('正在加载角色，请稍候…');return False
        accepted=super().submit(fn,callback,*arguments)
        if accepted:
            if getattr(fn,'__name__','')=='draw':
                same_outfit=all(self.state[key]==self.good[key]for key in('equipment','head_visible'))
                self.loading_kind='正在调整视角…'if same_outfit else'正在绘制搭配…'
            else:
                operation=arguments[0]if arguments and isinstance(arguments[0],str)else''
                self.loading_kind={'preset':'正在加载角色…','import':'正在导入预设…','appearance_preview':'正在更新模型…',
                    'appearance_save':'正在保存预设…'}.get(operation,'正在准备预览…')
            self.sync_loading()
        return accepted

    def sync_loading(self):
        startup=self.engine is None or not self.engine.ready.done();busy=not self.closing and(startup or self.job is not None)
        text='正在加载角色…'if startup else self.loading_kind
        prominent=busy and text not in('正在调整视角…','正在保存预设…')
        self.loading_label.setText(text);self.loading_panel.setVisible(prominent)
        self.loading_spinner.set_running(prominent);self.footer_spinner.set_running(busy);self.status_dot.setVisible(not busy)
        if busy:self.status_label.setText(text)
        if self.editor:self.editor.spinner.set_running(busy)

    def poll_startup(self):
        if self.engine is None:return
        super().poll_startup()
        if not self.closed:self.sync_loading()

    def poll_job(self):
        super().poll_job()
        if self.closed:return
        self.sync_loading()
        if self.editor and self.job is None:
            self.editor.set_busy(False)
            if '未完成'in self.raw_status:
                self.editor.status.setText('操作未完成，已保留原模型和预设。');self.editor.status.setToolTip(self.raw_status)

    def close(self):
        for spinner in(self.loading_spinner,self.footer_spinner):spinner.set_running(False)
        if self.editor:self.editor.spinner.set_running(False)
        if self.icon_timer is not None:self.root.after_cancel(self.icon_timer);self.icon_timer=None
        self.icon_pool.shutdown(wait=False,cancel_futures=True)
        if self.engine is None:
            self.closing=True;self.closed=True;self.root.destroy();return
        super().close()

    def show_dialog(self,dialog):
        if self.window.testAttribute(C.Qt.WA_DontShowOnScreen):dialog.setAttribute(C.Qt.WA_DontShowOnScreen)
        self.dialogs.append(dialog)
        def finished(_result):
            if dialog in self.dialogs:self.dialogs.remove(dialog)
            if self.editor is dialog:self.editor=None
            dialog.deleteLater()
        dialog.finished.connect(finished);dialog.show();return dialog

    def refresh_boot(self):
        self.boot=self.engine.bootstrap();self.dye_by_id={d['stain_id']:d for d in self.boot['dyes']}
        self.preset_ids=[p['id']for p in self.boot['presets']];self.preset_combo.blockSignals(True);self.preset_combo.clear()
        for p in self.boot['presets']:self.preset_combo.addItem(p['name'],p['id']);self.preset_combo.setItemData(self.preset_combo.count()-1,p['file_name'],C.Qt.ToolTipRole)
        if self.boot['preset_id']in self.preset_ids:self.preset_combo.setCurrentIndex(self.preset_ids.index(self.boot['preset_id']))
        self.preset_combo.blockSignals(False);mismatch=not self.boot['appearance']['model_matches_file']
        self.appearance_label.setText('模型修改尚未保存到预设'if mismatch else'外观与已保存预设一致')
        self.appearance_label.setStyleSheet('color:'+('#dfbe88'if mismatch else MUTED)+';font-size:11px')

    def update_outfit(self):
        if not self.boot:return
        for slot,choice in self.state['equipment'].items():
            if choice['item_id']and choice['item_id']not in self.items:
                result=equipment_rows(slot,str(choice['item_id']));self.items.update({r['item_id']:r for r in result['items']})
            self.outfit_cards[slot].display(choice,self.items.get(choice['item_id'],{}),slot==self.slot_key())
        self.worn_count.setText(str(sum(c['item_id']is not None for c in self.state['equipment'].values()))+' 件')
        self.head_check.blockSignals(True);self.head_check.setChecked(self.state['head_visible']);self.head_check.blockSignals(False)
        self.zoom_label.setText(f"{self.state['zoom']*100:.0f}%")
        for yaw,b in self.camera_buttons:b.setChecked(abs(self.state['yaw']-yaw)<.01)
        for s,b in self.slot_buttons.items():b.setChecked(s==self.slot_key())
        current=self.state['equipment'][self.slot_key()]['item_id'];model=self.items.get(current,{}).get('model_set')
        for i in range(self.catalog.count()):
            item=self.catalog.item(i);row=item.data(C.Qt.UserRole)
            item.setData(C.Qt.UserRole+1,row['model_set']==model if row.get('is_group')else row['item_id']==current)

    def rebuild_part_tabs(self):
        while self.part_bar.count():
            item=self.part_bar.takeAt(0)
            if item.widget():item.widget().deleteLater()
        self.slot_buttons={}
        for slot in self.group:
            b=button(SLOTS[slot],lambda s=slot:self.select_slot(s));b.setProperty('slotTab',True);b.setCheckable(True)
            b.setChecked(SLOTS[slot]==self.slot.get());self.part_bar.addWidget(b,1);self.slot_buttons[slot]=b

    def switch_group(self,group):
        self.group=group
        for buttons in(self.browse_groups,self.outfit_tabs):
            for i,b in enumerate(buttons):b.setChecked((i==0)==(group==ARMOR))
        for slot,card in self.outfit_cards.items():card.setVisible(slot in group)
        self.rebuild_part_tabs()
        if self.slot_key()not in group:self.select_slot(group[0])

    def select_slot(self,slot):
        if not self.boot:return
        group=ARMOR if slot in ARMOR else ACCESSORIES
        self.slot.set(SLOTS[slot]);self.folder=None;self.outer=None;self.query.set('');self.cancel_search()
        if group!=self.group:self.switch_group(group)
        self.update_outfit();self.load_catalog()

    def search_changed(self,text):self.query.value=text;self.schedule_search()

    def icon(self,identifier):
        if not identifier:return G.QIcon()
        if identifier not in self.icons:
            if self.engine and self.engine.item_icons and identifier not in self.pending_icons and not self.closing:
                self.pending_icons[identifier]=self.icon_pool.submit(self.engine.item_icons.png,identifier)
                if self.icon_timer is None:self.icon_timer=self.root.after(30,self.poll_icons)
            return G.QIcon()
        self.icons.move_to_end(identifier)
        while len(self.icons)>384:self.icons.popitem(last=False)
        return self.icons[identifier]

    def poll_icons(self):
        self.icon_timer=None
        if self.closing:return
        changed=set()
        for identifier,future in list(self.pending_icons.items()):
            if not future.done():continue
            self.pending_icons.pop(identifier)
            try:
                image=G.QPixmap();image.loadFromData(future.result());self.icons[identifier]=G.QIcon(image)
            except Exception:self.icons[identifier]=G.QIcon()
            changed.add(identifier)
        if changed:
            for i in range(self.catalog.count()):
                item=self.catalog.item(i);identifier=item.data(C.Qt.UserRole).get('icon_id')
                if identifier in changed:item.setIcon(self.icons[identifier])
            for card in self.outfit_cards.values():
                if getattr(card,'display_key',None)and card.display_key[3]in changed:card.display_key=None
            self.update_outfit()
        if self.pending_icons:self.icon_timer=self.root.after(30,self.poll_icons)

    def populate_catalog(self,rows,append=False):
        if not append:self.catalog.clear()
        for row in rows:
            item=W.QListWidgetItem(row['name']);item.setData(C.Qt.UserRole,row);item.setIcon(self.icon(row.get('icon_id')))
            item.setToolTip(row['name']+(' · 同模目录'if row.get('is_group')else''));self.catalog.addItem(item)

    def load_catalog(self,append=False):
        self.cancel_search()
        if not self.boot:return
        if append and not self.more:return
        if not append:self.offset=0;self.rows=[]
        result=equipment_rows(self.slot_key(),self.query.get(),self.offset,grouped=self.folder is None,model_set=self.folder)
        self.items.update({r['item_id']:r for r in result['items']});self.rows.extend(result['items']);self.offset+=len(result['items']);self.more=result['more']
        self.populate_catalog(result['items'],append);self.load_more.setVisible(self.more);self.back_button.setVisible(self.folder is not None)
        text='全部同模目录'if self.folder is None else self.outer['name'];self.breadcrumb.setText(text);self.breadcrumb.setToolTip(text)
        self.search.setPlaceholderText('搜索装备名称或编号'if self.folder is None else'搜索此目录的款式')
        self.count_note.setText(f"{result['total']:,} "+('个同模目录 · 单击浏览'if self.folder is None else'款装备 · 单击试穿'))
        if not append:self.catalog.verticalScrollBar().setValue(0)
        self.update_outfit()

    def activate_item(self,item):
        if not self.ready:return
        row=item.data(C.Qt.UserRole)
        if row.get('is_group'):
            self.outer={'query':self.query.get(),'rows':list(self.rows),'offset':self.offset,'more':self.more,'scroll':self.catalog.verticalScrollBar().value(),
                'name':row['name'],'count':self.count_note.text()}
            self.folder=row['model_set'];self.query.set('');self.load_catalog();return
        self.state['equipment'][self.slot_key()]={'item_id':row['item_id'],'first':0,'second':0};self.update_outfit();self.queue_render()

    def back_folder(self):
        if self.folder is None or not self.outer:return
        record=self.outer;self.folder=None;self.outer=None;self.query.set(record['query']);self.cancel_search()
        self.rows=record['rows'];self.offset=record['offset'];self.more=record['more'];self.populate_catalog(self.rows)
        self.catalog.doItemsLayout();self.catalog.verticalScrollBar().setValue(record['scroll'])
        self.breadcrumb.setText('全部同模目录');self.back_button.hide();self.load_more.setVisible(self.more);self.count_note.setText(record['count'])
        self.search.setPlaceholderText('搜索装备名称或编号');self.update_outfit()

    def head_toggled(self,checked):self.show_head.set(checked);self.toggle_head()

    def select_preset(self,index):
        if index>=0 and self.boot:self.submit(self.engine.run,self.accept_result,'preset',{'id':self.preset_ids[index]})

    def import_preset(self):
        if self.engine is None or not self.engine.ready.done():return self.message.set('正在加载角色，请稍候…')
        if self.job:return self.message.set('请等待当前模型准备完成')
        name,_=W.QFileDialog.getOpenFileName(self.window,'导入角色外观预设','','角色预设 (*.dat)')
        if not name:return
        try:raw=Path(name).read_bytes()
        except OSError as error:return self.message.set(str(error))
        def done(result):
            self.refresh_boot()
            if result['model_loaded']:self.accept_result(result)
            else:self.message.set('预设文件已导入，模型暂时无法加载：'+result['preview_error']);self.open_editor(result['preset']['id'])
        self.submit(self.engine.run,done,'import',{'name':Path(name).name,'data':base64.b64encode(raw).decode()})

    def palette(self,title,choices,selected,callback):
        parent=self.editor if self.editor and self.editor.isVisible()else self.window
        dialog=W.QDialog(parent);dialog.setWindowTitle(title);dialog.resize(550,610);dialog.setMinimumSize(460,460);dialog.setWindowModality(C.Qt.WindowModal)
        outer=vertical(dialog,22,14);outer.addWidget(label(title,'Title'))
        name=label('选择颜色，立即预览','Muted');outer.addWidget(name)
        search=W.QLineEdit();search.setPlaceholderText('搜索名称');search.setClearButtonEnabled(True);outer.addWidget(search)
        scroll=W.QScrollArea();scroll.setWidgetResizable(True);scroll.setHorizontalScrollBarPolicy(C.Qt.ScrollBarAlwaysOff)
        content=W.QWidget();grid=W.QGridLayout(content);grid.setSpacing(8);scroll.setWidget(content);outer.addWidget(scroll,1)
        def render(text=''):
            while grid.count():
                item=grid.takeAt(0)
                if item.widget():item.widget().hide();item.widget().deleteLater()
            rows=[choice for choice in choices if text in choice['label']]
            for i,choice in enumerate(rows):
                color=choice.get('color','#333b48');b=button('✓'if choice['value']==selected else'')
                b.setFixedSize(40,40);b.setToolTip(choice['label']);b.setAccessibleName(choice['label']);b.setStyleSheet(f'QPushButton {{ background:{color};color:{color_text(color)};border:1px solid #657387;border-radius:7px;font-size:18px; }} QPushButton:hover {{ border:2px solid white; }}')
                def pick(c=choice):callback(c['value']);dialog.accept()
                b.clicked.connect(lambda _=False,p=pick:p());b.enterEvent=lambda e,c=choice:name.setText(c['label']);grid.addWidget(b,i//8,i%8)
            grid.setRowStretch((len(rows)+7)//8,1)
        search.textChanged.connect(render);render();return self.show_dialog(dialog)

    def open_dyes(self,slot,channel):
        choice=self.state['equipment'][slot];item=self.items.get(choice['item_id'],{})
        if item.get('dye_count',0)<channel:return
        colors=[{'value':0,'label':'原色','color':'#333b48'}]+[{'value':d['stain_id'],'label':d['name'],'color':'#%06x'%(d['color']&0xffffff)}for d in self.boot['dyes']if d['stain_id']]
        item_id=choice['item_id']
        def pick(value):
            if self.state['equipment'][slot]['item_id']!=item_id:return
            self.state['equipment'][slot]['first'if channel==1 else'second']=value;self.update_outfit();self.queue_render()
        self.palette(SLOTS[slot]+f' · 第{channel}染色区',colors,choice['first'if channel==1 else'second'],pick)

    def open_editor(self,key=None):
        if not self.boot or self.job:return
        if self.editor and self.editor.isVisible():self.editor.raise_();return
        key=key or self.boot['preset_id']
        def done(record):
            self.editor_record=record;self.draft=deepcopy(record['appearance']['rendered_parameters']if record['appearance']['is_preview']else record['parameters'])
            self.editor=AppearanceDialog(self);self.show_dialog(self.editor)
        self.submit(self.engine.describe_appearance,done,key)

    def editor_change(self,option,value):
        field=option['field'];self.draft[field]=(self.draft[field]&~option['mask'])|value
        if field=='tribe':
            def done(record):self.editor_record['fields']=record['fields'];self.editor.build_fields()
            self.submit(self.engine.describe_appearance,done,self.editor_record['id'],value)
        self.editor.update_status()

    def submit_editor(self,preview):
        if self.job:return
        record=self.editor_record;draft=deepcopy(self.draft)
        payload={'id':record['id'],'sha256':record['sha256'],'changes':{k:v for k,v in draft.items()if v!=record['parameters'][k]}}
        def done(result):
            self.refresh_boot()
            if preview:self.accept_result(result);self.editor.accept();self.editor=None
            else:
                record['parameters']=deepcopy(result['preset']['parameters']);record['sha256']=result['preset']['sha256']
                self.editor.update_status();self.schedule_session_save();self.message.set('已保存预设，模型保持原样')
        self.editor.status.setText('正在更新角色…'if preview else'正在保存预设…')
        self.editor.set_busy(True)
        self.submit(self.engine.run,done,'appearance_preview'if preview else'appearance_save',payload)

    def save_outfit(self):
        if not self.ready or self.job:return
        name,ok=W.QInputDialog.getText(self.window,'收藏搭配','给这套搭配起个名字')
        if ok and name.strip():
            try:self.engine.save({'name':name,'state':self.good});self.message.set('已收藏这套搭配')
            except Exception as error:self.message.set(str(error))

    def saved_dialog(self):
        if not self.boot:return
        dialog=W.QDialog(self.window);dialog.setWindowTitle('我的搭配');dialog.resize(440,560);layout=vertical(dialog,22,14)
        layout.addWidget(label('我的搭配','Title'));layout.addWidget(label('让喜欢的搭配随时回来','Muted'))
        scroll=W.QScrollArea();scroll.setWidgetResizable(True);content=W.QWidget();rows=vertical(content,0,10);scroll.setWidget(content);layout.addWidget(scroll,1)
        saved=self.engine.saved()
        for record in saved:
            def choose(r=record):
                if self.job:return
                dialog.accept()
                def load():
                    if self.engine.active_preset!=r['preset_id']:self.engine.run('preset',{'id':r['preset_id']})
                    return self.engine.render(r['state'])
                self.submit(load,self.accept_result)
            b=button(record['name']+'\n'+record['time'],choose);b.setMinimumHeight(66);rows.addWidget(b)
        if not saved:rows.addWidget(label('还没有收藏。完成搭配后，点击“收藏这套”。','Muted'))
        rows.addStretch();self.show_dialog(dialog)


class AppearanceDialog(W.QDialog):
    def __init__(self,controller):
        super().__init__(controller.window);self.c=controller;self.setWindowTitle('编辑角色外观');self.resize(650,680)
        self.setMinimumSize(590,600);outer=vertical(self,22,14)
        outer.addWidget(label('角色外观','Title'));outer.addWidget(label(controller.editor_record['file_name']+' · 软件预设库','Muted'))
        self.tabs=W.QTabWidget();outer.addWidget(self.tabs,1);self.build_fields()
        progress=horizontal();self.spinner=LoadingSpinner(18);progress.addWidget(self.spinner)
        self.status=label('','Warning');progress.addWidget(self.status,1);outer.addLayout(progress)
        actions=horizontal();actions.addWidget(button('关闭',self.reject,ghost=True));actions.addStretch()
        self.save_button=button('保存预设',lambda:controller.submit_editor(False));actions.addWidget(self.save_button)
        self.update_button=button('更新模型',lambda:controller.submit_editor(True),primary=True);actions.addWidget(self.update_button);outer.addLayout(actions)
        self.update_status()

    def reject(self):
        if self.c.job is None:super().reject()

    def set_busy(self,busy):
        self.tabs.setEnabled(not busy);self.save_button.setEnabled(not busy);self.update_button.setEnabled(not busy)

    def build_fields(self):
        active=self.tabs.currentIndex()
        while self.tabs.count():
            page=self.tabs.widget(0);self.tabs.removeTab(0);page.deleteLater()
        self.forms={};c=self.c
        for name in('发型与体型','五官细节','颜色与彩绘'):
            scroll=W.QScrollArea();scroll.setWidgetResizable(True);content=W.QWidget();form=W.QFormLayout(content)
            form.setContentsMargins(8,20,16,20);form.setHorizontalSpacing(24);form.setVerticalSpacing(18);form.setFieldGrowthPolicy(W.QFormLayout.AllNonFixedFieldsGrow)
            scroll.setWidget(content);self.tabs.addTab(scroll,name);self.forms[name]=form
        for option in c.editor_record['fields']:
            field=option['field'];choices=list(option['choices']);value=c.draft[field]&option['mask']
            if not any(choice['value']==value for choice in choices):choices.append({'value':value,'label':str(value)+'（当前预设）'})
            group='颜色与彩绘'if option['kind']=='color'or field=='face_paint'else'发型与体型'if field in('tribe','hair','height','bust','tail_or_ear_type','ear_muscle_tail_size')else'五官细节'
            if option['kind']=='color':
                selected=next(choice for choice in choices if choice['value']==value);color=selected.get('color','#333b48')
                control=button('颜色 '+str(value));control.setIcon(swatch_icon(color));control.setIconSize(C.QSize(16,16));control.setStyleSheet('QPushButton { text-align:left; }')
                def palette(o=option,b=control):
                    def pick(v):
                        c.editor_change(o,v);chosen=next(x for x in o['choices']if x['value']==v)
                        b.setText('颜色 '+str(v));b.setIcon(swatch_icon(chosen.get('color','#fff')))
                    c.palette(o['label'],o['choices'],c.draft[o['field']],pick)
                control.clicked.connect(lambda _=False,p=palette:p())
            elif option['kind']=='slider':
                container=W.QWidget();row=horizontal(container,0);control=W.QSlider(C.Qt.Horizontal);control.setRange(min(x['value']for x in choices),max(x['value']for x in choices));control.setValue(value)
                number=label(str(value),'Muted');number.setFixedWidth(28);row.addWidget(control,1);row.addWidget(number)
                control.valueChanged.connect(lambda v,o=option,n=number:(n.setText(str(v)),c.editor_change(o,v)))
                self.forms[group].addRow(option['label'],container);continue
            else:
                control=ComboBox()
                for choice in choices:control.addItem(choice['label'],choice['value'])
                control.setCurrentIndex(next(i for i,x in enumerate(choices)if x['value']==value))
                control.activated.connect(lambda index,o=option,w=control:c.editor_change(o,w.itemData(index)))
            self.forms[group].addRow(option['label'],control)
        toggles=[('highlight_flags',128,'启用挑染'),('mouth',128,'启用唇色'),('eyes',128,'瞳孔形态选项'),('face_paint',128,'镜像彩绘')]+[('facial_features',1<<i,f'面部特征 {i+1}')for i in range(7)]
        for field,bit,text in toggles:
            control=W.QCheckBox(text);control.setChecked(bool(c.draft[field]&bit))
            def toggle(checked,f=field,b=bit):
                c.draft[f]=(c.draft[f]|b)if checked else(c.draft[f]&~b);self.update_status()
            control.toggled.connect(toggle);self.forms['颜色与彩绘'if field in('highlight_flags','face_paint')else'五官细节'].addRow('',control)
        if active>=0:self.tabs.setCurrentIndex(active)

    def update_status(self):
        c=self.c;saved=c.draft==c.editor_record['parameters'];updated=c.draft==c.engine.rendered_parameters and c.engine.active_preset==c.editor_record['id']
        self.status.setText(('已保存到预设'if saved else'修改尚未保存')+' · '+('模型已更新'if updated else'模型尚未更新'))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--game',type=Path,default=GAME);parser.add_argument('--preset',type=Path,default=DEFAULT_PRESET)
    args=parser.parse_args();os.chdir(ROOT);application=W.QApplication.instance()or W.QApplication(sys.argv[:1]);application.setStyle('Fusion');application.setStyleSheet(STYLE)
    application.setFont(G.QFont('Microsoft YaHei UI',10));window=WardrobeWindow();controller=QtClient(window,args.game,args.preset);window.show()
    return application.exec()


def startup_check():
    application=W.QApplication.instance()or W.QApplication(sys.argv[:1]);application.setStyle('Fusion');application.setStyleSheet(STYLE)
    window=WardrobeWindow();window.setAttribute(C.Qt.WA_DontShowOnScreen);window.winId();window.close();application.processEvents()


if __name__=='__main__':raise SystemExit(main())
