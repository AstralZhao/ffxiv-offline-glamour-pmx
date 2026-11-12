"""Automatic rig regressions using a small independently posed humanoid."""
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np

from pmx_export import Bone, bone_display_connections, build_model, export_scene
from pmx_rig import FINGERS


def humanoid():
    names, parents, locations = {}, {}, []
    def bone(name, parent, position):
        names[name] = len(names); parents[name] = parent; locations.append(position)
    bone('n_root', None, (0, 0, 0)); bone('n_hara', 'n_root', (0, 1, 0))
    bone('j_kosi', 'n_hara', (0, 1, 0)); bone('j_sebo_a', 'n_hara', (0, 1, 0))
    for name, parent, height in (('j_sebo_b','j_sebo_a',1.12),('j_sebo_c','j_sebo_b',1.24),('j_kubi','j_sebo_c',1.35),('j_kao','j_kubi',1.43)):
        bone(name,parent,(0,height,0))
    for side, sign in (('l', 1), ('r', -1)):
        for name, parent, x, y, z in (
                ('j_sako','j_sebo_c',.025,1.27,0),('j_ude_a','j_sako',.12,1.29,0),
                ('j_ude_b','j_ude_a',.29,1.12,0),('j_te','j_ude_b',.44,.97,0),
                ('j_asi_a','j_kosi',.08,.9,0),('j_asi_b','j_asi_a',.08,.53,.02),
                ('j_asi_c','j_asi_b',.08,.47,.015),('j_asi_d','j_asi_c',.08,.08,-.02),('j_asi_e','j_asi_d',.08,.015,.075)):
            bone(name+'_'+side, parent if parent in names else parent+'_'+side, (sign*x,y,z))
        for i, name in enumerate(FINGERS):
            bone(f'{name}_a_{side}', 'j_te_'+side, (sign*.49,.92,i*.012))
            bone(f'{name}_b_{side}', f'{name}_a_{side}', (sign*.52,.89,i*.012))
        bone('j_f_eye_'+side, 'j_kao', (sign*.026,1.47,.074))
        bone('j_f_eyepuru_'+side, 'j_f_eye_'+side, (sign*.026,1.47,.08))
        for region in ('mabup_01','mabdn_01','mayu','mmayu','miken_01','uslip','dslip','umlip_01','dmlip_01'):
            bone(f'j_f_{region}_{side}', 'j_kao', (sign*.026,1.47,.074))
    bone('j_f_ago','j_kao',(0,1.42,.04)); bone('j_f_dlip_01_l','j_f_ago',(.003,1.4,.08))
    bone('j_kami_a','j_kao',(0,1.5,-.05));bone('j_kami_b','j_kami_a',(0,1.4,-.08))
    bone('n_sippo_a','j_kosi',(0,.95,-.06));bone('n_sippo_b','n_sippo_a',(0,.9,-.2))
    bone('j_sk_f_a_l','j_kosi',(.1,.85,.1));bone('j_sk_f_b_l','j_sk_f_a_l',(.1,.7,.1))
    bind = np.tile(np.eye(4), (len(names),1,1));bind[:,:3,3] = locations
    outfit = SimpleNamespace(names=names,parents=parents,bind=bind)
    dtype = [('position','f8',3),('normal','f8',3),('uv','f8',4),('joints','i4',4),('weights','f8',4)]
    meshes = []
    for name, index in names.items():
        vertices = np.zeros(3,dtype=dtype)
        offset = np.array(((.01,-.01,.02),(.03,-.03,.02),(.055,-.055,.02)))
        if name.endswith('_r'): offset[:,0] *= -1
        vertices['position'] = np.array(locations[index])+offset
        vertices['normal'] = (0,0,1);vertices['joints'][:,0]=index;vertices['weights'][:,0]=1
        slot = ('Hair' if 'kami' in name else 'Tail' if 'sippo' in name else 'Feet' if 'j_asi_e' in name else 'Body')
        meshes.append({'slot':slot,'vertices':vertices,'indices':np.array([0,1,2]),'posed_positions':vertices['position'].copy(),
                       'palette':np.arange(len(names)),'material':{}})
    scene = SimpleNamespace(meshes=meshes,skinning=np.tile(np.eye(4),(len(names),1,1)))
    return scene,outfit


class RigTests(unittest.TestCase):
    def test_spine_tails_ignore_breasts_and_equipment_sockets(self):
        # Deliberately put the distracting branch before the anatomical child,
        # as in real FFXIV skeleton palettes.
        rows=[('n_hara',-1,(0,1,0)),('j_kosi',0,(0,1,0)),
              ('j_asi_a_l',1,(.08,.9,0)),('j_asi_a_r',1,(-.08,.9,0)),
              ('j_sebo_a',0,(0,1,0)),('j_sebo_b',4,(0,1.12,.01)),
              ('j_mune_l',5,(.04,1.18,-.05)),('j_sebo_c',5,(0,1.24,.01)),
              ('j_buki_sebo_l',7,(.08,1.3,.07)),('j_kubi',7,(0,1.35,0)),
              ('j_kao',9,(0,1.44,-.03)),('j_ago',10,(0,1.43,-.04))]
        bones=[Bone(name,np.array(pos),parent)for name,parent,pos in rows]
        tails=bone_display_connections(bones,1)
        self.assertEqual(tails[5],7)  # Chest to upper chest, not left breast.
        self.assertEqual(tails[7],9)  # Upper chest to neck, not back socket.
        self.assertAlmostEqual(tails[1][0],0.)
        self.assertLess(tails[1][1],0.)
        self.assertGreater(tails[10][1],0.)
        for bone,tail in zip(bones,tails):
            offset=bones[tail].position-bone.position if isinstance(tail,int)else np.asarray(tail)
            self.assertGreater(np.linalg.norm(offset),1e-6)

    def test_terminal_bones_follow_their_chain_and_controls_keep_their_direction(self):
        bones=[Bone('全ての親',np.zeros(3),-1),
               Bone('親指１',np.array((.4,.9,0)),0,english='j_oya_a_l'),
               Bone('親指２',np.array((.42,.85,-.02)),1,english='j_oya_b_l'),
               Bone('足首',np.array((.08,.08,.02)),0,english='j_asi_d_l'),
               Bone('つま先',np.array((.08,.015,-.07)),3,english='j_asi_e_l'),
               Bone('左足ＩＫ',np.array((.08,.08,.02)),0,tail=(0,0,-.08)),
               Bone('グルーブ',np.array((0,1,0)),0,tail=7),
               Bone('腰',np.array((0,1,0)),6,english='n_hara'),
               Bone('j_f_eye_l',np.array((.025,1.46,-.075)),0),
               Bone('j_f_eyepuru_l',np.array((.025,1.46,-.075)),8)]
        tails=bone_display_connections(bones,1)
        direction=bones[2].position-bones[1].position
        np.testing.assert_allclose(np.cross(tails[2],direction),0,atol=1e-12)
        self.assertGreater(np.dot(tails[2],direction),0)
        self.assertEqual(tails[4],(0.,0.,-.04))
        self.assertEqual(tails[5],(0,0,-.08))
        self.assertEqual(tails[6],(0.,.04,0.))
        self.assertEqual(tails[8],(0.,0.,-.04))
        self.assertEqual(tails[0],(0.,.08,0.))

    def test_rest_conversion_uses_all_eight_source_weights_before_pmx_reduction(self):
        scene,outfit=humanoid();mesh=scene.meshes[0]
        vertices=np.zeros(3,dtype=[('position','f8',3),('normal','f8',3),('uv','f8',4),('joints','i4',8),('weights','f8',8)])
        vertices['position']=((0,1,0),(.1,1,0),(0,1.1,0));vertices['normal']=(0,0,1)
        vertices['joints']=np.arange(8);vertices['weights']=np.arange(1,9)/36
        mesh['vertices']=vertices;mesh['posed_positions']=vertices['position'].copy();scene.meshes=[mesh]
        expected=build_model(scene,outfit,rig='standard',expressions=False,physics=False)
        for i in range(8):
            c,s=np.cos(i*.06),np.sin(i*.06)
            scene.skinning[i,:3,:3]=((c,-s,0),(s,c,0),(0,0,1))
            scene.skinning[i,:3,3]=(i*.01,0,0)
        blended=np.einsum('i,ijk->jk',vertices['weights'][0],scene.skinning[:8])
        mesh['posed_positions']=vertices['position']@blended[:3,:3].T+blended[:3,3]
        result=build_model(scene,outfit,rig='standard',expressions=False,physics=False)
        np.testing.assert_allclose(result.parts[0].positions,expected.parts[0].positions,atol=1e-10)
        np.testing.assert_allclose(result.parts[0].normals,expected.parts[0].normals,atol=1e-10)
        self.assertTrue(any('保留最强的四个权重' in w for w in result.warnings))

    def test_reference_pose_is_independent_of_preview_and_preserves_source(self):
        scene,outfit=humanoid()
        neutral=build_model(scene,outfit,rig='standard')
        c,s=np.cos(.3),np.sin(.3)
        transform=np.eye(4);transform[:3,:3]=((c,-s,0),(s,c,0),(0,0,1));transform[:3,3]=(.2,.3,.4)
        scene.skinning[:]=transform
        for mesh in scene.meshes:
            mesh['posed_positions']=mesh['posed_positions']@transform[:3,:3].T+transform[:3,3]
        before=scene.skinning.copy();positions=[m['posed_positions'].copy() for m in scene.meshes]
        posed=build_model(scene,outfit,rig='standard')
        for a,b in zip(neutral.parts,posed.parts):
            np.testing.assert_allclose(a.positions,b.positions,atol=1e-10)
            np.testing.assert_allclose(a.normals,b.normals,atol=1e-10)
        np.testing.assert_array_equal(scene.skinning,before)
        for mesh,position in zip(scene.meshes,positions):np.testing.assert_array_equal(mesh['posed_positions'],position)

    def test_standard_hierarchy_ik_and_eye_append_order(self):
        scene,outfit=humanoid();model=build_model(scene,outfit,rig='standard')
        names={bone.name:i for i,bone in enumerate(model.bones)}
        for name in ('全ての親','センター','グルーブ','上半身','下半身','首','頭','両目','左人指３','右小指３'):
            self.assertIn(name,names)
        for i,bone in enumerate(model.bones):
            self.assertLess(bone.parent,i)
            if bone.parent>=0:self.assertGreaterEqual(bone.layer,model.bones[bone.parent].layer)
            if bone.append_rotation:self.assertLess(bone.append_rotation[0],i)
        for side in ('左','右'):
            leg=model.bones[names[side+'足ＩＫ']]
            self.assertEqual(leg.parent,0)
            self.assertEqual(leg.ik['target'],names[side+'足首'])
            self.assertEqual(model.bones[names[side+'足首']].parent,names[side+'ひざ'])
            self.assertEqual([i for i,_ in leg.ik['links']],[names[side+'ひざ'],names[side+'足']])
            low,high=leg.ik['links'][0][1]
            self.assertLess(low[0],high[0]);self.assertLess(high[0],0)
            self.assertEqual(model.bones[names[side+'目']].append_rotation,(names['両目'],1.))

    def test_third_finger_has_real_normalized_weights(self):
        scene,outfit=humanoid();model=build_model(scene,outfit,rig='standard')
        tip=next(i for i,b in enumerate(model.bones)if b.name=='左人指３')
        influence=sum(float(p.weights[p.bones==tip].sum())for p in model.parts)
        self.assertGreater(influence,.5)
        for part in model.parts:
            np.testing.assert_allclose(part.weights.sum(axis=1),1.)
            self.assertTrue(np.all(part.bones>=0));self.assertTrue(np.all(part.bones<len(model.bones)))

    def test_winks_are_separate_and_combined_blink_is_their_sum(self):
        scene,outfit=humanoid();model=build_model(scene,outfit,rig='standard')
        count=sum(len(p.positions)for p in model.parts)
        def dense(name):
            m=next(m for m in model.morphs if m['name']==name);result=np.zeros((count,3));result[m['indices']]=m['offsets'];return result
        left,right=dense('ウィンク'),dense('ウィンク右')
        self.assertTrue(np.linalg.norm(left)>0);self.assertTrue(np.linalg.norm(right)>0)
        self.assertFalse(np.any((np.linalg.norm(left,axis=1)>0)&(np.linalg.norm(right,axis=1)>0)))
        np.testing.assert_allclose(dense('まばたき'),left+right,atol=1e-10)
        self.assertGreater(np.linalg.norm(dense('あ')),np.linalg.norm(dense('い')))

    def test_physics_references_weighted_parts_and_has_finite_limits(self):
        scene,outfit=humanoid();model=build_model(scene,outfit,rig='standard')
        dynamics=[r for r in model.rigids if r['mode']!=0]
        self.assertEqual(len(model.joints),len(dynamics));self.assertGreater(len(dynamics),0)
        for body in dynamics:
            self.assertGreater(body['mass'],0);self.assertTrue(np.all(np.array(body['size'])>0))
            self.assertTrue(any(np.any((p.bones==body['bone'])&(p.weights>0))for p in model.parts))
        for joint in model.joints:
            self.assertNotEqual(joint['a'],joint['b'])
            self.assertTrue(0<=joint['a']<len(model.rigids));self.assertTrue(0<=joint['b']<len(model.rigids))
            self.assertTrue(np.isfinite(joint['rotation']).all())
            self.assertTrue(np.all(np.array(joint['angular_min'])<=joint['angular_max']))

    def test_options_remove_features_and_missing_face_is_reported(self):
        scene,outfit=humanoid();model=build_model(scene,outfit,rig='standard',ik=False,expressions=False,physics=False)
        self.assertFalse(any(b.ik for b in model.bones));self.assertFalse(model.morphs);self.assertFalse(model.rigids);self.assertFalse(model.joints)
        from pmx_rig import _expressions
        _expressions(model,{})
        self.assertFalse(model.morphs);self.assertTrue(any('跳过自动表情' in warning for warning in model.warnings))

    @unittest.skipUnless(os.environ.get('PMX_REFERENCE_READER'),'Optional independent MMD Tools reader')
    def test_independent_reader_imports_rig_morphs_and_physics(self):
        spec=importlib.util.spec_from_file_location('independent_pmx',os.environ['PMX_REFERENCE_READER'])
        reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
        scene,outfit=humanoid()
        with tempfile.TemporaryDirectory()as directory:
            report=export_scene(scene,outfit,Path(directory)/'自动骨架.pmx',rig='standard')
            model=reader.load(report['path'])
            self.assertEqual(sum(b.isIK for b in model.bones),4)
            self.assertEqual(len(model.morphs),13)
            self.assertEqual(len(model.rigids),report['rigid_bodies'])
            self.assertEqual(len(model.joints),report['physics_joints'])
            self.assertTrue(all(m.type_index()==1 and len(m.offsets)>0 for m in model.morphs))
            for joint in model.joints:
                self.assertIsNotNone(joint.src_rigid);self.assertIsNotNone(joint.dest_rigid)
            for bone in model.bones:
                if bone.isIK:self.assertTrue(len(bone.ik_links)>0)


if __name__=='__main__':unittest.main()
