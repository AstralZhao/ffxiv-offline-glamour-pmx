"""Original weapon idle samples; native vertices remain in bind space."""
import hashlib,json,struct
from functools import lru_cache
from pathlib import Path
import numpy as np
from default_stance_probe import pap_header
from geometry_probe import local_matrix
from havok_probe import TagReader
from prepare_fixed_stance import normal_local_pose,world_pose
from static_spline_start import sample_start


def normal_timeline(raw):
    """Read the pinned weapon idle's separate material/body C010 tracks."""
    if len(raw)<12 or raw[:4]!=b'TMLB':raise ValueError('武器收起时间线格式不正确')
    size,count=struct.unpack_from('<II',raw,4)
    if size!=len(raw)or count!=9:raise ValueError('武器收起时间线布局已变化')
    at=12;clips=[]
    for _ in range(count):
        if at+8>len(raw):raise ValueError('武器收起时间线截断')
        tag,n=struct.unpack_from('<4sI',raw,at)
        if n<8 or at+n>len(raw):raise ValueError('武器收起时间线条目越界')
        if tag==b'C010':
            if n!=40:raise ValueError('武器收起动画条目已变化')
            start,end,relative=struct.unpack_from('<ffI',raw,at+24);offset=at+8+relative
            if start!=0 or end!=1 or not 0<=offset<len(raw):raise ValueError('武器收起动画采样范围已变化')
            end_name=raw.find(b'\0',offset)
            if not offset<end_name<=offset+32:raise ValueError('武器收起动画名称越界')
            clips.append(raw[offset:end_name].decode('ascii'))
        at+=n
    if sorted(clips)!=['cbnw_id0','csnw_id0']:raise ValueError('武器骨架／材质收起轨道已变化')
    return {'body':'cbnw_id0','material':'csnw_id0'}


@lru_cache(maxsize=1)
def profile():
    return json.loads((Path(__file__).resolve().parents[1]/'docs/cn-weapon-sheathed.json').read_bytes())


def bind_deltas(skeleton,binding,sample,extra=None):
    bones=skeleton['bones'];names=[b['name']for b in bones];parents=[b['parent']for b in bones]
    reference=np.asarray([local_matrix(b)for b in bones]);bind=world_pose(reference,parents)
    local=normal_local_pose(reference,names,names,binding,sample)
    skin=world_pose(local,parents)@np.linalg.inv(bind);result=dict(zip(names,skin))
    # Untracked partial bones keep their reference locals. Their nearest
    # authored shared ancestor supplies the same model-space skinning delta.
    if extra:
        for i,bone in enumerate(extra['bones']):
            if bone['name']in result:continue
            parent=i
            while parent>=0 and extra['bones'][parent]['name']not in names:
                parent=extra['bones'][parent]['parent']
            if parent<0:raise ValueError('书本附属骨架没有连接原始骨架')
            result[bone['name']]=result[extra['bones'][parent]['name']]
    if not all(np.isfinite(m).all()for m in result.values()):raise ValueError('武器收起姿态产生非有限值')
    return result


def sheathed_book(pack,model,body,skeleton,extra=None):
    # Books and sage arms both author their resting layout in cbnw_id0.
    # In particular, sage bind geometry is not the separated resting layout.
    if model//100 not in (17,27):return {},None
    data=profile()

    def read(path,digest):
        raw=pack.read(path)
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('书本收起姿态来源已变化：'+path)
        return raw
    timeline=read(data['timeline']['path'],data['timeline']['sha256']);clip=normal_timeline(timeline)['body']
    record=data['models'].get(str(model))
    if record is None:raise NotImplementedError('这款武器的原始收起姿态尚未核实')
    read(record['skeleton_path'],record['skeleton_sha256'])
    if extra:
        partial=data['partials'].get(f'{model}:{body}')
        if partial is None:raise NotImplementedError('这款武器的附属骨架尚未核实')
        read(partial['path'],partial['sha256'])
    raw=read(record['path'],record['sha256']);header=pap_header(raw)
    if header['model_type']!=3 or header['model_id']!=model:raise ValueError('书本动作模型来源不一致')
    clips=[c for c in header['clips']if c['name']==clip and not c['is_face']]
    containers=[o for o in TagReader(raw[header['havok_offset']:header['footer_offset']]).read()if o['__type__']=='hkaAnimationContainer']
    if len(clips)!=1 or len(containers)!=1:raise ValueError('书本收起动作不唯一')
    index=clips[0]['binding_index'];container=containers[0];binding=container['bindings'][index]
    if(binding['animation']is not container['animations'][index]or binding['blendHint']!=0
       or binding['originalSkeletonName']!=record['binding_name']):raise ValueError('书本收起骨架绑定已变化')
    sample=sample_start(binding['animation'],reviewed_nonpositive={int(k):v for k,v in record['nonpositive_scales'].items()})
    matrices=bind_deltas(skeleton,binding,sample,extra)
    return matrices,{'provider':'native-weapon-normal-idle-start-v1','clip':clip,'sample_time_seconds':0,
        'path':record['path'],'sha256':record['sha256'],'timeline_path':data['timeline']['path']}


def drawn_weapon(pack,model,body,skeleton,extra=None):
    from weapon_stance import profile,checked_read
    data=profile()

    record=data['weapons'].get(str(model))
    if record is None:raise NotImplementedError('这款武器的拔出姿态尚未核实')
    checked_read(pack,data,'chara/xls/animation/animation_work_table-weapon.awt')
    if record['mode']!='sample':
        if record.get('path'):checked_read(pack,data,record['path'])
        return {},{'provider':record['mode'],'sample_time_seconds':0}
    raw=checked_read(pack,data,record['path']);h=pap_header(raw);skpath=f'chara/weapon/w{model:04}/skeleton/base/b0001/skl_w{model:04}b0001.sklb';checked_read(pack,data,skpath)
    if h['model_type']!=3 or h['model_id']!=model:raise ValueError('武器拔出动画来源不一致')
    clip=next(c for c in h['clips']if c['name']==record['clip']and not c['is_face'])
    container=next(o for o in TagReader(raw[h['havok_offset']:h['footer_offset']]).read()if o['__type__']=='hkaAnimationContainer');binding=container['bindings'][clip['binding_index']]
    if binding['originalSkeletonName']!=record['binding_name']:raise ValueError('武器拔出骨架绑定不一致')
    sample=sample_start(binding['animation'],reviewed_nonpositive={int(k):v for k,v in record['nonpositive_scales'].items()})
    return bind_deltas(skeleton,binding,sample,extra),{'provider':'native-weapon-battle-idle-start-v1','clip':record['clip'],'path':record['path'],'sha256':data['sources'][record['path']],'sample_time_seconds':0}
