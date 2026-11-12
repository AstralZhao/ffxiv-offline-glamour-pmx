"""Deterministic MMD rig preparation from FFXIV's resolved skeleton and skin.

No learning model or external conversion program is needed. Expressions and
secondary-motion physics are deliberately conservative, editable starting points.
"""
import math
import re

import numpy as np

from pmx_export import Bone


CORE = {'n_hara': '腰', 'j_kosi': '下半身', 'j_sebo_a': '上半身',
        'j_sebo_b': '上半身2', 'j_sebo_c': '上半身3', 'j_kubi': '首', 'j_kao': '頭'}
LIMBS = {'j_sako': '肩', 'j_ude_a': '腕', 'j_ude_b': 'ひじ', 'j_te': '手首',
         'j_asi_a': '足', 'j_asi_b': 'ひざ', 'j_asi_d': '足首', 'j_asi_e': 'つま先',
         'j_f_eye': '目'}
FINGERS = {'j_oya': '親指', 'j_hito': '人指', 'j_naka': '中指', 'j_kusu': '薬指', 'j_ko': '小指'}


def reference_world(scene, outfit):
    """Customized bind A pose, retaining the resolved face below the head.

    The runtime's reviewed body rules also apply to bind transforms: they modify
    scales and relative offsets, independent of the selected idle animation.
    Face transforms are already resolved, including the user's facial sliders.
    """
    names = dict(outfit.names)
    required = set(CORE) | {f'{name}_{side}' for name in ('j_ude_a', 'j_ude_b', 'j_te', 'j_asi_a', 'j_asi_b', 'j_asi_d', 'j_asi_e') for side in ('l', 'r')}
    missing = sorted(required - names.keys())
    if missing:
        raise ValueError('PMX：标准骨架缺少必要的人形骨骼：'+', '.join(missing))
    target = np.array(outfit.bind, dtype=float, copy=True)
    customization = getattr(scene, 'pose', {}).get('customization', {})
    if customization.get('body'):
        from preset_customization import body_world
        ordered = sorted(names, key=names.get)
        parents = [names.get(outfit.parents[name], -1) for name in ordered]
        race = scene.pose['preset']['parameters']['race']
        target, _ = body_world(target, parents, ordered, customization['body'], race)
    current = np.asarray(scene.skinning) @ outfit.bind
    head = names['j_kao']
    head_delta = target[head] @ np.linalg.inv(current[head])
    for name, index in names.items():
        ancestor, seen = name, set()
        while ancestor is not None and ancestor != 'j_kao':
            if ancestor in seen:
                raise ValueError('PMX：骨骼父级存在循环')
            seen.add(ancestor)
            ancestor = outfit.parents[ancestor]
        if ancestor == 'j_kao' and index != head:
            target[index] = head_delta @ current[index]
    if not np.isfinite(target).all():
        raise ValueError('PMX：参考骨架含无效数值')
    return target


def _add(model, name, position, parent, **kwargs):
    index = len(model.bones)
    model.bones.append(Bone(name, np.array(position, dtype=float), parent, **kwargs))
    return index


def _finger_tip(model, first, second, name):
    """Split the distal native influence so MMD's third joint actually bends."""
    a, b = model.bones[first].position, model.bones[second].position
    direction = b-a
    length = np.linalg.norm(direction)
    if length < 1e-7:
        return
    direction /= length
    samples = []
    for part in model.parts:
        influence = np.sum(np.where(part.bones == second, part.weights, 0), axis=1)
        samples.extend(((part.positions[influence > .2]-b) @ direction).tolist())
    extent = max(float(np.percentile(samples, 95)) if samples else 0., length*.65)
    tip = _add(model, name, b+direction*extent*.5, second, movable=False,
               tail=tuple(direction*extent*.5))
    model.bones[second].tail = tip
    for part in model.parts:
        projected = (part.positions-b) @ direction
        amount = np.clip((projected/extent-.35)/.35, 0., 1.)
        for row in np.flatnonzero((part.bones == second).any(axis=1) & (amount > 0)):
            weights = {int(i): float(w) for i, w in zip(part.bones[row], part.weights[row]) if w > 0}
            moved = weights.get(second, 0.) * amount[row]
            if not moved:
                continue
            weights[second] -= moved
            weights[tip] = moved
            strongest = sorted(weights.items(), key=lambda x: (-x[1], x[0]))[:4]
            part.bones[row] = 0; part.weights[row] = 0
            total = sum(w for _, w in strongest)
            for column, (i, w) in enumerate(strongest):
                part.bones[row, column], part.weights[row, column] = i, w/total


def _standard_bones(model, lookup, with_ik):
    for bone in model.bones[1:]:
        bone.english, bone.visible, bone.movable = bone.name, False, False
    for source, japanese in CORE.items():
        model.bones[lookup[source]].name = japanese
        model.bones[lookup[source]].visible = True
    for side, prefix in (('l', '左'), ('r', '右')):
        for source, japanese in LIMBS.items():
            if source+'_'+side in lookup:
                bone = model.bones[lookup[source+'_'+side]]
                bone.name, bone.visible = prefix+japanese, True
        for source, japanese in FINGERS.items():
            pair = [lookup.get(f'{source}_{segment}_{side}') for segment in ('a', 'b')]
            if None in pair:
                continue
            first, second = pair
            for i, number in zip(pair, ('１', '２')):
                model.bones[i].name, model.bones[i].visible = prefix+japanese+number, True
            model.bones[first].tail = second
            if source != 'j_oya':
                _finger_tip(model, first, second, prefix+japanese+'３')
        # The native C bone is a shin helper, not the ankle joint. The ankle
        # must directly follow the knee for a two-link MMD leg IK chain.
        hip, knee, ankle, toe = [lookup[f'j_asi_{s}_{side}'] for s in ('a', 'b', 'd', 'e')]
        model.bones[ankle].parent = knee
        model.bones[hip].tail, model.bones[knee].tail, model.bones[ankle].tail = knee, ankle, toe
        shoulder, elbow, wrist = [lookup[f'{s}_{side}'] for s in ('j_ude_a', 'j_ude_b', 'j_te')]
        model.bones[shoulder].tail, model.bones[elbow].tail = elbow, wrist
        if f'j_naka_a_{side}' in lookup:
            model.bones[wrist].tail = lookup[f'j_naka_a_{side}']
        if with_ik:
            controller = _add(model, prefix+'足ＩＫ', model.bones[ankle].position, 0,
                english=side+' leg IK', layer=1, tail=(0., 0., -model.scale*.08),
                ik={'target': ankle, 'iterations': 40, 'angle': 1.,
                    'links': [(knee, ((-math.pi, 0., 0.), (-.002, 0., 0.))), (hip, None)]})
            _add(model, prefix+'つま先ＩＫ', model.bones[toe].position, controller,
                english=side+' toe IK', layer=2, tail=(0., 0., -model.scale*.05),
                ik={'target': toe, 'iterations': 12, 'angle': .5, 'links': [(ankle, None)]})
    pelvis = model.bones[lookup['n_hara']].position.copy()
    center = _add(model, 'センター', pelvis*np.array((0., .8, 0.)), 0, english='Center')
    groove = _add(model, 'グルーブ', pelvis, center, english='Groove')
    # Preserve native roots/auxiliaries while making center/groove drive the
    # entire body. IK controllers stay outside that hierarchy, fixing the feet.
    for i, bone in enumerate(model.bones[1:len(lookup)], 1):
        if bone.parent == 0:
            bone.parent = groove
    model.bones[center].tail = groove
    model.bones[groove].tail = lookup['n_hara']
    eyes = [lookup.get('j_f_eye_'+side) for side in ('l', 'r')]
    if None not in eyes:
        position = (model.bones[eyes[0]].position+model.bones[eyes[1]].position)/2
        control = _add(model, '両目', position+(0., model.scale*.06, -model.scale*.04), lookup['j_kao'],
                       english='Eyes', movable=False, tail=(0., 0., -model.scale*.06))
        for eye in eyes:
            model.bones[eye].append_rotation = (control, 1.)
            model.bones[eye].layer = 1


def _reorder(model):
    """Parents and append-transform sources precede their dependants."""
    ordered, seen, visiting = [], set(), set()
    def visit(i):
        if i in seen: return
        if i in visiting: raise ValueError('PMX：生成的骨架存在循环')
        visiting.add(i)
        bone = model.bones[i]
        for dependency in (bone.parent, bone.append_rotation[0] if bone.append_rotation else -1):
            if dependency >= 0: visit(dependency)
        visiting.remove(i); seen.add(i); ordered.append(i)
    for i in range(len(model.bones)): visit(i)
    remap = {old: new for new, old in enumerate(ordered)}
    model.bones = [model.bones[i] for i in ordered]
    for bone in model.bones:
        bone.parent = remap.get(bone.parent, -1)
        if bone.parent >= 0: bone.layer = max(bone.layer, model.bones[bone.parent].layer)
        if isinstance(bone.tail, (int, np.integer)): bone.tail = remap[bone.tail]
        if bone.append_rotation: bone.append_rotation = (remap[bone.append_rotation[0]], bone.append_rotation[1])
        if bone.ik:
            bone.ik['target'] = remap[bone.ik['target']]
            bone.ik['links'] = [(remap[i], limits) for i, limits in bone.ik['links']]
    array = np.asarray([remap[i] for i in range(len(remap))])
    for part in model.parts: part.bones = array[part.bones]


def _rotation_x(angle):
    c, s = math.cos(angle), math.sin(angle)
    return np.array(((1., 0., 0.), (0., c, -s), (0., s, c)))


def _expression(model, lookup, name, english, panel, commands):
    """Bake hierarchical facial bone movements into portable vertex morphs."""
    deltas = []
    for bone in model.bones:
        transform = np.eye(4)
        command = commands.get(bone.english)
        if command:
            translation, angle = command
            rotation = _rotation_x(angle)
            transform[:3, :3] = rotation
            transform[:3, 3] = bone.position+translation-rotation @ bone.position
        deltas.append((deltas[bone.parent] if bone.parent >= 0 else np.eye(4)) @ transform)
    deltas = np.asarray(deltas)
    indices, offsets, base = [], [], 0
    for part in model.parts:
        result = np.zeros_like(part.positions)
        for column in range(4):
            transforms = deltas[part.bones[:, column]]
            result += (np.einsum('nij,nj->ni', transforms[:, :3, :3], part.positions)+transforms[:, :3, 3])*part.weights[:, column, None]
        diff = result-part.positions
        active = np.linalg.norm(diff, axis=1) > model.scale*1e-7
        indices.extend((np.flatnonzero(active)+base).tolist()); offsets.extend(diff[active])
        base += len(part.positions)
    if indices:
        model.morphs.append({'name': name, 'english': english, 'panel': panel,
                             'indices': np.asarray(indices), 'offsets': np.asarray(offsets)})


def _expressions(model, lookup):
    required = {'j_f_mabup_01_l', 'j_f_mabup_01_r', 'j_f_ago'}
    if not required <= lookup.keys():
        model.warnings.append('当前面部缺少所需的控制骨骼，已跳过自动表情。')
        return
    # Scale translations to the actual eye spacing, not one race's body size.
    eye = [model.bones[lookup['j_f_eye_'+side]].position for side in ('l', 'r') if 'j_f_eye_'+side in lookup]
    unit = np.linalg.norm(eye[0]-eye[1]) if len(eye) == 2 else model.scale*.05
    zero = np.zeros(3)
    def blink(sides, upper=-.70, lower=.16):
        return {f'j_f_mab{lid}_01_{side}': (zero, angle) for side in sides for lid, angle in (('up', upper), ('dn', lower))}
    expressions = [('まばたき', 'Blink', 2, blink(('l', 'r'))),
                   ('ウィンク', 'Wink L', 2, blink(('l',))),
                   ('ウィンク右', 'Wink R', 2, blink(('r',))),
                   ('笑い', 'Happy eyes', 2, blink(('l', 'r'), -.52, .28))]
    for name, english, opening, spread, forward in (
            ('あ', 'A', -.24, .02, 0.), ('い', 'I', -.055, .10, 0.),
            ('う', 'U', -.065, -.09, -.04), ('え', 'E', -.14, .065, 0.), ('お', 'O', -.20, -.075, -.025)):
        commands = {'j_f_ago': (zero, opening)}
        for side, sign in (('l', 1), ('r', -1)):
            for region in ('uslip', 'dslip', 'umlip_01', 'dmlip_01'):
                commands[f'j_f_{region}_{side}'] = (np.array((sign*spread, 0., forward))*unit, 0.)
        expressions.append((name, english, 3, commands))
    smile = {f'j_f_{region}_{side}': (np.array((sign*.05, .055, 0.))*unit, 0.)
             for side, sign in (('l', 1), ('r', -1)) for region in ('uslip', 'dslip')}
    expressions.append(('にやり', 'Smile', 3, smile))
    for name, english, outer, inner in (('困る', 'Worried', -.04, .075), ('怒り', 'Angry', .045, -.055), ('上', 'Brows up', .08, .08)):
        commands = {f'j_f_{region}_{side}': (np.array((0., height, 0.))*unit, 0.)
                    for side in ('l', 'r') for region, height in (('mayu', outer), ('mmayu', (outer+inner)/2), ('miken_01', inner))}
        expressions.append((name, english, 1, commands))
    for args in expressions: _expression(model, lookup, *args)
    model.warnings.append('自动表情由面部骨骼近似生成（眨眼、口型、眉毛）；不同脸型及叠加表情可能需要在 PMX 编辑器中微调。'
                          if model.morphs else '当前可见网格没有可驱动的面部顶点，未生成表情。')


def _capsule_rotation(direction):
    """Euler XYZ for a capsule whose local +Y follows the segment."""
    vector = np.asarray(direction, dtype=float)
    vector /= max(np.linalg.norm(vector), 1e-10)
    # Rz(z) Rx(x) * (0,1,0) = (-sin(z)cos(x), cos(z)cos(x), sin(x)).
    return (math.asin(float(np.clip(vector[2], -1, 1))), 0., math.atan2(-vector[0], vector[1]))


def _physics(model, lookup):
    active = {}
    for part in model.parts:
        slot = part.source.get('slot')
        if slot not in ('Hair', 'Tail', 'Body', 'Legs'): continue
        for i in np.unique(part.bones[part.weights > .05]):
            source = model.bones[i].english.rsplit('/', 1)[-1]
            kind = ('hair' if slot == 'Hair' and re.fullmatch(r'j_(?:kami_.+|ex_h\d+_.+)', source) else
                    'tail' if slot == 'Tail' and re.fullmatch(r'n_sippo_[a-z]', source) else
                    'skirt' if slot in ('Body', 'Legs') and re.fullmatch(r'j_sk_[bfs]_[a-z]_[lr]', source) else None)
            if kind is None: continue
            weight = np.sum(np.where(part.bones == i, part.weights, 0), axis=1)
            points = part.positions[weight > .1]
            if len(points) >= 3:
                old = active.get(int(i))
                active[int(i)] = (kind, np.concatenate((old[1], points)) if old else points)
    if not active:
        model.warnings.append('当前可见网格没有可驱动的头发、尾巴或裙摆骨骼，未生成物理。')
        return
    children = {}
    for i, bone in enumerate(model.bones): children.setdefault(bone.parent, []).append(i)
    scale = max((model.bones[lookup['j_kao']].position[1]-min(model.bones[lookup['j_asi_d_'+side]].position[1]
                for side in ('l', 'r')))/1.4, model.scale*.2)
    def body(name, bone, a, b, radius, group=0, dynamic=False, collide=True):
        length = np.linalg.norm(b-a)
        result = len(model.rigids)
        model.rigids.append({'name': name, 'bone': bone, 'group': group,
            'mask': 0xfffe if dynamic and collide else 0xffff if not collide else 0xfff1,
            'shape': 2 if length > 1e-5 else 0, 'size': (radius, max(length-2*radius, .001*scale), radius),
            'position': (a+b)/2, 'rotation': _capsule_rotation(b-a) if length > 1e-5 else (0., 0., 0.),
            'mass': .06 if dynamic else 0., 'linear_damping': .8, 'angular_damping': .85,
            'mode': 2 if dynamic else 0})
        return result
    # Small interior colliders reduce false initial penetrations on tight gear.
    for start, end, radius in (('j_kosi', 'j_sebo_b', .065), ('j_sebo_b', 'j_kubi', .075), ('j_kao', 'j_kao', .065)):
        i, j = lookup[start], lookup[end]
        body('体/'+model.bones[i].name, i, model.bones[i].position, model.bones[j].position, radius*scale)
    for side in ('l', 'r'):
        for first, last, radius in (('a', 'b', .047), ('b', 'd', .032)):
            i, j = lookup[f'j_asi_{first}_{side}'], lookup[f'j_asi_{last}_{side}']
            body('体/'+model.bones[i].name, i, model.bones[i].position, model.bones[j].position, radius*scale)
    rigid_for, anchors = {}, {}
    for i, (kind, points) in sorted(active.items()):
        bone = model.bones[i]; a = bone.position
        descendants = children.get(i, [])
        prefix = {'hair': 'j_kami_', 'tail': 'n_sippo_', 'skirt': 'j_sk_'}[kind]
        descendants = [j for j in descendants if model.bones[j].english.rsplit('/', 1)[-1].startswith(prefix)]
        if descendants:
            b = model.bones[descendants[0]].position
        else:
            b = np.mean(points, axis=0)
            if np.linalg.norm(b-a) < .015*scale: b = a+np.array((0., -.025*scale, 0.))
        direction = b-a; length = max(np.linalg.norm(direction), 1e-8)
        projection = np.clip((points-a) @ direction/(length*length), 0., 1.)
        distances = np.linalg.norm(points-(a+projection[:, None]*direction), axis=1)
        radius = float(np.clip(np.percentile(distances, 40)*.5, .006*scale, .025*scale))
        parent = bone.parent
        if parent in rigid_for:
            parent_body = rigid_for[parent]
        else:
            if parent not in anchors:
                position = model.bones[parent].position
                anchors[parent] = body('固定/'+model.bones[parent].name, parent, position, position, .005*scale, collide=False)
            parent_body = anchors[parent]
        rigid_for[i] = body(kind+'/'+bone.name, i, a, b, radius, {'hair': 1, 'tail': 2, 'skirt': 3}[kind],
                           dynamic=True, collide=parent in active)
        angle = .55 if kind == 'tail' else .4
        model.joints.append({'name': kind+'/'+bone.name, 'a': parent_body, 'b': rigid_for[i],
            'position': a.copy(), 'rotation': _capsule_rotation(direction),
            'linear_min': (0., 0., 0.), 'linear_max': (0., 0., 0.),
            'angular_min': (-angle, -.2, -angle), 'angular_max': (angle, .2, angle),
            'linear_spring': (0., 0., 0.), 'angular_spring': (8., 4., 8.)})
    model.warnings.append('物理使用保守的头发、尾巴、裙摆刚体与身体碰撞体；未生成布料网格或胸部物理，剧烈动作和特殊装备仍需调参。')


def prepare_standard(model, *, ik=True, expressions=True, physics=True, check_cancel=lambda: None):
    lookup = {bone.name: i for i, bone in enumerate(model.bones)}
    # Ground only the actual feet; long hair/tails must not raise the character.
    foot_parts = [part for part in model.parts if part.source.get('slot') == 'Feet'] or model.parts
    floor = min(float(part.positions[:, 1].min()) for part in foot_parts)
    offset = np.array((0., floor, 0.))
    for part in model.parts: part.positions -= offset
    for bone in model.bones[1:]: bone.position -= offset
    _standard_bones(model, lookup, ik)
    _reorder(model)
    lookup = {bone.english: i for i, bone in enumerate(model.bones) if bone.english}
    check_cancel()
    if expressions: _expressions(model, lookup)
    check_cancel()
    if physics: _physics(model, lookup)
    controls = [(0, i) for i, bone in enumerate(model.bones) if i and bone.visible]
    model.displays = [('Root', 'Root', True, [(0, 0)]),
        ('表情', 'Facial', True, [(1, i) for i in range(len(model.morphs))]),
        ('操作', 'Controls', False, controls),
        ('補助', 'Native helpers', False, [(0, i) for i, bone in enumerate(model.bones) if i and not bone.visible])]
