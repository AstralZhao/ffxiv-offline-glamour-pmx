"""Nonmodal searchable emote trial, sharing the existing single GPU worker."""
from qt_theme import W,C,G,label,button,vertical,horizontal,ComboBox,AppearanceSlider
from emote_clock import EmoteClock
import json


class EmotePlayback:
    def __init__(self,controller):
        self.c=controller;self.clock=EmoteClock();self.dialog=None;self.current=None;self.metadata=None;self.pending_play=False
        self.timer=C.QTimer(controller.window);self.timer.setInterval(33);self.timer.timeout.connect(controller.tick_effects)

    @property
    def playing(self):return self.clock.playing and bool(self.c.state.get('emote'))

    def pause(self):self.clock.pause();self.timer.stop();self.sync()

    def stop(self):
        self.pending_play=False;self.pause();self.c.state.pop('emote',None);self.current=None;self.metadata=None
        self.c.update_outfit();self.c.queue_render();self.sync()

    def choose(self,identifier):
        if self.c.job is not None:return
        self.pending_play=False;self.pause();self.clock.time=0.;self.clock.duration=0.;self.clock.loop_start=None;self.current=identifier;self.metadata=None
        self.c.state['emote']={'id':identifier,'time':0.};self.c.update_outfit();self.c.queue_render();self.sync()

    def finish(self):
        if self.c.job is not None or not self.metadata or not self.metadata.get('end_available')or self.metadata.get('phase')=='end':return
        self.pause();self.clock.time=0.;self.clock.duration=0.;self.clock.loop_start=None;self.clock.repeat=False
        self.pending_play=True;self.metadata=None
        self.c.state['emote']={'id':self.current,'time':0.,'phase':'end'}
        self.c.update_outfit();self.c.queue_render();self.sync()

    def toggle(self):
        if not self.metadata:return
        if self.playing:self.pause()
        else:
            self.clock.suspend(False);self.clock.play();self.timer.start();self.sync()

    def choose_expression(self, identifier=None):
        if identifier is None:
            self.c.state.pop('expression', None)
        else:
            self.c.state['expression'] = identifier
        self.c.queue_render()

    def seek(self,value):
        if not self.metadata:return
        self.clock.seek(max(0.,min(float(value),self.clock.duration)))
        self.c.state['emote']={**self.c.state['emote'],'time':self.clock.sample()};self.c.queue_render();self.sync()

    def presented(self,result):
        record=result.get('emote');active=self.c.state.get('emote')
        if record is None or active is None or record['id']!=active['id']:return
        if record.get('phase','play')!=active.get('phase','play'):return
        self.current=record['id'];self.metadata=record;self.clock.duration=record['duration']
        self.clock.loop_start=record.get('loop_start')
        if not self.playing:self.clock.seek(record['time'])
        if not self.c.dirty:
            self.c.state['emote']={**active,'time':record['time']}
            self.c.good['emote']=dict(self.c.state['emote'])
        if self.pending_play:
            self.pending_play=False;self.clock.suspend(self.c.window.isMinimized()or not self.c.window.isVisible());self.clock.play();self.timer.start()
        if self.playing and self.clock.loop_start is None and not self.clock.repeat and record['time']>=record['duration']-1e-6:
            if record.get('phase')=='end':self.stop();return
            self.pause()
        self.sync()

    def sync(self):
        if self.dialog is not None:self.dialog.sync()

    def reconcile(self):
        """A rejected selection must restore controls for the retained frame."""
        active=self.c.state.get('emote');record=(self.c.engine.last or{}).get('emote')
        if active and record and active['id']==record['id']and active.get('phase','play')==record.get('phase','play')and(self.metadata is None or self.metadata['id']!=active['id']or self.metadata.get('phase','play')!=active.get('phase','play')):
            self.pending_play=False
            self.presented(self.c.engine.last);return True
        if not active and self.metadata is not None:
            self.pause();self.metadata=None;self.current=None;self.sync();return True
        return False

    def load_catalog(self,dialog):
        if self.dialog is not dialog or self.c.closing:return
        if self.c.job is not None or self.c.effect_deferred is not None:
            dialog.catalog_timer.start(100);return
        if not self.c.submit(self.c.engine.emote_catalog,lambda rows:dialog.load(rows)if self.dialog is dialog else None):
            dialog.catalog_timer.start(100)

    def open(self):
        if self.dialog is not None:self.dialog.show();self.dialog.raise_();return self.dialog
        self.dialog=EmoteDialog(self);self.dialog.finished.connect(lambda _:setattr(self,'dialog',None))
        self.c.show_dialog(self.dialog,near_cursor=True)
        dialog=self.dialog
        self.load_catalog(dialog)
        return dialog


class EmoteDialog(W.QDialog):
    def __init__(self,playback):
        super().__init__(playback.c.window);self.p=playback;self.c=playback.c;self.rows=[];self.visible_items={}
        self.setWindowTitle('情感动作 · 幻化柜');self.setWindowModality(C.Qt.NonModal);self.resize(440,630);self.setMinimumSize(360,460)
        outer=vertical(self,16,10);outer.addWidget(label('情感动作','Title'))
        hint=label('选择后从起点定格。可直接操作主预览视角、换装和染色。','Muted');hint.setWordWrap(True);outer.addWidget(hint)
        self.search=W.QLineEdit();self.search.setPlaceholderText('搜索动作名称或编号…');outer.addWidget(self.search)
        row=horizontal(spacing=8);self.filter=ComboBox()
        for name in('身体动作','收藏','最近使用','面部表情'):self.filter.addItem(name)
        row.addWidget(self.filter,1);self.favorite=button('☆ 收藏',self.toggle_favorite);row.addWidget(self.favorite);outer.addLayout(row)
        self.list=W.QListWidget();self.list.setIconSize(C.QSize(40,40));self.list.setUniformItemSizes(True)
        self.list.setStyleSheet('QListWidget::item { padding:7px; min-height:42px; } QListWidget::item:selected { background:#2e4c48; color:#b8eee2; }')
        outer.addWidget(self.list,1);self.status=label('正在读取动作目录…','Muted');self.status.setWordWrap(True);outer.addWidget(self.status)
        transport=horizontal();self.play=button('▶ 播放',self.p.toggle,primary=True);transport.addWidget(self.play)
        self.finish=button('结束动作',self.p.finish);self.finish.setToolTip('播放动作自带的结束段，完成后恢复站姿');transport.addWidget(self.finish)
        self.speed=ComboBox()
        for v in(.25,.5,1.,1.5,2.):self.speed.addItem(f'{v:g}×',v)
        self.speed.setCurrentIndex(2);self.speed.activated.connect(lambda i:self.p.clock.set_speed(self.speed.itemData(i)))
        transport.addWidget(self.speed);self.repeat=W.QCheckBox('循环播放');transport.addWidget(self.repeat);outer.addLayout(transport)
        self.position=AppearanceSlider(C.Qt.Horizontal);self.position.setRange(0,10000);outer.addWidget(self.position)
        controls=horizontal();self.time=W.QDoubleSpinBox();self.time.setDecimals(2);self.time.setSingleStep(.1);self.time.setSuffix(' 秒');self.time.setRange(0,0)
        self.time.wheelEvent=lambda e:e.ignore();controls.addWidget(self.time);self.end=label('/ 0.00 秒','Muted');controls.addWidget(self.end);controls.addStretch()
        controls.addWidget(button('清除表情',lambda:self.p.choose_expression()));controls.addWidget(button('恢复站姿',self.p.stop));outer.addLayout(controls)
        note=label('面部表情可与当前站姿或身体动作组合。','Muted');note.setWordWrap(True);outer.addWidget(note)
        self.search.textChanged.connect(self.populate);self.filter.currentIndexChanged.connect(self.populate)
        self.icon_timer=C.QTimer(self);self.icon_timer.setSingleShot(True);self.icon_timer.timeout.connect(self.load_icons)
        self.catalog_timer=C.QTimer(self);self.catalog_timer.setSingleShot(True);self.catalog_timer.timeout.connect(lambda:self.p.load_catalog(self))
        self.list.itemClicked.connect(self.choose);self.list.verticalScrollBar().valueChanged.connect(lambda _:self.load_icons())
        self.position.valueChanged.connect(lambda v:self.p.seek(v/10000*self.p.clock.duration))
        self.time.valueChanged.connect(self.p.seek);self.repeat.toggled.connect(self.set_repeat)
        self.favorites=set(self.setting('emote_favorites_v1'));self.recent=self.setting('emote_recent_v1');self.sync()

    def setting(self,key):
        try:
            value=json.loads(self.c.library.setting(key,'[]'))if self.c.library else[]
            return [v for v in value if type(v)is int and 0<v<65536][:512]if isinstance(value,list)else[]
        except(ValueError,TypeError):return []

    def save(self,key,values):
        if self.c.library:self.c.library.set_setting(key,json.dumps(list(values)))

    def set_repeat(self,value):
        at=self.p.clock.sample();self.p.clock.repeat=value;self.p.clock.seek(at)

    def load(self,rows):self.rows=rows;self.populate()

    def populate(self,*_):
        query=self.search.text().strip().casefold();mode=self.filter.currentIndex();selected=self.p.current
        self.list.clear();self.visible_items={};rows=self.rows
        if mode==2:rows=sorted(rows,key=lambda r:self.recent.index(r['id'])if r['id']in self.recent else 9999)
        for row in rows:
            if query and query not in row['name'].casefold()and query not in str(row['id']):continue
            if mode==1 and row['id']not in self.favorites or mode==2 and row['id']not in self.recent:continue
            if mode in (0,3) and row['facial']!=(mode==3):continue
            item=W.QListWidgetItem(('★ 'if row['id']in self.favorites else'')+f"{row['name']}  ·  {row['id']}")
            item.setData(C.Qt.UserRole,row);item.setToolTip(row['notice']);item.setSizeHint(C.QSize(300,56));self.list.addItem(item)
            self.visible_items[row['id']]=item
            if row['id']==(self.c.state.get('expression') if row['facial'] else selected):self.list.setCurrentItem(item)
        self.status.setText(f'{self.list.count()} 个选项 · 单击预览，播放后可随时暂停')
        self.icon_timer.start(0)
        self.sync()

    def load_icons(self):
        area=self.list.viewport().rect()
        for i in range(self.list.count()):
            item=self.list.item(i)
            if self.list.visualItemRect(item).intersects(area):item.setIcon(self.c.icon(item.data(C.Qt.UserRole)['icon']))

    def refresh_icons(self,changed):
        for item in self.visible_items.values():
            icon=item.data(C.Qt.UserRole)['icon']
            if icon in changed and icon in self.c.icons:item.setIcon(self.c.icons[icon])

    def choose(self,item):
        row=item.data(C.Qt.UserRole)
        self.recent=[row['id']]+[i for i in self.recent if i!=row['id']][:29];self.save('emote_recent_v1',self.recent)
        if row['facial']:self.p.choose_expression(row['id'])
        else:self.p.choose(row['id'])

    def toggle_favorite(self):
        item=self.list.currentItem()
        if item is None:return
        identifier=item.data(C.Qt.UserRole)['id']
        if identifier in self.favorites:self.favorites.remove(identifier)
        else:self.favorites.add(identifier)
        self.save('emote_favorites_v1',sorted(self.favorites));self.populate()

    def sync(self):
        ready=bool(self.p.metadata and self.c.state.get('emote'))
        if self.rows:
            text=f'{self.list.count()} 个选项 · 单击预览，播放后可随时暂停'
            if ready and self.p.metadata.get('pending_rules'):
                from emote_timeline import LABELS
                names=list(dict.fromkeys(LABELS.get(k,'其他动作控制')for k in self.p.metadata['pending_rules']))
                text+='\n待适配：'+'、'.join(names)
            if ready and self.p.metadata.get('standalone_preview'):
                text+='\n仅预览角色动作；交互、物品和特效暂不显示'
            elif ready and self.p.metadata.get('omitted_visuals'):
                text+='\n动作物品和特效暂不显示'
            self.status.setText(text)
            automatic=(self.p.metadata or{}).get('automatic_faces')or{}
            hint=automatic.get('notice')or('配套表情随动作播放和定位。'if automatic.get('enabled')else'')
            if ready and 'manual-face-priority'in self.p.metadata.get('pending_rules',()):
                hint='手动表情与配套表情的组合仍在适配；清除手动表情后恢复动作配套表情。'
            if ready and self.p.metadata.get('weapon_control_notices'):
                hint+='\n武器动画：'+'；'.join(dict.fromkeys(self.p.metadata['weapon_control_notices']))
            self.status.setToolTip(hint)
        ending=bool(ready and self.p.metadata.get('phase')=='end')
        self.finish.setVisible(bool(ready and self.p.metadata.get('end_available')));self.finish.setEnabled(ready and not ending)
        self.repeat.setEnabled(not ending)
        if ending:
            self.repeat.blockSignals(True);self.repeat.setChecked(False);self.repeat.blockSignals(False)
        self.play.setEnabled(ready);self.position.setEnabled(ready);self.time.setEnabled(ready)
        self.play.setText('Ⅱ 暂停'if self.p.playing else'▶ 播放')
        if not ready:return
        data=self.p.metadata;duration=data['duration'];value=data['time']
        self.end.setText(f'/ {duration:.2f} 秒')
        self.position.blockSignals(True);self.position.setValue(round(value/duration*10000));self.position.blockSignals(False)
        self.time.blockSignals(True);self.time.setRange(0,duration);self.time.setValue(value);self.time.blockSignals(False)
