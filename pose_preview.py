"""Original game standing poses over immutable character and equipment meshes.

sample_pose is the time-based provider boundary for future emote animation.
Current samples come from original standing loops; transition playback is absent.
"""
from dataclasses import dataclass
import copy
import hashlib
import math

import numpy as np

POSES=(('default','默认站姿'),*((f'pose_{i}',f'站姿 {i}')for i in range(1,7)))


def validate_pose(value):
    if not isinstance(value,str)or value not in {key for key,_ in POSES}:raise ValueError('姿势选项不正确')
    return value


@dataclass(frozen=True)
class PoseSample:
    world:object
    time_seconds:float
    provenance:dict


def sample_pose(pose_id,time_seconds=0.,*,scene=None,outfit=None,pose_path=None):
    validate_pose(pose_id)
    if type(time_seconds)not in(int,float)or not math.isfinite(time_seconds)or time_seconds<0:
        raise ValueError('姿势采样时间不正确')
    # The time boundary is reserved; current decoder only evaluates loop start.
    if time_seconds!=0:raise NotImplementedError('当前站姿仅支持原始循环起点，尚未接入动画播放')
    if pose_id=='default':return PoseSample(None,0.,{'provider':'original-default'})
    if scene is None or outfit is None or pose_path is None:raise ValueError('原生站姿需要角色和原始动作资源')
    from native_standing_pose import standing_world
    world,source=standing_world(scene,outfit,pose_id,pose_path)
    return PoseSample(world,0.,source)


def apply_pose(scene,outfit,pose_id,*,time_seconds=0.,pose_path=None):
    sample=sample_pose(pose_id,time_seconds,scene=scene,outfit=outfit,pose_path=pose_path)
    if sample.world is None:return scene
    names=[None]*len(outfit.names)
    for name,index in outfit.names.items():names[index]=name
    world=scene.skinning@outfit.bind
    delta=sample.world@np.linalg.inv(world)
    result=copy.copy(scene);result.meshes=[];result.skinning=delta@scene.skinning
    result.skinning.setflags(write=False)
    for mesh in scene.meshes:
        palette=np.asarray(mesh['palette']);old=mesh.get('joint_matrices')
        if old is None:old=scene.skinning[palette]
        if np.any(palette>=len(names)):
            item=outfit.item_metadata(mesh['item_id'],mesh['slot']);attached=outfit.attachment(item)
            reverse={index:name for name,index in attached['names'].items()};owners=[]
            for index in palette:
                name=reverse[int(index)]
                while name not in outfit.names:
                    name=attached['parents'][name]
                    if name is None:raise ValueError('姿势装备骨骼未连接角色')
                owners.append(outfit.names[name])
            moves=delta[owners]
        else:moves=delta[palette]
        joints=moves@old;joints.setflags(write=False)
        vertices=mesh['vertices'];positions=vertices['position'];weights=vertices['weights'].astype(float)/255.
        source=np.column_stack((positions,np.ones(len(positions))));posed=np.zeros((len(positions),3))
        for influence in range(weights.shape[1]):
            transformed=np.einsum('nij,nj->ni',joints[vertices['joints'][:,influence]],source)[:,:3]
            posed+=transformed*weights[:,influence,None]
        if not np.isfinite(posed).all():raise ValueError('姿势网格产生非有限值')
        result.meshes.append({**mesh,'joint_matrices':joints,'posed_positions':posed})
    result.bounds=np.vstack([scene.bounds,*[m['posed_positions'][np.unique(m['indices'])]for m in result.meshes if len(m['indices'])]])
    identity=getattr(scene,'outfit_cache_identity','')
    result.outfit_cache_identity=hashlib.sha256((identity+pose_id+sample.provenance['source_sha256']).encode()).hexdigest()
    result.preview_pose={'id':pose_id,'time_seconds':sample.time_seconds,**sample.provenance}
    return result
