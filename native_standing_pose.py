"""Read the original CN standing-loop first sample, with authored bindings."""
from collections import OrderedDict
import hashlib
import json
from pathlib import Path

import numpy as np

from default_stance_probe import pap_header
from havok_probe import TagReader,read_skeleton
from prepare_fixed_stance import idle_timeline,normal_local_pose,world_pose
from prepare_preset_customization import bundle_arrays
from preset_customization import body_world
from static_shared_idle import identity_shared_binding,local_shared_pose
from static_spline_start import sample_start


def sha(raw):return hashlib.sha256(raw).hexdigest()


def standing_world(scene,outfit,pose_id,pose_path):
    base=outfit.base;pack=base.pack
    cache=getattr(outfit,'native_standing_cache',None)
    if cache is None or cache['owner']is not base:
        path=Path(base.pose['source_assembly']['path']);assembly_raw=path.read_bytes()
        if sha(assembly_raw)!=base.pose['source_assembly']['sha256']:raise ValueError('角色原始骨架映射资料已改变')
        assembly=json.loads(assembly_raw)
        reference=bundle_arrays(assembly,path,'bind_geometry_bundle')
        posed=bundle_arrays(base.pose,Path(pose_path),'customized_stance_geometry_bundle')
        profile=json.loads((Path(__file__).resolve().parents[1]/'docs/cn-native-standing-poses.json').read_bytes())
        if profile['region']!='CN':raise ValueError('站姿资料版本不匹配')
        if sha(pack.read(profile['plt_path']))!=profile['plt_sha256']:raise ValueError('站姿动作资源路由已改变')
        cache={'owner':base,'reference':reference,'posed':posed,'profile':profile,'samples':OrderedDict()}
        outfit.native_standing_cache=cache
    if pose_id in cache['samples']:return cache['samples'][pose_id]
    race=base.pose['identity']['race_sex_id'];number=int(pose_id[5:]);record=cache['profile']['records'][f'{race}:{number}']
    raw=pack.read(record['path']);tmb=pack.read(record['timeline_path'])
    if sha(raw)!=record['pap_sha256']or sha(tmb)!=record['timeline_sha256']:raise ValueError('原始站姿动作文件已改变')
    timeline=idle_timeline(tmb);header=pap_header(raw)
    if header['model_id']!=record['source_race']or header['model_type']!=0 or timeline['clip_name']!=record['clip_name']:
        raise ValueError('站姿时间线与动作来源不一致')
    clips=[c for c in header['clips']if c['name']==record['clip_name']and not c['is_face']]
    if len(clips)!=1:raise ValueError('站姿动作轨道不唯一')
    containers=[o for o in TagReader(raw[header['havok_offset']:header['footer_offset']]).read()if o['__type__']=='hkaAnimationContainer']
    if len(containers)!=1:raise ValueError('站姿动作容器不唯一')
    container=containers[0];index=clips[0]['binding_index'];binding=container['bindings'][index]
    if binding['animation']is not container['animations'][index]or binding['originalSkeletonName']!=record['binding_name']or binding['blendHint']!=0:
        raise ValueError('站姿骨架绑定已改变')
    signed={int(k):v for k,v in record['nonpositive_scales'].items()}
    sample=sample_start(binding['animation'],reviewed_nonpositive=signed)
    target_path=f'chara/human/c{race:04}/skeleton/base/b0001/skl_c{race:04}b0001.sklb'
    source_race=record['source_race'];source_path=f'chara/human/c{source_race:04}/skeleton/base/b0001/skl_c{source_race:04}b0001.sklb'
    target_raw=pack.read(target_path);source_raw=pack.read(source_path)
    if sha(target_raw)!=record['target_skeleton_sha256']or sha(source_raw)!=record['source_skeleton_sha256']:raise ValueError('站姿原始骨架已改变')
    target=read_skeleton(target_raw);base_names=[b['name']for b in target['bones']];count=len(base_names)
    reference=cache['reference'];names=reference['skeleton_names'].tolist();parents=reference['skeleton_parents']
    if names[:count]!=base_names:raise ValueError('角色基础骨架与站姿不一致')
    local=cache['posed']['skeleton_face_local'].copy()
    # Untracked body channels use the authored reference; selected face/hair
    # parameters remain separate from the full-body animation tracks.
    local[:count]=reference['skeleton_local'][:count]
    if source_race!=race and race==301:
        local,_=local_shared_pose(target_raw,source_raw,binding,sample,local,names,target_race=race,source_race=source_race)
    else:
        if source_race!=race:
            binding,_=identity_shared_binding(target_raw,source_raw,binding,target_race=race,source_race=source_race)
        local=normal_local_pose(local,base_names,names,binding,sample)
    params=base.pose['preset']['parameters']
    world,_=body_world(world_pose(local,parents),parents,names,base.pose['customization']['body'],params['race'])
    current=scene.skinning@outfit.bind
    # Preserve settled facial/hair/ear partial locals under the new body
    # anchors. Original body tracks, including tail and skirt bones, remain.
    for i in range(count,len(names)):
        parent=int(parents[i])
        if parent>=0:world[i]=world[parent]@np.linalg.solve(current[parent],current[i])
        else:world[i]=current[i]
    if not np.isfinite(world).all():raise ValueError('原生站姿产生非有限骨骼变换')
    world.setflags(write=False)
    provenance={'provider':'native-standing-loop-start-v1','pose':number,'source_path':record['path'],
        'source_sha256':record['pap_sha256'],'timeline_path':record['timeline_path'],'clip':record['clip_name'],
        'source_race':source_race,'target_race':race,'sample_time_seconds':0}
    cache['samples'][pose_id]=(world,provenance)
    while len(cache['samples'])>6:cache['samples'].popitem(last=False)
    return world,provenance
