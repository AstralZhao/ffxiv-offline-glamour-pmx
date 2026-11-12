"""Small GPose-style studio controls; authored lighting, not native presets."""
from qt_theme import W,C,label,vertical,horizontal,ComboBox,button
from studio_lighting import LIGHT_TYPES,LIGHTS,DEFAULT,options,apply_preset,direction_angles,angle_direction,rotate_direction


class NoWheel:
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        if isinstance(self,W.QAbstractSpinBox):self.setFocusPolicy(C.Qt.StrongFocus)

    def wheelEvent(self,event):
        focused=W.QApplication.focusWidget()
        if isinstance(self,W.QAbstractSpinBox)and(self.hasFocus()or(focused is not None and self.isAncestorOf(focused))):
            return super().wheelEvent(event)
        event.ignore()


class Slider(NoWheel,W.QSlider):pass
class SpinBox(NoWheel,W.QSpinBox):pass
class DoubleSpinBox(NoWheel,W.QDoubleSpinBox):pass
class Choice(NoWheel,ComboBox):pass


def slider(value,maximum=200):
    widget=W.QWidget();row=horizontal(widget,0,8);control=Slider(C.Qt.Horizontal);control.setRange(0,maximum);control.setValue(value)
    number=SpinBox();number.setRange(0,maximum);number.setValue(value);number.setFixedWidth(80)
    if maximum==200:number.setSuffix('%')
    control.valueChanged.connect(number.setValue);number.valueChanged.connect(control.setValue)
    row.addWidget(control,1);row.addWidget(number);return widget,control


def rgb(values):
    widget=W.QWidget();row=horizontal(widget,0,8);controls=[]
    for text,value in zip(('红 R','绿 G','蓝 B'),values):
        row.addWidget(label(text));control=SpinBox();control.setRange(0,255);control.setValue(value);row.addWidget(control,1);controls.append(control)
    return widget,controls


def angle_control(value,minimum,maximum):
    widget=W.QWidget();row=horizontal(widget,0,8);control=Slider(C.Qt.Horizontal);control.setRange(minimum*10,maximum*10)
    number=DoubleSpinBox();number.setRange(minimum,maximum);number.setDecimals(1);number.setSuffix('°');number.setFixedWidth(95)
    number.setValue(value);control.setValue(round(number.value()*10))
    control.valueChanged.connect(lambda n:number.setValue(n/10));number.valueChanged.connect(lambda n:control.setValue(round(n*10)))
    row.addWidget(control,1);row.addWidget(number);return widget,control,number


class SceneLightingPanel(W.QWidget):
    changed=C.Signal()
    def __init__(self,selected,place_light=None,camera_rotation=None):
        super().__init__();selected=options(selected);self.place_light=place_light;self.camera_rotation=camera_rotation;self.light_controls=[]
        layout=vertical(self,0,12);form=W.QFormLayout();form.setVerticalSpacing(12)
        form.addRow(label('环境光','Section'));row,self.ambient=slider(selected['brightness']);form.addRow('亮度',row)
        row,self.ambient_rgb=rgb(selected['ambient_color']);form.addRow('颜色',row)
        h,v=direction_angles(selected['ambient_direction'])
        row,self.horizontal_slider,self.horizontal_angle=angle_control(h,-180,180);form.addRow('左右角度',row)
        row,self.elevation_slider,self.elevation_angle=angle_control(v,-90,90);form.addRow('照射高度',row)
        self.ambient_camera_button=button('用当前视角设置光向',self.aim_ambient_at_camera,ghost=True);self.ambient_camera_button.setAutoDefault(False)
        self.ambient_camera_button.setEnabled(camera_rotation is not None)
        self.ambient_camera_button.setToolTip('先转到希望光照来的角度，点击记录；再转回构图，光向保持固定。')
        self.direction_help=label('固定光向：左右角度0°从角色正前方来光，90°偏角色右方；高度90°为顶光。','Muted');self.direction_help.setWordWrap(True);form.addRow(self.direction_help)
        advanced=W.QWidget();advanced_layout=vertical(advanced,0,6);direction=W.QWidget();direction_row=horizontal(direction,0,6);self.ambient_direction=[]
        for axis,value in zip(('X','Y','Z'),selected['ambient_direction']):
            direction_row.addWidget(label(axis));spin=DoubleSpinBox();spin.setRange(-1,1);spin.setDecimals(6);spin.setSingleStep(.1);spin.setValue(value);direction_row.addWidget(spin,1);self.ambient_direction.append(spin)
        advanced_layout.addWidget(direction);advanced_layout.addWidget(label('XYZ表示来光方向，不是位置。','Muted'));advanced.hide()
        self.advanced_button=button('高级：方向 XYZ',ghost=True);self.advanced_button.setCheckable(True);self.advanced_button.setAutoDefault(False)
        direction_actions=W.QWidget();direction_buttons=horizontal(direction_actions,0,8);direction_buttons.addWidget(self.ambient_camera_button,1);direction_buttons.addWidget(self.advanced_button)
        self.advanced_button.toggled.connect(advanced.setVisible);form.addRow('',direction_actions);form.addRow(advanced)
        self.ambient_directionality=DoubleSpinBox();self.ambient_directionality.setRange(0,100);self.ambient_directionality.setDecimals(1);self.ambient_directionality.setSuffix('%');self.ambient_directionality.setValue(selected['ambient_directionality'])
        self.ambient_directionality.setToolTip('0%为均匀环境光，比例越高，指定方向的明暗越明显。');form.addRow('方向光比例',self.ambient_directionality)
        form.addRow(label('角色补光','Section'));row,self.fill=slider(selected['fill_brightness']);form.addRow('亮度',row)
        row,self.fill_rgb=rgb(selected['fill_color']);form.addRow('颜色',row);layout.addLayout(form)
        layout.addWidget(label('独立灯光','Section'));tabs=W.QTabWidget();layout.addWidget(tabs)
        for index,saved in enumerate(selected['lights']):
            page=W.QWidget();content=vertical(page,12,10);grid=W.QFormLayout();grid.setVerticalSpacing(10)
            enabled=W.QCheckBox('开启此灯');enabled.setChecked(saved[0]);kind=Choice()
            for key,(text,_,_)in LIGHT_TYPES.items():kind.addItem(text,key)
            kind.setCurrentIndex(kind.findData(saved[1]));grid.addRow(enabled);grid.addRow('类型',kind)
            row,colors=rgb(saved[2:5]);grid.addRow('颜色',row);position=W.QWidget();line=horizontal(position,0,6);axes=[]
            for axis,value in zip(('X','Y','Z'),saved[5:]):
                line.addWidget(label(axis));spin=DoubleSpinBox();spin.setRange(-100,100);spin.setDecimals(3);spin.setSingleStep(.1);spin.setValue(value);line.addWidget(spin,1);axes.append(spin)
            grid.addRow('固定位置',position);content.addLayout(grid)
            place=button('放在当前相机位置',lambda checked=False,i=index:self.place(i),ghost=True);place.setEnabled(place_light is not None);content.addWidget(place)
            tabs.addTab(page,'灯光 '+str(index+1));self.light_controls.append((enabled,kind,colors,axes))
        self.notice=label('三种类型提供不同亮度和范围；灯放好后不随相机移动。此为简化摄影棚灯光，不含投射阴影。','Muted');self.notice.setWordWrap(True);layout.addWidget(self.notice)
        for control in self.findChildren(W.QAbstractSpinBox):
            if control in self.ambient_direction:control.valueChanged.connect(self.xyz_edited)
            elif control in (self.horizontal_angle,self.elevation_angle):control.valueChanged.connect(self.angles_edited)
            else:control.valueChanged.connect(lambda *_:self.changed.emit())
        for control in (self.ambient,self.fill):control.valueChanged.connect(lambda *_:self.changed.emit())
        for enabled,kind,_,_ in self.light_controls:
            enabled.toggled.connect(lambda *_:self.changed.emit());kind.currentIndexChanged.connect(lambda *_:self.changed.emit())

    def aim_ambient_at_camera(self):
        rotation=self.camera_rotation()if self.camera_rotation else None
        if rotation is None:self.notice.setText('请等待角色预览完成后再记录光向。');return
        direction=rotate_direction((0,0,1),rotation,to_view=False)
        blocked=self.blockSignals(True)
        try:
            self.set_direction(direction)
        finally:self.blockSignals(blocked)
        self.notice.setText('已记录当前视角的来光方向；现在可以转回构图，光向不会跟着转动。');self.changed.emit()

    def set_direction(self,direction):
        for control,value in zip(self.ambient_direction,direction):
            blocked=control.blockSignals(True)
            try:control.setValue(value)
            finally:control.blockSignals(blocked)
        self.sync_angles()

    def sync_angles(self):
        h,v=direction_angles(tuple(c.value()for c in self.ambient_direction))
        for number,slider,value in ((self.horizontal_angle,self.horizontal_slider,h),(self.elevation_angle,self.elevation_slider,v)):
            a,b=number.blockSignals(True),slider.blockSignals(True)
            try:number.setValue(value);slider.setValue(round(number.value()*10))
            finally:number.blockSignals(a);slider.blockSignals(b)

    def xyz_edited(self):self.sync_angles();self.changed.emit()

    def angles_edited(self):
        self.set_direction(angle_direction(self.horizontal_angle.value(),self.elevation_angle.value()));self.changed.emit()

    def place(self,index):
        position=self.place_light()if self.place_light else None
        if position is None:self.notice.setText('请等待角色预览完成后再放置灯光。');return
        enabled,_,_,axes=self.light_controls[index]
        for spin,value in zip(axes,position):spin.setValue(value)
        enabled.setChecked(True);self.notice.setText('灯光已放在当前相机位置。')

    def value(self):
        return {'brightness':self.ambient.value(),'fill_brightness':self.fill.value(),
            'ambient_color':tuple(c.value()for c in self.ambient_rgb),'fill_color':tuple(c.value()for c in self.fill_rgb),
            'ambient_direction':tuple(c.value()for c in self.ambient_direction),'ambient_directionality':self.ambient_directionality.value(),'ambient_space':'world',
            'lights':tuple((enabled.isChecked(),kind.currentData(),*(c.value()for c in colors),*(c.value()for c in axes))for enabled,kind,colors,axes in self.light_controls)}

    def set_value(self,value):
        value=options(value);blocked=self.blockSignals(True)
        try:
            self.ambient.setValue(value['brightness']);self.fill.setValue(value['fill_brightness'])
            self.set_direction(value['ambient_direction'])
            self.ambient_directionality.setValue(value['ambient_directionality'])
            for controls,key in ((self.ambient_rgb,'ambient_color'),(self.fill_rgb,'fill_color')):
                for control,number in zip(controls,value[key]):control.setValue(number)
            for controls,row in zip(self.light_controls,value['lights']):
                enabled,kind,colors,axes=controls;enabled.setChecked(row[0]);kind.setCurrentIndex(kind.findData(row[1]))
                for control,number in zip((*colors,*axes),row[2:]):control.setValue(number)
        finally:self.blockSignals(blocked)
        self.changed.emit()


class LightingDialog(W.QDialog):
    previewRequested=C.Signal(object)

    def __init__(self,store,game,studio,*,parent=None,place_light=None,camera_rotation=None):
        super().__init__(parent);self.store=store;self.game=game;self.original=options(studio);self.setWindowTitle('灯光详细调整 · 幻化柜')
        self.setWindowModality(C.Qt.NonModal);self.resize(720,760);self.setMinimumSize(640,520)
        if parent and parent.testAttribute(C.Qt.WA_DontShowOnScreen):self.setAttribute(C.Qt.WA_DontShowOnScreen)
        outer=vertical(self,20,12);outer.addWidget(label('灯光详细调整','Title'))
        outer.addWidget(label('修改后实时预览，可以同时转动角色视角。','Muted'))
        scroll=W.QScrollArea();scroll.setWidgetResizable(True);scroll.setHorizontalScrollBarPolicy(C.Qt.ScrollBarAlwaysOff)
        page=W.QWidget();content=vertical(page,0,14);form=W.QFormLayout();self.preset=Choice();self.preset.addItem('选择并应用…',None)
        for key,text in LIGHTS.items():self.preset.addItem(text,key)
        for name in LIGHTS:
            if self.original==apply_preset(self.original,name):self.preset.setCurrentIndex(self.preset.findData(name));break
        self.preset.setToolTip('一次性填入下方所有灯光参数，不叠加额外效果；背景和抗锯齿保持不变。');form.addRow('灯光预设',self.preset);content.addLayout(form)
        self.panel=SceneLightingPanel(self.original,place_light,camera_rotation);content.addWidget(self.panel);content.addStretch();scroll.setWidget(page);outer.addWidget(scroll,1)
        self.status=label('预览修改尚未保存。','Muted');self.status.setWordWrap(True);outer.addWidget(self.status)
        self.preview_timer=C.QTimer(self);self.preview_timer.setSingleShot(True);self.preview_timer.setInterval(45)
        self.preview_timer.timeout.connect(self.preview_now);self.panel.changed.connect(self.lighting_edited);self.preset.activated.connect(self.choose_preset)
        actions=horizontal();self.reset_button=button('恢复默认',self.reset_lighting,ghost=True);self.cancel_button=button('取消',self.reject,ghost=True)
        self.save_button=button('保存并关闭',self.save,primary=True)
        for control in (self.reset_button,self.cancel_button,self.save_button):control.setAutoDefault(False)
        actions.addWidget(self.reset_button);actions.addStretch();actions.addWidget(self.cancel_button);actions.addWidget(self.save_button);outer.addLayout(actions)
        self.finished.connect(lambda _:self.preview_timer.stop())

    def studio_value(self):
        return options({**self.original,**self.panel.value()})

    def choose_preset(self,index):
        name=self.preset.itemData(index)
        if name is None:return
        self.set_preset_parameters(apply_preset(self.studio_value(),name),name)
        self.preview_now();self.status.setText('预设已填入下方参数，可以继续独立调整。')

    def set_preset_parameters(self,value,name):
        blocked=self.panel.blockSignals(True)
        try:self.panel.set_value(value)
        finally:self.panel.blockSignals(blocked)
        self.preset.setCurrentIndex(self.preset.findData(name))

    def lighting_edited(self):
        self.preset.setCurrentIndex(0);self.queue_preview()

    def queue_preview(self):
        if not self.preview_timer.isActive():self.preview_timer.start()
        self.status.setText('预览修改尚未保存。')

    def preview_now(self):
        self.preview_timer.stop();self.previewRequested.emit(self.studio_value())

    def reset_lighting(self):
        self.set_preset_parameters(DEFAULT,'original')
        self.preview_now();self.status.setText('已恢复默认灯光；背景和抗锯齿保持不变。')

    def save(self):
        value=self.studio_value()
        try:self.store.save(self.game,studio=value)
        except Exception as error:self.status.setText('未能保存：'+str(error));return
        self.saved_studio=value;self.preview_now();self.accept()

    def reject(self):self.preview_timer.stop();super().reject()
