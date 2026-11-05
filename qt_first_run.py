"""First-run source setup: no preview settings or storage scan."""
from qt_resource_source import ResourceDialog,ResourceSourcePanel
from qt_theme import vertical,label


class FirstRunDialog(ResourceDialog):
    def __init__(self,store,game=None,*,error='',parent=None):
        super().__init__(store,parent);self.setWindowTitle('欢迎使用幻化柜');self.resize(590,365);self.setMinimumWidth(510)
        outer=vertical(self,24,16);outer.addWidget(label('开始使用幻化柜','Title'))
        self.bind_source(ResourceSourcePanel(game,remembered=store.resource_inputs()));outer.addWidget(self.source_panel);outer.addStretch()
        self.add_footer(outer,first=True,error=error)
