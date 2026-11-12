"""Weapon browser metadata and dye controls using the existing Qt widgets."""
from copy import deepcopy
import json
from weapons import WEAPON_SLOTS,COMBAT_CATEGORIES,selection


def categories():
    from app import database
    from contextlib import closing
    with closing(database())as conn:
        return [(r[0],r[1])for r in conn.execute('SELECT DISTINCT ui_category,category_name FROM equipment WHERE ui_category IN ('+','.join('?'for _ in COMBAT_CATEGORIES)+') ORDER BY ui_category',COMBAT_CATEGORIES)]


def metadata(identifier):
    from app import database
    from contextlib import closing
    with closing(database())as conn:
        row=conn.execute('SELECT * FROM equipment WHERE item_id=?',(identifier,)).fetchone()
    if row is None or row['slot']not in WEAPON_SLOTS or row['ui_category']not in COMBAT_CATEGORIES:raise ValueError('请选择战斗武器')
    row=dict(row);row.update(model_set=f"{row['model_main']&0xffffffff}:{row['model_sub']&0xffffffff}",weapon=True,tags=[],favorite=False);return row


def catalogue_rows(request):
    from app import database
    from contextlib import closing
    from catalogue import level_filter
    with closing(database())as conn:
        fields='e.*,CAST(e.model_main & 4294967295 AS TEXT)||\':\'||CAST(e.model_sub & 4294967295 AS TEXT) AS model_set'
        base='e.slot=? AND e.ui_category IN ('+','.join('?'for _ in COMBAT_CATEGORIES)+')';args=[request['slot'],*COMBAT_CATEGORIES]
        if request.get('weapon_category'):base+=' AND e.ui_category=?';args.append(request['weapon_category'])
        match='';extra=[]
        for term in request['query'][:200].split():
            match+=" AND (e.name LIKE ? ESCAPE '\\' OR CAST(e.item_id AS TEXT)=?)";extra.extend(('%'+term.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%',term))
        levels,level_args=level_filter(request['min_level'],request['max_level'],column='e.level');match+=levels;extra+=level_args
        folder=request['model_set']
        if folder is not None:match+=' AND CAST(e.model_main & 4294967295 AS TEXT)||\':\'||CAST(e.model_sub & 4294967295 AS TEXT)=?';extra.append(str(folder))
        if request['grouped']:
            sql=('WITH ranked AS MATERIALIZED(SELECT '+fields+',ROW_NUMBER()OVER(PARTITION BY e.model_main & 4294967295,e.model_sub & 4294967295 ORDER BY e.dye_count DESC,e.item_id DESC) AS rank,COUNT(*)OVER(PARTITION BY e.model_main & 4294967295,e.model_sub & 4294967295) AS group_count FROM equipment e WHERE '+base+'), '
                'matched AS MATERIALIZED(SELECT DISTINCT CAST(e.model_main & 4294967295 AS TEXT)||\':\'||CAST(e.model_sub & 4294967295 AS TEXT) AS model_set FROM equipment e WHERE '+base+match+') '
                'SELECT ranked.* FROM ranked WHERE rank=1 AND model_set IN(SELECT model_set FROM matched) ORDER BY item_id DESC')
            values=[*args,*args,*extra]
        else:sql='SELECT '+fields+',1 AS group_count FROM equipment e WHERE '+base+match+' ORDER BY dye_count DESC,item_id DESC';values=[*args,*extra]
        rows=[{**dict(r),'weapon':True,'is_group':request['grouped'],'tags':[],'favorite':False}for r in conn.execute(sql,values)]
    offset=request['offset'];located=None;target=request.get('locate_item')
    if target:
        wanted=metadata(target)['model_set'];index=next((i for i,r in enumerate(rows)if(r['model_set']==wanted if request['grouped']else r['item_id']==target)),None)
        offset=(index//60)*60 if index is not None else 0;located=index%60 if index is not None else None
    result={'items':rows[offset:offset+60],'total':len(rows),'offset':offset,'more':offset+60<len(rows)}
    if located is not None:result['located_index']=located
    return result


def dye_settings(c):
    if not hasattr(c,'weapon_saved_dyes'):
        values={s:{'first':0,'second':0}for s in WEAPON_SLOTS};raw=c.library.setting('try_on_weapon_dyes_v1')if c.library else None
        try:
            saved=json.loads(raw)if raw and len(raw)<=2048 else None
            if isinstance(saved,dict)and set(saved)==set(WEAPON_SLOTS)and all(isinstance(v,dict)and set(v)=={'first','second'}and all(type(n)is int and 0<=n<=254 for n in v.values())for v in saved.values()):values=saved
        except(ValueError,TypeError):pass
        if not raw:
            for s,v in c.state.get('weapons',{}).items():values[s]={k:v[k]for k in('first','second')}
        c.weapon_saved_dyes=values
    return c.weapon_saved_dyes


def set_dyes(c,slot,values):
    settings=deepcopy(dye_settings(c));settings[slot].update(values)
    if c.library:c.library.set_setting('try_on_weapon_dyes_v1',json.dumps(settings))
    c.weapon_saved_dyes=settings
    choice=c.state.get('weapons',{}).get(slot,{})
    row=c.weapon_items.get(choice.get('item_id'),{});count=row.get('dye_count',0)
    for channel,key in enumerate(('first','second'),1):
        if key in values and choice.get('item_id'):choice[key]=values[key]if count>=channel else 0
    c.update_outfit();c.queue_render()


def dye_action(c,slot,channel=None,*,swap=False,reset=False):
    if not c.ready or c.closing or slot in c.hidden_weapons:return
    choice=c.state.get('weapons',{}).get(slot,{});row=c.weapon_items.get(choice.get('item_id'),{});count=row.get('dye_count',0)
    if swap:
        if count<2:return
        values=dye_settings(c)[slot];return set_dyes(c,slot,{'first':values['second'],'second':values['first']})
    if count<(channel or 1):return
    key='first'if channel==1 else'second'
    if reset:return set_dyes(c,slot,{key:0})
    from qt_theme import UNDYED_SWATCH_COLOR
    colors=[{'value':0,'label':'原色','color':UNDYED_SWATCH_COLOR,'metallic':False}]+[{'value':d['stain_id'],'label':d['name'],'color':'#%06x'%(d['color']&0xffffff),'metallic':d.get('metallic',False)}for d in c.boot['dyes']if d['stain_id']]
    dialog=c.palette(('主武器'if slot=='MainHand'else'副武器')+f' · 第{channel}染色区',colors,dye_settings(c)[slot][key],lambda v:set_dyes(c,slot,{key:v}),dye_sort=True)
    dialog.dye_slot=slot;dialog.dye_channel=key
