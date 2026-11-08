"""Static preview poses over immutable, already customized source skeletons.

sample_pose is the time-based provider boundary for future emote animation.
The current authored examples are static offsets, not native game emotes.
"""
from dataclasses import dataclass
import copy
import hashlib
import math

import numpy as np

POSES=(('default','默认站姿'),('arms_open','双臂舒展'),('left_arm','抬起左臂'),('head_turn','头部侧转'))
_OFFSETS={
    'default':(),
    'arms_open':(('j_ude_a_l',2,35.),('j_ude_a_r',2,-35.)),
    'left_arm':(('j_ude_a_l',2,65.),),
    'head_turn':(('j_kao',1,20.),),
}


def validate_pose(value):
    if not isinstance(value,str)or value not in _OFFSETS:raise ValueError('姿势选项不正确')
    return value


@dataclass(frozen=True)
class PoseSample:
    offsets:tuple
    # Future animated providers return the transforms at this timestamp.
    time_seconds:float


def sample_pose(pose_id,time_seconds=0.):
    validate_pose(pose_id)
    if type(time_seconds)not in(int,float)or not math.isfinite(time_seconds)or time_seconds<0:
        raise ValueError('姿势采样时间不正确')
    return PoseSample(_OFFSETS[pose_id],float(time_seconds))


def posed_world(world,names,parents,sample):
    """Preserve current proportions and secondary locals; rotate at joint pivots.

    Example rotations use character/model axes. Descendants inherit their
    actual parent transformation, including the existing static hair result.
    """
    lookup={name:i for i,name in enumerate(names)}
    if len(lookup)!=len(names)or len(parents)!=len(names)or world.shape!=(len(names),4,4)or not np.isfinite(world).all():
        raise ValueError('姿势骨架数据不正确')
    rotations={}
    for name,axis,degrees in sample.offsets:
        if name not in lookup:raise NotImplementedError('当前角色缺少姿势骨骼：'+name)
        radians=math.radians(degrees);c=math.cos(radians);s=math.sin(radians)
        rotation=np.eye(3);a,b=((1,2),(2,0),(0,1))[axis]
        rotation[a,a]=rotation[b,b]=c;rotation[a,b]=-s;rotation[b,a]=s
        rotations[lookup[name]]=rotation
    result=world.copy();changed=np.zeros(len(names),dtype=bool)
    for i,parent in enumerate(parents):
        if not -1<=parent<i:raise ValueError('姿势骨架父级顺序不正确')
        if parent>=0 and changed[parent]:
            result[i]=result[parent]@np.linalg.solve(world[parent],world[i]);changed[i]=True
        if i in rotations:
            result[i,:3,:3]=rotations[i]@result[i,:3,:3];changed[i]=True
    if not np.isfinite(result).all():raise ValueError('姿势变换产生非有限值')
    delta=np.repeat(np.eye(4)[None],len(names),axis=0)
    delta[changed]=result[changed]@np.linalg.inv(world[changed])
    return result,delta


def apply_pose(scene,outfit,pose_id,*,time_seconds=0.):
    sample=sample_pose(pose_id,time_seconds)
    if not sample.offsets:return scene
    names=[None]*len(outfit.names)
    for name,index in outfit.names.items():names[index]=name
    parents=[outfit.names[outfit.parents[name]]if outfit.parents[name]is not None else -1 for name in names]
    world=scene.skinning@outfit.bind
    _,delta=posed_world(world,names,parents,sample)
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
    result.outfit_cache_identity=hashlib.sha256((identity+pose_id+repr(sample.offsets)).encode()).hexdigest()
    result.preview_pose={'id':pose_id,'time_seconds':sample.time_seconds,'provider':'static-offset-v1'}
    return result
