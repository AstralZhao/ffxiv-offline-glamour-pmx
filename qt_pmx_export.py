"""PMX export settings; file work stays on the existing GPU worker."""
from pathlib import Path
import re

from PySide6 import QtCore as C,QtGui as G,QtWidgets as W
from qt_theme import button,label,vertical,horizontal


def export_dialog(client):
    dialog=W.QDialog(client.window);dialog.setWindowTitle('导出 MMD 模型');dialog.setModal(True)
    dialog.setMinimumWidth(460);layout=vertical(dialog,20,12)
    text=label('导出当前可见的角色和装备，包括已应用但尚未保存的外观。当前姿势将成为模型的初始姿势。')
    text.setWordWrap(True);layout.addWidget(text)
    form=W.QFormLayout();layout.addLayout(form)
    scale=W.QDoubleSpinBox();scale.setRange(.01,1000);scale.setDecimals(2);scale.setValue(12.5)
    scale.setToolTip('默认将游戏中的 1 米转换为 12.5 个 MMD 单位')
    form.addRow('尺寸倍率',scale)
    textures=W.QCheckBox('烘焙当前染色、肤色与发色');textures.setChecked(True);form.addRow('贴图',textures)
    resolution=W.QComboBox()
    for size in(512,1024,2048):resolution.addItem(f'{size} × {size}',size)
    resolution.setCurrentIndex(1);form.addRow('每个材质的贴图尺寸',resolution)
    textures.toggled.connect(resolution.setEnabled)
    note=label('保留原生骨架，可手动摆姿；没有自动生成标准 MMD 动作骨架、IK、表情或物理。使用 VMD 动作前需要适配。游戏反射、透射和粒子特效不会完整保留。','Muted')
    note.setWordWrap(True);layout.addWidget(note)
    footer=horizontal();footer.addStretch();footer.addWidget(button('取消',dialog.reject))
    save=button('选择位置并导出',lambda:None,primary=True);footer.addWidget(save);layout.addLayout(footer)

    def start():
        if client.job is not None or client.dirty or client.effect_deferred is not None:
            client.message.set('预览正在更新，请稍后再导出 PMX');return
        name=client.preset_name.get()or'角色'
        name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',name).rstrip('. ')or'角色'
        directory=C.QStandardPaths.writableLocation(C.QStandardPaths.DocumentsLocation)
        picker=W.QFileDialog(dialog,'保存 MMD 模型',directory,'MMD 模型 (*.pmx)')
        picker.setAcceptMode(W.QFileDialog.AcceptSave);picker.setDefaultSuffix('pmx');picker.selectFile(name+'.pmx')
        if picker.exec()!=W.QDialog.Accepted:return
        # The default suffix is applied BEFORE Qt confirms replacement.
        destination=Path(picker.selectedFiles()[0])
        options={'scale':scale.value(),'resolution':resolution.currentData(),
            'textures':textures.isChecked(),'overwrite':destination.exists()}
        def done(result):
            client.message.set(f'PMX 已导出：{result["vertices"]:,} 个顶点 · {result["bones"]} 根骨骼 · '+result['path'])
            message=W.QMessageBox(client.window);message.setWindowTitle('PMX 导出完成')
            message.setIcon(W.QMessageBox.Information)
            message.setText('模型已保存到：\n'+result['path'])
            message.setInformativeText('移动模型时请同时保留贴图文件夹：\n'+Path(result['assets']).name)
            message.setDetailedText('\n'.join(result['warnings'])+'\n\n导出报告：'+result['report'])
            opened=message.addButton('打开文件夹',W.QMessageBox.ActionRole)
            message.addButton('完成',W.QMessageBox.AcceptRole)
            opened.clicked.connect(lambda:G.QDesktopServices.openUrl(C.QUrl.fromLocalFile(str(destination.parent))))
            client.show_dialog(message)
        if client.submit(client.engine.export_pmx,done,str(destination),options):
            client.loading_kind='正在导出 PMX 模型…';client.sync_loading();dialog.accept()
    save.clicked.connect(start)
    return dialog
