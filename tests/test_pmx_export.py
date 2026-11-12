"""Geometry, rigging and transactional output regressions; no game/GPU needed."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np
from PIL import Image

from pmx_export import build_model, export_scene
from pmx_material import bake_uv, geometry_shader_source, pad_uv_islands, glass_rgba


def fixture(bone_count=2, influences=4):
    dtype=np.dtype([('position','<f4',3),('normal','<f4',3),('uv','<f4',4),
                    ('weights','u1',influences),('joints','u1',influences)])
    vertices=np.zeros(4,dtype=dtype)
    vertices['position']=[(0,0,1),(1,0,1),(0,1,1),(99,99,99)]
    vertices['normal']=(0,0,1)
    vertices['uv'][:,:2]=[(1,0),(2,0),(1,1),(99,99)]
    vertices['weights'][:,0]=255
    names={'bone'+str(i):i for i in range(bone_count)}
    parents={name:('bone'+str(i-1)if i else None)for name,i in names.items()}
    bind=np.tile(np.eye(4),(bone_count,1,1))
    bind[:,1,3]=np.arange(bone_count)
    outfit=SimpleNamespace(names=names,parents=parents,bind=bind)
    mesh={'slot':'Body','vertices':vertices,'indices':np.array([0,1,2],dtype=np.uint32),
          'posed_positions':vertices['position'].astype(float),'palette':np.arange(bone_count),
          'material':{'shader_flags':0},'material_path':'synthetic.mtrl','shader_path':'synthetic.shpk'}
    scene=SimpleNamespace(meshes=[mesh],skinning=np.tile(np.eye(4),(bone_count,1,1)))
    return scene,outfit,mesh


class GeometryTests(unittest.TestCase):
    def test_export_uses_visible_indices_and_does_not_mutate_source(self):
        scene,outfit,mesh=fixture()
        before=mesh['vertices'].tobytes()
        model=build_model(scene,outfit,scale=2)
        part=model.parts[0]
        self.assertEqual(len(part.positions),3)
        np.testing.assert_allclose(part.positions,[(0,0,-2),(2,0,-2),(0,2,-2)])
        np.testing.assert_array_equal(part.triangles,[(0,2,1)])
        np.testing.assert_allclose(part.normals,[(0,0,-1)]*3)
        self.assertEqual(mesh['vertices'].tobytes(),before)
        np.testing.assert_array_equal(part.uv,[(1,0),(2,0),(1,1)])
        self.assertEqual(model.bones[0].name,'全ての親')
        self.assertEqual([b.parent for b in model.bones],[-1,0,1])

    def test_normals_use_inverse_transpose_and_unit_length(self):
        scene,outfit,mesh=fixture()
        mesh['vertices']['normal']=(1,1,0)
        scene.skinning[0,0,0]=2
        part=build_model(scene,outfit).parts[0]
        np.testing.assert_allclose(part.normals,np.tile(np.array([1,2,0])/np.sqrt(5),(3,1)))

    def test_authored_zero_normals_use_surface_geometry(self):
        scene,outfit,mesh=fixture()
        mesh['vertices']['normal']=0
        np.testing.assert_allclose(build_model(scene,outfit).parts[0].normals,[(0,0,-1)]*3)

    def test_weights_merge_duplicate_bones_before_reducing_to_four(self):
        scene,outfit,mesh=fixture(7,8)
        mesh['vertices']['joints']=[0,0,1,2,3,4,5,6]
        mesh['vertices']['weights']=[20,30,40,35,30,25,20,55]
        model=build_model(scene,outfit)
        np.testing.assert_array_equal(model.parts[0].bones[0],[7,1,2,3])
        np.testing.assert_allclose(model.parts[0].weights[0],np.array([55,50,40,35])/180)
        self.assertTrue(any('3 个顶点' in w for w in model.warnings))

    def test_unused_invalid_joint_slots_are_ignored(self):
        scene,outfit,mesh=fixture()
        mesh['vertices']['joints'][:,1:]=255
        np.testing.assert_allclose(build_model(scene,outfit).parts[0].weights[:,0],1)

    def test_invalid_geometry_and_weights_fail_before_writing(self):
        for failure in('index','nan','weight','joint','cycle','incomplete'):
            with self.subTest(failure=failure):
                scene,outfit,mesh=fixture()
                if failure=='index':mesh['indices'][2]=100
                if failure=='nan':mesh['posed_positions'][0,0]=np.nan
                if failure=='weight':mesh['vertices']['weights']=0
                if failure=='joint':mesh['vertices']['joints'][:,0]=255
                if failure=='cycle':outfit.parents['bone0']='bone1'
                if failure=='incomplete':scene.omitted=['Body']
                with self.assertRaises(ValueError):build_model(scene,outfit)

    def test_invalid_scale_and_empty_scene(self):
        scene,outfit,mesh=fixture()
        for value in(0,-1,float('nan'),float('inf'),True):
            with self.assertRaises(ValueError):build_model(scene,outfit,scale=value)
        mesh['indices']=np.array([],dtype=np.uint32)
        with self.assertRaises(ValueError):build_model(scene,outfit)

    def test_attachment_owners_do_not_alias_and_follow_their_ancestor(self):
        scene,outfit,mesh=fixture()
        scene.skinning[:,0,3]=2
        names={**outfit.names,'extra':2};parents={**outfit.parents,'extra':'bone1'}
        bind=np.concatenate((outfit.bind,np.eye(4)[None]))
        bind[2,:3,3]=(0,2,0)
        attached={'names':names,'parents':parents,'bind':bind,'skinning':np.tile(np.eye(4),(3,1,1))}
        outfit.attachment=lambda item:attached
        scene.meshes=[]
        for slot,identifier in(('Head',1),('Facewear0',2)):
            vertices=mesh['vertices'].copy();vertices['joints'][:,0]=0
            scene.meshes.append({**mesh,'slot':slot,'vertices':vertices,'palette':np.array([2]),
                'joint_matrices':scene.skinning[:1],
                'attachment_item':{'slot':slot,'model_set':identifier}})
        model=build_model(scene,outfit,scale=1)
        extras=[(i,b)for i,b in enumerate(model.bones)if '/extra' in b.name]
        self.assertEqual(len(extras),2)
        for _,bone in extras:
            np.testing.assert_allclose(bone.position,[2,2,0])
            self.assertEqual(bone.parent,2)
        self.assertNotEqual(model.parts[0].bones[0,0],model.parts[1].bones[0,0])

    def test_visor_positions_override_only_the_head_attachment(self):
        scene,outfit,mesh=fixture()
        bind=np.concatenate((outfit.bind,np.eye(4)[None]));bind[2,1,3]=2
        attached={'names':{**outfit.names,'extra':2},'parents':{**outfit.parents,'extra':'bone1'},
                  'bind':bind,'skinning':np.tile(np.eye(4),(3,1,1))}
        outfit.attachment=lambda _:attached
        scene.visor_bones={'extra':np.eye(4)};scene.visor_bones['extra'][:3,3]=(1,2,3)
        mesh.update(slot='Head',palette=np.array([2]),joint_matrices=np.eye(4)[None],
                    attachment_item={'slot':'Head','model_set':3})
        np.testing.assert_allclose(build_model(scene,outfit,scale=1).bones[-1].position,[1,2,-3])


class FilesystemTests(unittest.TestCase):
    def test_publishes_pmx_and_relative_texture_bundle(self):
        scene,outfit,mesh=fixture()
        with tempfile.TemporaryDirectory() as directory:
            destination=Path(directory)/'中文 模型.pmx'
            image=Image.new('RGBA',(8,8),(255,0,0,128))
            result=export_scene(scene,outfit,destination,material_provider=lambda m:(image,m['vertices']['uv'][:,:2],[]))
            self.assertTrue(destination.read_bytes().startswith(b'PMX \x00\x00\x00@\x08'))
            report=json.loads(Path(result['report']).read_text(encoding='utf8'))
            self.assertEqual((report['vertices'],report['triangles'],report['bones'],report['textures']),(3,1,3,1))
            self.assertTrue((Path(result['assets'])/'material_000.png').is_file())
            self.assertNotIn(str(Path(directory)).encode('utf-16-le'),destination.read_bytes())
            self.assertFalse(list(Path(directory).glob('.pmx-*')))

    def test_existing_output_requires_explicit_overwrite(self):
        scene,outfit,_=fixture()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'model.pmx';path.write_bytes(b'previous')
            with self.assertRaises(FileExistsError):export_scene(scene,outfit,path)
            self.assertEqual(path.read_bytes(),b'previous')

    def test_failed_commit_preserves_old_pmx_and_its_assets(self):
        scene,outfit,_=fixture()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'model.pmx'
            previous=export_scene(scene,outfit,path)
            before={p.relative_to(directory):p.read_bytes()for p in Path(directory).rglob('*')if p.is_file()}
            with mock.patch('pmx_export.os.replace',side_effect=OSError('disk error')):
                with self.assertRaises(OSError):export_scene(scene,outfit,path,overwrite=True)
            after={p.relative_to(directory):p.read_bytes()for p in Path(directory).rglob('*')if p.is_file()}
            self.assertEqual(before,after)
            self.assertTrue(Path(previous['assets']).exists())
            self.assertFalse(list(Path(directory).glob('.pmx-*')))

    def test_cancel_after_staging_leaves_no_output(self):
        scene,outfit,_=fixture();calls=[]
        def cancel():
            calls.append(1)
            if len(calls)==4:raise RuntimeError('cancelled')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):export_scene(scene,outfit,Path(directory)/'model.pmx',check_cancel=cancel)
            self.assertEqual(list(Path(directory).iterdir()),[])

    def test_bake_error_does_not_replace_existing_model(self):
        scene,outfit,_=fixture()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'model.pmx';path.write_bytes(b'previous')
            with self.assertRaisesRegex(RuntimeError,'bake'):
                export_scene(scene,outfit,path,overwrite=True,material_provider=mock.Mock(side_effect=RuntimeError('bake')))
            self.assertEqual(path.read_bytes(),b'previous')
            self.assertEqual(list(Path(directory).iterdir()),[path])

    @unittest.skipUnless(os.environ.get('PMX_REFERENCE_READER'),'Optional independent MMD Tools reader')
    def test_roundtrip_with_independent_mmd_tools(self):
        spec=importlib.util.spec_from_file_location('pmx_reference',os.environ['PMX_REFERENCE_READER'])
        reference=importlib.util.module_from_spec(spec);spec.loader.exec_module(reference)
        scene,outfit,_=fixture()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'角色.pmx'
            export_scene(scene,outfit,path)
            result=reference.load(str(path))
            self.assertEqual((len(result.vertices),len(result.faces),len(result.bones)),(3,1,3))
            self.assertEqual(result.name,'角色')
            self.assertEqual(sum(m.vertex_count for m in result.materials),3)
            self.assertEqual(result.bones[2].parent,1)
            self.assertEqual(result.display[0].data,[(0,0)])
            np.testing.assert_allclose(result.vertices[0].weight.weights,[1,0,0,0])


class MaterialTests(unittest.TestCase):
    def test_glass_retains_tinted_transmission_and_reflection_against_white(self):
        transmission=np.array([[[.25,.5,.75]]]);reflection=np.array([[[.2,.8,.3,.4]]])
        rgba=glass_rgba(transmission,reflection)
        composited=rgba[:,:,:3]*rgba[:,:,3:4]+1-rgba[:,:,3:4]
        expected=transmission*(1-.4)+reflection[:,:,:3]*.4
        np.testing.assert_allclose(composited,expected)
        clear=glass_rgba(np.ones((1,1,3)),np.zeros((1,1,4)))
        self.assertTrue(np.isfinite(clear).all())
        self.assertEqual(clear[0,0,3],0)

    def test_uv_keeps_source_tiles_distinct_and_ignores_hidden_vertices(self):
        _,_,mesh=fixture();before=mesh['vertices'].tobytes()
        uv=bake_uv(mesh['vertices'],mesh['indices'],512)
        np.testing.assert_allclose(uv[0],[4/512]*2)
        np.testing.assert_allclose(uv[1],[1-4/512,4/512])
        self.assertEqual(mesh['vertices'].tobytes(),before)

    def test_padding_keeps_transparent_holes_inside_uv_islands(self):
        colors=np.zeros((7,7,4),dtype=np.float32);coverage=np.zeros((7,7),dtype=bool)
        colors[2:5,2:5]=(1,.5,0,1);coverage[2:5,2:5]=True;colors[3,3,3]=0
        padded=pad_uv_islands(colors,coverage,1)
        np.testing.assert_allclose(padded[1,3],colors[2,3])
        self.assertEqual(padded[3,3,3],0)
        self.assertEqual(padded[0,0,3],0)
        self.assertFalse(colors[1,3].any())

    def test_geometry_shader_preserves_semantics_and_uses_raw_uv_lookup(self):
        shader=geometry_shader_source([{'name':'SV_POSITION','index':0,'mask':'xyzw','system':'POS','type':'float'},
                                      {'name':'TEXCOORD','index':3,'mask':'xy','system':'NONE','type':'float'}])
        self.assertIn(b'TEXCOORD3',shader)
        self.assertIn(b'bakeIndices[primitive*3+corner]',shader)
        with self.assertRaises(ValueError):geometry_shader_source([])


class EngineTests(unittest.TestCase):
    def test_exports_last_successful_scene_without_rendering_again(self):
        from app import DemoEngine
        scene,outfit,_=fixture()
        engine=DemoEngine.__new__(DemoEngine);engine.status='ready';engine.last_scene=scene
        engine.character=SimpleNamespace(outfit=outfit);engine.load_cancel=SimpleNamespace(check=lambda:None)
        engine.game=Path('/unused-game').resolve();engine.render=mock.Mock(side_effect=AssertionError('must not render'))
        engine.scene_cache={'failed':SimpleNamespace(meshes=[])}
        with tempfile.TemporaryDirectory() as directory:
            result=engine.export_pmx(Path(directory)/'current.pmx',{'textures':False})
            self.assertEqual(result['vertices'],3)
        engine.render.assert_not_called()
        with self.assertRaises(ValueError):engine.export_pmx('unused.pmx',[])


if __name__=='__main__':unittest.main()
