"""Read-only catalogue queries for slot/model folders and exact item choices."""


FIELDS='item_id,name,icon_id,dye_count,level,category_name,(model_main & 65535) AS model_set'


def query_filter(query):
    if not query:return '',[]
    escaped=query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
    return " AND (name LIKE ? ESCAPE '\\' OR CAST(item_id AS TEXT)=?)",['%'+escaped+'%',query]


def equipment_rows(conn,slot,query='',offset=0,limit=60,*,grouped=False,model_set=None,preferred_item=0):
    slot='FingerL'if slot=='FingerR'else slot
    search,args=query_filter(query[:80].strip())
    if grouped:
        # Rank over the ENTIRE model group before searching. Searching a
        # one-dye variant must not rename its two-dye representative folder.
        cte=('WITH ranked AS (SELECT '+FIELDS+', '
            'ROW_NUMBER() OVER(PARTITION BY (model_main & 65535) ORDER BY dye_count DESC,item_id DESC) AS rank, '
            'COUNT(*) OVER(PARTITION BY (model_main & 65535)) AS group_count FROM equipment WHERE slot=?), '
            'matching AS (SELECT DISTINCT (model_main & 65535) AS model_set FROM equipment WHERE slot=?'+search+'), '
            'folders AS (SELECT ranked.* FROM ranked JOIN matching USING(model_set) WHERE rank=1) ')
        params=[slot,slot,*args]
        total,item_total=conn.execute(cte+'SELECT count(*),COALESCE(sum(group_count),0) FROM folders',params).fetchone()
        rows=conn.execute(cte+'SELECT * FROM folders ORDER BY '
            '(model_set=(SELECT model_main & 65535 FROM equipment WHERE item_id=?)) DESC,item_id DESC LIMIT ? OFFSET ?',
            [*params,preferred_item,limit,offset]).fetchall()
        result=[{**dict(row),'is_group':True}for row in rows]
        for row in result:row.pop('rank',None)
    else:
        where='slot=?';params=[slot]
        if model_set is not None:where+=' AND (model_main & 65535)=?';params.append(model_set)
        where+=search;params+=args
        total=conn.execute('SELECT count(*) FROM equipment WHERE '+where,params).fetchone()[0]
        item_total=total
        order='dye_count DESC,item_id DESC'if model_set is not None else'(item_id=?) DESC,item_id DESC'
        rows=conn.execute('SELECT '+FIELDS+' FROM equipment WHERE '+where+' ORDER BY '+order+' LIMIT ? OFFSET ?',
            [*params,*([]if model_set is not None else[preferred_item]),limit,offset]).fetchall()
        result=[dict(row)for row in rows]
    return {'items':result,'total':total,'item_total':item_total,'offset':offset,'more':offset+len(result)<total,'grouped':grouped,'model_set':model_set}
