"""Style folders and original facewear color variants, separate from dyes."""
from facewear import catalogue
from qt_theme import W,C,G,label,button,vertical,horizontal,visibility_icon


class FacewearCard(W.QFrame):
    def __init__(self,controller,slot):
        super().__init__();self.c=controller;self.slot=slot;self.setObjectName('OutfitCard')
        outer=vertical(self,8,5);row=horizontal(spacing=6)
        self.image=label();self.image.setFixedSize(32,32);row.addWidget(self.image)
        self.name=label('未试穿面饰');self.name.setMinimumWidth(0);row.addWidget(self.name,1)
        self.eye=button('',lambda:self.c.toggle_facewear(self.slot),ghost=True);self.eye.setFixedSize(24,24);self.eye.setIconSize(C.QSize(18,18));row.addWidget(self.eye)
        self.remove=button('×',lambda:self.c.choose_facewear(0,self.slot),ghost=True);self.remove.setFixedSize(24,24);row.addWidget(self.remove);outer.addLayout(row)
        outer.addWidget(button('选择面部配饰',self.c.open_facewear,ghost=True))

    def refresh(self):
        active=self.c.state.get('facewear',[0,0])[self.slot];hidden=self.c.hidden_facewear.get(self.slot,0);identifier=active or hidden
        item=catalogue()[2].get(identifier,{});text=item.get('name','未试穿面饰')
        self.name.setText(G.QFontMetrics(self.name.font()).elidedText(text,C.Qt.ElideRight,max(90,self.width()-112)))
        self.name.setToolTip(text+('（已取消试穿）'if hidden else''));self.image.setPixmap(self.c.icon(item['icon_id']).pixmap(32,32)if item else G.QPixmap())
        self.eye.setIcon(visibility_icon(bool(hidden)));self.eye.setToolTip('取消试穿面饰'if active else'恢复试穿面饰')
        self.eye.setEnabled(bool(identifier)and self.c.ready);self.remove.setEnabled(bool(identifier)and self.c.ready)


class FacewearDialog(W.QDialog):
    def __init__(self,controller):
        super().__init__(controller.window);self.c=controller;self.style_id=None;self.folder_scroll=0;self.icon_buttons={};self.selected_id=0
        self.setWindowTitle('面部配饰');self.setWindowModality(C.Qt.NonModal);self.resize(720,600);self.setMinimumSize(520,420)
        outer=vertical(self,16,10);header=horizontal();self.back=button('‹ 返回款式',self.go_back,ghost=True);header.addWidget(self.back)
        self.title=label('面部配饰 · 款式目录','Title');header.addWidget(self.title,1);outer.addLayout(header)
        self.search=W.QLineEdit();self.search.setPlaceholderText('搜索面饰名称、颜色或编号');self.search.setClearButtonEnabled(True);outer.addWidget(self.search)
        self.list=W.QListWidget();self.list.setViewMode(W.QListView.IconMode);self.list.setResizeMode(W.QListView.Adjust);self.list.setMovement(W.QListView.Static)
        self.list.setIconSize(C.QSize(64,64));self.list.setGridSize(C.QSize(108,112));self.list.setWordWrap(True);self.list.setSpacing(3);self.list.setHorizontalScrollBarPolicy(C.Qt.ScrollBarAlwaysOff);outer.addWidget(self.list,1)
        self.note=label('同款的不同颜色归在一个目录内；面饰颜色使用游戏原始变体。','Muted');self.note.setWordWrap(True);outer.addWidget(self.note)
        actions=horizontal();self.count=label('','Muted');actions.addWidget(self.count,1);self.wear=button('试穿所选颜色',self.apply,primary=True);actions.addWidget(self.wear);actions.addWidget(button('关闭',self.close,ghost=True));outer.addLayout(actions)
        self.list.itemClicked.connect(self.click);self.list.currentItemChanged.connect(self.select);self.search.textChanged.connect(self.populate);self.populate()

    def populate(self):
        self.icon_buttons.clear();self.list.clear();self.selected_id=0;self.wear.setEnabled(False)
        self.note.setText('同款的不同颜色归在一个目录内；面饰颜色使用游戏原始变体。')
        record,styles,variants=catalogue();terms=self.search.text().casefold().split();chosen=self.c.state.get('facewear',[0,0]);self.back.setVisible(self.style_id is not None)
        source=record['styles']if self.style_id is None else styles[self.style_id]['variants']
        self.title.setText('面部配饰 · 款式目录'if self.style_id is None else styles[self.style_id]['name']+' · 颜色')
        for row in source:
            text=row['name']+' '+str(row['id'])
            if self.style_id is None:text+=' '+' '.join(r['name']+' '+str(r['id'])for r in row['variants'])
            else:text+=' '+styles[self.style_id]['name']
            if not all(t in text.casefold()for t in terms):continue
            folder=self.style_id is None;name=row['name']+(f"\n{len(row['variants'])} 色 · 目录"if folder else'')
            item=W.QListWidgetItem(self.c.icon(row['icon_id']),name);item.setData(C.Qt.UserRole,row['id']);item.setToolTip(text)
            item.setTextAlignment(C.Qt.AlignHCenter);self.list.addItem(item);self.icon_buttons.setdefault(row['icon_id'],[]).append(item)
            if not folder and row['id']in chosen:self.list.setCurrentItem(item)
        self.count.setText(str(self.list.count())+(' 个款式目录'if self.style_id is None else' 个颜色变体'))
        self.list.doItemsLayout()

    def refresh_icons(self,changed):
        for identifier in changed:
            for item in self.icon_buttons.get(identifier,[]):item.setIcon(self.c.icon(identifier))

    def click(self,item):
        if self.style_id is not None:return
        self.folder_scroll=self.list.verticalScrollBar().value();self.style_id=item.data(C.Qt.UserRole);self.populate()

    def select(self,item,*_):
        self.selected_id=item.data(C.Qt.UserRole)if item and self.style_id is not None else 0
        self.wear.setEnabled(bool(self.selected_id))
        if self.selected_id:self.note.setText(catalogue()[2][self.selected_id]['name']+' · 选中后点击“试穿所选颜色”。')

    def go_back(self):
        self.style_id=None;self.populate();self.list.doItemsLayout();self.list.verticalScrollBar().setValue(self.folder_scroll)

    def apply(self):
        if self.selected_id:self.c.choose_facewear(self.selected_id)


def add_template_facewear(dialog,record):
    for identifier in record.get('state',{}).get('facewear',[0,0]):
        if not identifier:continue
        item=catalogue()[2].get(identifier)
        if item is None:continue
        card=W.QFrame();card.setObjectName('OutfitCard');row=horizontal(card,8,10);icon=label();icon.setFixedSize(36,36);row.addWidget(icon)
        dialog.detail_icons.append((icon,item['icon_id']));text=label('面部配饰 · '+item['name']);text.setWordWrap(True);row.addWidget(text,1);dialog.gear_layout.addWidget(card)
