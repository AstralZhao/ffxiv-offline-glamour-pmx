"""Read-only catalogue queries for slot/model folders and exact item choices."""


FIELDS='item_id,name,icon_id,dye_count,level,category_name,(model_main & 65535) AS model_set'


def query_filter(query):
    if not query:return '',[]
    escaped=query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
    return " AND (name LIKE ? ESCAPE '\\' OR CAST(item_id AS TEXT)=?)",['%'+escaped+'%',query]


def level_filter(min_level=None,max_level=None,*,column='level'):
    for value in(min_level,max_level):
        if value is not None and(type(value)is not int or not 1<=value<=999):raise ValueError('装备等级范围必须为1至999')
    if min_level is not None and max_level is not None and min_level>max_level:raise ValueError('最低等级不能高于最高等级')
    clauses=[];args=[]
    for value,operator in((min_level,'>='),(max_level,'<=')):
        if value is not None:clauses.append(column+operator+'?');args.append(value)
    return ''.join(' AND '+clause for clause in clauses),args


def equipment_rows(conn,slot,query='',offset=0,limit=60,*,grouped=False,model_set=None,preferred_item=0,min_level=None,max_level=None):
    slot='FingerL'if slot=='FingerR'else slot
    search,args=query_filter(query[:80].strip())
    levels,level_args=level_filter(min_level,max_level);search+=levels;args+=level_args
    if grouped:
        # Rank over the ENTIRE model group before searching. Searching a
        # one-dye variant must not rename its two-dye representative folder.
        cte=('WITH ranked AS MATERIALIZED (SELECT '+FIELDS+', '
            'ROW_NUMBER() OVER(PARTITION BY (model_main & 65535) ORDER BY dye_count DESC,item_id DESC) AS rank, '
            'COUNT(*) OVER(PARTITION BY (model_main & 65535)) AS group_count FROM equipment WHERE slot=?), '
            'matching AS (SELECT DISTINCT (model_main & 65535) AS model_set FROM equipment WHERE slot=?'+search+'), '
            'folders AS (SELECT ranked.* FROM ranked WHERE rank=1 AND model_set IN (SELECT model_set FROM matching)) ')
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
