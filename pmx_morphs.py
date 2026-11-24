"""Bake selected-face authored controls before reducing PMX skin weights.

These are vertex targets, not animation tracks. Eye, mouth and brow controls
are separated so a smile does not also add a second blink or eyebrow pose.
"""
from types import SimpleNamespace

import numpy as np


def region(name):
    if name.startswith('j_f_'):
        if any(word in name for word in ('mayu', 'miken')): return 'brows'
        if any(word in name for word in ('mab', 'eye', 'iris')): return 'eyes'
        if any(word in name for word in ('ago', 'lip', 'hoho', 'haguki', 'bero', 'hana')): return 'mouth'
    return 'mouth' if name == 'j_ago' else None


def local_deltas(neutral, expression, names, selected_region, side=None):
    """Use source-relative local deltas, retaining the customized neutral face."""
    result = {}
    for index, posed in expression.items():
        if not 0 <= index < len(names) or index not in neutral:
            raise ValueError('PMX：表情骨骼映射不完整')
        name = names[index]
        if region(name) != selected_region or side and not name.endswith('_'+side): continue
        delta = np.asarray(posed) @ np.linalg.inv(neutral[index])
        if delta.shape != (4, 4) or not np.isfinite(delta).all():
            raise ValueError('PMX：表情局部变换无效')
        if not np.allclose(delta, np.eye(4), atol=1e-10, rtol=0): result[index] = delta
    return result


def world_deltas(reference, parents, corrections):
    """Compose on native local axes; rotations are not around global PMX X."""
    reference = np.asarray(reference)
    result = np.empty_like(reference); inverse = np.linalg.inv(reference)
    done, visiting = set(), set()
    def visit(index):
        if index in done: return
        if index in visiting: raise ValueError('PMX：表情骨架父级存在循环')
        visiting.add(index); parent = int(parents[index])
        if parent >= 0:
            visit(parent)
            local = inverse[parent] @ reference[index]
        else: local = reference[index]
        if index in corrections: local = corrections[index] @ local
        result[index] = result[parent] @ local if parent >= 0 else local
        visiting.remove(index); done.add(index)
    for index in range(len(reference)): visit(index)
    delta = result @ inverse
    if not np.isfinite(delta).all(): raise ValueError('PMX：表情世界变换无效')
    return delta


def _pose_plan(character, key):
    from emote_preview import timeline
    from emote_timeline import TimelineGraph
    from facial_animation import static_plan
    raw = character.base.pack.read('chara/action/facial/pose/'+key+'.tmb')
    name, _ = timeline(raw, facial=True)
    return static_plan(character, name, library=TimelineGraph(raw).face_library)


def authored_targets(scene, outfit, reference, check_cancel=lambda: None):
    """Return None for source-less callers, otherwise source-checked targets.

    Unsupported authored sources are reported and skipped, rather than silently
    replacing them with fixed-angle approximations. The 0.0.9 source compiler
    checks the selected face, partial mapping and customization conflicts.
    """
    pose = getattr(scene, 'pose', {})
    if not hasattr(scene, 'pack') or not pose.get('skeletons'): return None
    from facial_animation import curve_plan, static_plan, combine_layers
    names = sorted(outfit.names, key=outfit.names.get)
    parents = [outfit.names.get(outfit.parents[name], -1) for name in names]
    character = SimpleNamespace(base=scene)
    targets, notices = [], []

    def add(name, english, panel, plan, selected_region, seconds=None, side=None):
        check_cancel()
        layer = (plan, 1., seconds) if seconds is not None else (plan, 1.)
        zero = (plan, 0., seconds) if seconds is not None else (plan, 0.)
        correction = local_deltas(combine_layers((zero,)), combine_layers((layer,)), names, selected_region, side)
        if not correction: raise ValueError('所选部位没有有效的原始表情控制')
        metadata = {key: plan.metadata[key] for key in ('path', 'sha256', 'clip', 'face_path', 'channel')}
        metadata.update(region=selected_region, sample_seconds=seconds, side=side)
        targets.append({'name': name, 'english': english, 'panel': panel,
                        'delta': world_deltas(reference, parents, correction), 'source': metadata})

    def attempt(label, action):
        check_cancel()
        try: action()
        except InterruptedError:
            raise
        except (OSError, ValueError, NotImplementedError) as error:
            notices.append('原始'+label+'读取不完整，已跳过对应表情：'+str(error))

    def eyes():
        plan = curve_plan(character, 'cfxb_blink1', channel=18)
        # Find the closed sample from this face's own eyelid rotation curve.
        # targets excludes inactive body anchors; use explicit source indices.
        sources = {source for source, target in plan.targets if names[target].startswith('j_f_mabup_01_')}
        tracks = [i for i, source in enumerate(plan.indices) if int(source) in sources]
        if not tracks: raise ValueError('缺少原始上眼睑轨道')
        times = np.linspace(0, plan.duration, plan.metadata['frames'])
        seconds = float(max(times, key=lambda t: float(np.linalg.norm(plan.at(float(t))[tracks, 1, :3]))))
        add('まばたき', 'Blink', 2, plan, 'eyes', seconds)
        add('ウィンク', 'Wink L', 2, plan, 'eyes', seconds, 'l')
        add('ウィンク右', 'Wink R', 2, plan, 'eyes', seconds, 'r')
    attempt('眨眼', eyes)
    attempt('笑眼', lambda: add('笑い', 'Happy eyes', 2, _pose_plan(character, 'happysmile'), 'eyes'))

    def mouth():
        plan = curve_plan(character, 'cfxl_lip_nor1', channel=19)
        # Reviewed phases of the resident speaking cycle: open, wide, pursed,
        # moderately open and rounded. Sample the face-specific authored curve;
        # these are approximate MMD vowels, not a phoneme decoder.
        for name, english, phase in (('あ', 'A', .075), ('い', 'I', .15), ('う', 'U', .30),
                                     ('え', 'E', .45), ('お', 'O', .375)):
            add(name, english, 3, plan, 'mouth', plan.duration*phase)
    attempt('口型', mouth)
    attempt('微笑', lambda: add('にやり', 'Smile', 3, _pose_plan(character, 'smile'), 'mouth'))
    attempt('困眉', lambda: add('困る', 'Worried', 1, _pose_plan(character, 'worry'), 'brows'))
    attempt('怒眉', lambda: add('怒り', 'Angry', 1, static_plan(character, 'cfxf_angry'), 'brows'))
    attempt('抬眉', lambda: add('上', 'Brows up', 1, _pose_plan(character, 'what'), 'brows'))
    return targets, notices


def bake_offsets(points, joints, weights, reference_matrices, deltas):
    """Exact LBS target minus neutral, with every original source influence.

    Transform the reconstructed source point by each joint before blending.
    Applying blended deltas to an already skinned point introduces cross terms
    and pulls shared lip/eyelid vertices away from their authored surface.
    """
    offsets = np.zeros((len(points), 3))
    matrices = (deltas-np.eye(4)) @ reference_matrices
    for column in range(weights.shape[1]):
        active = weights[:, column] > 0
        if not np.any(active): continue
        value = np.einsum('nij,nj->ni', matrices[joints[active, column]], points[active])
        offsets[active] += value[:, :3] * weights[active, column, None]
    if not np.isfinite(offsets).all(): raise ValueError('PMX：原始表情顶点含无效数值')
    return offsets


def palette_deltas(skeleton, palette, outfit, delta):
    """An extra attachment inherits only its nearest character ancestor."""
    result = []
    for index in palette:
        current = int(index)
        while current > 0:
            name = skeleton.bones[current].name
            if name in outfit.names: break
            current = skeleton.bones[current].parent
        result.append(delta[outfit.names[name]] if current > 0 else np.eye(4))
    return np.asarray(result)
