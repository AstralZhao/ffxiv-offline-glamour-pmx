"""Source motion types, shared LOCAL mappers and fixed drawn-weapon stances."""
from bisect import bisect_right
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
import hashlib,json,struct
import numpy as np
from havok_probe import read_skeleton,TagReader
from geometry_probe import local_matrix
from static_local_mapper import local_mapping


@lru_cache(maxsize=1)
def profile():return json.loads((Path(__file__).resolve().parents[1]/'docs/cn-weapon-drawn.json').read_bytes())


def motion_prefix(rows,raw):
    version,count=struct.unpack_from('<HH',raw);entries=list(struct.iter_unpack('<II',raw[4:4+count*8]))
    if version!=1 or not entries or [r[0]for r in entries]!=sorted(set(r[0]for r in entries)):raise ValueError('武器动作类型表格式不正确')
    ids=[r[0]for r in entries]
    def kind(model):
        if not model:return 'emp'
        at=bisect_right(ids,model)-1
        if at<0:raise ValueError('武器没有原始动作类型')
        return entries[at][1].to_bytes(4,'big').lstrip(b'\0').decode('ascii')
    from weapons import has_gauntlet
    main=rows.get('MainHand');off=rows.get('OffHand')
    if not main:raise ValueError('请先试穿主武器，再选择拔出武器姿势。')
    secondary=main['model_sub']
    if has_gauntlet(main):secondary=(main['model_main']&~65535)|((main['model_main']&65535)+50)
    return 'bt_'+kind(main['model_main']&65535)+'_'+kind((off['model_main']if off else secondary)&65535)


def skeleton_packet(raw):
    parsed=read_skeleton(raw);objects=TagReader(raw[parsed['havok_payload_offset']:]).read()
    primary=next(o for o in objects if o['__type__']=='hkaAnimationContainer')['skeletons'][0]
    return parsed,primary,[o['mapping']for o in objects if o['__type__']=='hkaSkeletonMapper']


def mapper_candidates(source,target):
    source_bones=source[0]['bones'];source_names=[b['name']for b in source_bones];found=[]
    for index,m in enumerate(target[2]):
        a=m['skeletonA'];names=[b['name']for b in a['bones']]
        if m['skeletonB']is not target[1]or not set(names)<=set(source_names):continue
        indices=[source_names.index(name)for name in names]
        if any((names[p]if p>=0 else None)!=(source_names[source_bones[i]['parent']]if source_bones[i]['parent']>=0 else None)for i,p in zip(indices,a['parentIndices'])):continue
        matrices=np.asarray([local_matrix(dict(translation=v[:3],rotation=v[4:8],scale=v[8:11]))for v in a['referencePose']])
        actual=np.asarray([local_matrix(source_bones[i])for i in indices])
        if np.max(abs(matrices-actual))>2e-5:continue
        if m['mappingType']!=1 or m['chainMappings']or m['keepUnmappedLocal']!=1:continue
        seen=set()
        for entry in m['simpleMappings']:
            ia,ib=entry['boneA'],entry['boneB']
            if not 0<=ia<len(names)or not 0<=ib<len(target[0]['bones'])or ib in seen:raise ValueError('武器姿态原始映射索引不正确')
            if names[ia]!=target[0]['bones'][ib]['name']:raise ValueError('武器姿态原始映射骨骼不一致')
            seen.add(ib)
        if seen|set(m['unmappedBones'])!=set(range(len(target[0]['bones']))):continue
        found.append(index)
    return found


def mapped_locals(skeletons,route,binding,sample):
    source=skeletons[route['source_race']];bones=source[0]['bones'];values=np.asarray(source[1]['referencePose'],dtype='<f4').copy()
    tracks=binding['transformTrackToBoneIndices']
    if binding['blendHint']!=0 or len(set(tracks))!=len(tracks)or len(tracks)!=len(sample['translation']):raise ValueError('持武器姿态绑定不正确')
    for i,b in enumerate(tracks):
        if not 0<=b<len(values):raise ValueError('持武器姿态轨道越界')
        values[b,:3]=sample['translation'][i];values[b,4:8]=sample['rotation'][i];values[b,8:11]=sample['scale'][i]
    for step in route['steps']:
        target=skeletons[step['target']]
        if step.get('compatible_tracks'):
            names=[b['name']for b in bones];target_names=[b['name']for b in target[0]['bones']]
            if not set(target_names)<=set(names)or route['source_race']!=101 or step['target']!=1701:raise ValueError('受检共用骨架已变化')
            missing=set(names)-set(target_names)
            if missing!={'n_sippo_'+c for c in'abcde'}:raise ValueError('共用前缀骨架尾骨集合已变化')
            for j,bone in enumerate(target[0]['bones']):
                original=bones[names.index(bone['name'])]
                if (names[original['parent']]if original['parent']>=0 else None)!=(target_names[bone['parent']]if bone['parent']>=0 else None):raise ValueError('共用骨架父链已变化')
            output=np.asarray(target[1]['referencePose'],dtype='<f4').copy()
            for i,b in enumerate(tracks):
                if names[b]not in target_names:continue
                j=target_names.index(names[b])
                if np.max(abs(local_matrix(bones[b])-local_matrix(target[0]['bones'][j])))>1e-4:raise ValueError('共用骨架参考状态已变化')
                output[j]=values[b]
        else:
            m=skeletons[step['owner']][2][step['mapper']];names=[b['name']for b in bones];output=np.asarray(target[1]['referencePose'],dtype='<f4').copy();out_names=[b['name']for b in target[0]['bones']]
            for entry in m['simpleMappings']:
                if step.get('reverse'):
                    name=m['skeletonB']['bones'][entry['boneB']]['name'];dest=m['skeletonA']['bones'][entry['boneA']]['name'];v=np.asarray(entry['aFromBTransform'],dtype='<f4').copy()
                    if np.any(v[8:11]==0):raise ValueError('原始局部骨架映射不能求逆')
                    v[:3]=-v[:3]/v[8:11];v[8:11]=1/v[8:11];v[3]=0;v[11]=1;q=v[4:8].copy();v[4:8]=q*np.array([-1,-1,-1,1])/float(q@q)
                    output[out_names.index(dest)]=local_mapping(values[names.index(name)],v)
                else:
                    name=m['skeletonA']['bones'][entry['boneA']]['name'];dest=m['skeletonB']['bones'][entry['boneB']]['name'];output[out_names.index(dest)]=local_mapping(values[names.index(name)],entry['aFromBTransform'])
        values=output;bones=target[0]['bones']
    return np.asarray([local_matrix(dict(translation=v[:3],rotation=v[4:8],scale=v[8:11]))for v in values])


def checked_read(pack,data,path):
    raw=pack.read(path)
    if hashlib.sha256(raw).hexdigest()!=data['sources'].get(path):raise ValueError('持武器姿态原始资源已变化：'+path)
    return raw


def verify_timeline(raw,expected):
    if raw[:4]!=b'TMLB'or len(raw)<12 or struct.unpack_from('<I',raw,4)[0]!=len(raw):raise ValueError('持武器时间线格式不正确')
    count=struct.unpack_from('<I',raw,8)[0];at=12;found=0
    if not 1<=count<=64:raise ValueError('持武器时间线条目过多')
    for _ in range(count):
        tag,n=struct.unpack_from('<4sI',raw,at)
        if n<8 or at+n>len(raw):raise ValueError('持武器时间线条目越界')
        if tag==b'C010'and n==40:
            start,end,relative=struct.unpack_from('<ffI',raw,at+24);pos=at+8+relative;end_name=raw.find(b'\0',pos)
            if not 0<=pos<end_name<=pos+128:raise ValueError('持武器时间线名称越界')
            if raw[pos:end_name].decode('ascii')==expected:
                if start!=0 or not np.isfinite(end)or end<=0:raise ValueError('持武器循环起点不正确')
                found+=1
        at+=n
    if found!=1:raise ValueError('持武器时间线与动作轨道不一致')


def standing_world(scene,outfit,pose_id,pose_path,weapons):
    from weapons import validate_rows
    from prepare_preset_customization import bundle_arrays
    from default_stance_probe import pap_header
    from static_spline_start import sample_start
    from prepare_fixed_stance import world_pose
    from preset_customization import body_world
    base=outfit.base;pack=base.pack;data=profile();race=outfit.race

    rows=validate_rows(outfit.database,weapons,outfit.stains);prefix=motion_prefix(rows,checked_read(pack,data,'chara/xls/weapontype/motion.wtd'))
    key=f'{race}:{prefix}:{int(pose_id[-1])}';record=data['bodies'].get(key)
    if record is None:raise NotImplementedError('当前武器组合的持武器姿态尚未核实')
    cache=getattr(outfit,'weapon_standing_cache',None)
    if cache is None or cache['owner']is not base:
        path=Path(base.pose['source_assembly']['path']);raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=base.pose['source_assembly']['sha256']:raise ValueError('角色参考骨架资料已变化')
        assembly=json.loads(raw);cache={'owner':base,'reference':bundle_arrays(assembly,path,'bind_geometry_bundle'),
            'posed':bundle_arrays(base.pose,Path(pose_path),'customized_stance_geometry_bundle'),'samples':OrderedDict(),'rigs':{}}
        outfit.weapon_standing_cache=cache
    if key in cache['samples']:cache['samples'].move_to_end(key);return cache['samples'][key]
    checked_read(pack,data,'chara/xls/animation/papLoadTable.plt')
    timeline_path='chara/action/'+('battle/idle'if pose_id=='weapon_1'else'emote/b_pose01_loop')+'.tmb'
    verify_timeline(checked_read(pack,data,timeline_path),record['clip'])
    raw=checked_read(pack,data,record['path']);h=pap_header(raw)
    if h['model_type']!=0 or h['model_id']!=record['mapping']['source_race']:raise ValueError('持武器动作来源骨架不一致')
    clip=next(c for c in h['clips']if c['name']==record['clip']and not c['is_face'])
    container=next(o for o in TagReader(raw[h['havok_offset']:h['footer_offset']]).read()if o['__type__']=='hkaAnimationContainer');index=clip['binding_index'];binding=container['bindings'][index]
    if binding['originalSkeletonName']!=record['binding_name']or binding['animation']is not container['animations'][index]:raise ValueError('持武器动作绑定不一致')
    sample=sample_start(binding['animation'],reviewed_nonpositive={int(k):v for k,v in record['nonpositive_scales'].items()})
    mapping=record['mapping'];needed={race,mapping['source_race']}|{s['target']for s in mapping['steps']}|{s['owner']for s in mapping['steps']if'owner'in s}
    for identity in needed:
        if identity not in cache['rigs']:
            skpath=f'chara/human/c{identity:04}/skeleton/base/b0001/skl_c{identity:04}b0001.sklb';cache['rigs'][identity]=skeleton_packet(checked_read(pack,data,skpath))
    reference=cache['reference'];names=reference['skeleton_names'].tolist();parents=reference['skeleton_parents'];bones=cache['rigs'][race][0]['bones'];count=len(bones)
    if names[:count]!=[b['name']for b in bones]:raise ValueError('角色持武器骨架顺序不一致')
    local=cache['posed']['skeleton_face_local'].copy();local[:count]=mapped_locals(cache['rigs'],mapping,binding,sample)
    params=base.pose['preset']['parameters'];world,_=body_world(world_pose(local,parents),parents,names,base.pose['customization']['body'],params['race']);current=scene.skinning@outfit.bind
    for i in range(count,len(names)):
        parent=int(parents[i]);world[i]=world[parent]@np.linalg.solve(current[parent],current[i])if parent>=0 else current[i]
    if not np.isfinite(world).all():raise ValueError('持武器姿态产生非有限骨架')
    world.setflags(write=False);provenance={'provider':'native-weapon-standing-start-v1','source_sha256':data['sources'][record['path']],
        'source_path':record['path'],'clip':record['clip'],'motion_prefix':prefix,'sample_time_seconds':0,'mapping':mapping}
    cache['samples'][key]=(world,provenance)
    while len(cache['samples'])>6:cache['samples'].popitem(last=False)
    return world,provenance
