"""Single-frame template sharing, using the current character and camera."""
from copy import deepcopy
from pathlib import Path
import re

from qt_runtime import enable
enable()
from PySide6 import QtCore as C,QtGui as G,QtWidgets as W
from qt_theme import label,button,vertical,horizontal
from app import SLOTS,parse_state

def sharing_state(current,record):
    """Replace only the template-owned fields; never load its legacy preset."""
    state=deepcopy(current);template=parse_state(record['state'])
    state['equipment']=template['equipment']
    for key in('facewear','weapons'):
        state.pop(key,None)
        if template.get(key):state[key]=deepcopy(template[key])
    state['preview_mode']='character'
    return parse_state(state)


def capture_frame(engine,state):
    """GPU worker only. Read one native frame, then restore the active view."""
    original=engine.last
    if original is None:raise ValueError('请等待角色预览完成')
    renderer=engine.renderer;surface=renderer.native_surface;frame=engine.effect_frame
    try:
        # Keep the same render size/camera/postprocessing. Only the destination
        # changes, so the capture has exactly the viewport's field of view.
        renderer.native_surface=None
        result=engine.render(state,effect_frame=frame)
        if not result.get('image'):raise ValueError('角色图片未能生成')
        key=result['image'].rsplit('/',1)[-1].removesuffix('.png')
        with engine.lock:raw=engine.images[key]
        return raw
    finally:
        renderer.native_surface=surface
        try:
            engine.render(original['state'],effect_frame=frame)
        finally:
            # Share generation is not a template application or a session edit.
            with engine.lock:engine.last=original


def capture_result(engine,state):
    try:return {'png':capture_frame(engine,state)}
    except Exception as error:return {'error':str(error)}


def information(browser,record):
    """All slots, including empty slots and the original two dye choices."""
    from facewear import catalogue
    from qt_weapons import metadata
    state=parse_state(record['state']);rows=[]
    def add(section,slot,name,choice):
        identifier=choice.get('item_id')
        dyes=[];details=[]
        if identifier:
            for i,key in enumerate(('first','second'),1):
                value=choice.get(key,0)
                if value:
                    dye=browser.c.dye_by_id.get(value,{})
                    dyes.append(f'{i}区 · '+dye.get('name',str(value)))
                    details.append({'channel':i,'name':dye.get('name',str(value)),'color':dye.get('color',0x829499)&0xffffff})
        rows.append({'section':section,'slot':slot,'name':name,'dyes':'    '.join(dyes),'dye_details':details,'selected':bool(identifier)})
    for slot,name in(('MainHand','主武器'),('OffHand','副武器')):
        choice=state.get('weapons',{}).get(slot,{});identifier=choice.get('item_id')
        meta=metadata(identifier)if identifier else{}
        add('武器',name,meta.get('name','未设置'),choice)
    for slot,name in SLOTS.items():
        choice=state['equipment'][slot];identifier=choice.get('item_id');meta=browser.metadata.get(identifier,{})
        add('防具'if slot in('Head','Body','Gloves','Legs','Feet')else'饰品',name,
            meta.get('name',f'装备 #{identifier}')if identifier else'未设置',choice)
    data,_,variants=catalogue()
    for slot in sorted({style['slot']for style in data['styles']}):
        identifier=state.get('facewear',[0,0])[slot];meta=variants.get(identifier,{})
        add('面饰','面饰',meta.get('name',f'面饰 #{identifier}')if identifier else'未设置',{'item_id':identifier})
    return rows


def font(size,bold=False):
    value=G.QFont('Microsoft YaHei UI');value.setPixelSize(size);value.setBold(bold);return value


def equipment_layout(rows,column_width=298):
    """Pair wardrobe with weapons/accessories; an unworn cell stays blank."""
    slots=(('头部','主武器'),('面饰','副武器'),('上装','耳饰'),('手部','项链'),
        ('下装','手镯'),('脚部','右戒指'),(None,'左戒指'))
    selected={row['slot']:row for row in rows if row['selected']}
    names=G.QFontMetrics(font(17,True));small=G.QFontMetrics(font(12))
    flags=int(C.Qt.TextWordWrap|C.Qt.AlignLeft|C.Qt.AlignTop)
    entries=[];y=82
    for pair in slots:
        band=[]
        for column,slot in enumerate(pair):
            row=selected.get(slot)
            if row is None:continue
            nh=max(22,names.boundingRect(C.QRect(0,0,column_width,1000),flags,row['name']).height())
            details=[];dx=0;line=0
            for dye in row['dye_details']:
                value=f"{dye['channel']}区  {dye['name']}";w=small.horizontalAdvance(value)+19
                if dx and dx+w>column_width:dx=0;line+=1
                # A single long dye label may itself wrap without clipping.
                dh=max(18,small.boundingRect(C.QRect(0,0,column_width-19,1000),flags,value).height())
                details.append((dx,line*21,value,dye['color'],dh));dx+=min(column_width,w)+12
                if dh>18:dx=0;line+=(dh+20)//21
            dye_height=max((dy+dh for dx,dy,value,color,dh in details),default=0)
            block=20+nh+(dye_height+5 if details else 0)+12
            band.append({'column':column,'top':y,'height':block,'name_height':nh,'dyes':details,'row':row})
        if band:
            entries.extend(band);y+=max(entry['height']for entry in band)+12
    return entries,y


def compose(frame,width,height,title,preset,rows):
    """Paste the real crop at its own bounds; never extend its background."""
    if frame.isNull():raise ValueError('角色图片为空')
    width=max(1,min(int(width),frame.width()));height=max(1,min(int(height),frame.height()))
    crop=frame.copy((frame.width()-width)//2,(frame.height()-height)//2,width,height)
    margin=28;gap=24;column_width=298;gutter=24;panel=column_width*2+gutter
    body_width=width+gap+panel;layouts,list_height=equipment_layout(rows,column_width)
    name_font=font(17,True);small_font=font(12);slot_font=font(11,True);title_font=font(30,True)
    flags=int(C.Qt.TextWordWrap|C.Qt.AlignLeft|C.Qt.AlignTop)
    title_height=max(34,G.QFontMetrics(title_font).boundingRect(C.QRect(0,0,body_width,1000),flags,title).height())
    top=margin+title_height+68;content_height=max(height,list_height);bottom=52
    image=G.QImage(body_width+margin*2,top+content_height+bottom,G.QImage.Format_RGB32);image.fill(G.QColor('#f5f3ed'))
    p=G.QPainter(image)
    try:
        p.setRenderHint(G.QPainter.Antialiasing);p.setRenderHint(G.QPainter.TextAntialiasing)
        def text(rect,value,face,color):
            p.setFont(face);p.setPen(G.QColor(color));p.drawText(rect,flags,value)
        p.fillRect(C.QRect(margin,margin,3,13),G.QColor('#2d756b'))
        text(C.QRect(margin+12,margin-1,body_width-12,18),'幻化柜   /   GLAMOUR LOOKBOOK',slot_font,'#48645e')
        text(C.QRect(margin,margin+25,body_width,title_height+2),title,title_font,'#20343a')
        sub=G.QFontMetrics(small_font).elidedText('CHARACTER   /   '+preset,C.Qt.ElideRight,body_width)
        text(C.QRect(margin,margin+title_height+35,body_width,20),sub,small_font,'#6b7a79')
        # The photo ends exactly at the crop edge. Extra list height belongs
        # to the paper, not a fabricated extension of the studio background.
        p.drawImage(margin,top,crop)
        x=margin+width+gap
        text(C.QRect(x,top,panel-80,28),'穿搭清单',font(21,True),'#20343a')
        text(C.QRect(x+panel-80,top+7,80,20),f'{len(layouts):02}  PIECES',slot_font,'#637672')
        p.setPen(G.QPen(G.QColor('#c9d0c8'),1));p.drawLine(x,top+40,x+panel,top+40)
        for column,heading in enumerate(('衣装 / 面饰','武器 / 饰品')):
            if any(entry['column']==column for entry in layouts):
                text(C.QRect(x+column*(column_width+gutter),top+55,column_width,20),heading,slot_font,'#2d756b')
        for entry in layouts:
            row=entry['row'];tx=x+entry['column']*(column_width+gutter);dy=top+entry['top']
            text(C.QRect(tx,dy,column_width,17),row['slot'],slot_font,'#76837d')
            text(C.QRect(tx,dy+20,column_width,entry['name_height']+2),row['name'],name_font,'#20343a')
            for dx,offset,value,color,dh in entry['dyes']:
                cy=dy+24+entry['name_height']+offset
                p.setPen(G.QPen(G.QColor('#a7b0a7'),.7));p.setBrush(G.QColor.fromRgb(color));p.drawEllipse(C.QPointF(tx+dx+5,cy+7),4,4)
                text(C.QRect(tx+dx+18,cy-1,column_width-dx-18,dh+2),value,small_font,'#61756e')
            p.setPen(G.QPen(G.QColor('#e1e1d8'),1));p.drawLine(tx,dy+entry['height'],tx+column_width,dy+entry['height'])
        p.setPen(G.QPen(G.QColor('#d4d8ce'),1));p.drawLine(margin,image.height()-36,image.width()-margin,image.height()-36)
        text(C.QRect(margin,image.height()-24,body_width,20),'幻化柜    /    投影模版',slot_font,'#78887d')
    finally:p.end()
    return image


def save_png(image,path):
    output=C.QSaveFile(str(path))
    if not output.open(C.QIODevice.WriteOnly):raise OSError(output.errorString())
    if not image.save(output,'PNG'):
        output.cancelWriting();raise OSError('图片写入失败')
    if not output.commit():raise OSError(output.errorString())


class ImagePreview(W.QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.image=G.QImage();self.setMinimumSize(260,240)
        self.setSizePolicy(W.QSizePolicy.Expanding,W.QSizePolicy.Expanding)
    def paintEvent(self,event):
        p=G.QPainter(self);p.fillRect(self.rect(),G.QColor('#080c12'))
        if not self.image.isNull():
            size=self.image.size().scaled(self.size()-C.QSize(24,24),C.Qt.KeepAspectRatio)
            rect=C.QRect(C.QPoint((self.width()-size.width())//2,(self.height()-size.height())//2),size)
            p.setRenderHint(G.QPainter.SmoothPixmapTransform);p.drawImage(rect,self.image)
        p.end()


class TemplateShareDialog(W.QDialog):
    def __init__(self,browser,record):
        super().__init__(browser);self.browser=browser;self.c=browser.c;self.record=deepcopy(record)
        self.setWindowTitle('生成分享图');self.resize(1060,820);self.setMinimumSize(720,550)
        self.setWindowModality(C.Qt.WindowModal);self.busy=False;self.frame=G.QImage();self.output=G.QImage()
        self.rows=information(browser,self.record);self.source_state=sharing_state(self.c.good,self.record)
        boot=self.c.boot or{};active=boot.get('preset_id')
        self.preset_name=next((p['name']for p in boot.get('presets',[])if p['id']==active),'当前角色')
        outer=vertical(self,20,12);outer.addWidget(label('分享图预览','Title'))
        hint=label('使用当前角色预设及主界面的站姿、视角和打光。宽高只居中裁剪角色图，装备信息会完整保留。','Muted');hint.setWordWrap(True);outer.addWidget(hint)
        controls=horizontal();controls.addWidget(label('角色图宽'))
        self.crop_width=W.QSpinBox();self.crop_width.setSuffix(' px');controls.addWidget(self.crop_width)
        controls.addWidget(label('高'));self.crop_height=W.QSpinBox();self.crop_height.setSuffix(' px');controls.addWidget(self.crop_height)
        self.reset_button=button('完整画面',self.reset_crop,ghost=True);controls.addWidget(self.reset_button);controls.addStretch()
        self.dimensions=label('','Muted');controls.addWidget(self.dimensions);outer.addLayout(controls)
        self.preview=ImagePreview();outer.addWidget(self.preview,1)
        self.progress=W.QProgressBar();self.progress.setRange(0,0);self.progress.setFixedHeight(4);self.progress.setTextVisible(False)
        self.progress.setStyleSheet('QProgressBar { background:#222c38;border:0; } QProgressBar::chunk { background:#89d5c4; }');outer.addWidget(self.progress)
        self.status=label('正在生成角色单帧图…','Muted');self.status.setWordWrap(True);outer.addWidget(self.status)
        actions=horizontal();self.original_button=button('查看原图',self.full_preview);actions.addWidget(self.original_button);actions.addStretch()
        actions.addWidget(button('关闭',self.reject,ghost=True));self.save_button=button('保存 PNG 图片',self.save,primary=True);actions.addWidget(self.save_button);outer.addLayout(actions)
        self.timer=C.QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(80);self.timer.timeout.connect(self.compose)
        for control in(self.crop_width,self.crop_height):control.valueChanged.connect(lambda:self.timer.start())
        self.enable_controls(False)

    def enable_controls(self,enabled):
        for widget in(self.crop_width,self.crop_height,self.reset_button,self.original_button,self.save_button):widget.setEnabled(enabled)

    def generate(self):
        self.busy=True
        def done(result):
            self.busy=False;self.progress.hide()
            if result.get('error'):
                self.status.setText('分享图未生成：'+result['error']);return
            self.frame=G.QImage.fromData(result['png'],'PNG')
            if self.frame.isNull():self.status.setText('角色图片读取失败，请关闭后重试。');return
            self.enable_controls(True);self.reset_crop()
        if not self.c.submit(capture_result,done,self.c.engine,self.source_state):
            self.busy=False;self.progress.hide();self.status.setText('预览正在更新，请关闭后稍后重试。')

    def reset_crop(self):
        if self.frame.isNull():return
        for control,maximum in((self.crop_width,self.frame.width()),(self.crop_height,self.frame.height())):
            control.blockSignals(True);control.setRange(1,maximum);control.setValue(maximum);control.blockSignals(False)
        self.compose()

    def compose(self):
        self.timer.stop()
        if self.frame.isNull():return
        self.output=compose(self.frame,self.crop_width.value(),self.crop_height.value(),self.record['name'],self.preset_name,self.rows)
        self.preview.image=self.output;self.preview.update();self.dimensions.setText(f'成图 {self.output.width()} × {self.output.height()} px')
        self.status.setText('当前预览即保存内容。保留主界面放大构图，角色图按原始像素裁剪；未穿部位留空。')

    def full_preview(self):
        self.compose()
        if self.output.isNull():return
        dialog=W.QDialog(self);dialog.setWindowTitle('分享图 · 原始尺寸');dialog.resize(960,760)
        outer=vertical(dialog,12,10);scroll=W.QScrollArea();image=label();image.setPixmap(G.QPixmap.fromImage(self.output));scroll.setWidget(image)
        outer.addWidget(scroll,1);outer.addWidget(button('关闭',dialog.accept));self.c.show_dialog(dialog)

    def save(self):
        self.compose()
        if self.output.isNull():return
        name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',self.record['name']).strip(' .')or'投影模版'
        folder=C.QStandardPaths.writableLocation(C.QStandardPaths.PicturesLocation)
        path,_=W.QFileDialog.getSaveFileName(self,'保存分享图',str(Path(folder)/(name+'.png')),'PNG 图片 (*.png)')
        if not path:return
        if not path.lower().endswith('.png'):path+='.png'
        try:save_png(self.output,path)
        except Exception as error:self.status.setText('保存失败：'+str(error));return
        self.status.setText('图片已保存：'+path)

    def reject(self):
        if self.busy:self.status.setText('正在完成单帧绘制，请稍候再关闭。');return
        super().reject()

    def closeEvent(self,event):
        if self.busy:event.ignore();self.status.setText('正在完成单帧绘制，请稍候再关闭。')
        else:super().closeEvent(event)
