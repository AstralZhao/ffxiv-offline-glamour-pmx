"""Tabbed in-app settings; first-run setup lives in qt_first_run."""
from qt_resource_source import ResourceDialog,ResourceSourcePanel,W,C
from qt_theme import label,vertical,horizontal,ComboBox
from studio_lighting import BACKGROUNDS,LIGHTS,options
from qt_storage import StoragePanel


class SettingsDialog(ResourceDialog):
    def __init__(self,store,game=None,*,parent=None,storage_context=None,update_controller=None):
        super().__init__(store,parent);self.setWindowTitle('设置 · 幻化柜');self.resize(760,640);self.setMinimumSize(640,520)
        outer=vertical(self,22,14);outer.addWidget(label('设置','Title'));outer.addWidget(label('资源、场景、存储空间与软件更新。','Muted'))
        self.tabs=W.QTabWidget();outer.addWidget(self.tabs,1)
        self.bind_source(ResourceSourcePanel(game,export=True,remembered=store.resource_inputs()));self.add_page(self.source_panel,'资源')
        studio=W.QWidget();content=vertical(studio,0,18);content.addWidget(label('预览摄影棚','Section'))
        form=W.QFormLayout();form.setVerticalSpacing(16);self.studio_background=ComboBox();self.studio_light=ComboBox()
        for key,(text,_)in BACKGROUNDS.items():self.studio_background.addItem(text,key)
        for key,text in LIGHTS.items():self.studio_light.addItem(text,key)
        selected=store.studio();self.studio_background.setCurrentIndex(self.studio_background.findData(selected['background']));self.studio_light.setCurrentIndex(self.studio_light.findData(selected['lighting']))
        self.studio_brightness=W.QSlider(C.Qt.Horizontal);self.studio_brightness.setRange(25,200);self.studio_brightness.setValue(selected['brightness'])
        intensity=W.QWidget();row=horizontal(intensity,0);row.addWidget(self.studio_brightness,1);self.brightness_label=label(str(selected['brightness'])+'%');self.brightness_label.setMinimumWidth(45);row.addWidget(self.brightness_label)
        self.studio_brightness.valueChanged.connect(lambda value:self.brightness_label.setText(str(value)+'%'))
        form.addRow('背景',self.studio_background);form.addRow('打光方式',self.studio_light);form.addRow('灯光亮度',intensity);content.addLayout(form)
        hint=label('背景和灯光可独立选择。点击“保存并应用”更新预览，下次启动会恢复。','Muted');hint.setWordWrap(True);content.addWidget(hint);content.addStretch()
        self.add_page(studio,'场景');self.storage=StoragePanel(store.root,storage_context);self.add_page(self.storage,'存储')
        if update_controller is not None:
            from qt_updates import UpdatePanel
            self.updates=UpdatePanel(update_controller);self.add_page(self.updates,'更新')
        self.add_footer(outer);self.storage.cleaningChanged.connect(self.storage_busy);self.finished.connect(lambda _:self.storage.shutdown())

    def add_page(self,widget,name):
        if isinstance(widget,StoragePanel):self.tabs.addTab(widget,name);return
        scroll=W.QScrollArea();scroll.setWidgetResizable(True);scroll.setHorizontalScrollBarPolicy(C.Qt.ScrollBarAlwaysOff)
        page=W.QWidget();layout=vertical(page,12,12);layout.addWidget(widget);layout.addStretch();scroll.setWidget(page);self.tabs.addTab(scroll,name)

    def studio_value(self):return options({'background':self.studio_background.currentData(),'lighting':self.studio_light.currentData(),'brightness':self.studio_brightness.value()})
    def extra_busy(self,value):self.tabs.setEnabled(not value)
    def storage_busy(self,value):self.save_button.setEnabled(not value);self.tabs.tabBar().setEnabled(not value);self.source_panel.setEnabled(not value)
    def reject(self):self.storage.shutdown();super().reject()
