"""Versioned, bounded text codes containing only a name, gear and dyes."""
import base64
import binascii
import hashlib
import hmac
import json

PREFIX='HGG1'
SLOTS=('Head','Body','Gloves','Legs','Feet','Ears','Neck','Wrists','FingerR','FingerL')
MAX_CODE=4096


def _name(value):
    if not isinstance(value,str)or not value.strip()or len(value.strip())>32:raise ValueError('模版名称须为1至32个字符')
    value=value.strip()
    if any(ord(c)<32 or 0xd800<=ord(c)<=0xdfff for c in value):raise ValueError('模版名称含无效字符')
    return value


def encode(name,equipment):
    from app import parse_state
    state=parse_state({'equipment':equipment});gear=[]
    for slot in SLOTS:
        c=state['equipment'][slot];gear.append([c['item_id']or 0,c['first'],c['second']])
    raw=json.dumps({'v':1,'region':'CN','name':_name(name),'gear':gear},ensure_ascii=False,separators=(',',':')).encode('utf8')
    return PREFIX+'.'+base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')+'.'+hashlib.sha256(raw).hexdigest()[:16]


def decode(code):
    if not isinstance(code,str)or len(code)>MAX_CODE:raise ValueError('请输入有效的模版码（最长4096字符）')
    code=''.join(code.split());parts=code.split('.')
    if len(parts)!=3 or parts[0]!=PREFIX:raise ValueError('模版码格式或版本不受支持')
    if len(parts[2])!=16 or any(c not in'0123456789abcdef'for c in parts[2]):raise ValueError('模版码校验值不正确')
    try:raw=base64.b64decode(parts[1]+'='*((-len(parts[1]))%4),altchars=b'-_',validate=True)
    except(binascii.Error,ValueError):raise ValueError('模版码内容不完整')from None
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest()[:16],parts[2]):raise ValueError('模版码校验失败，请完整复制后重试')
    try:record=json.loads(raw)
    except(ValueError,UnicodeError,RecursionError):raise ValueError('模版码内容无法读取')from None
    if not isinstance(record,dict)or set(record)!={'v','region','name','gear'}or type(record['v'])is not int or record['v']!=1 or record['region']!='CN':raise ValueError('模版码来源或版本不受支持')
    name=_name(record['name']);gear=record['gear']
    if not isinstance(gear,list)or len(gear)!=len(SLOTS):raise ValueError('模版码装备部位不完整')
    equipment={}
    for slot,choice in zip(SLOTS,gear):
        if not isinstance(choice,list)or len(choice)!=3 or any(type(n)is not int for n in choice):raise ValueError('模版码装备参数不正确')
        item,first,second=choice
        if not 0<=item<=1000000 or not 0<=first<=254 or not 0<=second<=254 or item==0 and(first or second):raise ValueError('模版码装备或染色范围不正确')
        equipment[slot]={'item_id':item or None,'first':first,'second':second}
    return {'name':name,'state':{'equipment':equipment}}


MAX_BATCH_TEMPLATES=10000
MAX_BATCH_BYTES=16*1024*1024


def encode_batch(records):
    if not isinstance(records,list)or not 1<=len(records)<=MAX_BATCH_TEMPLATES:
        raise ValueError('请选择1至10000套模版')
    codes=[encode(r['name'],r['state']['equipment'])for r in records]
    raw=(json.dumps({'format':'HGG-templates','version':1,'codes':codes},ensure_ascii=False,indent=2)+'\n').encode('utf8')
    if len(raw)>MAX_BATCH_BYTES:raise ValueError('模版文件超过16 MiB，请分批导出')
    return raw


def decode_batch(raw):
    if not isinstance(raw,bytes)or len(raw)>MAX_BATCH_BYTES:raise ValueError('模版文件不能超过16 MiB')
    try:bundle=json.loads(raw.decode('utf-8-sig'))
    except(ValueError,UnicodeError,RecursionError):raise ValueError('无法读取模版文件，请选择批量导出的模版文件')from None
    if not isinstance(bundle,dict)or set(bundle)!={'format','version','codes'}or bundle['format']!='HGG-templates'or type(bundle['version'])is not int or bundle['version']!=1:
        raise ValueError('模版文件格式或版本不受支持')
    codes=bundle['codes']
    if not isinstance(codes,list)or not 1<=len(codes)<=MAX_BATCH_TEMPLATES:raise ValueError('模版文件须包含1至10000套模版')
    rows=[]
    for i,code in enumerate(codes,1):
        try:rows.append(decode(code))
        except ValueError as error:raise ValueError(f'第{i}套模版：{error}')from error
    return rows
