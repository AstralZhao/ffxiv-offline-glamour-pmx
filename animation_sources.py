"""Authored animation aliases and ordered PAP lookup, independent of sampling."""
import re,struct
from animation_mapping import MotionLines
from default_stance_probe import pap_header

AET='chara/xls/animation/AnimationExchangeTable.aet'
MLT='chara/xls/animation/MotionLineTable.mlt'


class AnimationExchanges:
    def __init__(self,raw):
        if type(raw)is not bytes or not 12<=len(raw)<=1024*1024 or raw[:8]!=b'tea\x000001':
            raise ValueError('动作替换表格式不正确')
        count,rows=struct.unpack_from('<HH',raw,8)
        if not 0<count<=8192 or rows>8192:raise ValueError('动作替换表数量越界')
        names=[];at=12
        for _ in range(count):
            end=raw.find(b'\0',at,min(len(raw),at+193))
            if end<at:raise ValueError('动作替换表名称截断')
            name=raw[at:end].decode('ascii')
            if not re.fullmatch('[a-z0-9_]*',name):raise ValueError('动作替换表名称不正确')
            names.append(name);at=end+1
        if len(raw)-at!=rows*6:raise ValueError('动作替换表记录长度不正确')
        self.rows={}
        for key,first,second in struct.iter_unpack('<hhh',raw[at:]):
            # Original parser always adds first, and adds second iff signed
            # second >= 0. Keep the authored order, including an empty alias.
            if not 0<=key<count or not 0<=first<count or second>=count:
                raise ValueError('动作替换表名称引用越界')
            name=names[key]
            if not name or name in self.rows:raise ValueError('动作替换表名称重复或为空')
            self.rows[name]=(names[first],)+((names[second],)if second>=0 else())

    def aliases(self,name):return self.rows.get(name,())


def facial_clip(pack,name,rig,variant,*,library=None,primary_paths=(),channel=17):
    """Normal C010 route: primary group, declared group, then resident face.

    Each group tries the exact name before its source AET aliases. This does
    not admit special root overrides or the MLT resident-only dispatch.
    """
    if not re.fullmatch('[a-z0-9_]+',name):raise ValueError('表情片段名称不正确')
    if channel not in(17,18,19):raise ValueError('表情来源通道不正确')
    if not re.fullmatch('c[0-9]{4}',rig)or not re.fullmatch('f[0-9]{4}',variant):
        raise ValueError('表情资源身份不正确')
    if library is not None and not re.fullmatch('[a-z0-9_]+(?:/[a-z0-9_]+)*',library):
        raise ValueError('表情库名称不正确')
    flags=MotionLines(pack.read(MLT)).flags(name)
    if channel==18 and not name.startswith('cfxb_'):
        raise NotImplementedError('此眨眼的动作线路尚未接入')
    if channel==19 and(not name.startswith('cfxl_')or flags!=0x229):
        raise NotImplementedError('此口型的动作线路尚未接入')
    if flags&0x10:raise NotImplementedError('此表情的专用驻留调度尚未接入')
    aliases=AnimationExchanges(pack.read(AET)).aliases(name)
    paths=list(primary_paths)
    if any(not isinstance(p,str)or not p.startswith('chara/human/')or not p.endswith('.pap')or'..'in p.split('/')for p in paths):
        raise ValueError('表情主资源组路径不正确')
    root=f'chara/human/{rig}/animation/{variant}'
    if library is not None:paths.append(f'{root}/nonresident/{library}.pap')
    paths.append(f'{root}/resident/face.pap')
    attempted=[]
    for path in dict.fromkeys(paths):
        attempted.append(path)
        try:raw=pack.read(path)
        except FileNotFoundError:continue
        header=pap_header(raw)
        for candidate in(name,*aliases):
            matches=[c for c in header['clips']if c['name']==candidate]
            if not matches:continue
            if len(matches)!=1:raise ValueError('表情资源片段名称重复')
            selected=matches[0]
            if not selected['is_face']or selected['type']!=channel:
                raise NotImplementedError('此表情的眨眼或口型轨道组合尚未接入')
            if header['model_id']!=int(rig[1:])or header['model_type']!=0:
                raise NotImplementedError('此表情主资源需要额外骨架映射')
            return raw,header,selected,{'path':path,'requested_clip':name,'clip':candidate,
                'library':library,'source_order':tuple(attempted),'alias_used':candidate!=name}
    raise ValueError('表情片段不存在')
