"""Real-resource expression regression for both Au Ra female clans and faces."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, default=ROOT/'local_runtime/dev-v0.0.9')
    parser.add_argument('--output', type=Path, default=ROOT/'test_output/expressions')
    parser.add_argument('--all-faces', action='store_true')
    parser.add_argument('--all-races', action='store_true')
    args = parser.parse_args()
    runtime, output, game = args.runtime.resolve(), args.output.resolve(), args.game.resolve()
    output.mkdir(parents=True, exist_ok=True); os.chdir(runtime)
    sys.path[:0] = [str(runtime/'demo'), str(runtime/'tools')]
    import numpy as np
    from app import DemoEngine
    from pmx_export import build_model, export_source
    from pmx_morphs import region
    engine = DemoEngine(game, runtime/'defaults/test.dat', preset_directory=output/'presets')
    try:
        engine.ready.result()
        if engine.status != 'ready': raise RuntimeError(engine.error)

        def check(name, textured=False):
            state = deepcopy(engine.last['state']); state['effects_visible'] = False
            state['equipment'] = {slot: {'item_id': None, 'first': 0, 'second': 0} for slot in state['equipment']}
            state['weapons'] = {}; state['facewear'] = [0, 0]; state['pose'] = 'default'
            state['emote'] = None; state['expression'] = None
            engine.render(state)
            scene = engine.last_scene; before = scene.skinning.copy()
            model = build_model(scene, engine.character.outfit, rig='standard', scale=1)
            assert model.expression_source == 'native_facial', model.expression_source
            assert len(model.morphs) == 13, model.warnings
            count = sum(len(p.positions) for p in model.parts)
            def dense(name):
                morph = next(m for m in model.morphs if m['name'] == name)
                values = np.zeros((count, 3)); values[morph['indices']] = morph['offsets']; return values
            np.testing.assert_allclose(dense('まばたき'), dense('ウィンク')+dense('ウィンク右'), atol=1e-10)
            with mock.patch('pmx_morphs.authored_targets', return_value=None):
                legacy = build_model(scene, engine.character.outfit, rig='standard', scale=1)
            for a, b in zip(model.parts, legacy.parts):
                np.testing.assert_array_equal(a.positions, b.positions)
                np.testing.assert_array_equal(a.weights, b.weights)
            np.testing.assert_array_equal(scene.skinning, before)
            base = 0; outside = []
            for part in model.parts:
                if part.source['slot'] != 'Face': outside.extend(range(base, base+len(part.positions)))
                base += len(part.positions)
            measurements = []
            for m in model.morphs:
                values = dense(m['name'])
                assert np.isfinite(values).all()
                assert np.max(np.linalg.norm(values[outside], axis=1), initial=0) < 1e-8
                old = next(old for old in legacy.morphs if old['name'] == m['name'])
                measurements.append({'name': m['name'], 'vertices': len(m['indices']),
                    'max_mm': float(np.linalg.norm(m['offsets'], axis=1).max()*1000),
                    'previous_max_mm': float(np.linalg.norm(old['offsets'], axis=1).max()*1000)})
            # Cross-check against the existing runtime facial player, independent
            # of the new PMX local-delta and source-weight baking path.
            player = engine.emote_player()
            smile_id = next(r['id'] for r in player.rows if r['key'] == 'facial/pose/smile')
            animated, _ = player.apply(scene, None, smile_id)
            played = build_model(export_source(animated, 'native'), engine.character.outfit,
                                 rig='standard', scale=1, expressions=False, physics=False)
            original_names = {index: name for name, index in engine.character.outfit.names.items()}
            mouth_masks = []
            for source_mesh in scene.meshes:
                if not len(source_mesh['indices']): continue
                vertices = source_mesh['vertices'][np.unique(source_mesh['indices'])]
                palette_mouth = np.array([region(original_names.get(int(i), '')) == 'mouth' for i in source_mesh['palette']])
                mouth_masks.append(np.all((vertices['weights'] == 0) | palette_mouth[vertices['joints']], axis=1))
            pure_mouth = np.concatenate(mouth_masks)
            assert pure_mouth.sum() > 20
            baked = np.concatenate([p.positions for p in model.parts])+dense('にやり')
            previewed = np.concatenate([p.positions for p in played.parts])
            source_error = float(np.linalg.norm(baked[pure_mouth]-previewed[pure_mouth], axis=1).max())
            assert source_error < 2e-6, source_error
            options = {'rig': 'standard', 'textures': textured, 'resolution': 1024, 'overwrite': True}
            new = engine.export_pmx(output/(name+'_after.pmx'), options)
            if textured:
                with mock.patch('pmx_morphs.authored_targets', return_value=None):
                    engine.export_pmx(output/(name+'_before.pmx'), options)
            result = {'case': name, 'parameters': engine.rendered_parameters, 'morphs': measurements,
                'neutral_and_weights_unchanged': True, 'non_face_vertices_unchanged': True,
                'smile_source_error_mm': source_error*1000, 'smile_source_vertices': int(pure_mouth.sum()),
                'separate_winks_match_blink': True, 'source': new['morph_sources'], 'path': new['path']}
            print(json.dumps({'case': name, 'morphs': len(measurements), 'source': new['expression_source']}, ensure_ascii=False), flush=True)
            return result

        def run():
            records = [check('au_ra_default', textured=True)]
            if args.all_faces:
                for tribe in (11, 12):
                    result = engine.create_preset({'profile': 'au-ra-female', 'tribe': tribe, 'name': f'expression-clan-{tribe}.dat'})
                    assert result.get('model_loaded'), result
                    record = engine.describe_appearance(engine.active_preset)
                    for face in range(1, 5):
                        engine.preview_appearance({'id': record['id'], 'sha256': record['sha256'], 'changes': {'face': face}})
                        records.append(check(f'au_ra_clan{tribe}_face{face}', textured=face==4))
            if args.all_races:
                from builtin_presets import CREATION_PROFILES
                for profile in CREATION_PROFILES:
                    result = engine.create_preset({'profile': profile['id'], 'tribe': profile['tribes'][0], 'name': 'expression-'+profile['id']+'.dat'})
                    assert result.get('model_loaded'), result
                    records.append(check(profile['id']))
            (output/'validation.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf8')
        engine.pool.submit(run).result()
    finally:
        engine.pool.submit(engine.close_gpu).result(); engine.pool.shutdown(wait=True)


if __name__ == '__main__': main()
