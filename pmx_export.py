"""PMX 2.0 export of the resolved preview, independent of Qt and D3D.

Export either the displayed native rig or an automatically prepared MMD rig.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import shutil
import struct
import tempfile
import uuid

import numpy as np


DEFAULT_SCALE = 12.5  # FFXIV metres -> MMD units (one MMD unit = 8 cm).
AXES = np.array((1., 1., -1.))  # Y up, reflected Z; reverse triangle winding.


@dataclass
class Bone:
    name: str
    position: np.ndarray
    parent: int
    english: str = ''
    tail: int | tuple | None = None
    movable: bool = True
    visible: bool = True
    layer: int = 0
    append_rotation: tuple | None = None
    ik: dict | None = None


@dataclass
class Part:
    name: str
    positions: np.ndarray
    normals: np.ndarray
    uv: np.ndarray
    bones: np.ndarray
    weights: np.ndarray
    triangles: np.ndarray
    texture: object = None  # Pillow image, kept out of the binary writer.
    double_sided: bool = True
    source: dict = field(default_factory=dict)


@dataclass
class Model:
    name: str
    bones: list[Bone]
    parts: list[Part]
    scale: float
    warnings: list[str] = field(default_factory=list)
    morphs: list = field(default_factory=list)
    rigids: list = field(default_factory=list)
    joints: list = field(default_factory=list)
    displays: list = field(default_factory=list)
    rig: str = 'native'


def _finite(value, shape, label):
    array = np.asarray(value, dtype=float)
    if array.shape != shape or not np.isfinite(array).all():
        raise ValueError('PMX：'+label+'尺寸不符或含无效数值')
    return array


class _Skeleton:
    def __init__(self, scene, outfit, scale, reference=None):
        self.scene, self.outfit, self.scale = scene, outfit, scale
        self.names = dict(outfit.names)
        if set(self.names.values()) != set(range(len(self.names))):
            raise ValueError('PMX：角色骨骼索引不连续')
        self.bind = _finite(outfit.bind, (len(self.names), 4, 4), '绑定骨架')
        skinning = _finite(scene.skinning, self.bind.shape, '姿势矩阵')
        self.world = skinning @ self.bind
        self.reference = reference
        self.current = [np.eye(4)]
        self.target = [np.eye(4)]
        self.bones = [Bone('全ての親', np.zeros(3), -1)]
        self.lookup = {}
        self.visiting = set()
        self.attachments = {}
        for name in self.names:
            self.add('', name, self.names, outfit.parents, self.world)

    def add(self, scope, name, names, parents, world):
        key = (scope, name)
        if key in self.lookup:
            return self.lookup[key]
        if key in self.visiting:
            raise ValueError('PMX：骨骼父级存在循环：'+name)
        if name not in names or name not in parents:
            raise ValueError('PMX：骨骼缺少父级信息：'+name)
        self.visiting.add(key)
        parent = parents[name]
        if parent is None:
            parent_index = 0
        elif parent in self.names:
            parent_index = self.add('', parent, self.names, self.outfit.parents, self.world)
        else:
            parent_index = self.add(scope, parent, names, parents, world)
        index = len(self.bones)
        current = world[names[name]]
        target = current
        if self.reference is not None:
            if name in self.names:
                target = self.reference[self.names[name]]
            else:
                ancestor = parent_index
                # Preserve the attachment's local transform, including visors.
                target = self.target[ancestor] @ np.linalg.solve(self.current[ancestor], current)
        self.current.append(current)
        self.target.append(target)
        self.bones.append(Bone((scope+'/' if scope else '')+name,
                               target[:3, 3]*AXES*self.scale, parent_index))
        self.lookup[key] = index
        self.visiting.remove(key)
        return index

    def palette(self, mesh):
        palette = np.asarray(mesh['palette'])
        if palette.ndim != 1 or palette.dtype.kind not in 'iu' or np.any(palette < 0):
            raise ValueError('PMX：网格骨骼调色板无效')
        names, parents, world, scope = self.names, self.outfit.parents, self.world, ''
        if np.any(palette >= len(self.names)):
            item = mesh.get('attachment_item')
            if item is None:
                item = self.outfit.item_metadata(mesh['item_id'], mesh['slot'])
            key = (item['slot'], item['model_set'])
            if key not in self.attachments:
                attached = self.outfit.attachment(item)
                names, parents = attached['names'], attached['parents']
                bind = _finite(attached['bind'], (len(names), 4, 4), '装备绑定骨架')
                original = _finite(attached['skinning'], bind.shape, '装备姿势') @ bind
                world = original.copy()
                # The pose provider moves each attached subtree with its real
                # character ancestor, including ancestors absent from a palette.
                for name, index in names.items():
                    if name in self.names:
                        world[index] = self.world[self.names[name]]
                        continue
                    ancestor, seen = name, set()
                    while ancestor not in self.names:
                        if ancestor is None or ancestor in seen:
                            raise ValueError('PMX：装备骨骼未连接角色')
                        seen.add(ancestor)
                        ancestor = parents[ancestor]
                    base = self.names[ancestor]
                    world[index] = self.world[base] @ np.linalg.solve(original[base], original[index])
                if item['slot'] == 'Head':
                    for name, matrix in getattr(self.scene, 'visor_bones', {}).items():
                        if name in names:
                            world[names[name]] = matrix
                self.attachments[key] = (names, parents, world, f'{key[0]}_{key[1]}')
            names, parents, world, scope = self.attachments[key]
        reverse = {index: name for name, index in names.items()}
        result = []
        for value in palette:
            if int(value) not in reverse:
                raise ValueError('PMX：网格引用不存在的骨骼')
            name = reverse[int(value)]
            result.append(self.add('' if name in self.names else scope, name, names, parents, world))
        return np.asarray(result, dtype=np.int32)


def _weights(vertices, palette):
    raw = np.asarray(vertices['weights'], dtype=float)
    joints = np.asarray(vertices['joints'])
    if (raw.ndim != 2 or raw.shape != joints.shape or not 1 <= raw.shape[1] <= 8
            or joints.dtype.kind not in 'iu' or not np.isfinite(raw).all() or np.any(raw < 0)):
        raise ValueError('PMX：蒙皮权重无效')
    active = raw > 0
    if np.any(joints[active] >= len(palette)) or np.any(joints[active] < 0):
        raise ValueError('PMX：蒙皮引用超出网格骨骼范围')
    totals = raw.sum(axis=1)
    if np.any(totals <= 0):
        raise ValueError('PMX：顶点没有有效骨骼权重')
    normalized = raw / totals[:, None]
    mapped = palette[np.where(active, joints, 0)]
    bones = np.zeros((len(raw), 4), dtype=np.int32)
    weights = np.zeros((len(raw), 4), dtype=float)
    reduced, discarded = 0, 0.
    for index, (row, amounts) in enumerate(zip(mapped, normalized)):
        merged = {}
        for bone, weight in zip(row, amounts):
            if weight > 0:
                merged[int(bone)] = merged.get(int(bone), 0.) + float(weight)
        ranked = sorted(merged.items(), key=lambda pair: (-pair[1], pair[0]))
        if len(ranked) > 4:
            reduced += 1
            discarded = max(discarded, sum(w for _, w in ranked[4:]))
        kept = ranked[:4]
        total = sum(w for _, w in kept)
        for slot, (bone, weight) in enumerate(kept):
            bones[index, slot], weights[index, slot] = bone, weight/total
    return bones, weights, normalized, reduced, discarded


def _normals(scene, mesh, vertices, weights, positions, triangles):
    source = _finite(vertices['normal'], (len(vertices), 3), '法线')
    joints = mesh.get('joint_matrices')
    if joints is None:
        joints = scene.skinning[np.asarray(mesh['palette'])]
    joints = _finite(joints, (len(mesh['palette']), 4, 4), '网格蒙皮矩阵')
    matrices = np.zeros((len(vertices), 3, 3))
    for column in range(weights.shape[1]):
        active = weights[:, column] > 0
        matrices[active] += (joints[vertices['joints'][active, column], :3, :3]
                             * weights[active, column, None, None])
    # Inverse transpose handles nonuniform character/body scaling.
    normals = np.einsum('nji,nj->ni', np.linalg.pinv(matrices), source)
    lengths = np.linalg.norm(normals, axis=1)
    zero = lengths < 1e-10
    if np.any(zero):
        geometry = np.zeros_like(normals)
        tri = positions[triangles]
        face = np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0])
        for column in range(3):
            np.add.at(geometry, triangles[:, column], face)
        normals[zero] = geometry[zero]
        lengths = np.linalg.norm(normals, axis=1)
        normals[lengths < 1e-10] = (0, 1, 0)
        lengths = np.linalg.norm(normals, axis=1)
    return normals / lengths[:, None] * AXES


def _rest_mesh(scene, mesh, vertices, positions, weights, palette, rig):
    """Undo the actual blended preview transform, then apply the reference rig.

    All source influences participate before reduction to PMX's four weights.
    Solving against the resolved positions also retains baked shape changes.
    """
    old = mesh.get('joint_matrices')
    if old is None:
        old = scene.skinning[np.asarray(mesh['palette'])]
    old = _finite(old, (len(palette), 4, 4), '网格蒙皮矩阵')
    delta = np.asarray(rig.target)[palette] @ np.linalg.inv(np.asarray(rig.current)[palette])
    new = delta @ old
    blends = [np.zeros((len(vertices), 4, 4)) for _ in range(2)]
    for column in range(weights.shape[1]):
        active = weights[:, column] > 0
        indices = vertices['joints'][active, column]
        for blend, matrices in zip(blends, (old, new)):
            blend[active] += matrices[indices] * weights[active, column, None, None]
    source = np.linalg.solve(blends[0], np.column_stack((positions, np.ones(len(positions))))[..., None])
    result = (blends[1] @ source)[:, :3, 0]
    if not np.isfinite(result).all():
        raise ValueError('PMX：无法恢复参考姿势')
    return result, {**mesh, 'joint_matrices': new}


def build_model(scene, outfit, *, name='FFXIV', scale=DEFAULT_SCALE, material_provider=None,
                check_cancel=lambda: None, rig='native', ik=True, expressions=True, physics=True):
    """Consume final visible indices and posed positions, never change the scene.

    material_provider(mesh) returns (Pillow RGBA image, UV array, notices).
    With no provider the explicitly requested untextured export uses source UVs.
    """
    if type(scale) not in (int, float) or not math.isfinite(scale) or not 0 < scale <= 10000:
        raise ValueError('PMX：缩放比例必须大于 0 且不超过 10000')
    if getattr(scene, 'pending_meshes', ()) or getattr(scene, 'omitted', ()):
        raise ValueError('PMX：当前角色仍有未加载的可见部件')
    if rig not in ('native', 'standard') or any(type(v) is not bool for v in (ik, expressions, physics)):
        raise ValueError('PMX：骨架选项不正确')
    standard = rig == 'standard'
    reference = None
    if standard:
        from pmx_rig import reference_world
        reference = reference_world(scene, outfit)
    skeleton = _Skeleton(scene, outfit, scale, reference)
    model = Model(str(name), skeleton.bones, [], float(scale), [
        '当前预览姿势作为初始姿势；保留原生骨骼名称，可手动摆姿。',
        '未自动生成 MMD 标准骨骼映射、IK、表情、刚体或物理关节；使用 VMD 前需要适配。',
        '不导出装备粒子特效，MMD 光照与游戏预览存在差异。',
    ])
    model.rig = rig
    if standard:
        model.warnings = ['使用自定义身材的 A 姿势与自动生成的 MMD 骨架；VMD 动作仍需按角色比例调整。',
                          '不导出装备粒子特效，MMD 光照与游戏预览存在差异。']
    for number, mesh in enumerate(scene.meshes):
        check_cancel()
        indices = np.asarray(mesh['indices'])
        if not indices.size:
            continue
        if indices.dtype.kind not in 'iu' or indices.size % 3:
            raise ValueError('PMX：三角形索引格式不正确')
        indices = indices.reshape(-1, 3)
        source = mesh['vertices']
        if np.any(indices < 0) or np.any(indices >= len(source)):
            raise ValueError('PMX：三角形引用不存在的顶点')
        used, inverse = np.unique(indices, return_inverse=True)
        triangles = inverse.reshape(-1, 3)
        vertices = source[used]
        positions = _finite(mesh['posed_positions'], (len(source), 3), '姿势顶点')[used]
        palette = skeleton.palette(mesh)
        bones, weights, original, reduced, loss = _weights(vertices, palette)
        normal_mesh = mesh
        if standard:
            positions, normal_mesh = _rest_mesh(scene, mesh, vertices, positions, original, palette, skeleton)
        normals = _normals(scene, normal_mesh, vertices, original, positions, triangles)
        uv = np.asarray(vertices['uv'], dtype=float)[:, :2]
        texture, notices = None, []
        if material_provider is not None:
            texture, all_uv, notices = material_provider(mesh)
            uv = _finite(all_uv, (len(source), 2), '烘焙 UV')[used]
        if reduced:
            model.warnings.append(f'{mesh["slot"]}/{number}：{reduced} 个顶点保留最强的四个权重；最大舍弃权重 {loss:.2%}。')
        model.warnings.extend(notices)
        model.parts.append(Part(
            f'{mesh["slot"]}_{number:03}', positions*AXES*scale, normals,
            _finite(uv, (len(used), 2), 'UV'), bones, weights,
            triangles[:, (0, 2, 1)].astype(np.uint32), texture,
            not bool(mesh.get('material', {}).get('shader_flags', 0) & 1),
            {key: mesh.get(key) for key in ('slot', 'source_mesh', 'material_path', 'shader_path', 'item_id', 'dyes')},
        ))
    if not model.parts:
        raise ValueError('PMX：没有可导出的可见三角形')
    if standard:
        from pmx_rig import prepare_standard
        check_cancel()
        prepare_standard(model, ik=ik, expressions=expressions, physics=physics, check_cancel=check_cancel)
    model.warnings = list(dict.fromkeys(model.warnings))
    return model


def _string(stream, value):
    raw = str(value).encode('utf-16-le')
    stream.write(struct.pack('<i', len(raw)))
    stream.write(raw)


def bone_display_connections(bones, scale):
    """Choose anatomical bone tails, independent of child insertion order.

    PMX stores joint positions separately from display connections. Blender
    uses those connections to construct its rest-bone directions, so selecting
    an arbitrary child (a breast or weapon socket, for example) is incorrect.
    This changes neither the joints nor the deformation hierarchy.
    """
    source = {bone.english or bone.name: i for i, bone in enumerate(bones)}
    children = {}
    for i, bone in enumerate(bones): children.setdefault(bone.parent, []).append(i)
    chains = {'j_sebo_a': 'j_sebo_b', 'j_sebo_b': 'j_sebo_c', 'j_sebo_c': 'j_kubi', 'j_kubi': 'j_kao'}
    for side in ('l', 'r'):
        for start, end in (('j_sako','j_ude_a'),('j_ude_a','j_ude_b'),('j_ude_b','j_te'),
                           ('j_te','j_naka_a'),('j_asi_a','j_asi_b'),('j_asi_b','j_asi_d'),('j_asi_c','j_asi_d'),('j_asi_d','j_asi_e')):
            chains[start+'_'+side] = end+'_'+side
        for finger in ('j_oya','j_hito','j_naka','j_kusu','j_ko'):
            chains[f'{finger}_a_{side}'] = f'{finger}_b_{side}'
    small = .04*scale
    epsilon = 1e-6*scale
    def usable(i, tail):
        if isinstance(tail, (int, np.integer)):
            return 0 <= tail < len(bones) and np.linalg.norm(bones[tail].position-bones[i].position) > epsilon
        return tail is not None and np.asarray(tail).shape == (3,) and np.isfinite(tail).all() and np.linalg.norm(tail) > epsilon
    def continuation(i):
        bone = bones[i]
        if bone.parent >= 0:
            delta = bone.position-bones[bone.parent].position
            length = np.linalg.norm(delta)
            if length > epsilon:
                return tuple(delta/length * np.clip(length*.6, small*.25, small*1.5))
        # Coincident facial pivots face forward; hair pivots extend downward.
        name = (bone.english or bone.name).rsplit('/', 1)[-1]
        if name.startswith('j_f_'): return (0., 0., -small*.5)
        if name.startswith(('j_kami_', 'j_ex_h')): return (0., -small, 0.)
        return (0., small, 0.)
    result = []
    for i, bone in enumerate(bones):
        name = bone.english or bone.name
        tail = None
        if bone.name == '全ての親':
            tail = (0., small*2, 0.)
        elif bone.name in ('グルーブ', '腰') or name in ('n_root', 'n_hara'):
            tail = (0., small, 0.)
        elif name == 'j_kosi' and all(n in source for n in ('j_asi_a_l', 'j_asi_a_r')):
            # The lower-body control points between both hips, never at one leg.
            tail = tuple((bones[source['j_asi_a_l']].position+bones[source['j_asi_a_r']].position)/2-bone.position)
        elif name == 'j_kao':
            tail = (0., small*3, 0.)
        elif name in ('j_f_eye_l', 'j_f_eye_r'):
            tail = (0., 0., -small)
        elif name in ('j_asi_e_l', 'j_asi_e_r') and bone.parent >= 0:
            delta = bone.position-bones[bone.parent].position
            delta = delta.copy(); delta[1] = 0.
            length = np.linalg.norm(delta)
            tail = tuple(delta/length*small) if length > epsilon else (0., 0., -small)
        elif name in chains and chains[name] in source:
            tail = source[chains[name]]
        if not usable(i, tail): tail = bone.tail
        if not usable(i, tail):
            candidates = [child for child in children.get(i, ()) if usable(i, child)]
            # A branching bone has no unique direction. Continue its incoming
            # chain rather than depending on palette/dictionary ordering.
            tail = candidates[0] if len(candidates) == 1 else continuation(i)
        result.append(tail)
    return result


def write_pmx(stream, model, texture_paths):
    """Write PMX 2.0, UTF-16LE, four-byte indices and BDEF4 weights."""
    if len(texture_paths) != len(model.parts):
        raise ValueError('PMX：贴图列表与材质数量不符')
    count = sum(len(part.positions) for part in model.parts)
    faces = sum(part.triangles.size for part in model.parts)
    if max(count, faces, len(model.bones)) >= 2**31:
        raise ValueError('PMX：模型超过索引容量')
    stream.write(struct.pack('<4sf9B', b'PMX ', 2., 8, 0, 0, 4, 4, 4, 4, 4, 4))
    for value in (model.name, model.name, '\r\n'.join(model.warnings), 'Exported by FFXIV Offline Glamour. Rig: '+model.rig):
        _string(stream, value)
    stream.write(struct.pack('<i', count))
    dtype = np.dtype([('position', '<f4', 3), ('normal', '<f4', 3), ('uv', '<f4', 2),
                      ('deform', 'u1'), ('bones', '<i4', 4), ('weights', '<f4', 4), ('edge', '<f4')])
    for part in model.parts:
        data = np.zeros(len(part.positions), dtype=dtype)
        for field, value in (('position', part.positions), ('normal', part.normals), ('uv', part.uv),
                             ('bones', part.bones), ('weights', part.weights)):
            data[field] = value
        data['deform'] = 2  # BDEF4, including vertices with fewer influences.
        data['edge'] = 1.
        stream.write(data.tobytes())
    stream.write(struct.pack('<i', faces))
    offset = 0
    for part in model.parts:
        stream.write(np.asarray(part.triangles+offset, dtype='<u4').tobytes())
        offset += len(part.positions)
    textures = list(dict.fromkeys(path for path in texture_paths if path is not None))
    stream.write(struct.pack('<i', len(textures)))
    for path in textures:
        _string(stream, path.replace('/', '\\'))
    stream.write(struct.pack('<i', len(model.parts)))
    for part, texture in zip(model.parts, texture_paths):
        _string(stream, part.name)
        _string(stream, part.name)
        stream.write(struct.pack('<4f3ff3fB4ff', 1., 1., 1., 1., 0., 0., 0., 1., .5, .5, .5,
                                 14 | int(part.double_sided), 0., 0., 0., 1., 0.))
        # No sphere map or toon texture; those require MMD-specific authoring.
        stream.write(struct.pack('<iiBBi', textures.index(texture) if texture else -1, -1, 0, 0, -1))
        _string(stream, json.dumps(part.source, ensure_ascii=False))
        stream.write(struct.pack('<i', part.triangles.size))
    stream.write(struct.pack('<i', len(model.bones)))
    tails = bone_display_connections(model.bones, model.scale)
    for index, bone in enumerate(model.bones):
        _string(stream, bone.name)
        _string(stream, bone.english or ('Root' if index == 0 else bone.name))
        tail = tails[index]
        indexed_tail = isinstance(tail, (int, np.integer))
        flags = 0x12 | (4 if bone.movable else 0) | (8 if bone.visible else 0) | int(indexed_tail)
        if bone.append_rotation is not None: flags |= 0x100
        if bone.ik is not None: flags |= 0x20
        stream.write(struct.pack('<3fiiH', *bone.position, bone.parent, bone.layer, flags))
        stream.write(struct.pack('<i', tail) if indexed_tail else struct.pack('<3f', *(tail if tail is not None else (0., .1*model.scale, 0.))))
        if bone.append_rotation is not None:
            stream.write(struct.pack('<if', *bone.append_rotation))
        if bone.ik is not None:
            data = bone.ik
            stream.write(struct.pack('<iifi', data['target'], data['iterations'], data['angle'], len(data['links'])))
            for link, limits in data['links']:
                stream.write(struct.pack('<iB', link, int(limits is not None)))
                if limits is not None: stream.write(struct.pack('<6f', *limits[0], *limits[1]))
    stream.write(struct.pack('<i', len(model.morphs)))
    for morph in model.morphs:
        _string(stream, morph['name']); _string(stream, morph['english'])
        stream.write(struct.pack('<BBi', morph['panel'], 1, len(morph['indices'])))  # Vertex morph.
        data = np.zeros(len(morph['indices']), dtype=[('index', '<i4'), ('delta', '<f4', 3)])
        data['index'], data['delta'] = morph['indices'], morph['offsets']
        stream.write(data.tobytes())
    groups = model.displays or [('Root', 'Root', True, [(0, 0)]), ('表情', 'Facial', True, []),
              ('骨格', 'Native bones', False, [(0, i) for i in range(1, len(model.bones))])]
    stream.write(struct.pack('<i', len(groups)))
    for japanese, english, special, bones in groups:
        _string(stream, japanese)
        _string(stream, english)
        stream.write(struct.pack('<Bi', int(special), len(bones)))
        for kind, index in bones:
            stream.write(struct.pack('<Bi', kind, index))
    stream.write(struct.pack('<i', len(model.rigids)))
    for body in model.rigids:
        _string(stream, body['name']); _string(stream, body['name'])
        stream.write(struct.pack('<iBHB', body['bone'], body['group'], body['mask'], body['shape']))
        stream.write(struct.pack('<14fB', *body['size'], *body['position'], *body['rotation'],
                                 body['mass'], body['linear_damping'], body['angular_damping'],
                                 0., .5, body['mode']))
    stream.write(struct.pack('<i', len(model.joints)))
    for joint in model.joints:
        _string(stream, joint['name']); _string(stream, joint['name'])
        stream.write(struct.pack('<Bii', 0, joint['a'], joint['b']))
        for key in ('position', 'rotation', 'linear_min', 'linear_max', 'angular_min', 'angular_max',
                    'linear_spring', 'angular_spring'):
            stream.write(struct.pack('<3f', *joint[key]))


def export_scene(scene, outfit, destination, *, name=None, scale=DEFAULT_SCALE,
                 material_provider=None, overwrite=False, check_cancel=lambda: None,
                 rig='native', ik=True, expressions=True, physics=True):
    """Publish the PMX last, so a failed export never replaces an existing model.

    Each export owns a new assets directory; an overwrite cannot damage the
    textures used by an older PMX or unrelated files in the chosen directory.
    """
    destination = Path(destination).absolute()
    if destination.suffix.lower() != '.pmx':
        raise ValueError('请选择 .pmx 文件名')
    if destination.is_symlink() or destination.is_dir():
        raise ValueError('PMX 输出必须是普通文件')
    if destination.exists() and not overwrite:
        raise FileExistsError('PMX 文件已存在：'+str(destination))
    model = build_model(scene, outfit, name=name or destination.stem, scale=scale,
                        material_provider=material_provider, check_cancel=check_cancel,
                        rig=rig, ik=ik, expressions=expressions, physics=physics)
    check_cancel()
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.pmx-', dir=destination.parent))
    assets_name = 'pmx_assets_'+uuid.uuid4().hex[:16]
    assets = destination.parent/assets_name
    published = False
    moved_assets = False
    try:
        folder = stage/assets_name
        folder.mkdir()
        textures = []
        for index, part in enumerate(model.parts):
            check_cancel()
            relative = None
            if part.texture is not None:
                relative = f'{assets_name}/material_{index:03}.png'
                part.texture.save(stage/relative, format='PNG')
            textures.append(relative)
        report = {'format': 'PMX 2.0', 'model': destination.name, 'scale': model.scale,
                  'pose': 'customized A pose' if rig == 'standard' else 'current preview as rest pose',
                  'rig': model.rig, 'morphs': len(model.morphs), 'rigid_bodies': len(model.rigids),
                  'physics_joints': len(model.joints), 'ik_bones': sum(b.ik is not None for b in model.bones),
                  'vertices': sum(len(p.positions) for p in model.parts),
                  'triangles': sum(len(p.triangles) for p in model.parts), 'bones': len(model.bones),
                  'materials': len(model.parts), 'textures': sum(t is not None for t in textures),
                  'warnings': model.warnings, 'parts': [p.source for p in model.parts]}
        (folder/'export.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
        with (stage/'model.pmx').open('xb') as stream:
            write_pmx(stream, model, textures)
            stream.flush()
            os.fsync(stream.fileno())
        check_cancel()
        folder.rename(assets)
        moved_assets = True
        if overwrite:
            os.replace(stage/'model.pmx', destination)
        else:
            # Windows rename / POSIX link atomically refuses a file that
            # appeared since the save dialog. Paths share a filesystem.
            if os.name == 'nt':
                os.rename(stage/'model.pmx', destination)
            else:
                os.link(stage/'model.pmx', destination)
        published = True
        return {**report, 'path': str(destination), 'assets': str(assets),
                'report': str(assets/'export.json')}
    finally:
        # Only paths created by this invocation are removed, never user folders.
        root=destination.parent.resolve()
        if stage.resolve().parent == root:
            shutil.rmtree(stage)
        if not published and moved_assets and assets.resolve().parent == root:
            shutil.rmtree(assets)
