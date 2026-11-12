"""Facewear cards and metadata rows for the shared equipment browser."""
from facewear import catalogue
from qt_theme import W,C,G,label,button,vertical,horizontal,visibility_icon


class FacewearCard(W.QFrame):
    def __init__(self,controller,slot):
        super().__init__();self.c=controller;self.slot=slot;self.setObjectName('OutfitCard');self.setFixedHeight(80)
        self.setCursor(C.Qt.PointingHandCursor);outer=horizontal(self,8,9)
        self.image=label();self.image.setFixedSize(42,42);outer.addWidget(self.image)
        content=vertical(spacing=5);outer.addLayout(content,1);top=horizontal();top.addWidget(label('面饰','Muted'));top.addStretch()
        self.eye=button('',lambda:self.c.toggle_facewear(self.slot),ghost=True);self.eye.setFixedSize(26,24);self.eye.setStyleSheet('padding:2px');self.eye.setIconSize(C.QSize(20,20));top.addWidget(self.eye)
        self.remove=button('×',lambda:self.c.choose_facewear(0,self.slot),ghost=True);self.remove.setProperty('clear',True);self.remove.setFixedSize(16,16);top.addWidget(self.remove);content.addLayout(top)
        self.name=label('未试穿面饰');self.name.setMinimumWidth(0);content.addWidget(self.name)

    def mousePressEvent(self,event):
        if event.button()==C.Qt.LeftButton:self.c.open_facewear()
        super().mousePressEvent(event)

    def refresh(self):
        active=self.c.state.get('facewear',[0,0])[self.slot];hidden=self.c.hidden_facewear.get(self.slot,0);identifier=active or hidden
        item=catalogue()[2].get(identifier,{});text=item.get('name','未试穿面饰')
        self.name.setText(G.QFontMetrics(self.name.font()).elidedText(text,C.Qt.ElideRight,max(70,self.width()-82)))
        self.setToolTip(text+('（已取消试穿）'if hidden else'')+'\n点击在装备列表中选择面饰')
        self.image.setPixmap(self.c.icon(item['icon_id']).pixmap(40,40)if item else G.QPixmap())
        self.eye.setIcon(visibility_icon(bool(hidden)));self.eye.setToolTip('取消试穿面饰'if active else'恢复试穿面饰')
        self.eye.setVisible(bool(identifier));self.remove.setVisible(bool(identifier))
        self.eye.setEnabled(bool(identifier)and self.c.ready);self.remove.setEnabled(bool(identifier)and self.c.ready)


def catalogue_rows(request):
    """Local text only; fetch icons only when the resulting page is displayed."""
    record,styles,variants=catalogue();folder=request['model_set'];terms=request['query'].casefold().split();rows=[]
    source=record['styles']if folder is None else styles[folder]['variants']
    for item in source:
        grouped=folder is None
        text=item['name']+' '+str(item['id'])
        text+=' '+' '.join(v['name']+' '+str(v['id'])for v in item['variants'])if grouped else' '+styles[folder]['name']
        if not all(term in text.casefold()for term in terms):continue
        rows.append({'item_id':item['variants'][0]['id']if grouped else item['id'],'name':item['name'],'icon_id':item['icon_id'],
            'model_set':item['id']if grouped else folder,'is_group':grouped,'group_count':len(item['variants'])if grouped else 1,
            'facewear':True,'group_label':'颜色','level':0,'category_name':'面饰','dye_count':0,'tags':[],'favorite':False})
    offset=request['offset'];located=None;target=request.get('locate_item')
    if target:
        style=variants.get(target,{}).get('style_id')
        found=next((i for i,row in enumerate(rows)if(row['model_set']==style if folder is None else row['item_id']==target)),None)
        offset=(found//60)*60 if found is not None else 0;located=found%60 if found is not None else None
    result={'items':rows[offset:offset+60],'offset':offset,'more':offset+60<len(rows),'total':len(rows)}
    if located is not None:result['located_index']=located
    return result


def add_template_facewear(dialog,record):
    data,_,variants=catalogue();chosen=record.get('state',{}).get('facewear',[0,0])
    for slot in sorted({style['slot']for style in data['styles']}):
        identifier=chosen[slot];item=variants.get(identifier)
        card=W.QFrame();card.setObjectName('OutfitCard');card.setProperty('facewearSlot',slot)
        card.setMinimumHeight(58);card.setSizePolicy(W.QSizePolicy.Expanding,W.QSizePolicy.Fixed)
        row=horizontal(card,8,10);icon=label();icon.setFixedSize(36,36);row.addWidget(icon)
        if item:dialog.detail_icons.append((icon,item['icon_id']))
        name=item['name']if item else(f'面饰 #{identifier}'if identifier else'未设置')
        text=label('面饰 · '+name);text.setWordWrap(True);row.addWidget(text,1);dialog.gear_layout.addWidget(card)
