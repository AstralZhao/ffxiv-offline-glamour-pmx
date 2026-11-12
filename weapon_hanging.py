"""Source-bound static gravity pose for the reviewed free katana tassel.

This is a frozen single-leaf equilibrium, not an animated PHYB integrator.
No collider, attraction, wind, or moving-parent history is synthesized.
"""
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import numpy as np
from geometry_probe import local_matrix
from prepare_fixed_stance import world_pose


@dataclass(frozen=True)
class HangingSource:
    names:tuple
    parents:tuple
    reference:np.ndarray
    chain:object
    prepared:object
    gravity:tuple
    axis:tuple
    angle:float
    paths:tuple


def prepare(pack,model,body,skeleton,extra):
    # This selects a reviewed source topology shared by the original and
    # replica, not an item-specific visible angle. Other physics stays scoped.
    if (model,body)!=(2215,3):return None
    if extra is None:raise ValueError('武器挂绳缺少原始附属骨架')
    from static_phyb_source import read_phyb_source
    from static_phyb_extension import _Reader
    from static_chain_preparation import prepare_source_node_chain
    paths=((f'chara/weapon/w{model:04}/skeleton/base/b0001/phy_w{model:04}b0001.phyb','f97cb31e929b24b5a420287717c7f7273738f486de4504d1f1cef2ea0623b4d4'),
        (f'chara/weapon/w{model:04}/skeleton/parts/p{body:04}/phy_w{model:04}p{body:04}.phyb','4973e4d753f603094d8e2fd437755e4f665a689895030ab6365423e0d00bad90'))
    sources=[]
    for path,digest in paths:
        raw=pack.read(path)
        if hashlib.sha256(raw).hexdigest()!=digest:raise NotImplementedError('这款武器的挂绳物理来源已变化，需重新核对')
        sources.append(read_phyb_source(raw))
    base,source=sources
    if any(s.source_chains for s in base.simulators)or len(source.simulators)!=1:raise ValueError('挂绳物理模拟器布局不一致')
    sim=source.simulators[0]
    if (len(sim.source_chains)!=1 or sim.values.flags!=0x13 or any(sim.values.wind)
        or any((sim.collisions,sim.collision_connectors,sim.connectors,sim.attracts,sim.pins,sim.springs,sim.post_alignments))):
        raise NotImplementedError('挂绳静止重力规则不支持该物理约束')
    chain=sim.source_chains[0];nodes=chain.values.nodes
    if (chain.kind!=0 or len(nodes)!=2 or nodes[-1].name or any(n.attraction or any(n.plane)or any(n.collision_flags)for n in nodes)
        or nodes[-1].gravity_scale<=0):raise NotImplementedError('挂绳不是已核对的自由单骨骼链')
    bones=list(skeleton['bones']);names=[b['name']for b in bones];parents=[b['parent']for b in bones];locals_=[local_matrix(b)for b in bones]
    extra_bones=extra['bones']
    for i,b in enumerate(extra_bones):
        if b['name']in names:
            if b['parent']!=-1:raise ValueError('挂绳附属骨架共享节点不是根')
            continue
        p=b['parent']
        if p<0 or extra_bones[p]['name']not in names:raise ValueError('挂绳缺少原始父节点')
        parents.append(names.index(extra_bones[p]['name']));names.append(b['name']);locals_.append(local_matrix(b))
    reference=world_pose(np.asarray(locals_),parents)
    prep=prepare_source_node_chain(chain,names,np.asarray(parents),reference,selected_terminal_scale=(1,1,1))
    if len(prep.bone_indices)!=1 or any(p==prep.bone_indices[0]for p in parents):raise ValueError('挂绳不是独立末端骨骼')
    # At full enabled physics strength the authored enabled Delta overrides
    # the baseline cone (original1bec211..1bec2bb). Read the actual source
    # root, not the virtual terminal's auxiliary velocity/filter settings.
    r=_Reader(source.extended);root=r.table(r.pointer(0),(2,2,4));s=r.table(r.vector(root[2],64)[0],(4,4,4,1,4,4))
    c=r.table(r.vector(s[0],128)[0],(4,4,4));n=r.table(r.vector(c[0],32)[0],(4,4,4,None,4,4,4,4))
    delta=r.delta(n[6]);axis=nodes[0].cone_axis;angle=nodes[0].cone_angle
    if delta is not None and delta.enabled:
        axis=delta.axis;angle=float(np.float32(np.float32(delta.angle_degrees)*np.float32(np.pi/180)))
    reference.setflags(write=False)
    return HangingSource(tuple(names),tuple(parents),reference,chain,prep,sim.values.gravity,tuple(axis),angle,paths)


class HangingPoses:
    def __init__(self):self.entries=OrderedDict()

    def resolve(self,source,attachment,deltas):
        from static_hair_frame import normalize
        from static_hair_preparation import cone_from_cosine,baseline_cosine
        from static_node_hair import prepare_current_chain
        from static_chain_output import prepared_chain_output
        key=(source.paths,np.asarray(attachment,dtype='<f8').tobytes(),tuple((n,np.asarray(deltas[n],dtype='<f8').tobytes())for n in source.names if n in deltas))
        if key in self.entries:
            self.entries.move_to_end(key);return self.entries[key]
        current=np.asarray([deltas.get(n,np.eye(4))@b for n,b in zip(source.names,source.reference)])
        gravity=np.linalg.solve(np.asarray(attachment)[:3,:3],source.gravity)
        segment=normalize(gravity)*np.float32(source.prepared.rest_lengths[-1])
        if not np.any(segment):raise ValueError('挂绳缺少有限重力方向')
        target,initial=prepare_current_chain(source.prepared,source.chain,current,source.reference)
        target[1]=target[0]+segment
        i=source.prepared.bone_indices[0];parent=source.parents[i]
        cosine,sine=cone_from_cosine(baseline_cosine(source.angle))
        points,_,output,_=prepared_chain_output(target,np.zeros_like(target),initial,current[parent,:3,:3],
            np.asarray(source.prepared.bone_corrections),np.asarray([normalize(source.axis)]),np.asarray([cosine]),np.asarray([sine]))
        bone=np.eye(4);bone[:3]=output[0,:3];bone[:3,3]=current[i,:3,3]
        result=dict(deltas);delta=bone@np.linalg.inv(source.reference[i]);delta.setflags(write=False);result[source.names[i]]=delta
        notice={'provider':'source-single-leaf-static-gravity-v1','bone':source.names[i],'gravity_world':list(source.gravity),
            'source_constraint_angle':source.angle,'source_constraint_axis':list(source.axis),'fixed_physics_strength':1.,
            'dynamic_physics':False,'source_paths':[p for p,_ in source.paths]}
        self.entries[key]=(result,notice)
        while len(self.entries)>8:self.entries.popitem(last=False)
        return result,notice
