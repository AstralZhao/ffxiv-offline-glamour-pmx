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
import time

from qt_runtime import enable
enable()
from PySide6 import QtCore as C,QtGui as G,QtWidgets as W

from app import DemoEngine,GAME,DEFAULT_PRESET,ROOT,SLOTS,equipment_rows,parse_state
from client import NativeClient,color_text
from effect_clock import EffectClock
from app_paths import paths,migrate,no_links
from qt_theme import STYLE,ACCENT,MUTED,label,button,vertical,horizontal,polish,EquipmentDelegate,IconEquipmentDelegate,equipment_hover_html,ComboBox,SearchComboBox,PresetComboBox,HairstyleComboBox,DraggableColorButton,slot_icon,swatch_icon,visibility_icon,LoadingSpinner

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


class FutureDelivery(C.QObject):
    """Deliver worker completion on the UI thread without polling gaps."""
    finished=C.Signal(object)
    def __init__(self,callback,parent):
        super().__init__(parent);self.callback=callback
        self.finished.connect(self.deliver,C.Qt.QueuedConnection)
    @C.Slot(object)
    def deliver(self,future):self.callback(future)


class NativeView(W.QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.controller=None;self.native_active=False
        self.setAttribute(C.Qt.WA_NativeWindow);self.setAttribute(C.Qt.WA_OpaquePaintEvent)
        self.setMinimumSize(280,300);self.setMouseTracking(True);self.setCursor(C.Qt.OpenHandCursor)
        self.setSizePolicy(W.QSizePolicy.Expanding,W.QSizePolicy.Expanding)
    def paintEngine(self):return None if self.native_active else super().paintEngine()
    def paintEvent(self,event):
        if not self.native_active:
            painter=G.QPainter(self);painter.fillRect(self.rect(),G.QColor('#0b0d13'))
            painter.setPen(G.QColor('#95a1b5'));painter.drawText(self.rect(),C.Qt.AlignCenter,'正在恢复角色预览…')
            return
        # Re-present the retained GPU result after exposure, without another
        # character build, particle update, image conversion or disk read.
        if self.controller:self.controller.request_present()
    def activate_native(self):
        if self.native_active:return
        self.native_active=True;self.setAttribute(C.Qt.WA_PaintOnScreen);self.setAttribute(C.Qt.WA_NoSystemBackground)
        self.update()
    def watch_window(self):
        self.window().installEventFilter(self)
        handle=self.window().windowHandle()
        if handle:handle.installEventFilter(self)
    def eventFilter(self,watched,event):
        if event.type()in(C.QEvent.Show,C.QEvent.Expose,C.QEvent.WindowActivate,C.QEvent.WindowStateChange):
            if self.controller:self.controller.request_present()
        return super().eventFilter(watched,event)
    def showEvent(self,event):
        super().showEvent(event)
        if self.controller:self.controller.request_present()
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


def hairstyle_choices_with_names(choices):
    try:
        profile=json.loads((ROOT/'artifacts/cn-data/hairstyle-names.json').read_bytes())
        expected=json.loads((ROOT/'docs/cn-barber-hair-menu.json').read_bytes())['client_version']
        names=profile['hairstyles']if profile['version']==1 and profile['region']=='CN'and profile['client_version']==expected else{}
    except(OSError,ValueError,KeyError):names={}
    rows=[]
    for choice in choices:
        key=f"{choice.get('icon_id',0)}:{choice.get('unlock_link',0)}:{choice['value']}";record=names.get(key)
        name=record['name']if record else'当前预设'if not choice.get('icon_id')else'基础发型'if not choice.get('unlock_link')else'未命名发型'
        rows.append({**choice,'label':f"发型 {choice['value']} · {name}"})
    return rows


class OutfitCard(W.QFrame):
    selected=C.Signal(str)
    def __init__(self,slot,controller):
        super().__init__();self.slot=slot;self.controller=controller;self.setObjectName('OutfitCard');self.setFixedHeight(88)
        self.setCursor(C.Qt.PointingHandCursor);outer=horizontal(self,8,9)
        self.icon=label();self.icon.setFixedSize(42,42);self.icon.setAlignment(C.Qt.AlignCenter);outer.addWidget(self.icon)
        content=vertical(spacing=2);outer.addLayout(content,1)
        top=horizontal();self.part=label(SLOTS[slot],'Muted');self.part.setStyleSheet('font-size:10px');top.addWidget(self.part);top.addStretch()
        self.visibility=button('',lambda:self.controller.toggle_slot_visibility(self.slot),ghost=True)
        self.visibility.setFixedSize(26,24);self.visibility.setIconSize(C.QSize(20,20));self.visibility.setStyleSheet('padding:2px');top.addWidget(self.visibility)
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
        self.controller.hidden_equipment.pop(self.slot,None)
        self.controller.state['equipment'][self.slot]={'item_id':None,'first':0,'second':0}
        self.controller.update_outfit();self.controller.queue_render()
    def mousePressEvent(self,event):
        if event.button()==C.Qt.LeftButton:self.selected.emit(self.slot)
        super().mousePressEvent(event)
    def display(self,choice,item,selected,hidden=False):
        key=(choice['item_id'],choice['first'],choice['second'],item.get('icon_id'),item.get('name'),item.get('dye_count'),selected,self.width(),tuple(item.get('tags',[])),hidden)
        if getattr(self,'display_key',None)==key:return
        self.display_key=key
        self.visibility.setVisible(bool(choice['item_id']));self.visibility.setIcon(visibility_icon(hidden))
        self.visibility.setAccessibleName('显示装备'if hidden else'隐藏装备')
        self.visibility.setToolTip('恢复这件装备及其染色'if hidden else'暂时取消试穿，保留装备与染色以便恢复')
        self.part.setText(SLOTS[self.slot]+(' · 已隐藏'if hidden else''))
        self.setProperty('selected',selected);polish(self)
        text=item.get('name','未装备');tooltip=text+'\n标签：'+(' / '.join(item.get('tags',[]))or'暂无标签')if item else text
        tooltip=equipment_hover_html(self.controller.icon(item.get('icon_id')),tooltip)if item else tooltip
        self.setToolTip(tooltip);self.name.setToolTip(tooltip);self.icon.setToolTip(tooltip)
        self.name.setText(G.QFontMetrics(self.name.font()).elidedText(text,C.Qt.ElideRight,max(70,self.width()-82)))
        self.name.setStyleSheet('color:'+('#edf1f7'if item else'#748296')+';font-size:12px;font-weight:500')
        self.icon.setPixmap(self.controller.icon(item.get('icon_id')).pixmap(40,40)if item else slot_icon(self.slot))
        self.clear.setVisible(bool(item));count=item.get('dye_count',0)
        for ch,b in enumerate(self.dyes,1):
            stain=choice['first'if ch==1 else'second'];dye=self.controller.dye_by_id.get(stain)
            name=dye['name']if dye and stain else'原色'
            color='#%06x'%(dye['color']&0xffffff)if dye and stain else'#91a0b4'
            b.setEnabled(count>=ch and not hidden)
            b.setText(f'{ch}区');b.setIcon(swatch_icon(color,unavailable=count<ch));b.setIconSize(C.QSize(16,16))
            b.setStyleSheet('QPushButton:disabled { background:#38252b; color:#e3aaa3; border:1px dashed #a46367; font-weight:600; }'if count<ch else'')
            b.setToolTip(f'第{ch}染色区 · '+(name if count>=ch else'不可染色'))


class WardrobeWindow(W.QMainWindow):
    def __init__(self):
        super().__init__();self.controller=None;self.allow_close=False;self.setWindowTitle('幻化柜')
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
    def __init__(self,window,game=GAME,preset=DEFAULT_PRESET,*,preset_directory=None,session_path=None,settings_handler=None,studio=None):
        from studio_lighting import options
        self.studio=options(studio)
        self.window=window;window.controller=self;self.root=TimerHost(window)
        self.settings_handler=settings_handler;self.game=Path(game).resolve()
        from network_cancellation import LoadCancellation
        from transfer_progress import TransferProgress,bind
        self.load_cancel=LoadCancellation();self.icon_progress=TransferProgress(cancel=self.load_cancel)
        self.closed=False;self.closing=False;self.job=None;self.dirty=False;self.timer=None;self.generation=0
        self.effect_clock=EffectClock();self.effect_future=None;self.effect_deferred=None;self.effect_poll=None
        self.effect_last_frame=30.
        self.effect_started_at=0.;self.effect_delivery=FutureDelivery(self.effect_completed,window)
        self.present_future=None;self.present_timer=None;self.present_requested=False
        self.present_delivery=FutureDelivery(self.present_completed,window)
        self.occlusion_timer=C.QTimer(window);self.occlusion_timer.setInterval(250)
        self.occlusion_timer.timeout.connect(self.request_present)
        self.effect_timer=C.QTimer(window);self.effect_timer.setInterval(33);self.effect_timer.timeout.connect(self.tick_effects)
        self.session_timer=None;self.accept_baseline=None;self.boot=None;self.ready=False
        self.catalog_serial=0;self.catalog_job=None;self.catalog_queued=None;self.catalog_timer=None;self.catalog_loading=False
        self.catalog_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='WardrobeSearch')
        self.state=parse_state({});self.good=deepcopy(self.state);self.items={};self.icons=OrderedDict();self.hidden_equipment={}
        self.rows=[];self.folder=None;self.outer=None;self.offset=0;self.more=False
        self.page_index=0;self.page_count=1;self.catalog_total=0
        self.editor=None;self.editor_record=None;self.draft=None;self.drag=None;self.search_timer=None;self.group=ARMOR
        self.session_path=Path(session_path)if session_path else paths().user/'native-session.json'
        self.message=Value('正在准备角色与装备…');self.slot=Value(SLOTS['Head']);self.query=Value('');self.show_head=Value(True)
        self.preset_name=Value('');self.folder_name=Value('全部同模目录');self.appearance_note=Value('');self.selected_note=Value('')
        self.dye_by_id={};self.raw_status='';self.dialogs=[];self.build_ui()
        self.message.callback=self.display_status;self.display_status(self.message.get())
        self.engine=None;self.icon_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='WardrobeIcons',initializer=bind,initargs=(self.icon_progress,));self.pending_icons={};self.icon_timer=None
        self.library=None
        from equipment_library import EquipmentLibrary
        self.library_future=self.icon_pool.submit(EquipmentLibrary,self.session_path.parent/'equipment-library.sqlite',ROOT/'artifacts/cn-data/catalog.sqlite',game=game)
        self.loading_kind='正在加载角色…';self.sync_loading()
        # Let Qt paint the complete shell before starting asset preparation.
        self.root.after(50,lambda:self.start_engine(game,preset,preset_directory))

    def start_engine(self,game,preset,preset_directory):
        if self.closing:return
        try:
            session=None
            if self.session_path.is_file():
                try:
                    if self.session_path.stat().st_size<=128*1024:session=json.loads(self.session_path.read_bytes())
                except(ValueError,OSError):pass
            hwnd=int(self.viewport.winId());self.viewport.watch_window()
            self.engine=DemoEngine(Path(game),Path(preset),preset_directory=preset_directory,native_hwnd=hwnd,
                startup_session=session,defer_native_present=True,studio=self.studio,load_cancel=self.load_cancel)
        except Exception as error:
            self.message.set('角色未能加载：'+str(error));self.loading_spinner.set_running(False);self.footer_spinner.set_running(False);self.loading_panel.hide();return
        self.poll_startup()

    def build_ui(self):
        central=W.QWidget();self.window.setCentralWidget(central);outer=vertical(central,0,0)
        header=W.QFrame();header.setObjectName('Header');header.setFixedHeight(86);h=horizontal(header,22,14)
        brand=vertical(spacing=0);brand.addWidget(label('GLAMOUR DRESSER','Eyebrow'));brand.addWidget(label('幻化柜','Brand'));h.addLayout(brand)
        h.addSpacing(15);self.tagline=label('试遍幻化，保存每一种可能。','Muted');h.addWidget(self.tagline);h.addStretch()
        self.character_manager=CharacterManagerDialog(self);self.preset_combo=self.character_manager.preset_combo
        self.character_name_label=label('正在加载角色…','Muted');self.character_name_label.setMaximumWidth(210);h.addWidget(self.character_name_label)
        self.character_manager_button=button('角色管理',self.open_character_manager);h.addWidget(self.character_manager_button)
        h.addWidget(button('投影模版',self.saved_dialog,ghost=True));h.addWidget(button('保存模版',self.save_outfit,primary=True))
        h.addWidget(button('设置',self.open_settings,ghost=True));outer.addWidget(header)
        body=W.QWidget();body_layout=horizontal(body,18,16);outer.addWidget(body,1)
        self.browser=W.QFrame();self.browser.setObjectName('Panel');self.browser.setFixedWidth(340);left=vertical(self.browser,16,12)
        groups=horizontal(spacing=6);groups.addWidget(label('挑选装备','Title'));groups.addStretch();self.browse_groups=[]
        for text,parts in(('防具',ARMOR),('饰品',ACCESSORIES)):
            b=button(text,lambda p=parts:self.switch_group(p));b.setFixedWidth(60);b.setCheckable(True);b.setChecked(parts==ARMOR);groups.addWidget(b);self.browse_groups.append(b)
        left.addLayout(groups);self.part_bar=horizontal(spacing=5);self.slot_buttons={};left.addLayout(self.part_bar);self.rebuild_part_tabs()
        self.search=W.QLineEdit();self.search.setClearButtonEnabled(True);self.search.setPlaceholderText('名称、编号、标签 · 支持多词组合')
        self.search.setToolTip('空格分隔多个条件（同时满足）。#标签、标签:礼服 只搜索标签。')
        self.search.textChanged.connect(self.search_changed);left.addWidget(self.search)
        self.search.returnPressed.connect(self.load_catalog)
        self.query.callback=lambda text:self.search.setText(text)if self.search.text()!=text else None
        self.level_timer=C.QTimer(self.window);self.level_timer.setSingleShot(True);self.level_timer.setInterval(250);self.level_timer.timeout.connect(self.filters_changed)
        levels=horizontal(spacing=6);levels.addWidget(label('装备等级','Muted'))
        self.min_level=W.QSpinBox();self.max_level=W.QSpinBox()
        for control,text in((self.min_level,'最低穿戴等级'),(self.max_level,'最高穿戴等级')):
            control.setRange(0,999);control.setSpecialValueText('不限');control.setButtonSymbols(W.QAbstractSpinBox.NoButtons)
            control.setAlignment(C.Qt.AlignCenter);control.setFixedSize(48,28);control.setStyleSheet('padding:4px 6px');control.setToolTip(text+'；0表示不限。按穿戴所需等级筛选，不是装备品级。')
            control.setKeyboardTracking(False);control.valueChanged.connect(lambda _,c=control:self.level_changed(c))
        levels.addWidget(self.min_level);levels.addWidget(label('—','Muted'));levels.addWidget(self.max_level);levels.addStretch()
        self.reset_filters_button=button('重置',self.reset_filters,ghost=True);self.reset_filters_button.setToolTip('清除搜索、等级、标签与仅收藏筛选')
        self.reset_filters_button.setStyleSheet('padding:7px 4px');levels.addWidget(self.reset_filters_button);left.addLayout(levels)
        filters=horizontal(spacing=6);self.tag_picker=SearchComboBox();self.tag_picker.setMinimumWidth(90);self.tag_picker.addItem('',None)
        self.tag_picker.setToolTip('输入文字搜索已有标签，选择后加入筛选；下拉列表支持滚轮浏览')
        self.tag_picker.setSizeAdjustPolicy(W.QComboBox.AdjustToMinimumContentsLengthWithIcon);self.tag_picker.setMinimumContentsLength(5)
        self.tag_picker.activated.connect(self.pick_tag);filters.addWidget(self.tag_picker,1)
        self.favorite_filter=button('★ 收藏',self.filters_changed);self.favorite_filter.setCheckable(True);self.favorite_filter.setToolTip('只显示收藏的装备；同模目录匹配任一收藏成员')
        filters.addWidget(self.favorite_filter);left.addLayout(filters)
        self.active_tags=[];self.tag_summary=button('',self.clear_tag_filters,ghost=True);self.tag_summary.hide();left.addWidget(self.tag_summary)
        nav=horizontal(spacing=4);self.back_button=button('‹',self.back_folder,ghost=True);self.back_button.setFixedWidth(29);nav.addWidget(self.back_button)
        self.breadcrumb=label('全部装备','Muted');nav.addWidget(self.breadcrumb,1)
        self.view_toggle=button('图标',self.toggle_catalog_view);self.view_toggle.setCheckable(True);self.view_toggle.setStyleSheet('padding:5px 8px');nav.addWidget(self.view_toggle)
        nav.addWidget(button('刷新',self.load_catalog,ghost=True));left.addLayout(nav)
        self.catalog=W.QListWidget();self.catalog.setMouseTracking(True);self.catalog.setItemDelegate(EquipmentDelegate(self.catalog))
        self.catalog.setContextMenuPolicy(C.Qt.CustomContextMenu);self.catalog.customContextMenuRequested.connect(self.catalog_context)
        self.catalog.setVerticalScrollMode(W.QAbstractItemView.ScrollPerPixel);self.catalog.setHorizontalScrollBarPolicy(C.Qt.ScrollBarAlwaysOff)
        self.catalog.itemClicked.connect(self.activate_item)
        enter=G.QShortcut(G.QKeySequence('Return'),self.catalog);enter.activated.connect(lambda:self.activate_item(self.catalog.currentItem())if self.catalog.currentItem()else None)
        left.addWidget(self.catalog,1)
        pages=horizontal(spacing=5);self.previous_page=button('‹ 上一页',lambda:self.go_page(self.page_index-1),ghost=True)
        pages.addWidget(self.previous_page);pages.addStretch();self.page_number=W.QSpinBox();self.page_number.setRange(1,1)
        self.page_number.setPrefix('第 ');self.page_number.setSuffix(' 页');self.page_number.setMaximumWidth(90)
        self.page_number.setToolTip('输入页码后按回车跳转');self.page_number.editingFinished.connect(lambda:self.go_page(self.page_number.value()-1))
        pages.addWidget(self.page_number);self.page_total=label('/ 1','Muted');self.page_total.setMinimumWidth(35);pages.addWidget(self.page_total);pages.addStretch()
        self.next_page=button('下一页 ›',lambda:self.go_page(self.page_index+1),ghost=True);pages.addWidget(self.next_page);left.addLayout(pages)
        for b in(self.previous_page,self.next_page):b.setStyleSheet('padding:7px 2px;font-size:11px')
        self.sync_pagination();self.back_button.hide()
        catalog_status=horizontal();self.catalog_spinner=LoadingSpinner(16);catalog_status.addWidget(self.catalog_spinner)
        self.count_note=label('正在读取装备目录…','Muted');self.count_note.setStyleSheet('font-size:10px');catalog_status.addWidget(self.count_note,1)
        left.addLayout(catalog_status);body_layout.addWidget(self.browser)
        self.icon_mode=False;self.set_catalog_view(True,persist=False)
        scene=W.QFrame();scene.setObjectName('Panel');middle=vertical(scene,1,0)
        title=horizontal(margin=16);title.addWidget(label('试衣预览','Section'));title.addStretch()
        self.loading_panel=W.QWidget();loading=horizontal(self.loading_panel,0,6)
        self.loading_spinner=LoadingSpinner(36 if(Path(self.game)/'network-source.json').is_file()else 20);loading.addWidget(self.loading_spinner)
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
        title=horizontal();title.addWidget(label('当前模版','Title'));title.addStretch();self.worn_count=label('0 件','Badge');title.addWidget(self.worn_count)
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
            card.setContextMenuPolicy(C.Qt.CustomContextMenu)
            card.customContextMenuRequested.connect(lambda point,s=slot:self.outfit_context(s,point))
        cards.addStretch()
        self.head_check=W.QCheckBox('显示头饰');self.head_check.setChecked(True);self.head_check.toggled.connect(self.head_toggled);right.addWidget(self.head_check)
        self.effects_check=W.QCheckBox('显示装备特效');self.effects_check.setChecked(True)
        self.effects_check.setToolTip('默认定格显示。可播放装备特效，角色保持静态；尚未支持的特效分支会提示。')
        self.effects_check.toggled.connect(self.effects_toggled);right.addWidget(self.effects_check)
        self.effects_play=button('▶ 播放特效',self.toggle_effect_playback,ghost=True);self.effects_play.setCheckable(True)
        self.effects_play.setEnabled(False);self.effects_play.setToolTip('播放 / 暂停装备特效。暂停保留当前帧；重新启动默认暂停。')
        right.addWidget(self.effects_play)
        body_layout.addWidget(self.dressing)
        footer=W.QFrame();footer.setObjectName('Footer');f=horizontal(footer,12,8)
        self.status_dot=label('●');self.status_dot.setStyleSheet('color:#89d5c4;font-size:9px');f.addWidget(self.status_dot)
        policy=self.status_dot.sizePolicy();policy.setRetainSizeWhenHidden(True);self.status_dot.setSizePolicy(policy)
        self.footer_spinner=LoadingSpinner(16);f.addWidget(self.footer_spinner)
        self.status_label=label('正在准备角色…','Muted');self.status_label.setSizePolicy(W.QSizePolicy.Expanding,W.QSizePolicy.Preferred);f.addWidget(self.status_label,1)
        self.detail_button=button('查看原因',self.show_details,ghost=True);self.detail_button.hide();f.addWidget(self.detail_button)
        f.addWidget(button('操作说明',self.open_guide,ghost=True))
        self.signature_label=label('泠雨@沃仙曦染','Muted');self.signature_label.setTextInteractionFlags(C.Qt.TextSelectableByMouse)
        f.addWidget(self.signature_label);outer.addWidget(footer)
        self.compact=None;self.adjust_density()

    def adjust_density(self):
        compact=self.window.width()<1300
        if compact==getattr(self,'compact',None):return
        self.compact=compact;self.browser.setFixedWidth(300 if compact else 340);self.dressing.setFixedWidth(264 if compact else 290)
        self.tagline.setVisible(not compact);self.character_name_label.setMaximumWidth(135 if compact else 210)
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

    def apply_studio(self,value):
        from studio_lighting import options
        self.studio=options(value)
        if self.closing:return
        if not self.ready or self.job is not None or self.effect_future is not None:
            self.root.after(80,lambda:self.apply_studio(self.studio));return
        selected=dict(self.studio);state=deepcopy(self.state)
        def draw():
            previous=self.engine.studio
            try:self.engine.studio=selected;return self.engine.render(state)
            except Exception:
                self.engine.studio=previous;raise
        self.submit(draw,self.accept_result)

    def open_guide(self):
        from user_guide import GUIDE,GUIDE_STYLE,GUIDE_SECTIONS
        dialog=W.QDialog(self.window);dialog.setWindowTitle('操作说明 · 幻化柜');dialog.resize(880,760);dialog.setMinimumSize(640,480)
        layout=vertical(dialog,16,12);content=horizontal(spacing=14)
        sidebar=vertical(spacing=8);sidebar.addWidget(label('使用目录','Section'))
        navigation=W.QListWidget();navigation.setFixedWidth(154);navigation.setAccessibleName('使用说明章节')
        navigation.setStyleSheet('QListWidget { background:#191e27;border:1px solid #2b3443;border-radius:8px;padding:4px; } QListWidget::item { padding:10px 8px;border-radius:5px; } QListWidget::item:selected { background:#2e4c48;color:#b8eee2; } QListWidget::item:hover { background:#25323e; }')
        for anchor,title in GUIDE_SECTIONS:
            item=W.QListWidgetItem(title);item.setData(C.Qt.UserRole,anchor);navigation.addItem(item)
        sidebar.addWidget(navigation,1);content.addLayout(sidebar)
        class GuideBrowser(W.QTextBrowser):
            def resizeEvent(self,event):
                super().resizeEvent(event)
                # Reflow percentage-width tables after the viewport changes.
                document=self.document();document.markContentsDirty(0,document.characterCount())
        browser=GuideBrowser();browser.setOpenExternalLinks(False);browser.setAccessibleName('幻化柜使用说明正文')
        browser.setStyleSheet('QTextBrowser { background:#191e27;color:#dce4ef;border:1px solid #2b3443;border-radius:8px;padding:0px;selection-background-color:#345751; }')
        document=browser.document();document.setDefaultFont(G.QFont('Microsoft YaHei UI',10))
        document.setDefaultStyleSheet(GUIDE_STYLE);document.setDocumentMargin(20);browser.setHtml(GUIDE)
        block=document.begin()
        while block.isValid():
            cursor=G.QTextCursor(block);format_=block.blockFormat()
            format_.setLineHeight(130 if format_.headingLevel()else 145,G.QTextBlockFormat.ProportionalHeight.value)
            cursor.setBlockFormat(format_);block=block.next()
        navigation.currentItemChanged.connect(lambda current,_:browser.scrollToAnchor(current.data(C.Qt.UserRole))if current else None)
        navigation.setCurrentRow(0);content.addWidget(browser,1);layout.addLayout(content,1)
        actions=horizontal();actions.addStretch();actions.addWidget(button('知道了',dialog.accept,primary=True));layout.addLayout(actions)
        self.show_dialog(dialog);return dialog

    def submit(self,fn,callback,*arguments):
        if self.engine is None or not self.engine.ready.done():
            self.message.set('正在加载角色，请稍候…');return False
        if self.effect_future is not None:
            # Finish the single in-flight presentation before model/save work.
            # Do not reject a click just because a playback frame is drawing.
            if self.effect_deferred is not None:return False
            self.effect_deferred=(fn,callback,arguments,deepcopy(self.state),self.generation)
            self.loading_kind='正在准备预览…';self.sync_loading();return True
        if fn==self.engine.run and arguments and arguments[0]=='outfit':
            original_callback=callback
            def callback(result):
                self.hidden_equipment.clear();original_callback(result)
        accepted=super().submit(fn,callback,*arguments)
        if accepted:
            if getattr(fn,'__name__','')=='draw':
                same_outfit=all(self.state[key]==self.good[key]for key in('equipment','head_visible','effects_visible'))
                self.loading_kind='正在调整视角…'if same_outfit else'正在绘制模版…'
            else:
                operation=arguments[0]if arguments and isinstance(arguments[0],str)else''
                self.loading_kind={'preset':'正在加载角色…','import':'正在导入预设…','create':'正在新建预设…','appearance_preview':'正在更新模型…',
                    'appearance_save':'正在保存预设…'}.get(operation,'正在准备预览…')
            self.sync_loading()
        return accepted

    def sync_loading(self):
        startup=self.engine is None or not self.engine.ready.done();busy=not self.closing and(startup or self.job is not None or self.effect_deferred is not None)
        text='正在加载角色…'if startup else self.loading_kind
        prominent=busy and text not in('正在调整视角…','正在保存预设…')and not getattr(self,'live_edit_job',False)
        meter=getattr(self.engine,'transfer_progress',None)if self.engine else None
        transfer=meter.snapshot()if busy and meter else None
        from transfer_progress import presentation
        fraction,text=presentation(transfer,text)
        for spinner in(self.loading_spinner,self.footer_spinner):spinner.set_progress(fraction)
        self.loading_label.setText(text);self.loading_panel.setVisible(prominent)
        self.loading_spinner.set_running(prominent);self.footer_spinner.set_running(busy);self.status_dot.setVisible(not busy)
        if busy:self.status_label.setText(text)
        if self.editor:self.editor.spinner.set_running(busy)
        if self.editor:self.editor.spinner.set_progress(fraction)
        self.character_manager.update_state()

    def poll_startup(self):
        if self.closing:return self.close()
        if self.engine is None:return
        if self.library is None:
            if not self.library_future.done():self.root.after(30,self.poll_startup);return
            try:
                self.library=self.library_future.result();self.refresh_tag_picker()
                self.set_catalog_view(self.library.setting('icon_mode','1')=='1',persist=False)
            except Exception as error:self.message.set('装备标签库未能加载：'+str(error));self.loading_spinner.set_running(False);self.footer_spinner.set_running(False);return
        super().poll_startup()
        if self.ready:self.request_present()
        if not self.closed:self.sync_loading()

    def poll_job(self):
        super().poll_job()
        if self.closed:return
        if self.present_requested:self.request_present()
        self.sync_loading()
        if self.editor and self.job is None:
            self.live_edit_job=False
            self.editor.set_busy(False)
            if '未完成'in self.raw_status:
                self.editor.status.setText('操作未完成，已保留原模型和预设。');self.editor.status.setToolTip(self.raw_status)

    def close(self):
        if self.closed:return
        self.load_cancel.set()
        if self.engine:self.engine.cancel_loading()
        self.level_timer.stop()
        self.occlusion_timer.stop()
        self.effect_timer.stop();self.effect_clock.pause(self.effect_last_frame)
        self.effect_deferred=None
        if self.present_timer is not None:self.root.after_cancel(self.present_timer);self.present_timer=None
        self.present_requested=False
        if self.present_future is not None and not self.present_future.done():
            self.closing=True;self.ready=False;self.root.after(30,self.close);return
        if self.effect_future is not None and not self.effect_future.done():
            self.closing=True;self.ready=False;self.root.after(30,self.close);return
        for spinner in(self.loading_spinner,self.footer_spinner):spinner.set_running(False)
        if self.editor:self.editor.spinner.set_running(False)
        self.catalog_spinner.set_running(False)
        if self.catalog_timer is not None:self.root.after_cancel(self.catalog_timer);self.catalog_timer=None
        self.catalog_queued=None;self.catalog_pool.shutdown(wait=False,cancel_futures=True)
        if self.icon_timer is not None:self.root.after_cancel(self.icon_timer);self.icon_timer=None
        self.icon_pool.shutdown(wait=False,cancel_futures=True)
        # The icon pool also owns the equipment-library startup job. Let its
        # cancelled reads/SQLite context unwind before replacing this client.
        if not self.library_future.done()or any(not f.done()for f in self.pending_icons.values()):
            self.closing=True;self.ready=False;self.message.set('正在取消旧资源加载，请稍候…')
            self.root.after(30,self.close);return
        if self.engine is None:
            self.closing=True;self.closed=True;self.root.destroy();return
        super().close()

    def show_dialog(self,dialog):
        if self.window.testAttribute(C.Qt.WA_DontShowOnScreen):dialog.setAttribute(C.Qt.WA_DontShowOnScreen)
        self.dialogs.append(dialog)
        def finished(_result):
            if dialog in self.dialogs:self.dialogs.remove(dialog)
            if self.editor is dialog:self.editor=None
            self.character_manager.update_state()
            dialog.deleteLater()
        dialog.finished.connect(finished);dialog.show();return dialog

    def open_settings(self):
        if self.settings_handler:self.settings_handler();return
        from settings import SettingsStore
        from qt_settings import SettingsDialog
        dialog=SettingsDialog(SettingsStore(),self.game,parent=self.window,storage_context=self.storage_identity,memory_release=self.release_memory)
        dialog.accepted.connect(lambda:self.apply_studio(dialog.saved_studio)if dialog.saved_game==self.game else self.message.set('资源来源已保存，下次启动软件时生效'))
        self.show_dialog(dialog)

    def release_memory(self):
        from concurrent.futures import Future
        result=Future()
        if not self.ready or self.engine is None or self.closing:
            result.set_exception(RuntimeError('请等待角色加载完成'));return result
        def work():
            try:return self.engine.release_memory(),None
            except Exception as error:return None,error
        def done(record):
            value,error=record
            if error is not None:
                self.message.set('释放内存未完成：'+str(error));result.set_exception(error)
            else:
                self.message.set('闲置内存缓存已清理，当前预览保留。');result.set_result(value)
        if not self.submit(work,done):result.set_exception(RuntimeError('请等待当前操作完成后重试'))
        return result

    def storage_identity(self):
        cache=getattr(self.engine,'characters',None)if self.engine is not None else None
        return deepcopy(cache.identity)if cache is not None else None

    def refresh_boot(self):
        self.boot=self.engine.bootstrap();self.dye_by_id={d['stain_id']:d for d in self.boot['dyes']}
        selected=self.preset_combo.currentData()
        if self.character_manager.last_active!=self.boot['preset_id']:selected=self.boot['preset_id']
        self.preset_ids=[p['id']for p in self.boot['presets']];self.preset_combo.blockSignals(True);self.preset_combo.clear()
        for p in self.boot['presets']:
            text=p['name']+' · '+p['file_name']if p['name']!=p['file_name']else p['name']
            self.preset_combo.addItem(text,p['id']);self.preset_combo.setItemData(self.preset_combo.count()-1,text,C.Qt.ToolTipRole)
        selected=selected if selected in self.preset_ids else self.boot['preset_id']
        if selected in self.preset_ids:self.preset_combo.setCurrentIndex(self.preset_ids.index(selected))
        self.preset_combo.lineEdit().setCursorPosition(0)
        self.preset_combo.blockSignals(False);mismatch=not self.boot['appearance']['model_matches_file']
        self.appearance_label.setText('模型修改尚未保存到预设'if mismatch else'外观与已保存预设一致')
        self.appearance_label.setStyleSheet('color:'+('#dfbe88'if mismatch else MUTED)+';font-size:11px')
        record=next((p for p in self.boot['presets']if p['id']==self.boot['preset_id']),None)
        text=(record or{}).get('name','未选择角色');self.character_name_label.setText(G.QFontMetrics(self.character_name_label.font()).elidedText(text,C.Qt.ElideRight,self.character_name_label.maximumWidth()))
        self.character_name_label.setToolTip(text);self.character_manager.last_active=self.boot['preset_id'];self.character_manager.update_state()

    def toggle_slot_visibility(self,slot):
        if not self.ready or self.job is not None:return
        if slot in self.hidden_equipment:
            self.state['equipment'][slot]=self.hidden_equipment.pop(slot)
        else:
            choice=self.state['equipment'][slot]
            if choice['item_id']is None:return
            self.hidden_equipment[slot]=deepcopy(choice)
            self.state['equipment'][slot]={'item_id':None,'first':0,'second':0}
        self.update_outfit();self.queue_render()

    def clear_outfit(self):
        self.hidden_equipment.clear();super().clear_outfit()

    def update_outfit(self):
        if not self.boot:return
        for slot,choice in self.state['equipment'].items():
            if choice['item_id']:self.hidden_equipment.pop(slot,None)
            hidden=slot in self.hidden_equipment
            choice=self.hidden_equipment.get(slot,choice)
            if choice['item_id']and choice['item_id']not in self.items:
                result=equipment_rows(slot,str(choice['item_id']),library=self.library);self.items.update({r['item_id']:r for r in result['items']})
            self.outfit_cards[slot].display(choice,self.items.get(choice['item_id'],{}),slot==self.slot_key(),hidden)
        self.worn_count.setText(str(sum(c['item_id']is not None for c in self.state['equipment'].values()))+' 件')
        self.head_check.blockSignals(True);self.head_check.setChecked(self.state['head_visible']);self.head_check.blockSignals(False)
        self.effects_check.blockSignals(True);self.effects_check.setChecked(self.state['effects_visible']);self.effects_check.blockSignals(False)
        if not self.state['effects_visible']:self.pause_effects()
        self.effects_play.setEnabled(self.ready and self.state['effects_visible'])
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
        self.slot.set(SLOTS[slot]);self.folder=None;self.outer=None;self.cancel_search()
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
            if self.editor:self.editor.refresh_icons(changed)
            for i in range(self.catalog.count()):
                item=self.catalog.item(i);identifier=item.data(C.Qt.UserRole).get('icon_id')
                if identifier in changed:item.setIcon(self.icons[identifier])
            for card in self.outfit_cards.values():
                key=getattr(card,'display_key',None)
                if key and (key[3]in changed or any(self.library.dye_icon(stain)in changed for stain in key[1:3]if stain)):
                    card.display_key=None
            for dialog in self.dialogs:
                if getattr(dialog,'icon_id',None)in changed:dialog.icon_label.setPixmap(self.icons[dialog.icon_id].pixmap(60,60))
                if dialog is not self.editor and hasattr(dialog,'refresh_icons'):dialog.refresh_icons(changed)
            self.update_outfit()
        if self.pending_icons:self.icon_timer=self.root.after(30,self.poll_icons)

    def populate_catalog(self,rows,append=False):
        if not append:self.catalog.clear()
        for row in rows:
            item=W.QListWidgetItem(row['name']);item.setData(C.Qt.UserRole,row);item.setIcon(self.icon(row.get('icon_id')))
            item.setToolTip(self.equipment_tooltip(row));self.catalog.addItem(item)

    def equipment_tooltip(self,row):
        folder=row.get('is_group')and row.get('group_count',1)>1
        tooltip=row['name']+(f" · 同模 {row['group_count']} 款"if folder else'')
        return tooltip+'\n'+(('含收藏款式\n'if folder else'已收藏\n')if row.get('favorite')else'')+('代表款标签：'if folder else'标签：')+(' / '.join(row.get('tags',[]))or'暂无标签')+'\n右键查看详情、编辑标签和收藏'

    def set_catalog_view(self,icons,persist=True):
        self.icon_mode=bool(icons);self.view_toggle.setChecked(self.icon_mode)
        self.view_toggle.setText('图标'if self.icon_mode else'列表');self.view_toggle.setToolTip('切换图标 / 列表显示')
        if self.icon_mode:
            self.catalog.setViewMode(W.QListView.IconMode);self.catalog.setResizeMode(W.QListView.Adjust)
            self.catalog.setMovement(W.QListView.Static);self.catalog.setWrapping(True)
            self.catalog.setGridSize(C.QSize(72,78));self.catalog.setItemDelegate(IconEquipmentDelegate(self.catalog))
        else:
            self.catalog.setViewMode(W.QListView.ListMode);self.catalog.setWrapping(False);self.catalog.setGridSize(C.QSize())
            self.catalog.setItemDelegate(EquipmentDelegate(self.catalog))
        self.catalog.doItemsLayout()
        if persist and self.library:self.library.set_setting('icon_mode','1'if self.icon_mode else'0')

    def toggle_catalog_view(self):self.set_catalog_view(not self.icon_mode)

    def refresh_tag_picker(self):
        self.tag_picker.blockSignals(True);self.tag_picker.clear();self.tag_picker.addItem('',None)
        for row in self.library.tags():self.tag_picker.addItem(row['tag']+f" ({row['count']})",row['tag'])
        self.tag_picker.blockSignals(False)

    def pick_tag(self,index):
        tag=self.tag_picker.itemData(index);self.tag_picker.setCurrentIndex(0);self.tag_picker.clearEditText()
        # The native completer can finish writing its text after activation.
        C.QTimer.singleShot(0,lambda:(self.tag_picker.setCurrentIndex(0),self.tag_picker.clearEditText())if not self.closing else None)
        if tag and tag not in self.active_tags:self.active_tags.append(tag);self.filters_changed()

    def clear_tag_filters(self):self.active_tags=[];self.filters_changed()

    def level_bounds(self):return self.min_level.value()or None,self.max_level.value()or None

    def filter_signature(self):return self.favorite_filter.isChecked(),tuple(self.active_tags),self.level_bounds()

    def level_changed(self,control):
        low,high=self.level_bounds()
        if low is not None and high is not None and low>high:
            other=self.max_level if control is self.min_level else self.min_level
            other.blockSignals(True);other.setValue(control.value());other.blockSignals(False)
        self.level_timer.start()

    def reset_filters(self):
        self.level_timer.stop();self.cancel_search();self.search.blockSignals(True);self.query.set('');self.search.clear();self.search.blockSignals(False)
        for control in(self.min_level,self.max_level):control.blockSignals(True);control.setValue(0);control.blockSignals(False)
        self.favorite_filter.setChecked(False);self.active_tags=[];self.tag_picker.setCurrentIndex(0);self.tag_picker.clearEditText();self.filters_changed()

    def filters_changed(self):
        self.level_timer.stop()
        self.tag_summary.setVisible(bool(self.active_tags));text=' + '.join(self.active_tags)
        self.tag_summary.setText(G.QFontMetrics(self.tag_summary.font()).elidedText('标签：'+text+'  ×',C.Qt.ElideRight,255))
        self.tag_summary.setToolTip('同时满足：'+text+'\n点击清除标签筛选');self.load_catalog()

    def refresh_library(self):
        self.refresh_tag_picker();self.library.refresh_annotations(self.rows,self.slot_key())
        for i,row in enumerate(self.rows):
            if i>=self.catalog.count():break
            item=self.catalog.item(i);item.setData(C.Qt.UserRole,row);item.setToolTip(self.equipment_tooltip(row))
        self.catalog.viewport().update()
        if self.outer:self.library.refresh_annotations(self.outer['rows'],self.slot_key())
        for choice in self.state['equipment'].values():
            if choice['item_id']:self.items[choice['item_id']]=self.library.item(choice['item_id'])
        self.update_outfit()

    def toggle_favorite(self,identifier):
        if not self.library:return
        try:
            row=self.library.item(identifier);self.library.set_favorite(identifier,not row['favorite']);self.refresh_library()
            self.message.set('已取消装备收藏；下次搜索或刷新时重新排序'if row['favorite']else'装备已收藏；下次搜索或刷新时优先显示')
        except Exception as error:self.message.set('收藏未能保存：'+str(error))

    def catalog_context(self,position):
        item=self.catalog.itemAt(position)
        if not item or not self.library:return
        menu=self.equipment_context_menu(item)
        try:menu.exec(self.catalog.viewport().mapToGlobal(position))
        finally:menu.deleteLater()

    def equipment_context_menu(self,item):
        row=item.data(C.Qt.UserRole);menu=W.QMenu(self.catalog)
        menu.addAction('查看详情 / 编辑标签',lambda:self.open_equipment_details(row))
        exact=self.library.item(row['item_id']);folder=row.get('is_group')and row.get('group_count',1)>1
        menu.addAction('复制装备名称',lambda:W.QApplication.clipboard().setText(exact['name']))
        text=('取消收藏'if exact['favorite']else'收藏装备')+('（目录代表款式）'if folder else'')
        menu.addAction(text,lambda:self.toggle_favorite(row['item_id']))
        if folder:menu.addAction('打开同模目录',lambda:self.activate_item(item))
        else:
            related=menu.addMenu('同套装备');menu.related_menus=[related]
            groups=self.library.related_equipment(row['item_id'])
            if groups:
                related.addAction('查看同套装备…',lambda:self.open_related_equipment(row['item_id']))
                related.addSeparator()
                for group in groups:
                    target=related.addMenu(group['name'])if len(groups)>1 else related
                    if target is not related:menu.related_menus.append(target)
                    if group.get('inferred'):
                        hint=target.addAction('名称关联候选，请确认款式');hint.setEnabled(False)
                    for member in group['items']:
                        if member['item_id']==row['item_id']:continue
                        action=target.addAction(self.icon(member.get('icon_id')),SLOTS[member['slot']]+' · '+member['name'])
                        action.triggered.connect(lambda _=False,m=member:self.try_equipment_rows([m]))
            else:
                action=related.addAction('未找到同套装备');action.setEnabled(False)
        # Keep submenu QAction wrappers alive alongside their QMenus in PySide.
        menu.related_actions=[child.menuAction()for child in getattr(menu,'related_menus',[])]
        return menu

    def outfit_context(self,slot,position):
        identifier=self.state['equipment'][slot]['item_id']
        if not identifier or not self.library:return
        item=W.QListWidgetItem();item.setData(C.Qt.UserRole,self.library.item(identifier))
        menu=self.equipment_context_menu(item)
        try:menu.exec(self.outfit_cards[slot].mapToGlobal(position))
        finally:menu.deleteLater()

    def open_related_equipment(self,identifier):
        groups=self.library.related_equipment(identifier)
        if not groups:self.message.set('未找到这件装备的同套关联');return
        return self.show_dialog(RelatedEquipmentDialog(self,identifier,groups))

    def try_equipment_rows(self,rows):
        if not self.ready or self.closing:return
        slots=[row['slot']for row in rows]
        if len(set(slots))!=len(slots):self.message.set('此候选有同部位的多件装备，请逐件选择试穿');return
        for row in rows:
            slot=row['slot']
            if slot not in SLOTS:continue
            self.items[row['item_id']]=row;self.hidden_equipment.pop(slot,None)
            if self.state['equipment'][slot]['item_id']!=row['item_id']:
                self.state['equipment'][slot]={'item_id':row['item_id'],'first':0,'second':0}
        self.update_outfit();self.queue_render()

    def open_equipment_details(self,row):
        if not self.library:return
        dialog=EquipmentDetailsDialog(self,row);self.show_dialog(dialog);return dialog

    def sync_pagination(self):
        self.page_number.blockSignals(True);self.page_number.setRange(1,self.page_count);self.page_number.setValue(self.page_index+1);self.page_number.blockSignals(False)
        self.page_total.setText('/ '+str(self.page_count));available=not self.catalog_loading
        self.previous_page.setEnabled(available and self.page_index>0)
        self.next_page.setEnabled(available and self.page_index+1<self.page_count)
        self.page_number.setEnabled(available and self.page_count>1)

    def go_page(self,page):
        if self.catalog_loading or page==self.page_index or not 0<=page<self.page_count:return
        self.load_catalog(page=page)

    def load_catalog(self,append=False,*,page=None,restore=None):
        self.cancel_search()
        self.level_timer.stop()
        if not self.boot or self.closing:return
        new_query=page is None and not append
        page=(restore.get('page',0)if restore else 0)if new_query else self.page_index+1 if append else page
        if not new_query and(self.catalog_loading or not 0<=page<self.page_count):return
        self.catalog_serial+=1;self.catalog_loading=True;self.catalog.setEnabled(False);self.catalog_spinner.set_running(True)
        self.sync_pagination()
        self.count_note.setText('正在检索装备…')
        request={'serial':self.catalog_serial,'slot':self.slot_key(),'query':self.query.get(),'offset':page*60,'page':page,'new_query':new_query,
            'grouped':self.folder is None,'model_set':self.folder,'library':self.library,'favorites_only':self.favorite_filter.isChecked(),
            'tags':tuple(self.active_tags),'min_level':self.level_bounds()[0],'max_level':self.level_bounds()[1],
            'favorite_snapshot':None if new_query else getattr(self,'favorite_snapshot',None),'restore':restore}
        if self.catalog_job is not None:self.catalog_queued=request
        else:self.start_catalog_request(request)

    def fetch_catalog(self,request):
        snapshot=request['favorite_snapshot']
        if request['new_query']and request['library']:snapshot=request['library'].favorite_ids()
        result=equipment_rows(request['slot'],request['query'],request['offset'],grouped=request['grouped'],model_set=request['model_set'],
            library=request['library'],favorites_only=request['favorites_only'],tags=request['tags'],favorite_snapshot=snapshot,
            min_level=request['min_level'],max_level=request['max_level'])
        return result,snapshot

    def start_catalog_request(self,request):
        self.catalog_job=(self.catalog_pool.submit(self.fetch_catalog,request),request)
        self.catalog_timer=self.root.after(20,self.poll_catalog)

    def poll_catalog(self):
        self.catalog_timer=None
        if self.closing:return
        future,request=self.catalog_job
        if not future.done():self.catalog_timer=self.root.after(20,self.poll_catalog);return
        self.catalog_job=None
        try:
            result,snapshot=future.result()
            if request['serial']==self.catalog_serial:self.apply_catalog_result(result,snapshot,request)
        except Exception as error:
            if request['serial']==self.catalog_serial:
                self.catalog_loading=False;self.catalog_spinner.set_running(False);self.catalog.setEnabled(True)
                self.catalog.clear();self.rows=[];self.offset=0;self.more=False;self.page_index=0;self.page_count=1;self.catalog_total=0;self.sync_pagination()
                self.count_note.setText('检索未完成，请点击刷新重试');self.message.set('装备检索未能完成：'+str(error))
        if self.catalog_queued is not None:
            latest=self.catalog_queued;self.catalog_queued=None;self.start_catalog_request(latest)

    def apply_catalog_result(self,result,snapshot,request):
        self.favorite_snapshot=snapshot;self.catalog_total=result['total'];self.page_count=max(1,(self.catalog_total+59)//60)
        if request['page']>=self.page_count:
            self.catalog_loading=False;self.load_catalog(page=self.page_count-1,restore=request['restore']);return
        self.page_index=request['page'];self.rows=list(result['items']);self.offset=request['offset']+len(self.rows);self.more=result['more']
        self.items.update({r['item_id']:r for r in self.rows})
        self.populate_catalog(self.rows);self.back_button.setVisible(self.folder is not None)
        text='全部装备'if self.folder is None else self.outer['name'];self.breadcrumb.setText(text);self.breadcrumb.setToolTip(text)
        self.search.setPlaceholderText('名称、编号、标签 · 多词组合'if self.folder is None else'搜索此目录的款式或标签')
        self.count_note.setText(f"{result['total']:,} "+('项 · 收藏优先 · 右键查看详情'if self.folder is None else'款装备 · 收藏优先'))
        self.catalog.verticalScrollBar().setValue(0)
        self.catalog_loading=False;self.catalog_spinner.set_running(False);self.catalog.setEnabled(True)
        self.sync_pagination()
        self.update_outfit()
        restore=request['restore']
        if restore:
            self.catalog.doItemsLayout();self.catalog.verticalScrollBar().setValue(restore['scroll'])

    def activate_item(self,item):
        if not self.ready:return
        row=item.data(C.Qt.UserRole)
        if row.get('is_group')and row.get('group_count',1)>1:
            self.outer={'query':self.query.get(),'rows':list(self.rows),'offset':self.offset,'more':self.more,'scroll':self.catalog.verticalScrollBar().value(),
                'name':row['name'],'count':self.count_note.text(),'page':self.page_index,'pages':self.page_count,'total':self.catalog_total,
                'filters':self.filter_signature(),'favorite_snapshot':self.favorite_snapshot}
            self.folder=row['model_set'];self.query.set('');self.load_catalog();return
        self.state['equipment'][self.slot_key()]={'item_id':row['item_id'],'first':0,'second':0};self.update_outfit();self.queue_render()

    def back_folder(self):
        if self.folder is None or not self.outer:return
        record=self.outer;self.folder=None;self.outer=None;self.query.set(record['query']);self.cancel_search()
        rebuild=record.get('filters')!=self.filter_signature()
        if rebuild:
            self.load_catalog(restore=record);return
        else:
            self.catalog_serial+=1;self.catalog_queued=None;self.catalog_loading=False;self.catalog_spinner.set_running(False);self.catalog.setEnabled(True)
            self.rows=record['rows'];self.offset=record['offset'];self.more=record['more'];self.favorite_snapshot=record['favorite_snapshot'];self.populate_catalog(self.rows)
            self.page_index=record['page'];self.page_count=record['pages'];self.catalog_total=record['total'];self.sync_pagination()
        self.catalog.doItemsLayout();self.catalog.verticalScrollBar().setValue(record['scroll'])
        self.breadcrumb.setText('全部装备');self.back_button.hide()
        if not rebuild:self.count_note.setText(record['count'])
        self.search.setPlaceholderText('名称、编号、标签 · 多词组合');self.update_outfit()

    def head_toggled(self,checked):self.show_head.set(checked);self.toggle_head()

    def effects_toggled(self,checked):
        if not checked:self.pause_effects()
        self.state['effects_visible']=checked;self.effects_play.setEnabled(self.ready and checked);self.queue_render()

    def request_present(self):
        if self.closing or not self.ready or not self.viewport.isVisible()or self.window.isMinimized():return
        self.present_requested=True
        if self.present_timer is None and self.present_future is None and self.job is None and self.effect_future is None:
            self.present_timer=self.root.after(0,self.pump_present)

    def pump_present(self):
        self.present_timer=None
        if self.closing or not self.ready or not self.present_requested:return
        if not self.viewport.isVisible()or self.window.isMinimized():return
        if self.present_future is not None or self.job is not None or self.effect_future is not None:return
        self.present_requested=False;self.viewport.activate_native()
        def present():
            surface=self.engine.renderer.native_surface
            surface.present_enabled=True
            shown=surface.recover()
            return shown,surface.last_present_status==0x087a0001
        self.present_future=self.engine.pool.submit(present)
        self.present_future.add_done_callback(self.present_delivery.finished.emit)

    def present_completed(self,future):
        if future is not self.present_future:return
        self.present_future=None
        try:
            shown,occluded=future.result()
            if occluded and not self.closing:self.occlusion_timer.start()
            else:self.occlusion_timer.stop()
        except Exception as error:
            self.occlusion_timer.stop();self.message.set('预览画面未能恢复：'+str(error))
        if self.present_requested:self.request_present()

    def pause_effects(self):
        self.effect_timer.stop();self.effect_clock.pause(self.effect_last_frame)
        self.effects_play.setChecked(False);self.effects_play.setText('▶ 播放特效')

    def toggle_effect_playback(self):
        if not self.effects_play.isChecked():self.pause_effects();return
        if not self.ready or not self.state['effects_visible']:self.pause_effects();return
        self.effect_clock.play();self.effects_play.setText('Ⅱ 暂停特效');self.effect_timer.start()

    def pump_render(self):
        if self.effect_future is not None:return
        super().pump_render()

    def render_effect_frame(self):
        return self.effect_clock.sample()if self.effect_clock.playing and self.state['effects_visible']else None

    def effect_frame_presented(self,result):
        if 'effect_frame'not in result:return
        self.effect_last_frame=result['effect_frame']
        if not self.effect_clock.playing:self.effect_clock.pause(self.effect_last_frame)

    def tick_effects(self):
        if(self.closing or not self.ready or not self.effect_clock.playing or not self.state['effects_visible']
            or self.job is not None or self.effect_future is not None or self.dirty or self.dialogs
            or self.window.isMinimized()or not self.window.isVisible()):return
        if not self.engine.last or not self.engine.last.get('equipment_effects'):return
        frame=self.effect_clock.sample();state=deepcopy(self.state)
        self.effect_started_at=time.perf_counter()
        self.effect_future=self.engine.pool.submit(self.engine.render,state,effect_frame=frame)
        self.effect_future.add_done_callback(self.effect_delivery.finished.emit)

    def effect_completed(self,future):
        if future is self.effect_future:self.poll_effects()

    def poll_effects(self):
        self.effect_poll=None
        if self.effect_future is None:return
        if not self.effect_future.done():self.effect_poll=self.root.after(10,self.poll_effects);return
        future=self.effect_future;self.effect_future=None
        try:
            result=future.result();self.effect_frame_presented(result)
        except Exception as error:
            self.pause_effects();self.message.set('特效播放未能完成，已暂停。'+str(error))
        if self.closing:return
        if self.effect_deferred is not None:
            fn,callback,arguments,baseline,generation=self.effect_deferred;self.effect_deferred=None
            if self.submit(fn,callback,*arguments)and self.job is not None:
                self.job=(self.job[0],self.job[1],generation,baseline)
        elif self.dirty:self.pump_render()
        elif self.effect_clock.playing and self.effects_play.isChecked():
            # Keep the 30Hz presentation cap, but don't wait for another
            # periodic tick after a slow frame has already exceeded it.
            elapsed=(time.perf_counter()-self.effect_started_at)*1000
            self.root.after(max(0,round(1000/30-elapsed)),self.tick_effects)
        if self.present_requested:self.request_present()

    def select_preset(self,index):
        if index>=0 and self.boot:self.submit(self.engine.run,self.accept_result,'preset',{'id':self.preset_ids[index]})

    def open_character_manager(self):
        dialog=self.character_manager;dialog.update_state()
        if dialog not in self.dialogs:self.dialogs.append(dialog)
        if self.window.testAttribute(C.Qt.WA_DontShowOnScreen):dialog.setAttribute(C.Qt.WA_DontShowOnScreen)
        dialog.show();dialog.raise_()

    def delete_selected_preset(self):
        if not self.boot or self.job is not None:return
        record=next((p for p in self.boot['presets']if p['id']==self.preset_combo.currentData()),None)
        if record is None:return
        if len(self.boot['presets'])<2:return self.message.set('请至少保留一份角色预设')
        if self.editor:return self.message.set('请先关闭外观编辑窗口，再删除预设')
        message='删除“'+record['name']+'”（'+record['file_name']+'）？\n只删除软件内的预设副本，原始导入文件不受影响。此操作不可撤销。'
        if record['id']==self.boot['preset_id']:message+='\n当前角色会先切换到另一份预设；切换失败时不会删除。'
        if record['id']==self.boot['preset_id']and self.boot.get('appearance',{}).get('is_preview'):message+='\n当前尚未保存的外观修改会随切换丢弃。'
        if W.QMessageBox.question(self.character_manager,'删除角色预设',message,W.QMessageBox.Yes|W.QMessageBox.No,W.QMessageBox.No)!=W.QMessageBox.Yes:return
        def done(result):
            if result['switched']:self.accept_result(result)
            else:self.refresh_boot();self.schedule_session_save()
            self.message.set('预设已删除，原始导入文件未改动'if result['deleted']else'已切换角色，但预设未删除：'+result['delete_error'])
        self.submit(self.engine.run,done,'delete_preset',{'id':record['id'],'sha256':record['sha256']})

    def preset_import_directory(self):
        if any((self.game/name).is_file()for name in('network-source.json','resource-pack.json')):return ''
        directory=self.game/'game/My Games/FINAL FANTASY XIV - A Realm Reborn'
        return str(directory if directory.is_dir()else self.game)

    def import_preset(self):
        if self.engine is None or not self.engine.ready.done():return self.message.set('正在加载角色，请稍候…')
        if self.job:return self.message.set('请等待当前模型准备完成')
        parent=self.character_manager if self.character_manager.isVisible()else self.window
        name,_=W.QFileDialog.getOpenFileName(parent,'导入角色外观预设',self.preset_import_directory(),'角色预设 (*.dat)')
        if not name:return
        try:raw=Path(name).read_bytes()
        except OSError as error:return self.message.set(str(error))
        def done(result):
            self.refresh_boot()
            if result['model_loaded']:self.accept_result(result)
            else:self.message.set('预设文件已导入，模型暂时无法加载：'+result['preview_error']);self.open_editor(result['preset']['id'])
        self.submit(self.engine.run,done,'import',{'name':Path(name).name,'data':base64.b64encode(raw).decode()})

    def create_preset(self):
        if self.engine is None or not self.engine.ready.done():return self.message.set('正在加载角色，请稍候…')
        if self.job:return self.message.set('请等待当前模型准备完成')
        from builtin_presets import CREATION_PROFILES
        from cn_character import CLANS
        parent=self.character_manager if self.character_manager.isVisible()else self.window
        dialog=W.QDialog(parent);dialog.setWindowTitle('新建角色预设');dialog.setMinimumWidth(420);dialog.setWindowModality(C.Qt.WindowModal)
        outer=vertical(dialog,22,14);outer.addWidget(label('新建角色预设','Title'))
        outer.addWidget(label('种族和性别创建后固定，其他外观可在创建后编辑。','Muted'))
        form=W.QFormLayout();profile=ComboBox();tribe=ComboBox();name=W.QLineEdit();name.setPlaceholderText('留空使用默认名称')
        reversed_profiles=tuple(reversed(CREATION_PROFILES))
        race_order=dict.fromkeys(p['race'] for p in reversed_profiles)
        by_id={p['id']:p for p in CREATION_PROFILES}
        for race in race_order:
            for p in reversed_profiles:
                if p['race']==race:profile.addItem(p['label'],p['id'])
        def update_clans(index=None):
            tribe.clear()
            for value in by_id[profile.currentData()]['tribes']:tribe.addItem(CLANS[value],value)
        profile.currentIndexChanged.connect(update_clans);update_clans()
        form.addRow('角色类型',profile);form.addRow('部族',tribe);form.addRow('预设名称',name);outer.addLayout(form)
        actions=horizontal(spacing=8);actions.addStretch();actions.addWidget(button('取消',dialog.reject,ghost=True))
        def create():
            payload={'profile':profile.currentData(),'tribe':tribe.currentData(),'name':name.text().strip()}
            dialog.accept()
            def done(result):
                self.refresh_boot()
                if result['model_loaded']:self.accept_result(result)
                else:self.message.set('预设已新建，模型暂时无法加载：'+result['preview_error']);self.open_editor(result['preset']['id'])
            self.submit(self.engine.run,done,'create',payload)
        actions.addWidget(button('创建并预览',create,primary=True));outer.addLayout(actions);return self.show_dialog(dialog)

    def palette(self,title,choices,selected,callback,*,dye_sort=False):
        parent=self.editor if self.editor and self.editor.isVisible()else self.window
        dialog=W.QDialog(parent);dialog.setWindowTitle(title);dialog.resize(550,610);dialog.setMinimumSize(460,460);dialog.setWindowModality(C.Qt.WindowModal)
        outer=vertical(dialog,22,14);outer.addWidget(label(title,'Title'))
        name=label('选择颜色，立即预览','Muted');outer.addWidget(name)
        choices=list(choices);defaults=list(choices)
        if dye_sort:
            layout=paths();order_path=no_links(layout.user/'dye-order.json',layout.root)
            try:
                with order_path.open('rb')as file:record=json.loads(file.read(4097))
                order=record['order']
                if type(record['version'])is not int or record['version']!=1 or not isinstance(order,list)or len(order)>255 or any(type(v)is not int or not 0<=v<=254 for v in order)or len(set(order))!=len(order):raise ValueError('Invalid dye order')
                rank={v:i for i,v in enumerate(order)}
                choices.sort(key=lambda c:(c['value']!=0,rank.get(c['value'],len(rank)+defaults.index(c))))
            except(OSError,ValueError,KeyError,TypeError,RecursionError):pass
            bar=horizontal();dialog.sort_button=button('自定义排序');dialog.sort_button.setCheckable(True);bar.addWidget(dialog.sort_button)
            dialog.reset_order_button=button('恢复默认顺序');bar.addWidget(dialog.reset_order_button);bar.addStretch();outer.addLayout(bar)

        search=W.QLineEdit();search.setPlaceholderText('搜索名称');search.setClearButtonEnabled(True);outer.addWidget(search)
        scroll=W.QScrollArea();scroll.setWidgetResizable(True);scroll.setHorizontalScrollBarPolicy(C.Qt.ScrollBarAlwaysOff)
        content=W.QWidget();grid=W.QGridLayout(content);grid.setSpacing(3);grid.setAlignment(C.Qt.AlignTop|C.Qt.AlignLeft);scroll.setWidget(content);outer.addWidget(scroll,1)
        dialog.icon_buttons={}
        def refresh_icons(changed):
            for identifier in changed:
                for control in dialog.icon_buttons.get(identifier,[]):control.setIcon(self.icon(identifier))
        dialog.refresh_icons=refresh_icons
        def save_order(rows):
            order_path.parent.mkdir(parents=True,exist_ok=True);no_links(order_path,layout.root)
            file=C.QSaveFile(str(order_path));file.setDirectWriteFallback(False)
            if not file.open(C.QIODevice.WriteOnly):raise OSError(file.errorString())
            raw=(json.dumps({'version':1,'order':[c['value']for c in rows]},ensure_ascii=False)+'\n').encode('utf8')
            if file.write(raw)!=len(raw):
                error=file.errorString();file.cancelWriting();raise OSError(error)
            if not file.commit():raise OSError(file.errorString())

        def move_color(source,target):
            if not dye_sort or not dialog.sort_button.isChecked()or source==target or not source or not target:return
            moved=next((c for c in choices if c['value']==source),None)
            if moved is None or not any(c['value']==target for c in choices):return
            rows=[c for c in choices if c['value']!=source];at=next(i for i,c in enumerate(rows)if c['value']==target)
            # Move before an earlier target, or after a later target.
            if next(i for i,c in enumerate(choices)if c['value']==source)<next(i for i,c in enumerate(choices)if c['value']==target):at+=1
            rows.insert(at,moved)
            try:save_order(rows)
            except(OSError,ValueError)as error:name.setText('排序未保存：'+str(error));return
            choices[:]=rows
            # Keep the active QDrag source alive: move existing widgets only.
            widgets={}
            while grid.count():
                item=grid.takeAt(0);widget=item.widget()
                if widget is not None:widgets[widget.value]=widget
            for i,c in enumerate(choices):grid.addWidget(widgets[c['value']],i//12,i%12)
            name.setText('顺序已保存；可继续拖动，原色固定在首位。')

        def render(text=''):

            dialog.icon_buttons={}
            while grid.count():
                item=grid.takeAt(0)
                if item.widget():item.widget().hide();item.widget().deleteLater()
            rows=[choice for choice in choices if text in choice['label']]
            for i,choice in enumerate(rows):
                color=choice.get('color','#333b48');b=DraggableColorButton(choice['value'],str(id(dialog)),move_color)if dye_sort else button('')
                if dye_sort:
                    b.sorting=dialog.sort_button.isChecked();b.setCursor(C.Qt.OpenHandCursor if b.sorting and b.value else C.Qt.PointingHandCursor)
                border='2px solid #d2fff5'if choice['value']==selected else'1px solid #58626c'
                b.setFixedSize(28,28);b.setToolTip(choice['label']);b.setAccessibleName(choice['label'])
                b.setStyleSheet(f'QPushButton {{ background:{color};border:{border};border-radius:0;padding:0; }} QPushButton:hover {{ border:2px solid white; }}')
                def pick(c=choice):
                    if dye_sort and dialog.sort_button.isChecked():return
                    callback(c['value']);dialog.accept()
                b.clicked.connect(lambda _=False,p=pick:p());b.enterEvent=lambda e,c=choice:name.setText(c['label']);grid.addWidget(b,i//12,i%12)
            grid.setRowStretch((len(rows)+11)//12,1)
        if dye_sort:
            def sorting(active):
                search.clear();search.setEnabled(not active);dialog.sort_button.setText('完成排序'if active else'自定义排序')
                name.setText('拖动色块调整顺序，自动保存；原色固定在首位。'if active else'选择颜色，立即预览');render()
            def reset_order():
                try:save_order(defaults)
                except(OSError,ValueError)as error:name.setText('排序未保存：'+str(error));return
                choices[:]=defaults
                if dialog.sort_button.isChecked():dialog.sort_button.setChecked(False)
                else:render()
                name.setText('已恢复默认顺序。')
            dialog.sort_button.toggled.connect(sorting);dialog.reset_order_button.clicked.connect(reset_order)
        search.textChanged.connect(render);render();return self.show_dialog(dialog)

    def dye_icon(self,stain):
        identifier=self.library.dye_icon(stain)if self.library and stain else None
        return self.icon(identifier)if identifier else G.QIcon()

    def open_dyes(self,slot,channel):
        choice=self.state['equipment'][slot];item=self.items.get(choice['item_id'],{})
        if item.get('dye_count',0)<channel:return
        colors=[{'value':0,'label':'原色','color':'#333b48'}]+[{'value':d['stain_id'],'label':d['name'],'color':'#%06x'%(d['color']&0xffffff),'icon_id':self.library.dye_icon(d['stain_id'])if self.library else None}for d in self.boot['dyes']if d['stain_id']]
        item_id=choice['item_id']
        def pick(value):
            if self.state['equipment'][slot]['item_id']!=item_id:return
            self.state['equipment'][slot]['first'if channel==1 else'second']=value;self.update_outfit();self.queue_render()
        self.palette(SLOTS[slot]+f' · 第{channel}染色区',colors,choice['first'if channel==1 else'second'],pick,dye_sort=True)

    def open_editor(self,key=None):
        if not self.boot or self.job:return
        if self.editor and self.editor.isVisible():self.editor.raise_();return
        key=key or self.boot['preset_id']
        def done(record):
            self.editor_record=record;self.draft=deepcopy(record['appearance']['rendered_parameters']if record['appearance']['is_preview']else record['parameters'])
            self.editor_menu_tribe=None
            self.editor=AppearanceDialog(self);self.show_dialog(self.editor)
        self.submit(self.engine.describe_appearance,done,key)

    def editor_change(self,option,value):
        field=option['field'];self.draft[field]=(self.draft[field]&~option['mask'])|value
        if field=='face'and self.editor:
            from cn_barber_hair import hair_compatible
            hair=next(o for o in self.editor_record['fields']if o['field']=='hair')
            current=next((choice for choice in hair['choices']if choice['value']==self.draft['hair']),None)
            if current is not None and not hair_compatible(current,self.draft):
                self.draft['hair']=next(choice['value']for choice in hair['choices']if hair_compatible(choice,self.draft))
            self.editor.build_fields()
        if field=='tribe':
            self.editor_menu_tribe=value
        self.editor.update_status()
        if field=='tribe':self.editor.preview_timer.start(120)
        else:self.queue_appearance_preview()

    def queue_appearance_preview(self):
        if self.editor and self.editor.realtime.isChecked():self.editor.preview_timer.start(120)

    def flush_appearance_preview(self):
        if not self.editor or not self.editor.isVisible():return
        if self.job is not None:self.editor.preview_timer.start(80);return
        tribe=getattr(self,'editor_menu_tribe',None)
        if tribe is not None:
            self.editor_menu_tribe=None
            def done(record):
                if self.editor and self.draft['tribe']==tribe:
                    self.editor_record['fields']=record['fields'];self.editor.build_fields()
                self.queue_appearance_preview()
            self.submit(self.engine.describe_appearance,done,self.editor_record['id'],tribe);return
        if not self.editor.realtime.isChecked():return
        if self.engine.active_preset==self.editor_record['id']and self.draft==self.engine.rendered_parameters:return
        self.submit_editor(True,live=True)

    def submit_editor(self,preview,*,live=False):
        if self.job:return
        record=self.editor_record;draft=deepcopy(self.draft)
        payload={'id':record['id'],'sha256':record['sha256'],'changes':{k:v for k,v in draft.items()if v!=record['parameters'][k]}}
        def done(result):
            self.refresh_boot()
            if preview:
                self.accept_result(result)
                if self.editor:
                    self.editor.set_busy(False);self.editor.update_status()
                    if self.draft!=draft:self.queue_appearance_preview()
            else:
                record['parameters']=deepcopy(result['preset']['parameters']);record['sha256']=result['preset']['sha256']
                self.editor.update_status();self.schedule_session_save()
                self.message.set('已保存预设，模型保持原样'if result.get('cache_ready',True)else'预设已保存，缓存准备未完成：'+str(result.get('cache_error','')))
        self.editor.status.setText('正在更新角色…'if preview else'正在保存预设…')
        self.editor.set_busy(True,live=live)
        self.live_edit_job=live
        self.submit(self.engine.run,done,'appearance_preview'if preview else'appearance_save',payload)

    def save_outfit(self):
        if not self.ready or self.job:return
        name,ok=W.QInputDialog.getText(self.window,'保存模版','给这套模版起个名字')
        if ok and name.strip():
            try:self.engine.save({'name':name,'state':self.good});self.message.set('模版已保存')
            except Exception as error:self.message.set(str(error))

    def saved_dialog(self):
        if not self.boot:return
        from qt_saved_outfits import SavedOutfitsDialog
        self.show_dialog(SavedOutfitsDialog(self))


class CharacterManagerDialog(W.QDialog):
    def __init__(self,controller):
        super().__init__(controller.window);self.c=controller;self.last_active=None
        self.setWindowTitle('角色管理');self.resize(560,390);self.setMinimumWidth(480)
        outer=vertical(self,22,14);heading=horizontal();heading.addWidget(label('角色管理','Title'));heading.addStretch()
        self.count=label('','Badge');heading.addWidget(self.count);outer.addLayout(heading)
        self.current=label('正在加载角色…','Muted');self.current.setWordWrap(True);outer.addWidget(self.current)
        self.preset_combo=PresetComboBox();self.preset_combo.setMinimumWidth(300)
        self.preset_combo.setToolTip('输入名称或文件名搜索预设，选择后点击“使用此角色”切换')
        self.preset_combo.activated.connect(self.update_state);outer.addWidget(self.preset_combo)
        self.info=label('','Muted');self.info.setWordWrap(True);self.info.setMinimumHeight(60);outer.addWidget(self.info)
        use=horizontal();self.use_button=button('使用此角色',lambda:controller.select_preset(self.preset_combo.currentIndex()),primary=True)
        self.edit_button=button('编辑外观',lambda:controller.open_editor(self.preset_combo.currentData()))
        use.addWidget(self.use_button,1);use.addWidget(self.edit_button,1);outer.addLayout(use)
        actions=horizontal();self.new_button=button('新建预设',controller.create_preset);self.import_button=button('导入预设',controller.import_preset)
        self.delete_button=button('删除预设',controller.delete_selected_preset)
        self.delete_button.setStyleSheet('QPushButton {color:#edaaa6;} QPushButton:disabled {color:#586577;}')
        for b in(self.new_button,self.import_button,self.delete_button):actions.addWidget(b,1)
        outer.addLayout(actions)
        self.status=label('','Warning');self.status.setWordWrap(True);outer.addWidget(self.status);outer.addStretch()
        footer=horizontal();footer.addStretch();footer.addWidget(button('关闭',self.reject,ghost=True));outer.addLayout(footer)
        def finished(_):
            if self in controller.dialogs:controller.dialogs.remove(self)
        self.finished.connect(finished)

    def update_state(self,*_):
        c=self.c;boot=c.boot or{};records=boot.get('presets',[]);key=self.preset_combo.currentData()
        selected=next((p for p in records if p['id']==key),None)
        active=next((p for p in records if p['id']==boot.get('preset_id')),None)
        self.count.setText(str(len(records))+' 份预设');self.current.setText('当前使用：'+active['name']if active else'正在加载角色…')
        if selected:
            p=selected['parameters'];sex='女性'if p['gender']else'男性'
            self.info.setText(selected['file_name']+'\n'+selected['clan']+' · '+sex+' · 发型 '+str(p['hair'])+(' · 正在使用'if selected is active else''))
        else:self.info.setText('选择要使用、编辑或删除的角色预设。')
        engine=getattr(c,'engine',None);available=bool(engine is not None and engine.ready.done()and boot)
        busy=c.job is not None or c.editor is not None
        self.preset_combo.setEnabled(available and not busy)
        for b in(self.new_button,self.import_button):b.setEnabled(available and not busy)
        for b in(self.use_button,self.edit_button):b.setEnabled(available and not busy and selected is not None)
        self.delete_button.setEnabled(available and not busy and selected is not None and len(records)>1)
        self.delete_button.setToolTip('至少保留一份角色预设'if len(records)<2 else'删除软件内的预设副本，原始导入文件不受影响')
        self.status.setText('请先关闭外观编辑窗口，再切换或删除角色。'if c.editor else'正在处理，请稍候…'if c.job is not None else
            '当前角色的外观修改尚未保存；请在“编辑外观”中保存。'if boot.get('appearance',{}).get('is_preview')else'')


class RelatedEquipmentDialog(W.QDialog):
    def __init__(self,controller,identifier,groups):
        super().__init__(controller.window);self.c=controller;self.groups=groups;self.identifier=identifier
        self.setWindowTitle('同套装备');self.resize(470,640);self.setMinimumSize(390,410)
        outer=vertical(self,16,10);outer.addWidget(label('同套装备','Title'))
        self.picker=ComboBox()
        for group in groups:self.picker.addItem(group['name'])
        outer.addWidget(self.picker);self.note=label('','Muted');self.note.setWordWrap(True);outer.addWidget(self.note)
        self.list=W.QListWidget();self.list.setMouseTracking(True);self.list.setItemDelegate(EquipmentDelegate(self.list,use_item_selection=True))
        self.list.setSelectionMode(W.QAbstractItemView.SingleSelection)
        self.list.setVerticalScrollMode(W.QAbstractItemView.ScrollPerPixel);outer.addWidget(self.list,1)
        actions=horizontal();self.try_one=button('试穿所选',self.apply_one,primary=True);actions.addWidget(self.try_one)
        self.try_all=button('试穿这一套',self.apply_all);actions.addWidget(self.try_all);actions.addStretch()
        actions.addWidget(button('关闭',self.accept,ghost=True));outer.addLayout(actions)
        self.picker.currentIndexChanged.connect(self.populate)
        self.list.currentItemChanged.connect(lambda current,_:self.try_one.setEnabled(current is not None))
        self.populate(0)

    def populate(self,index):
        group=self.groups[index];self.list.clear()
        self.note.setText('按名称、模型、职业和等级找到的候选，请确认款式。'if group.get('inferred')else'游戏原生套装关联。先选中装备，再点击“试穿所选”替换对应部位。')
        rows=sorted(group['items'],key=lambda r:(list(SLOTS).index(r['slot']),r['item_id']))
        for row in rows:
            item=W.QListWidgetItem(row['name']);item.setData(C.Qt.UserRole,row);item.setIcon(self.c.icon(row.get('icon_id')))
            item.setToolTip(self.c.equipment_tooltip(row));self.list.addItem(item)
            if row['item_id']==self.identifier:self.list.setCurrentItem(item)
        self.try_one.setEnabled(self.list.currentItem()is not None)
        self.try_all.setEnabled(len({r['slot']for r in rows})==len(rows))
        self.try_all.setToolTip('只替换本套包含的部位，保留其他装备和当前角色。'if self.try_all.isEnabled()else'有多个同部位候选，请单件试穿。')

    def refresh_icons(self,changed):
        for i in range(self.list.count()):
            item=self.list.item(i);identifier=item.data(C.Qt.UserRole).get('icon_id')
            if identifier in changed:item.setIcon(self.c.icon(identifier))

    def apply_one(self):
        item=self.list.currentItem()
        if item:self.c.try_equipment_rows([item.data(C.Qt.UserRole)])

    def apply_all(self):self.c.try_equipment_rows(self.groups[self.picker.currentIndex()]['items'])


class EquipmentDetailsDialog(W.QDialog):
    def __init__(self,controller,row):
        super().__init__(controller.window);self.c=controller;self.item_id=row['item_id'];self.setWindowTitle('装备详情与标签');self.resize(560,650)
        outer=vertical(self,22,14);heading=horizontal();self.icon_label=label();self.icon_label.setFixedSize(64,64);heading.addWidget(self.icon_label)
        names=vertical(spacing=4);self.name=label('','Title');self.name.setWordWrap(True);names.addWidget(self.name);self.info=label('','Muted');self.info.setWordWrap(True);names.addWidget(self.info)
        heading.addLayout(names,1);outer.addLayout(heading)
        self.member_picker=ComboBox();outer.addWidget(self.member_picker)
        self.member_picker.setSizeAdjustPolicy(W.QComboBox.AdjustToMinimumContentsLengthWithIcon);self.member_picker.setMinimumContentsLength(12)
        self.favorite_button=button('☆ 收藏装备',self.toggle_favorite);self.favorite_button.setCheckable(True)
        self.favorite_button.setToolTip('独立保存收藏状态，不会保存或丢弃正在编辑的标签');outer.addWidget(self.favorite_button)
        outer.addWidget(label('装备标签','Section'))
        hint=label('每件装备可添加多个标签，用逗号分隔。自动标签也可以修改或删除。','Muted');hint.setWordWrap(True);outer.addWidget(hint)
        self.tag_edit=W.QPlainTextEdit();self.tag_edit.setPlaceholderText('例如：礼服，优雅，我的常用模版');self.tag_edit.setMaximumHeight(110);outer.addWidget(self.tag_edit)
        outer.addWidget(label('当前标签（点标签进行检索）','Muted'))
        tag_scroll=W.QScrollArea();tag_scroll.setWidgetResizable(True);tag_scroll.setHorizontalScrollBarPolicy(C.Qt.ScrollBarAlwaysOff);tag_scroll.setMinimumHeight(85);tag_scroll.setMaximumHeight(140)
        self.tag_buttons=W.QWidget();self.tag_layout=W.QGridLayout(self.tag_buttons);self.tag_layout.setContentsMargins(0,0,0,0);self.tag_layout.setSpacing(6)
        tag_scroll.setWidget(self.tag_buttons);outer.addWidget(tag_scroll)
        self.origin_note=label('','Muted');self.origin_note.setWordWrap(True);outer.addWidget(self.origin_note)
        self.error=label('','Warning');self.error.setWordWrap(True);outer.addWidget(self.error);outer.addStretch()
        actions=horizontal();actions.addWidget(button('关闭',self.reject,ghost=True));actions.addStretch();actions.addWidget(button('保存标签',self.save,primary=True));outer.addLayout(actions)
        exact=controller.library.item(self.item_id);members=controller.library.members(exact['slot'],exact['model_set'])
        for member in members:self.member_picker.addItem(member['name']+' · #'+str(member['item_id']),member['item_id'])
        self.member_picker.setCurrentIndex(next(i for i,m in enumerate(members)if m['item_id']==self.item_id));self.member_picker.setVisible(len(members)>1)
        self.member_picker.setToolTip('同模目录内选择具体装备，每件的标签和收藏独立保存')
        self.member_picker.activated.connect(lambda index:self.load_item(self.member_picker.itemData(index)));self.load_item(self.item_id)

    def load_item(self,identifier):
        self.item_id=identifier;row=self.c.library.item(identifier);self.icon_id=row['icon_id'];self.name.setText(row['name']);self.icon_label.setPixmap(self.c.icon(row['icon_id']).pixmap(60,60))
        self.info.setText(f"#{identifier}  ·  {SLOTS.get(row['slot'],row['slot'])}  ·  Lv.{row['level']}  ·  {row['dye_count']} 个染色区\n模型 {row['model_set']}  ·  {row['category_name']}")
        self.update_favorite_button(row['favorite']);self.tag_edit.setPlainText('，'.join(row['tags']));self.error.clear()
        while self.tag_layout.count():
            item=self.tag_layout.takeAt(0)
            if item.widget():item.widget().hide();item.widget().deleteLater()
        for i,tag in enumerate(row['tag_details']):
            def search(value=tag['tag']):
                if value not in self.c.active_tags:self.c.active_tags.append(value)
                self.c.select_slot(row['slot']);self.c.filters_changed();self.accept()
            b=button(tag['tag'],search,ghost=True);b.setText(G.QFontMetrics(b.font()).elidedText(tag['tag'],C.Qt.ElideRight,92));b.setMinimumWidth(0)
            source={'model':'模型覆盖规则','name':'名称建议','dye':'染色能力','user':'用户标签'}[tag['source']]
            b.setToolTip(tag['tag']+' · '+source);self.tag_layout.addWidget(b,i//4,i%4)
        automatic=[t['tag']for t in row['tag_details']if t['origin']=='auto']
        self.origin_note.setText('自动建议：'+('、'.join(automatic)if automatic else'无')+'\n覆盖类标签读取模型配套规则，风格标签来自名称建议；均可修改，不自动添加部位标签。')

    def save(self):
        from equipment_library import parse_tags
        try:
            self.c.library.save(self.item_id,parse_tags(self.tag_edit.toPlainText()))
            self.c.refresh_library();self.load_item(self.item_id);self.c.message.set('装备标签已保存，收藏状态不变')
        except Exception as error:self.error.setText(str(error))

    def update_favorite_button(self,favorite):
        self.favorite_button.setChecked(favorite);self.favorite_button.setText('★ 已收藏（点击取消）'if favorite else'☆ 收藏装备')

    def toggle_favorite(self):
        self.c.toggle_favorite(self.item_id)
        self.update_favorite_button(self.c.library.item(self.item_id)['favorite'])


class AppearanceDialog(W.QDialog):
    def __init__(self,controller):
        super().__init__(controller.window);self.c=controller;self.setWindowTitle('编辑角色外观');self.resize(650,680)
        self.setMinimumSize(590,600);outer=vertical(self,22,14)
        outer.addWidget(label('角色外观','Title'));outer.addWidget(label(controller.editor_record['file_name']+' · 软件预设库','Muted'))
        self.tabs=W.QTabWidget();outer.addWidget(self.tabs,1);self.build_fields()
        self.preview_timer=C.QTimer(self);self.preview_timer.setSingleShot(True);self.preview_timer.timeout.connect(controller.flush_appearance_preview)
        self.realtime=W.QCheckBox('实时预览（修改不会自动保存）');self.realtime.setChecked(True);outer.addWidget(self.realtime)
        self.realtime.toggled.connect(lambda active:controller.queue_appearance_preview()if active else self.preview_timer.stop())
        progress=horizontal();self.spinner=LoadingSpinner(18);progress.addWidget(self.spinner)
        self.status=label('','Warning');progress.addWidget(self.status,1);outer.addLayout(progress)
        actions=horizontal();actions.addWidget(button('关闭',self.reject,ghost=True));actions.addStretch()
        self.save_button=button('保存预设',lambda:controller.submit_editor(False));actions.addWidget(self.save_button)
        self.update_button=button('更新模型',lambda:controller.submit_editor(True),primary=True);actions.addWidget(self.update_button);outer.addLayout(actions)
        self.update_status()

    def reject(self):
        if self.c.job is None:super().reject()

    def set_busy(self,busy,*,live=False):
        self.tabs.setEnabled(not busy or live);self.save_button.setEnabled(not busy);self.update_button.setEnabled(not busy)

    def build_fields(self):
        active=self.tabs.currentIndex()
        while self.tabs.count():
            page=self.tabs.widget(0);self.tabs.removeTab(0);page.deleteLater()
        self.forms={};self.choice_controls={};self.feature_controls={};self.field_controls={};self.toggle_controls={};c=self.c
        for name in('发型与体型','五官细节','特征与彩绘'):
            scroll=W.QScrollArea();scroll.setWidgetResizable(True);content=W.QWidget();form=W.QFormLayout(content)
            form.setContentsMargins(8,20,16,20);form.setHorizontalSpacing(24);form.setVerticalSpacing(18);form.setFieldGrowthPolicy(W.QFormLayout.AllNonFixedFieldsGrow)
            scroll.setWidget(content);self.tabs.addTab(scroll,name);self.forms[name]=form
        rows={}
        for option in c.editor_record['fields']:
            field=option['field'];choices=list(option['choices']);value=c.draft[field]&option['mask']
            if field=='hair':
                from cn_barber_hair import hair_compatible
                choices=[choice for choice in choices if hair_compatible(choice,c.draft)]
            if not any(choice['value']==value for choice in choices):choices.append({'value':value,'label':str(value)+'（当前预设）'})
            if field=='hair':choices=hairstyle_choices_with_names(sorted(choices,key=lambda choice:choice['value']))
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
                self.field_controls[field]=container;rows[field]=(option['label'],container);continue
            else:
                control=HairstyleComboBox()if field=='hair'else ComboBox()
                control.setProperty('appearance_field',field)
                if field=='hair':control.setToolTip(f'共 {len(choices)} 款；包含理发店解锁发型，无需游戏内已解锁。按当前种族、性别和脸型筛选。')
                if field!='hair'and any(choice.get('icon_id')for choice in choices):
                    control.setIconSize(C.QSize(44,44));control.setMinimumHeight(50);control.setMaxVisibleItems(7)
                    control.setStyleSheet('QComboBox { combobox-popup:0; }')
                    view=W.QListView(control);view.setIconSize(C.QSize(44,44));view.setUniformItemSizes(True);view.setMaximumHeight(380)
                    view.setVerticalScrollBarPolicy(C.Qt.ScrollBarAsNeeded);view.setVerticalScrollMode(W.QAbstractItemView.ScrollPerPixel)
                    control.setView(view)
                for choice in choices:control.addItem(c.icon(choice.get('icon_id')),choice['label'],choice['value'])
                self.choice_controls[field]=(control,choices)
                control.setCurrentIndex(next(i for i,x in enumerate(choices)if x['value']==value))
                control.activated.connect(lambda index,o=option,w=control:c.editor_change(o,w.itemData(index)))
            self.field_controls[field]=control;rows[field]=(option['label'],control)
        toggles=[('highlight_flags',128,'启用挑染'),('mouth',128,'启用唇色'),('eyes',128,'使用小虹膜'),('face_paint',128,'镜像彩绘')]+[('facial_features',1<<i,f'面部特征 {i+1}')for i in range(7)]
        if c.draft['race']==7:toggles=[t for t in toggles if t[:2]!=('mouth',128)]
        for field,bit,text in toggles:
            control=W.QCheckBox(text);control.setChecked(bool(c.draft[field]&bit))
            if field=='facial_features':
                control.setIconSize(C.QSize(40,40));control.setMinimumHeight(44);self.feature_controls[bit]=control
            def toggle(checked,f=field,b=bit):
                c.draft[f]=(c.draft[f]|b)if checked else(c.draft[f]&~b);self.update_status();c.queue_appearance_preview()
            control.toggled.connect(toggle)
            self.toggle_controls[field,bit]=control;rows[field,bit]=('',control)
        # Place each color and switch beside the appearance it changes. These
        # are the original controls/callbacks; resource and DAT values are intact.
        layout=[('发型与体型',[
            ('身份与肤色',['tribe','skin_color']),
            ('发型与颜色',['hair','hair_color',('highlight_flags',128),'highlight_color']),
            ('体型与特征',['height','bust','ear_muscle_tail_size','tail_or_ear_type'])]),
            ('五官细节',[
                ('脸部轮廓',['face','jaw']),
                ('眉眼',['eyebrows','eyes',('eyes',128),'right_eye_color','left_eye_color']),
                ('鼻与嘴',['nose','mouth',('mouth',128)]+([]if c.draft['race']==7 else['lip_color_or_fur_pattern']))]),
            ('特征与彩绘',[
                ('面部特征',[('facial_features',1<<i)for i in range(7)]+['facial_feature_color']),
                ('毛纹',['lip_color_or_fur_pattern']if c.draft['race']==7 else[]),
                ('面部彩绘',['face_paint',('face_paint',128),'face_paint_color'])])]
        for tab,sections in layout:
            form=self.forms[tab]
            for title,keys in sections:
                present=[key for key in keys if key in rows]
                if not present:continue
                heading=label(title,'Section');heading.setContentsMargins(0,8,0,2);form.addRow(heading)
                if title=='面部特征':
                    grid=W.QWidget();cells=W.QGridLayout(grid);cells.setContentsMargins(0,0,0,0);cells.setHorizontalSpacing(16)
                    for index,key in enumerate(key for key in present if isinstance(key,tuple)):
                        cells.addWidget(rows.pop(key)[1],index//2,index%2)
                    form.addRow(grid)
                for key in present:
                    if key in rows:form.addRow(*rows.pop(key))
        # Retain future source-menu fields even before a dedicated section is
        # assigned to them; no native option is silently dropped by the layout.
        for text,control in rows.values():self.forms['特征与彩绘'].addRow(text,control)
        self.refresh_icons()
        if active>=0:self.tabs.setCurrentIndex(active)

    def refresh_icons(self,changed=None):
        for control,choices in self.choice_controls.values():
            for index,choice in enumerate(choices):
                identifier=choice.get('icon_id')
                if identifier and(changed is None or identifier in changed):control.setItemIcon(index,self.c.icon(identifier))
        face=next((o for o in self.c.editor_record['fields']if o['field']=='face'),{})
        choice=next((o for o in face.get('choices',())if o['value']==self.c.draft['face']),{})
        for index in range(7):
            icons=choice.get('feature_icons',());identifier=icons[index]if index<len(icons)else 0
            if changed is None or identifier in changed:self.feature_controls[1<<index].setIcon(self.c.icon(identifier))

    def update_status(self):
        c=self.c;saved=c.draft==c.editor_record['parameters'];updated=c.draft==c.engine.rendered_parameters and c.engine.active_preset==c.editor_record['id']
        self.status.setText(('已保存到预设'if saved else'修改尚未保存')+' · '+('模型已更新'if updated else'模型尚未更新'))


class DesktopSession(C.QObject):
    """Apply game-directory changes after the old GPU worker releases HWND."""
    def __init__(self,application,store,game,preset=None):
        super().__init__(application);self.application=application;self.store=store;self.next_game=None;self.settings_dialog=None
        self.preset=preset or (ROOT/'defaults/test.dat' if (ROOT/'defaults/test.dat').is_file() else paths().presets/'test.dat');self.controller=None
        self.timer=C.QTimer(self);self.timer.setInterval(40);self.timer.timeout.connect(self.poll);self.timer.start()
        self.update_folder=None
        self.start(game)
        from qt_updates import UpdateController
        self.updates=UpdateController(self)

    def start(self,game):
        self.window=WardrobeWindow();self.controller=QtClient(self.window,game,self.preset,settings_handler=self.open_settings,studio=self.store.studio());self.window.show()

    def open_settings(self):
        if self.settings_dialog:self.settings_dialog.raise_();return
        from qt_settings import SettingsDialog
        dialog=SettingsDialog(self.store,self.controller.game,parent=self.window,storage_context=self.controller.storage_identity,update_controller=self.updates,memory_release=self.controller.release_memory)
        self.settings_dialog=dialog
        def finish(result):
            self.settings_dialog=None
            if result==W.QDialog.Accepted:
                if dialog.saved_game==self.controller.game:
                    self.controller.apply_studio(dialog.saved_studio)
                else:self.next_game=dialog.saved_game;self.controller.close()
            dialog.deleteLater()
        dialog.finished.connect(finish);dialog.show()

    def poll(self):
        if not self.controller.closed:return
        self.window.deleteLater()
        if self.update_folder:
            from updates import launch_helper
            try:launch_helper(self.update_folder)
            except Exception as error:
                W.QMessageBox.warning(None,'更新未开始',str(error));self.update_folder=None;self.start(self.controller.game);return
            self.timer.stop();self.application.quit();return
        if self.next_game:
            game=self.next_game;self.next_game=None;self.start(game)
        else:self.timer.stop();self.application.quit()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--game',type=Path);parser.add_argument('--preset',type=Path)
    args=parser.parse_args();migrate();os.chdir(ROOT);application=W.QApplication.instance()or W.QApplication(sys.argv[:1]);application.setStyle('Fusion');application.setStyleSheet(STYLE)
    application.setFont(G.QFont('Microsoft YaHei UI',10));application.setQuitOnLastWindowClosed(False)
    from settings import SettingsStore,validate_game
    from qt_first_run import FirstRunDialog
    store=SettingsStore();game,error=store.read()
    if args.game:
        try:game=store.save(validate_game(args.game));error=''
        except Exception as issue:game=None;error=str(issue)
    automatic_host=store.network_setup_host()if not args.game else None
    if game is None or automatic_host:
        from qt_first_run import open_source_setup
        game=open_source_setup(store,args.game,error=error,automatic_host=automatic_host)
        if game is None:return 0
    session=DesktopSession(application,store,game,args.preset)
    return application.exec()


def startup_check():
    application=W.QApplication.instance()or W.QApplication(sys.argv[:1]);application.setStyle('Fusion');application.setStyleSheet(STYLE)
    window=WardrobeWindow();window.setAttribute(C.Qt.WA_DontShowOnScreen);window.winId();window.close();application.processEvents()


if __name__=='__main__':raise SystemExit(main())
