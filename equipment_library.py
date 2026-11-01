"""Local, per-item tags/favorites alongside the read-only CN catalogue."""
from contextlib import closing,contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading

PARTS={'Head':'头饰','Body':'上装','Gloves':'手套','Legs':'下装','Feet':'鞋靴','Ears':'耳饰','Neck':'项链','Wrists':'手镯','FingerL':'戒指'}
RULES=(('礼服',('礼服','礼裙','晚礼')),('裙装',('裙','礼服')),('铠甲',('铠','甲','锁子')),
    ('长袍',('长袍','法袍','僧袍','道袍')),('衬衫',('衬衫',)),('兜帽',('兜帽',)),('面具',('面具','面罩')),
    ('长靴',('长靴','高筒','过膝')),('短靴',('短靴','矮靴')),('凉鞋',('凉鞋','便凉鞋')),
    ('泳装',('泳','比基尼','海滩')),('运动',('运动','球衣','球鞋','跑鞋','卫衣')),
    ('冬装',('冬','雪','保暖','羽绒')),('东方风格',('东方','旗袍','和服','浴衣','汉服')),
    ('休闲',('休闲','便服','便裤','便鞋','卫衣','牛仔')),('花卉',('花冠','花环','玫瑰','百合','樱花')),
    ('眼镜',('眼镜','墨镜','单片镜')),('手工风格',('围裙','工作服','工装')))


def automatic_tags(row):
    tags=[('不可染色','单染色','双染色')[min(2,max(0,row['dye_count']))]]
    for tag,words in RULES:
        if any(word in row['name']for word in words):tags.append(tag)
    return list(dict.fromkeys(tags))


def clean_tags(values):
    if not isinstance(values,(list,tuple))or len(values)>40:raise ValueError('每件装备最多40个标签')
    result=[];seen=set()
    for value in values:
        if not isinstance(value,str):raise ValueError('标签必须是文字')
        value=' '.join(value.split()).strip('#')
        if not value:continue
        if len(value)>24 or any(c in value for c in(',','，',';','；','\x00')):raise ValueError('标签最长24字，请勿包含逗号或分号')
        if value.casefold()not in seen:result.append(value);seen.add(value.casefold())
    return result


def parse_tags(text):return clean_tags(re.split(r'[,，;；\n]+',text))


def like(value):return '%'+value.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'


class EquipmentLibrary:
    def __init__(self,path,catalog,*,game=None):
        self.path=Path(path).resolve();self.catalog=Path(catalog).resolve();self.lock=threading.RLock()
        if self.path==self.catalog:raise ValueError('装备标签库必须独立于原始目录')
        from catalogue_cache import ensure_catalogue_cache
        self.catalog_cache=ensure_catalogue_cache(self.catalog)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect()as conn:
            conn.executescript('''CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS favorites(item_id INTEGER PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS tags(item_id INTEGER NOT NULL,tag TEXT NOT NULL COLLATE NOCASE,origin TEXT NOT NULL,PRIMARY KEY(item_id,tag));
                CREATE INDEX IF NOT EXISTS tags_label ON tags(tag,item_id);
                CREATE TABLE IF NOT EXISTS model_hints(slot TEXT NOT NULL,model_set INTEGER NOT NULL,tag TEXT NOT NULL,PRIMARY KEY(slot,model_set,tag));
                CREATE TABLE IF NOT EXISTS hidden_auto(item_id INTEGER NOT NULL,tag TEXT NOT NULL COLLATE NOCASE,PRIMARY KEY(item_id,tag));''')
        with closing(sqlite3.connect(self.catalog.as_uri()+'?mode=ro',uri=True))as source:
            source.row_factory=sqlite3.Row
            rows=[dict(r)for r in source.execute('SELECT item_id,name,slot,dye_count,model_main FROM equipment WHERE slot IN('+','.join('?'for _ in PARTS)+') ORDER BY item_id',list(PARTS))]
        self.model_tags={};self.feature_source=None
        if game is not None:
            from equipment_tag_features import load_model_features
            self.model_tags,self.feature_source=load_model_features(game,rows)
            with self.connect()as conn:
                conn.execute('DELETE FROM model_hints')
                conn.executemany('INSERT INTO model_hints VALUES(?,?,?)',[(slot,model,tag)for(slot,model),tags in self.model_tags.items()for tag in tags])
                conn.execute("INSERT OR REPLACE INTO settings VALUES('model_feature_source',?)",(json.dumps(self.feature_source),))
        else:
            with self.connect()as conn:
                for slot,model,tag in conn.execute('SELECT slot,model_set,tag FROM model_hints ORDER BY slot,model_set,tag'):
                    self.model_tags.setdefault((slot,model),[]).append(tag)
                stored=conn.execute("SELECT value FROM settings WHERE key='model_feature_source'").fetchone()
                if stored:self.feature_source=json.loads(stored[0])
        signature=hashlib.sha256(json.dumps([3,RULES,rows,self.feature_source],ensure_ascii=False).encode()).hexdigest()
        with self.connect()as conn:
            existing=conn.execute("SELECT value FROM settings WHERE key='auto_signature'").fetchone()
            if existing is None or existing[0]!=signature:
                conn.execute("DELETE FROM tags WHERE origin='auto'")
                conn.executemany("INSERT OR IGNORE INTO tags(item_id,tag,origin) SELECT ?,?,'auto' WHERE NOT EXISTS(SELECT 1 FROM hidden_auto WHERE item_id=? AND tag=?)",
                    [(r['item_id'],tag,r['item_id'],tag)for r in rows for tag in self.automatic_tags(r)])
                conn.execute("INSERT OR REPLACE INTO settings VALUES('auto_signature',?)",(signature,))

    def automatic_tags(self,row):
        return list(dict.fromkeys(automatic_tags(row)+self.model_tags.get((row['slot'],row['model_main']&65535),[])))

    @contextmanager
    def connect(self):
        conn=sqlite3.connect(self.path,timeout=10)
        try:
            with conn:yield conn
        finally:conn.close()

    def item(self,identifier):
        with closing(sqlite3.connect(self.catalog.as_uri()+'?mode=ro',uri=True))as conn:
            conn.row_factory=sqlite3.Row;row=conn.execute('SELECT *,model_main & 65535 AS model_set FROM equipment WHERE item_id=?',(identifier,)).fetchone()
        if row is None:raise ValueError('装备不存在')
        result=dict(row)
        if result['slot']not in PARTS:raise ValueError('此装备部位不在当前软件范围内')
        with self.connect()as conn:
            result['favorite']=bool(conn.execute('SELECT 1 FROM favorites WHERE item_id=?',(identifier,)).fetchone())
            result['tag_details']=[{'tag':r[0],'origin':r[1]}for r in conn.execute('SELECT tag,origin FROM tags WHERE item_id=? ORDER BY origin,tag',(identifier,))]
        model_tags=self.model_tags.get((result['slot'],result['model_set']),[])
        for detail in result['tag_details']:
            detail['source']='user'if detail['origin']=='user'else'model'if detail['tag']in model_tags else'name'if detail['tag']in dict(RULES)else'dye'
        result['tags']=[r['tag']for r in result['tag_details']];return result

    def members(self,slot,model):
        slot='FingerL'if slot=='FingerR'else slot
        with closing(sqlite3.connect(self.catalog.as_uri()+'?mode=ro',uri=True))as conn:
            return [{'item_id':r[0],'name':r[1]}for r in conn.execute('SELECT item_id,name FROM equipment WHERE slot=? AND (model_main & 65535)=? ORDER BY dye_count DESC,item_id DESC',(slot,model))]

    def save(self,identifier,tags,*,favorite=None):
        tags=clean_tags(tags);row=self.item(identifier);auto=self.automatic_tags(row)
        with self.lock,self.connect()as conn:
            conn.execute('DELETE FROM tags WHERE item_id=?',(identifier,));conn.execute('DELETE FROM hidden_auto WHERE item_id=?',(identifier,))
            desired={tag.casefold()for tag in tags}
            conn.executemany('INSERT INTO hidden_auto VALUES(?,?)',[(identifier,tag)for tag in auto if tag.casefold()not in desired])
            auto_lookup={tag.casefold()for tag in auto}
            conn.executemany('INSERT INTO tags VALUES(?,?,?)',[(identifier,tag,'auto'if tag.casefold()in auto_lookup else'user')for tag in tags])
            if favorite is not None:
                if type(favorite)is not bool:raise ValueError('收藏状态不正确')
                if favorite:conn.execute('INSERT OR IGNORE INTO favorites VALUES(?)',(identifier,))
                else:conn.execute('DELETE FROM favorites WHERE item_id=?',(identifier,))
        return self.item(identifier)

    def set_favorite(self,identifier,value):
        if type(value)is not bool:raise ValueError('收藏状态不正确')
        self.item(identifier)
        with self.lock,self.connect()as conn:
            if value:conn.execute('INSERT OR IGNORE INTO favorites VALUES(?)',(identifier,))
            else:conn.execute('DELETE FROM favorites WHERE item_id=?',(identifier,))

    def favorite_ids(self):
        with self.connect()as conn:return tuple(r[0]for r in conn.execute('SELECT item_id FROM favorites ORDER BY item_id'))

    def tags(self):
        with self.connect()as conn:return [{'tag':r[0],'count':r[1]}for r in conn.execute('SELECT tag,count(*) FROM tags GROUP BY tag ORDER BY count(*) DESC,tag LIMIT 500')]

    def setting(self,key,default=None):
        with self.connect()as conn:row=conn.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return row[0]if row else default

    def set_setting(self,key,value):
        with self.connect()as conn:conn.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,str(value)))

    def refresh_annotations(self,rows,slot):
        """Refresh badges/tags in place, without querying or changing order."""
        if not rows:return
        slot='FingerL'if slot=='FingerR'else slot;tag_map={};ids=list(dict.fromkeys(row['item_id']for row in rows))
        with self.connect()as conn:
            favorites={r[0]for r in conn.execute('SELECT item_id FROM favorites')}
            for start in range(0,len(ids),500):
                chunk=ids[start:start+500]
                for identifier,tag in conn.execute('SELECT item_id,tag FROM tags WHERE item_id IN('+','.join('?'for _ in chunk)+') ORDER BY origin,tag',chunk):
                    tag_map.setdefault(identifier,[]).append(tag)
        with closing(sqlite3.connect(self.catalog.as_uri()+'?mode=ro',uri=True))as conn:
            conn.execute('ATTACH DATABASE ? AS notes',(str(self.path),))
            groups=dict(conn.execute('SELECT e.model_main & 65535,count(*) FROM equipment e JOIN notes.favorites f ON f.item_id=e.item_id WHERE e.slot=? GROUP BY e.model_main & 65535',(slot,)))
        for row in rows:
            row['tags']=tag_map.get(row['item_id'],[])
            if row.get('is_group'):
                row['favorite_count']=groups.get(row['model_set'],0);row['favorite']=row['favorite_count']>0
            else:row['favorite']=row['item_id']in favorites

    def equipment_rows(self,conn,slot,query='',offset=0,limit=60,*,grouped=False,model_set=None,preferred_item=0,favorites_only=False,tags=(),favorite_snapshot=None):
        slot='FingerL'if slot=='FingerR'else slot;tags=clean_tags(list(tags));conn.execute('ATTACH DATABASE ? AS notes',(str(self.path),))
        cache_attached=False
        try:
            prefix='';prefix_args=[];favorite_table='notes.favorites'
            if favorite_snapshot is not None:
                if any(type(value)is not int or value<1 for value in favorite_snapshot):raise ValueError('收藏排序快照不正确')
                prefix='WITH favorite_order AS (SELECT CAST(value AS INTEGER) AS item_id FROM json_each(?)) '
                prefix_args=[json.dumps(list(dict.fromkeys(favorite_snapshot)))];favorite_table='favorite_order'
            predicates=['e.slot=?'];args=[slot]
            if model_set is not None:predicates.append('(e.model_main & 65535)=?');args.append(model_set)
            if favorites_only:predicates.append('EXISTS(SELECT 1 FROM '+favorite_table+' WHERE item_id=e.item_id)')
            for tag in tags:
                predicates.append('EXISTS(SELECT 1 FROM notes.tags WHERE item_id=e.item_id AND tag=? COLLATE NOCASE)');args.append(tag)
            for term in query[:200].strip().split():
                tag_only=term.startswith(('#','标签:','标签：','tag:'))
                value=term.lstrip('#')if term.startswith('#')else term.split(':',1)[-1].split('：',1)[-1]if tag_only else term
                if not value:continue
                tag_sql="EXISTS(SELECT 1 FROM notes.tags WHERE item_id=e.item_id AND tag LIKE ? ESCAPE '\\')"
                if tag_only:predicates.append(tag_sql);args.append(like(value))
                else:
                    predicates.append("(e.name LIKE ? ESCAPE '\\' OR CAST(e.item_id AS TEXT)=? OR "+tag_sql+')')
                    args.extend((like(value),value,like(value)))
            where=' AND '.join(predicates)
            fields='item_id,name,icon_id,dye_count,level,category_name,(model_main & 65535) AS model_set'
            if grouped:
                # Select representative before filtering; favorite/search can
                # match any ONE member, without renaming the model folder.
                # The source-keyed folder index is shared by all pages and
                # ships with the package; tags/favorites remain live queries.
                conn.execute('ATTACH DATABASE ? AS cached',(str(self.catalog_cache),))
                cache_attached=True
                cte=('WITH ranked AS (SELECT '+fields+',1 AS rank,group_count FROM cached.model_folders WHERE slot=?),'
                    'matched AS (SELECT DISTINCT e.model_main & 65535 AS model_set FROM equipment e WHERE '+where+'),'
                    'favorite_groups AS (SELECT x.model_main & 65535 AS model_set,count(*) AS favorite_count FROM equipment x '
                    'JOIN '+favorite_table+' f ON f.item_id=x.item_id WHERE x.slot=? GROUP BY x.model_main & 65535),'
                    'folders AS (SELECT ranked.*,COALESCE(favorite_groups.favorite_count,0) AS favorite_count '
                    'FROM ranked LEFT JOIN favorite_groups USING(model_set) WHERE rank=1 AND ranked.model_set IN (SELECT model_set FROM matched)) ')
                if prefix:cte=prefix.rstrip()+','+cte.removeprefix('WITH ')
                parameters=[*prefix_args,slot,*args,slot]
                total,item_total=conn.execute(cte+'SELECT count(*),COALESCE(sum(group_count),0) FROM folders',parameters).fetchone()
                rows=conn.execute(cte+'SELECT * FROM folders ORDER BY (favorite_count>0) DESC,(model_set=(SELECT model_main & 65535 FROM equipment WHERE item_id=?)) DESC,item_id DESC LIMIT ? OFFSET ?',
                    [*parameters,preferred_item,limit,offset]).fetchall()
                result=[{**dict(row),'is_group':True,'favorite':row['favorite_count']>0}for row in rows]
                for row in result:row.pop('rank',None)
            else:
                total=conn.execute(prefix+'SELECT count(*) FROM equipment e WHERE '+where,[*prefix_args,*args]).fetchone()[0];item_total=total
                select=','.join('e.'+f if f!='(model_main & 65535) AS model_set'else'(e.model_main & 65535) AS model_set'for f in fields.split(','))
                order='dye_count DESC,item_id DESC'if model_set is not None else'(e.item_id=?) DESC,e.item_id DESC'
                rows=conn.execute(prefix+'SELECT '+select+',EXISTS(SELECT 1 FROM '+favorite_table+' WHERE item_id=e.item_id) AS favorite FROM equipment e WHERE '+where+' ORDER BY favorite DESC,'+order+' LIMIT ? OFFSET ?',
                    [*prefix_args,*args,*([]if model_set is not None else[preferred_item]),limit,offset]).fetchall()
                result=[{**dict(row),'favorite':bool(row['favorite'])}for row in rows]
            for row in result:
                row['tags']=[r[0]for r in conn.execute('SELECT tag FROM notes.tags WHERE item_id=? ORDER BY origin,tag',(row['item_id'],))]
            if favorite_snapshot is not None:self.refresh_annotations(result,slot)
            return {'items':result,'total':total,'item_total':item_total,'offset':offset,'more':offset+len(result)<total,'grouped':grouped,'model_set':model_set}
        finally:
            if cache_attached:conn.execute('DETACH DATABASE cached')
            conn.execute('DETACH DATABASE notes')
