"""Regressions for v0.0.9 GPU animation and independent weapon palettes."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

import numpy as np

from pmx_export import AXES, build_model, export_source
from scripts import run_dev
from tests.test_pmx_export import fixture
from tests.test_pmx_rig import humanoid


def weapon_mesh(template, anchor, slot='MainHand', part=0):
    vertices=template['vertices'].copy()
    vertices['joints']=0
    matrices=np.tile(np.eye(4),(2,1,1));matrices[:,:3,3]=(1,2,3)
    return {**template,'slot':slot,'item_id':100,'weapon_part':part,
            'weapon_attachment_bone':anchor,'vertices':vertices,'palette':np.arange(2),
            'joint_matrices':matrices,'posed_positions':vertices['position']+(1,2,3)}


class AnimationExportTests(unittest.TestCase):
    def test_native_export_samples_gpu_pose_instead_of_stale_cpu_positions(self):
        scene,outfit,mesh=fixture();base=copy.deepcopy(scene)
        scene.emote_base_scene=base
        scene.skinning[:,0,3]=4
        before=mesh['posed_positions'].copy()
        model=build_model(scene,outfit,scale=1)
        np.testing.assert_allclose(model.parts[0].positions,(before[:3]+(4,0,0))*AXES)
        np.testing.assert_array_equal(mesh['posed_positions'],before)
        snapshot=export_source(scene)
        self.assertIs(export_source(snapshot),snapshot)

    def test_standard_export_uses_neutral_face_and_body_during_animation(self):
        base,outfit=humanoid();animated=copy.deepcopy(base)
        animated.emote_base_scene=base
        animated.skinning[:,0,3]=5
        animated.skinning[outfit.names['j_f_ago'],1,3]=-.2
        expected=build_model(base,outfit,rig='standard')
        actual=build_model(export_source(animated,'standard'),outfit,rig='standard')
        for a,b in zip(actual.parts,expected.parts):np.testing.assert_allclose(a.positions,b.positions)
        for a,b in zip(actual.bones,expected.bones):np.testing.assert_allclose(a.position,b.position)
        self.assertEqual(len(actual.morphs),13)
        self.assertTrue(any('动作播放前'in text for text in actual.warnings))

    def test_weapon_palettes_are_isolated_and_follow_the_correct_body_anchor(self):
        scene,outfit,mesh=fixture()
        left=weapon_mesh(mesh,'bone1');right=weapon_mesh(mesh,'bone0','OffHand')
        scene.meshes.extend((left,right));model=build_model(scene,outfit,scale=1)
        props=[(i,b)for i,b in enumerate(model.bones)if b.name.startswith('weapon_')]
        self.assertEqual(len(props),2)
        self.assertEqual([model.bones[b.parent].name for i,b in props],['bone1','bone0'])
        for part,source,(index,bone)in zip(model.parts[1:],(left,right),props):
            np.testing.assert_allclose(part.positions,source['posed_positions'][:3]*AXES)
            self.assertTrue((part.bones[:,0]==index).all())
            np.testing.assert_allclose(part.weights[:,0],1)

    def test_standard_weapon_follows_a_pose_without_binding_to_fingers_or_root(self):
        scene,outfit=humanoid();anchor='j_te_l'
        scene.skinning[outfit.names[anchor],0,3]=1
        weapon=weapon_mesh(scene.meshes[0],anchor);scene.meshes.append(weapon)
        current=(scene.skinning@outfit.bind)[outfit.names[anchor],:3,3]
        model=build_model(scene,outfit,rig='standard',scale=1)
        index=next(i for i,b in enumerate(model.bones)if b.name.startswith('weapon_'))
        bone=model.bones[index];part=model.parts[-1]
        self.assertEqual(model.bones[bone.parent].english,anchor)
        np.testing.assert_allclose(part.positions-bone.position,(weapon['posed_positions']-current)*AXES,atol=1e-12)
        self.assertTrue((part.bones[:,0]==index).all())
        controls=next(row[3]for row in model.displays if row[1]=='Controls')
        self.assertIn((0,index),controls)


class RuntimeBaselineTests(unittest.TestCase):
    def test_old_runtime_is_not_reused_or_overwritten(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);local=root/'local_runtime';old=local/'dev/artifacts'
            old.mkdir(parents=True);marker=old/'source-runtime.downloaded.json';marker.write_text('old baseline')
            (root/'assets').mkdir()
            archive=local/(run_dev.TAG+'.zip');prefix='release/程序/'
            with zipfile.ZipFile(archive,'w')as package:
                package.writestr(prefix+'artifacts/source-runtime.json',json.dumps({'version':1,'format':'source-py-v1','modules':{}}))
                package.writestr(prefix+'docs/client-version.json',json.dumps({'version':run_dev.TAG}))
                package.writestr(prefix+'demo/launch.py','# new runtime')
            with mock.patch.object(run_dev,'ROOT',root),mock.patch.object(run_dev,'download_archive'):
                runtime=run_dev.prepare()
                self.assertEqual(runtime,local/('dev-'+run_dev.TAG))
                self.assertEqual(marker.read_text(),'old baseline')
                (runtime/'docs/client-version.json').write_text(json.dumps({'version':'v0.0.7-beta.3'}))
                with self.assertRaisesRegex(ValueError,'does not match'):run_dev.prepare()


if __name__=='__main__':unittest.main()
