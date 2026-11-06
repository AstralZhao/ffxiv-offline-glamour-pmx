"""Visual system for the native wardrobe; no character-rendering code."""
from qt_runtime import enable
enable()
from PySide6 import QtCore as C,QtGui as G,QtWidgets as W

BG='#101319';PANEL='#191e27';RAISED='#222936';BORDER='#2b3443';TEXT='#edf1f7';MUTED='#8c9bad';ACCENT='#89d5c4'

STYLE='''
QWidget { color:#edf1f7; font-family:"Microsoft YaHei UI"; font-size:13px; }
QMainWindow, QDialog { background:#101319; }
QFrame#Panel { background:#191e27; border:1px solid #2b3443; border-radius:12px; }
QFrame#Header { background:#101319; border-bottom:1px solid #252c38; }
QFrame#Footer { border-top:1px solid #252c38; }
QLabel { background:transparent; border:0; }
QLabel#Title { font-size:18px; font-weight:600; }
QLabel#Brand { font-size:22px; font-weight:600; letter-spacing:3px; }
QLabel#Eyebrow { color:#89d5c4; font-size:10px; letter-spacing:2px; }
QLabel#Muted { color:#8c9bad; }
QLabel#Section { font-size:14px; font-weight:600; }
QLabel#Badge { color:#89d5c4; background:#243b3b; border-radius:8px; padding:4px 9px; font-size:11px; }
QLabel#Warning { color:#dfbe88; }
QPushButton, QToolButton { background:#222936; border:1px solid #323c4e; border-radius:7px; padding:7px 12px; }
QPushButton:hover, QToolButton:hover { background:#2a3444; border-color:#607082; }
QPushButton:pressed { background:#344556; }
QPushButton:disabled, QToolButton:disabled { color:#586577; background:#1b222d; border-color:#27303e; }
QPushButton[primary="true"] { color:#142c29; background:#89d5c4; border-color:#89d5c4; font-weight:600; }
QPushButton[primary="true"]:hover { background:#a3e4d5; border-color:#a3e4d5; }
QPushButton[ghost="true"] { background:transparent; border-color:transparent; color:#aab7c8; }
QPushButton[ghost="true"]:hover { background:#252e3b; color:#edf1f7; }
QPushButton:checked { color:#a6e7d8; background:#2a4243; border-color:#5d9c93; }
QPushButton[slotTab="true"] { padding:8px 0; font-size:11px; }
QPushButton[dye="true"] { border-radius:5px; padding:3px 7px; font-size:10px; text-align:left; }
QPushButton[clear="true"] { padding:0; border:0; background:transparent; color:#8290a3; font-size:16px; }
QPushButton[clear="true"]:hover { color:#f0b7aa; background:#3a2c32; }
QFrame#OutfitCard { background:#202733; border:1px solid #303a49; border-radius:8px; }
QFrame#OutfitCard[selected="true"] { border-color:#79b9af; background:#24383d; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit { background:#121821; border:1px solid #323d4d; border-radius:7px; padding:8px 10px; selection-background-color:#386c65; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color:#89d5c4; }
QTextBrowser { background:#191e27; color:#edf1f7; border:1px solid #323d4d; border-radius:8px; padding:8px; selection-background-color:#386c65; selection-color:#ffffff; }
QComboBox::drop-down { border:0; width:25px; }
QComboBox QAbstractItemView { background:#202733; border:1px solid #3a485b; selection-background-color:#345751; outline:0; padding:5px; }
QListWidget { background:transparent; border:0; outline:0; }
QListWidget::item { border:0; background:transparent; }
QScrollArea { border:0; background:transparent; }
QScrollArea > QWidget > QWidget { background:transparent; }
QScrollBar:vertical { background:transparent; width:6px; margin:2px 0; }
QScrollBar::handle:vertical { background:#3b475a; border-radius:3px; min-height:32px; }
QScrollBar::handle:vertical:hover { background:#66778c; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
QCheckBox { spacing:8px; color:#abb8ca; }
QCheckBox::indicator { width:15px; height:15px; border:1px solid #526074; border-radius:4px; background:#141b24; }
QCheckBox::indicator:checked { background:#89d5c4; border-color:#89d5c4; image:none; }
QRadioButton { spacing:8px; } QRadioButton::indicator { width:14px; height:14px; border:1px solid #607082; border-radius:8px; background:#141b24; }
QRadioButton::indicator:checked { background:#89d5c4; border:2px solid #345751; }
QToolTip { color:#edf1f7; background:#283344; border:1px solid #56667b; padding:6px; }
QMenu { background:#202733; border:1px solid #45556b; padding:5px; }
QMenu::item { padding:8px 16px; border-radius:4px; }
QMenu::item:selected { background:#345751; }
QTabWidget::pane { border:0; }
QTabBar::tab { background:transparent; color:#8c9bad; padding:10px 15px; border-bottom:2px solid transparent; }
QTabBar::tab:selected { color:#a6e7d8; border-bottom-color:#89d5c4; }
QSlider::groove:horizontal { background:#303b4b; height:4px; border-radius:2px; }
QSlider::handle:horizontal { background:#a3e4d5; width:12px; margin:-4px 0; border-radius:6px; }
'''


def label(text='',name=None):
    widget=W.QLabel(text)
    if name:widget.setObjectName(name)
    return widget


def button(text,callback=None,*,primary=False,ghost=False):
    widget=W.QPushButton(text);widget.setCursor(C.Qt.PointingHandCursor)
    if primary:widget.setProperty('primary',True)
    if ghost:widget.setProperty('ghost',True)
    if callback:widget.clicked.connect(lambda _checked=False:callback())
    return widget


def vertical(parent=None,margin=0,spacing=10):
    layout=W.QVBoxLayout(parent);layout.setContentsMargins(margin,margin,margin,margin);layout.setSpacing(spacing);return layout


def horizontal(parent=None,margin=0,spacing=8):
    layout=W.QHBoxLayout(parent);layout.setContentsMargins(margin,margin,margin,margin);layout.setSpacing(spacing);return layout


def polish(widget):widget.style().unpolish(widget);widget.style().polish(widget);widget.update()


class LoadingSpinner(W.QWidget):
    """GUI-only animation: never redraws the native character viewport."""
    def __init__(self,size=20,parent=None):
        super().__init__(parent);self.setFixedSize(size,size);self.angle=0;self.progress=None
        policy=self.sizePolicy();policy.setRetainSizeWhenHidden(True);self.setSizePolicy(policy)
        self.clock=C.QElapsedTimer();self.timer=C.QTimer(self);self.timer.setInterval(33)
        self.timer.timeout.connect(self.advance);self.setAttribute(C.Qt.WA_TransparentForMouseEvents)
        self.setAccessibleName('正在加载');self.hide()

    def set_running(self,running):
        if running and not self.timer.isActive():self.clock.start();self.timer.start()
        elif not running:self.timer.stop()
        self.setVisible(running)

    def advance(self):self.angle=(self.clock.elapsed()*.36)%360;self.update()

    def set_progress(self,value=None):
        value=None if value is None else max(0.,min(1.,float(value)))
        if value==self.progress:return
        self.progress=value;self.setAccessibleName('正在加载'if value is None else f'当前下载进度 {value:.0%}');self.update()

    def paintEvent(self,event):
        painter=G.QPainter(self);painter.setRenderHint(G.QPainter.Antialiasing)
        rect=C.QRectF(3,3,self.width()-6,self.height()-6)
        painter.setPen(G.QPen(G.QColor('#344552'),2,C.Qt.SolidLine,C.Qt.RoundCap));painter.drawEllipse(rect)
        painter.setPen(G.QPen(G.QColor(ACCENT),2,C.Qt.SolidLine,C.Qt.RoundCap))
        if self.progress is None:painter.drawArc(rect,round(-self.angle*16),100*16)
        else:
            painter.drawArc(rect,90*16,-round(360*16*self.progress))
            if self.width()>=32:
                font=G.QFont(self.font());font.setPixelSize(10);painter.setFont(font)
                painter.drawText(self.rect(),C.Qt.AlignCenter,f'{self.progress:.0%}')


class ComboBox(W.QComboBox):
    def wheelEvent(self,event):
        # Let the surrounding page scroll without changing the selected value.
        # The popup's item view handles its own wheel scrolling separately.
        event.ignore()

    def paintEvent(self,event):
        super().paintEvent(event);painter=G.QPainter(self);painter.setRenderHint(G.QPainter.Antialiasing)
        painter.setPen(G.QPen(G.QColor('#aab7c8'),1.4));x=self.width()-16;y=self.height()/2
        painter.drawPolyline(G.QPolygonF([C.QPointF(x-4,y-2),C.QPointF(x,y+2),C.QPointF(x+4,y-2)]))


class SearchComboBox(ComboBox):
    """Editable existing-choice search with bounded, scrollable popups."""
    def __init__(self,parent=None):
        super().__init__(parent);self.setEditable(True);self.setInsertPolicy(W.QComboBox.NoInsert)
        self.setMaxVisibleItems(10);self.setStyleSheet('QComboBox { combobox-popup: 0; }')
        self.lineEdit().setPlaceholderText('输入搜索标签');self.lineEdit().setClearButtonEnabled(True)
        view=W.QListView(self);view.setUniformItemSizes(True);self.setView(view)
        completer=self.completer();completer.setCompletionMode(W.QCompleter.PopupCompletion)
        completer.setFilterMode(C.Qt.MatchContains);completer.setCaseSensitivity(C.Qt.CaseInsensitive);completer.setMaxVisibleItems(10)
        completer.activated[str].connect(self.complete_text)
        self.lineEdit().returnPressed.connect(lambda:self.complete_text(self.currentText()))
        for popup in (view,completer.popup()):
            popup.setMaximumHeight(300);popup.setMinimumWidth(240)
            popup.setVerticalScrollBarPolicy(C.Qt.ScrollBarAsNeeded);popup.setHorizontalScrollBarPolicy(C.Qt.ScrollBarAlwaysOff)
            popup.setTextElideMode(C.Qt.ElideRight);popup.setVerticalScrollMode(W.QAbstractItemView.ScrollPerPixel)

    def complete_text(self,text):
        if not text.strip():return
        index=self.findText(text,C.Qt.MatchExactly)
        if index<0:
            self.completer().setCompletionPrefix(text);model=self.completer().completionModel()
            if model.rowCount():index=self.findText(model.index(0,0).data(),C.Qt.MatchExactly)
        if index>0:self.activated.emit(index)


class PresetComboBox(ComboBox):
    """Search the original preset choices without inserting arbitrary names."""
    def __init__(self,parent=None):
        super().__init__(parent);self.setEditable(True);self.setInsertPolicy(W.QComboBox.NoInsert)
        self.lineEdit().setPlaceholderText('搜索角色预设…');self.setMaxVisibleItems(10)
        self.setStyleSheet('QComboBox { combobox-popup:0; }')
        self.currentIndexChanged.connect(lambda _:self.lineEdit().setCursorPosition(0))
        view=W.QListView(self);view.setMinimumWidth(320);view.setMaximumHeight(360);self.setView(view)
        view.setVerticalScrollBarPolicy(C.Qt.ScrollBarAsNeeded)
        completer=self.completer();completer.setCompletionMode(W.QCompleter.PopupCompletion)
        completer.setFilterMode(C.Qt.MatchContains);completer.setCaseSensitivity(C.Qt.CaseInsensitive);completer.setMaxVisibleItems(10)
        completer.popup().setMinimumWidth(320);completer.popup().setMaximumHeight(360)
        completer.activated[str].connect(self.choose_preset)
        self.lineEdit().returnPressed.connect(lambda:self.choose_preset(self.currentText()))
        W.QApplication.instance().installEventFilter(self)

    def eventFilter(self,watched,event):
        if event.type()==C.QEvent.MouseButtonPress and isinstance(watched,W.QWidget):
            popups=(self.view().window(),self.completer().popup())
            inside=self is watched or self.isAncestorOf(watched)or any(p is watched or p.isAncestorOf(watched)for p in popups)
            if not inside and watched.window()is self.window():
                self.hidePopup();self.completer().popup().hide()
                self.lineEdit().clearFocus();self.clearFocus();self.restore_selected_text()
        return super().eventFilter(watched,event)

    def focusOutEvent(self,event):
        super().focusOutEvent(event)
        C.QTimer.singleShot(0,self.restore_when_unfocused)

    def restore_when_unfocused(self):
        if not self.hasFocus()and not self.lineEdit().hasFocus()and not self.completer().popup().isVisible()and not self.view().isVisible():
            self.restore_selected_text()

    def restore_selected_text(self):
        self.setEditText(self.itemText(self.currentIndex()))
        self.lineEdit().deselect();self.lineEdit().setCursorPosition(0)

    def choose_preset(self,text):
        index=self.findText(text,C.Qt.MatchExactly)
        if index>=0 and index!=self.currentIndex():
            self.setCurrentIndex(index);self.activated.emit(index)


def visibility_icon(hidden=False):
    image=G.QPixmap(40,40);image.setDevicePixelRatio(2);image.fill(C.Qt.transparent)
    painter=G.QPainter(image);painter.setRenderHint(G.QPainter.Antialiasing)
    painter.setPen(G.QPen(G.QColor('#dfbe88'if hidden else'#a3d9ce'),1.5,C.Qt.SolidLine,C.Qt.RoundCap,C.Qt.RoundJoin))
    path=G.QPainterPath()
    if hidden:
        path.moveTo(3,8);path.cubicTo(6,14,14,14,17,8);painter.drawPath(path)
        for x,y,dx in((5,11,-1.5),(10,13,0),(15,11,1.5)):
            painter.drawLine(C.QPointF(x,y),C.QPointF(x+dx,y+3))
    else:
        path.moveTo(2,10);path.cubicTo(6,3,14,3,18,10);path.cubicTo(14,17,6,17,2,10)
        painter.drawPath(path);painter.setBrush(G.QColor('#a3d9ce'));painter.drawEllipse(C.QPointF(10,10),2.2,2.2)
    painter.end();return G.QIcon(image)


def swatch_icon(color,*,unavailable=False):
    image=G.QPixmap(32,32);image.setDevicePixelRatio(2);image.fill(C.Qt.transparent)
    painter=G.QPainter(image);painter.setRenderHint(G.QPainter.Antialiasing)
    painter.setBrush(G.QColor(color));painter.setPen(G.QPen(G.QColor('#778495'),1))
    painter.drawRoundedRect(C.QRectF(1.5,1.5,13,13),3,3)
    if unavailable:
        painter.setBrush(G.QColor('#38252b'));painter.setPen(G.QPen(G.QColor('#f1aaa1'),1.6))
        painter.drawEllipse(C.QRectF(1.5,1.5,13,13));painter.drawLine(C.QPointF(3.5,12.5),C.QPointF(12.5,3.5))
    painter.end();icon=G.QIcon(image)
    if unavailable:icon.addPixmap(image,G.QIcon.Disabled)
    return icon


def slot_icon(slot):
    image=G.QPixmap(80,80);image.setDevicePixelRatio(2);image.fill(C.Qt.transparent);p=G.QPainter(image);p.setRenderHint(G.QPainter.Antialiasing)
    p.setPen(G.QPen(G.QColor('#72879a'),1.5,C.Qt.SolidLine,C.Qt.RoundCap,C.Qt.RoundJoin));p.setBrush(C.Qt.NoBrush)
    def polygon(points):p.drawPolygon(G.QPolygonF([C.QPointF(*point)for point in points]))
    if slot=='Head':polygon([(8,18),(13,10),(27,10),(32,18),(28,25),(12,25)]);p.drawLine(9,18,31,18)
    elif slot=='Body':polygon([(14,9),(7,14),(11,20),(14,18),(14,32),(26,32),(26,18),(29,20),(33,14),(26,9),(20,12)])
    elif slot=='Gloves':
        path=G.QPainterPath();path.moveTo(14,32);path.lineTo(9,23);path.lineTo(9,18);path.quadTo(12,15,15,21)
        for x,y in((15,11),(18,11),(18,21),(18,8),(21,8),(21,21),(21,10),(24,10),(24,22),(24,13),(27,13),(27,28),(23,33)):
            path.lineTo(x,y)
        p.drawPath(path)
    elif slot=='Legs':polygon([(12,9),(28,9),(27,32),(22,32),(20,18),(18,32),(11,32)]);p.drawLine(12,14,28,14)
    elif slot=='Feet':polygon([(15,9),(25,9),(25,23),(32,28),(32,32),(10,32),(10,25),(15,23)]);p.drawLine(11,28,30,28)
    elif slot=='Ears':p.drawLine(20,9,20,15);p.drawEllipse(C.QRectF(12,15,16,18));p.drawEllipse(C.QRectF(18,7,4,4))
    elif slot=='Neck':p.drawArc(C.QRectF(9,5,22,24),180*16,180*16);polygon([(16,28),(20,34),(24,28),(20,25)])
    elif slot=='Wrists':p.drawEllipse(C.QRectF(8,11,24,14));p.drawArc(C.QRectF(8,17,24,14),180*16,180*16);p.drawLine(8,18,8,24);p.drawLine(32,18,32,24)
    else:p.drawEllipse(C.QRectF(10,15,20,19));polygon([(15,13),(20,7),(25,13),(20,18)])
    p.end();return image


def equipment_hover_html(icon,text):
    from html import escape
    image=''
    if isinstance(icon,G.QIcon) and not icon.isNull():
        pixmap=icon.pixmap(80,80).scaled(80,80,C.Qt.KeepAspectRatio,C.Qt.SmoothTransformation)
        buffer=C.QBuffer();buffer.open(C.QIODevice.WriteOnly);pixmap.save(buffer,'PNG')
        import base64
        data=base64.b64encode(bytes(buffer.data())).decode('ascii')
        image='<div align="center"><img width="80" height="80" src="data:image/png;base64,'+data+'"></div><br>'
    return '<div style="max-width:280px">'+image+escape(text).replace('\n','<br>')+'</div>'


class EquipmentHoverDelegate(W.QStyledItemDelegate):
    def helpEvent(self,event,view,option,index):
        if event.type()==C.QEvent.ToolTip and index.isValid():
            text=index.data(C.Qt.ToolTipRole)or''
            W.QToolTip.showText(event.globalPos(),equipment_hover_html(index.data(C.Qt.DecorationRole),text),view,option.rect,10000)
            return True
        return super().helpEvent(event,view,option,index)


class EquipmentDelegate(EquipmentHoverDelegate):
    def sizeHint(self,option,index):return C.QSize(250,82)

    def paint(self,painter,option,index):
        row=index.data(C.Qt.UserRole)
        if not row:return
        painter.save();painter.setRenderHint(G.QPainter.Antialiasing)
        area=option.rect.adjusted(0,3,-5,-3);selected=bool(index.data(C.Qt.UserRole+1))
        hover=bool(option.state&W.QStyle.State_MouseOver)
        painter.setBrush(G.QColor('#253b3e'if selected else'#252f3d'if hover else'#202733'))
        painter.setPen(G.QPen(G.QColor('#79b9af'if selected else'#3c4d60'if hover else'#2d3746')))
        painter.drawRoundedRect(C.QRectF(area),8,8)
        iconbox=C.QRect(area.left()+8,area.top()+4,68,68)
        painter.fillRect(iconbox,G.QColor('#131922'))
        icon=index.data(C.Qt.DecorationRole)
        if isinstance(icon,G.QIcon):icon.paint(painter,C.QRect(iconbox.left()+6,iconbox.top()+6,55,55))
        x=area.left()+86;right=area.right()-14
        painter.setPen(G.QColor(TEXT));font=G.QFont('Microsoft YaHei UI',10);font.setWeight(G.QFont.Medium);painter.setFont(font)
        title=painter.fontMetrics().elidedText(row['name'],C.Qt.ElideRight,max(10,right-x-18))
        painter.drawText(C.QRect(x,area.top()+12,right-x-18,24),C.Qt.AlignVCenter,title)
        if row.get('favorite'):
            painter.setPen(G.QColor('#e9cb86'));painter.drawText(C.QRect(right-15,area.top()+12,18,24),C.Qt.AlignCenter,'★')
        painter.setFont(G.QFont('Microsoft YaHei UI',8));painter.setPen(G.QColor(ACCENT if row.get('is_group')else MUTED))
        detail=(f"同模 · {row['group_count']} 款"if row.get('is_group')and row.get('group_count',1)>1 else f"Lv.{row['level']} · {row['category_name']}")
        detail=painter.fontMetrics().elidedText(detail,C.Qt.ElideRight,max(10,right-x-47))
        painter.drawText(C.QRect(x,area.top()+39,right-x-45,22),C.Qt.AlignVCenter,detail)
        for i in range(2):
            painter.setBrush(G.QColor(ACCENT if row['dye_count']>i else'#384353'));painter.setPen(C.Qt.NoPen)
            painter.drawEllipse(C.QPointF(right-22+i*13,area.top()+51),3,3)
        painter.restore()


class IconEquipmentDelegate(EquipmentHoverDelegate):
    def sizeHint(self,option,index):return C.QSize(72,78)

    def paint(self,painter,option,index):
        row=index.data(C.Qt.UserRole)
        if not row:return
        painter.save();painter.setRenderHint(G.QPainter.Antialiasing)
        area=C.QRectF(option.rect.x()+3,option.rect.y()+4,64,66)
        selected=bool(index.data(C.Qt.UserRole+1));hover=bool(option.state&W.QStyle.State_MouseOver)
        painter.setBrush(G.QColor('#253b3e'if selected else'#293343'if hover else'#202733'))
        painter.setPen(G.QPen(G.QColor('#89d5c4'if selected else'#56677c'if hover else'#334051')))
        folder=row.get('is_group')and row.get('group_count',1)>1
        if folder:
            painter.drawRoundedRect(C.QRectF(area.left()+2,area.top()-2,27,13),3,3)
        painter.drawRoundedRect(area,7,7)
        icon=index.data(C.Qt.DecorationRole)
        if isinstance(icon,G.QIcon):icon.paint(painter,C.QRect(round(area.left()+5),round(area.top()+6),54,54))
        if row.get('favorite'):
            painter.setPen(G.QColor('#f5d184'));painter.setFont(G.QFont('Microsoft YaHei UI',10));painter.drawText(C.QRectF(area.right()-18,area.top(),18,20),C.Qt.AlignCenter,'★')
        if folder:
            badge=C.QRectF(area.right()-28,area.bottom()-18,27,17);painter.setPen(C.Qt.NoPen);painter.setBrush(G.QColor('#263e43'));painter.drawRoundedRect(badge,4,4)
            painter.setPen(G.QColor('#a6e7d8'));painter.setFont(G.QFont('Microsoft YaHei UI',8));painter.drawText(badge,C.Qt.AlignCenter,str(row['group_count']))
        painter.restore()
