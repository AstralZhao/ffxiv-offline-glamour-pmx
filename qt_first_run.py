"""First-run source setup: no preview settings or storage scan."""
from qt_resource_source import ResourceDialog,ResourceSourcePanel
from qt_theme import vertical,label


class FirstRunDialog(ResourceDialog):
    def __init__(self,store,game=None,*,error='',parent=None):
        super().__init__(store,parent);self.setWindowTitle('欢迎使用幻化柜');self.resize(590,400);self.setMinimumSize(510,400)
        outer=vertical(self,24,16);outer.addWidget(label('开始使用幻化柜','Title'))
        self.bind_source(ResourceSourcePanel(game,remembered=store.resource_inputs()));outer.addWidget(self.source_panel);outer.addStretch()
        self.add_footer(outer,first=True,error=error)


def open_source_setup(store,game=None,*,error='',automatic_host=None):
    """Use the existing cancellable worker for normal first use or saved-host initialization."""
    from qt_resource_source import C,W
    dialog=FirstRunDialog(store,None if automatic_host else game,error=error)
    if automatic_host:
        dialog.setWindowTitle('正在连接网络资源 · 幻化柜')
        dialog.network_mode.setChecked(True);dialog.path.setText(automatic_host)
        C.QTimer.singleShot(0,dialog.save)
    if dialog.exec()!=W.QDialog.Accepted:return None
    return dialog.saved_game
