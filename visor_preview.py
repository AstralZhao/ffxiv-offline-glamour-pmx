"""Original GMP final-state visor controls; no transition animation."""
import copy
import hashlib
import math
import struct

import numpy as np


def final_parameters(record,adjusted=False):
    if type(adjusted)is not bool:raise TypeError('头部装备调整状态不正确')
    raw=bytes.fromhex(record['value_hex'])
    if len(raw)!=5:raise ValueError('头部装备调整参数长度不正确')
    word=int.from_bytes(raw[:4],'little');enabled=bool(word&1)
    # CN44653f..446640: three independent 10-bit bone angles, NOT XYZ
    # Euler angles on the whole helmet. Fifth-byte nibbles drive RGB.
    angles=[float(np.float32(np.float32((word>>shift)&1023)*np.float32(.01745329238474369)))
        if enabled and adjusted else 0. for shift in(2,12,22)]
    color=float(np.float32(np.float32(raw[4]>>4 if adjusted else raw[4]&15)/np.float32(10)))if enabled else 0.
    return {'enabled':enabled,'adjusted':adjusted,'angles':angles,'material_rgb':color,
        'material_override':enabled and raw[4]!=0xaa}


def rotated_world(matrix,angle):
    """CN463dc2..463eff: model-space Q * local-Z rotation; retain T/S."""
    value=np.array(matrix,copy=True);scale=np.linalg.norm(value[:3,:3],axis=0)
    if np.any(scale<=1e-12):raise ValueError('头饰骨骼缩放不可逆')
    rotation=value[:3,:3]/scale
    if not np.allclose(rotation.T@rotation,np.eye(3),atol=2e-4,rtol=0):
        raise NotImplementedError('头饰调整骨骼含尚未核实的剪切变换')
    c=math.cos(angle);s=math.sin(angle)
    value[:3,:3]=rotation@np.array(((c,-s,0),(s,c,0),(0,0,1)))@np.diag(scale)
    return value


def apply_visor(scene,outfit,info,adjusted=False):
    from geometry_probe import resolve_mesh
    item=info.get('items',{}).get('Head')
    displayed=bool(item and info.get('component_visibility',{}).get('Head')and'gmp'in item)
    report={'item_id':item['item_id']if item else None,'supported':False,'adjusted':adjusted,'displayed':displayed}
    result=copy.copy(scene);result.visor=report
    if not displayed:return result
    source=outfit.model(item);parameters=final_parameters(item['gmp'],adjusted)
    attributes={a for m in source for p in m.submeshes for a in p.attributes}
    report.update(supported=parameters['enabled']or'atr_vsr'in attributes,attribute_switch='atr_vsr'in attributes,**parameters)
    if not report['supported']:return result
    # Only resolve the original head attribute/index slots. Current EQP/IMC
    # masks and authored shape priority are retained in both end states.
    plan=info['visibility']['Head'];by_mesh={m.source_mesh:m for m in source}
    attached=None;names=outfit.names;parents=outfit.parents;bind=outfit.bind
    missing={n for m in source for n in m.bone_names}-names.keys()
    if missing:
        attached=outfit.attachment(item);names=attached['names'];parents=attached['parents']
        # attachment() already verifies shared roots and original EST locals.
        # Its extra inverse-bind matrices are unnecessary: derive each current
        # extra world from the selected standing pose's nearest core ancestor.
    original=scene.skinning@bind
    worlds={name:original[index].copy()for name,index in outfit.names.items()}
    if attached:
        # Reference extra world is reconstructed from explicit source parents.
        _,skeleton=outfit.extra_source(item)
        from geometry_probe import local_matrix
        for bone in skeleton['bones']:
            name=bone['name']
            if name not in worlds:worlds[name]=worlds[parents[name]]@local_matrix(bone)
    initial={name:matrix.copy()for name,matrix in worlds.items()}
    changed=[]
    for name,angle in zip(('j_ex_met_va','j_ex_met_vb','j_ex_met_vc'),parameters['angles']):
        if not angle or name not in worlds:continue
        old=worlds[name].copy();new=rotated_world(old,angle);delta=new@np.linalg.inv(old)
        for child in worlds:
            ancestor=child
            while ancestor is not None and ancestor!=name:ancestor=parents[ancestor]
            if ancestor==name:worlds[child]=delta@worlds[child]
        changed.append(name)
    moves={name:worlds[name]@np.linalg.inv(initial[name])for name in worlds}
    result.meshes=[]
    for mesh in scene.meshes:
        if mesh['slot']!='Head':result.meshes.append(mesh);continue
        mesh={**mesh};raw=by_mesh[mesh['source_mesh']]
        mesh['indices']=resolve_mesh(raw,plan['hidden_attributes']+(['atr_vsr']if adjusted else[]),
            plan['enabled_shapes'],plan['disabled_variant_attributes']).indices
        if not len(mesh['indices']):continue
        if parameters['material_override']:
            mesh['extra_constants']={**mesh.get('extra_constants',{}),
                'g_MaterialParameterDynamic':struct.pack('<4f',*([parameters['material_rgb']]*3),1.)}
        if changed:
            old=mesh.get('joint_matrices',scene.skinning[mesh['palette']]if max(mesh['palette'],default=-1)<len(scene.skinning)else None)
            if old is None:raise ValueError('头饰调整缺少原始骨骼矩阵')
            # MDL bone order is the mesh's local palette order.
            joints=np.asarray([moves[name]@matrix for name,matrix in zip(raw.bone_names,old)])
            if len(joints)!=len(old)or not np.isfinite(joints).all():raise ValueError('头饰调整矩阵不正确')
            joints.setflags(write=False);mesh['joint_matrices']=joints
            v=mesh['vertices'];points=np.column_stack((v['position'],np.ones(len(v))))
            positions=np.zeros((len(v),3));weights=v['weights'].astype(float)/255.
            for i in range(weights.shape[1]):positions+=np.einsum('nij,nj->ni',joints[v['joints'][:,i]],points)[:,:3]*weights[:,i,None]
            if not np.isfinite(positions).all():raise ValueError('头饰调整顶点产生非有限值')
            mesh['posed_positions']=positions
        result.meshes.append(mesh)
    result.visor_bones={name:worlds[name]for name in worlds if name.startswith('j_ex_met_')}
    report['rotated_bones']=changed
    result.outfit_cache_identity=hashlib.sha256((getattr(scene,'outfit_cache_identity','')+'visor-v1'+str(adjusted)+item['gmp']['value_hex']).encode()).hexdigest()
    return result
