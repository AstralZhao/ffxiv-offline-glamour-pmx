"""Tabbed in-app settings; first-run setup lives in qt_first_run."""
from qt_resource_source import ResourceDialog,ResourceSourcePanel,W,C
from qt_theme import label,vertical,horizontal,button
from qt_scene_lighting import Choice,slider
from studio_lighting import BACKGROUNDS,LIGHTS,options,apply_preset
from qt_storage import StoragePanel


class SettingsDialog(ResourceDialog):
    sceneUpdated=C.Signal(object)
    lightingRequested=C.Signal(object)

    def __init__(self,store,game=None,*,parent=None,storage_context=None,update_controller=None,memory_release=None,place_light=None,studio=None):
        super().__init__(store,parent);self.setWindowTitle('设置 · 幻化柜');self.resize(760,640);self.setMinimumSize(640,520)
        outer=vertical(self,22,14);outer.addWidget(label('设置','Title'));outer.addWidget(label('资源、场景、存储空间与软件更新。','Muted'))
        self.tabs=W.QTabWidget();outer.addWidget(self.tabs,1)
        self.bind_source(ResourceSourcePanel(game,export=True,remembered=store.resource_inputs()));self.add_page(self.source_panel,'资源')
        studio_page=W.QWidget();content=vertical(studio_page,0,18);content.addWidget(label('预览摄影棚','Section'))
        form=W.QFormLayout();form.setVerticalSpacing(16);self.studio_background=Choice()
        for key,(text,_)in BACKGROUNDS.items():self.studio_background.addItem(text,key)
        self.selected=options(store.studio()if studio is None else studio);self.lighting_handoff=False
        selected=self.selected;self.studio_background.setCurrentIndex(self.studio_background.findData(selected['background']))
        self.studio_light=Choice();self.studio_light.addItem('自定义',None)
        for key,text in LIGHTS.items():self.studio_light.addItem(text,key)
        for name in LIGHTS:
            if selected==apply_preset(selected,name):self.studio_light.setCurrentIndex(self.studio_light.findData(name));break
        intensity,self.studio_brightness=slider(selected['brightness'])
        intensity.setToolTip('手动调整后，环境光和角色补光同步设为此亮度；其他灯光参数可在灯光详细调整中设置。')
        self.simple_lighting_loading=False
        self.studio_light.activated.connect(self.choose_simple_lighting);self.studio_brightness.valueChanged.connect(self.simple_brightness_changed)
        self.studio_antialiasing=Choice();self.studio_antialiasing.addItem('关闭','off');self.studio_antialiasing.addItem('SMAA · 边缘抗锯齿','smaa')
        self.studio_antialiasing.addItem('高画质 · 2×超采样','ssaa')
        self.studio_antialiasing.addItem('MSAA · 4×多重采样','msaa4')
        self.studio_antialiasing.setCurrentIndex(self.studio_antialiasing.findData(selected['antialiasing']))
        form.addRow('背景',self.studio_background);form.addRow('打光方式',self.studio_light);form.addRow('灯光亮度',intensity)
        form.addRow('抗锯齿',self.studio_antialiasing);content.addLayout(form)
        actions=horizontal();self.update_scene_button=button('更新场景',self.update_scene,primary=True);self.lighting_button=button('灯光详细调整…',self.open_lighting)
        actions.addWidget(self.update_scene_button);actions.addWidget(self.lighting_button);actions.addStretch();content.addLayout(actions)
        hint=label('在此选择打光方式、同步调整环境光与补光亮度。“更新场景”预览当前设置，不关闭窗口。“灯光详细调整”将打开独立窗口，实时调节光向、颜色和三盏灯。','Muted');hint.setWordWrap(True);content.addWidget(hint)
        hint=label('选项按当前实现的通常开销由低到高排列。MSAA 减少细小模型边缘的漏采；2×超采样以四倍像素绘制。多重采样和超采样会增加显卡与显存开销。','Muted');hint.setWordWrap(True);content.addWidget(hint);content.addStretch()
        self.add_page(studio_page,'场景');self.storage=StoragePanel(store.root,storage_context,memory_release=memory_release);self.add_page(self.storage,'存储')
        if update_controller is not None:
            from qt_updates import UpdatePanel
            self.updates=UpdatePanel(update_controller);self.add_page(self.updates,'更新')
        self.add_footer(outer);self.storage.cleaningChanged.connect(self.storage_busy);self.finished.connect(lambda _:self.storage.shutdown())

    def add_page(self,widget,name):
        if isinstance(widget,StoragePanel):self.tabs.addTab(widget,name);return
        scroll=W.QScrollArea();scroll.setWidgetResizable(True);scroll.setHorizontalScrollBarPolicy(C.Qt.ScrollBarAlwaysOff)
        page=W.QWidget();layout=vertical(page,12,12);layout.addWidget(widget);layout.addStretch();scroll.setWidget(page);self.tabs.addTab(scroll,name)

    def studio_value(self):return options({**self.selected,'background':self.studio_background.currentData(),'antialiasing':self.studio_antialiasing.currentData()})
    def choose_simple_lighting(self,index):
        name=self.studio_light.itemData(index)
        if name is None:return
        self.selected=apply_preset(self.studio_value(),name)
        self.simple_lighting_loading=True
        try:self.studio_brightness.setValue(self.selected['brightness'])
        finally:self.simple_lighting_loading=False
    def simple_brightness_changed(self,value):
        if self.simple_lighting_loading:return
        self.selected=options({**self.selected,'brightness':value,'fill_brightness':value});self.studio_light.setCurrentIndex(0)
    def update_scene(self):
        self.sceneUpdated.emit(self.studio_value());self.status.setText('场景预览已请求更新；保存并应用可保留设置。')
    def open_lighting(self):
        value=self.studio_value();self.lighting_handoff=True;self.reject();self.lightingRequested.emit(value)
    def extra_busy(self,value):self.tabs.setEnabled(not value)
    def storage_busy(self,value):self.save_button.setEnabled(not value);self.tabs.tabBar().setEnabled(not value);self.source_panel.setEnabled(not value)
    def reject(self):self.storage.shutdown();super().reject()
