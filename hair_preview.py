"""Replace only the selected source Hair component, preserving base/face work."""
import copy,json
from pathlib import Path
import numpy as np
from appearance_preview import material_only,clone_outfit
from preset_character import import_preset,load_character_skeletons,PresetCharacter,select_profile
from source_skeleton_map import SourceSkeletonMap
from preset_face_shapes import face_shape_plan
from preset_customization import parameters,body_world,sha
from prepare_fixed_stance import world_pose,skin_positions
from prepare_preset_customization import bundle_arrays
from render_preset_materials import PresetMaterialScene


def hair_preview(character,path,directory):
    source=character.base;new=import_preset(path);p=new['parameters'];old=source.pose['preset']['parameters']
    if old['hair']==p['hair']or not material_only({**old,'hair':p['hair']},p):return None
    pack=source.pack
    if (pack.game_root/'game/ffxivgame.ver').read_text().strip()!=source.version:raise ValueError('Hair source version changed')
    assembly_path=Path(source.pose['source_assembly']['path']);assembly=json.loads(assembly_path.read_bytes())
    reference=bundle_arrays(assembly,assembly_path,'bind_geometry_bundle')
    previous=bundle_arrays(source.pose,character.pose_path,'customized_stance_geometry_bundle')
    skeletons,records,absent=load_character_skeletons(pack,p);mapping=SourceSkeletonMap(skeletons,records);rig=mapping.rig
    # Only the final Hair partial may change. Existing body/face palettes keep
    # their exact names, indices, parents and source-reference transforms.
    stable=[r for r in records if r.get('est_table')!='hairskeletontemplate']
    old_stable=[r for r in source.pose['skeletons']if r.get('est_table')!='hairskeletontemplate']
    if stable!=old_stable:raise ValueError('Hair replacement changed a base/face skeleton')
    prefix=max(mapping.partials[len(stable)-1]['to_merged'])+1
    if (rig.names[:prefix]!=reference['skeleton_names'].tolist()[:prefix]or
        not np.array_equal(rig.parents[:prefix],reference['skeleton_parents'][:prefix])or
        not np.array_equal(rig.local[:prefix],reference['skeleton_local'][:prefix])):
        raise ValueError('Hair replacement changed the stable rig prefix')
    idle=rig.local.copy();idle[:prefix]=previous['skeleton_idle_local'][:prefix]
    local=idle.copy();local[:prefix]=previous['skeleton_face_local'][:prefix]
    profile=json.loads((Path(__file__).resolve().parents[1]/'docs/cn-customization-profile.json').read_bytes())
    cmp=pack.read(profile['cmp_path'])
    if sha(cmp)!=profile['cmp_sha256']:raise ValueError('Hair appearance palette changed')
    custom=parameters(cmp,p);face_world=world_pose(local,rig.parents)
    world,application=body_world(face_world,rig.parents,rig.names,custom['body'],p['race'])
    model,_=body_world(face_world,rig.parents,rig.names,{**custom['body'],'height_scale':1.},p['race'])
    matrices=world@rig.inverse_bind
    partial=PresetCharacter.__new__(PresetCharacter);partial.pack=pack;partial.preset=new;partial.race=select_profile(p)
    partial.source_map=mapping;partial.rig=rig;partial.skeleton_records=records;partial.models=[];partial.model_records=[];partial.face_plan=face_shape_plan(p)
    race=partial.race;hair=p['hair'];mdl=f'chara/human/c{race:04}/obj/hair/h{hair:04}/model/c{race:04}h{hair:04}_hir.mdl'
    partial.load_model('Hair',mdl,race,[])
    assembly={**assembly,'models':list(assembly['models']),'bind_geometry_bundle':dict(assembly['bind_geometry_bundle'])}
    ci=next(i for i,c in enumerate(assembly['models'])if c['slot']=='Hair')
    assembly['models'][ci]=partial.model_records[0]
    for i,c in enumerate(assembly['models']):
        if i!=ci and any(max(m['palette_to_rig'])>=prefix for m in c['meshes']):
            raise ValueError('Non-hair component depends on a replaced Hair bone')
    reference={k:v for k,v in reference.items()if not k.startswith(f'component_{ci}_mesh_')}
    for mi,mesh in enumerate(partial.models[0][1]):
        key=f'component_{ci}_mesh_{mi}_'
        for name in('positions','normals','weights','joints','indices'):reference[key+name]=getattr(mesh,name)
        reference[key+'palette_to_rig']=np.asarray(partial.model_records[0]['meshes'][mi]['palette_to_rig'],dtype=np.int32)
    reference.update(skeleton_local=rig.local,skeleton_bind=rig.bind,skeleton_inverse_bind=rig.inverse_bind,
        skeleton_names=np.asarray(rig.names),skeleton_parents=np.asarray(rig.parents))
    posed=dict(reference)
    for i,c in enumerate(assembly['models']):
        for mi,_ in enumerate(c['meshes']):
            key=f'component_{i}_mesh_{mi}_'
            if i==ci:
                positions,normals=skin_positions(*(reference[key+n]for n in('positions','normals','weights','joints','palette_to_rig')),matrices)
            else:positions,normals=previous[key+'positions'],previous[key+'normals']
            posed[key+'positions']=positions;posed[key+'normals']=normals
    posed.update(skeleton_idle_local=idle,skeleton_idle_world=world_pose(idle,rig.parents),skeleton_face_local=local,
        skeleton_face_world=face_world,skeleton_customized_model=model,skeleton_customized_world=world,skeleton_skinning_matrices=matrices)
    assembly.update(preset=new,skeletons=records,absent_extra_skeletons=absent,source_skeleton_map=mapping.report(),
        merged_bones=len(rig.names),shared_skeleton_anchors=rig.shared_anchors)
    result=copy.copy(source);result.pose={**source.pose,'customization':dict(source.pose['customization']),
        'source_assembly':dict(source.pose['source_assembly']),
        'customized_stance_geometry_bundle':dict(source.pose['customized_stance_geometry_bundle'])};result.skinning=matrices
    result.pose.update(preset=new,models=assembly['models'],skeletons=records,absent_extra_skeletons=absent,
        source_skeleton_map=assembly['source_skeleton_map'],merged_bones=len(rig.names),shared_skeleton_anchors=rig.shared_anchors)
    result.pose['customization'].update(custom);result.pose['customization']['application']=application
    result.pose['incremental_hair']={'base_and_face_sources_unchanged':True,'fixed_idle_and_face_local_reused':True,'hair_reference_rebound':True}
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True);pose_path=directory/'pose.json'
    np.savez(directory/'reference.npz',**reference);np.savez(directory/'posed.npz',**posed)
    assembly['bind_geometry_bundle'].update(file='reference.npz',sha256=sha((directory/'reference.npz').read_bytes()))
    raw=json.dumps(assembly,ensure_ascii=False).encode();(directory/'assembly.json').write_bytes(raw)
    result.pose['source_assembly'].update(path=str(directory/'assembly.json'),sha256=sha(raw),bundle_sha256=assembly['bind_geometry_bundle']['sha256'])
    result.pose['customized_stance_geometry_bundle'].update(file='posed.npz',sha256=sha((directory/'posed.npz').read_bytes()))
    pose_path.write_text(json.dumps(result.pose,ensure_ascii=False),encoding='utf8')
    result.appearance={name:bytes.fromhex(custom[field]['data_hex'])for name,field in(('g_CustomizeParameter','customize_buffer'),('g_DecalColor','decal_color_buffer'))}
    result.identity={**source.identity,'preset_sha256':new['source_sha256'],'pose_report_sha256':sha(pose_path.read_bytes()),'reference_report_sha256':sha(raw)}
    result.packages=dict(source.packages);result.meshes=[];result.hidden_meshes=[];result.pending_meshes=[];result.omitted=[]
    PresetMaterialScene.prepare_components(result,reference,posed,selected_slots={'Hair'},reusable_meshes=source.meshes)
    replacement=result.meshes;meshes=[]
    for c in assembly['models']:
        meshes.extend(replacement if c['slot']=='Hair'else[m for m in source.meshes if m['slot']==c['slot']])
    result.meshes=meshes;result.hidden_meshes=[m for m in source.hidden_meshes if m['slot']!='Hair']+result.hidden_meshes
    result.pending_meshes=[m for m in source.pending_meshes if m['slot']!='Hair']+result.pending_meshes
    result.omitted=list(source.omitted);result.bounds=np.vstack([m['posed_positions']for m in meshes])
    result=character.hair_scenes.resolve(result,pose_path)
    from preset_static_character import PresetStaticCharacter
    candidate=PresetStaticCharacter.__new__(PresetStaticCharacter);candidate.base=result;candidate.pose_path=pose_path
    candidate.hair_scenes=character.hair_scenes;candidate.closed=False;candidate.preparation_identity={'hair_swap':True}
    candidate.outfit=clone_outfit(character,result,geometry_changed=True)
    candidate.outfit.names={name:i for i,name in enumerate(rig.names)}
    if hasattr(candidate.outfit,'bind'):candidate.outfit.bind=rig.bind
    if hasattr(candidate.outfit,'parents'):candidate.outfit.parents={name:(rig.names[parent]if parent>=0 else None)for name,parent in zip(rig.names,rig.parents)}
    if hasattr(character,'resource_hashes'):candidate.resource_hashes=dict(character.resource_hashes)
    return candidate
