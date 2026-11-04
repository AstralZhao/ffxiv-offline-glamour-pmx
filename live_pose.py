"""Re-evaluate same-source facial/body parameters without reassembling a character."""
import copy,json
from pathlib import Path
import numpy as np
from preset_character import import_preset
from preset_customization import parameters,body_world,sha
from prepare_preset_customization import bundle_arrays
from prepare_fixed_stance import world_pose,skin_positions
from facial_parameters import select_parameters,apply_parameters
from havok_probe import read_skeleton
from geometry_probe import decode_model,resolve_mesh
from preset_face_shapes import face_shape_plan,resolve_face_mesh

STRUCTURE=('race','gender','body_type','tribe','face','hair','tail_or_ear_type')

def same_sources(old,new):
    return all(old[k]==new[k]for k in STRUCTURE)and(old['race']!=7 or old['lip_color_or_fur_pattern']==new['lip_color_or_fur_pattern'])

def geometry_preview(character,path,directory):
    source=character.base;new=import_preset(path);p=new['parameters'];old=source.pose['preset']['parameters']
    if not same_sources(old,p)or old['facial_features']!=p['facial_features']:return None
    context=getattr(character,'live_context',None)
    if context is None:
        assembly_path=Path(source.pose['source_assembly']['path']);assembly=json.loads(assembly_path.read_bytes())
        reference=bundle_arrays(assembly,assembly_path,'bind_geometry_bundle')
        posed=bundle_arrays(source.pose,character.pose_path,'customized_stance_geometry_bundle')
        originals={m['slot']:decode_model(source.pack.read(m['path']))for m in source.pose['models']if m['slot']in('Face','Hair')}
        context=(assembly,reference,posed['skeleton_idle_local'],originals);character.live_context=context
    assembly,ref,idle,originals=context
    assembly={**assembly,'models':[{**c,'meshes':[dict(m)for m in c['meshes']]}for c in assembly['models']],
        'bind_geometry_bundle':dict(assembly['bind_geometry_bundle'])};arrays=dict(ref)
    profile=json.loads((Path(__file__).resolve().parents[1]/'docs/cn-customization-profile.json').read_bytes())
    pack=source.pack;cmp=pack.read(profile['cmp_path']);fpeb=pack.read(profile['face_parameters']['path'])
    if sha(cmp)!=profile['cmp_sha256']or sha(fpeb)!=profile['face_parameters']['sha256']:raise ValueError('Live appearance source changed')
    custom=parameters(cmp,p);records=[r for r in source.pose['skeletons']if r.get('est_table')=='faceskeletontemplate']
    if len(records)!=1:raise ValueError('Live face source ambiguous')
    face=records[0];face_raw=pack.read(face['path'])
    if sha(face_raw)!=face['sha256']:raise ValueError('Live face skeleton changed')
    partial=next(r for r in assembly['source_skeleton_map']['partials']if r['path']==face['path'])
    selection=select_parameters(fpeb,assembly['identity']['race_sex_id'],p)
    names=ref['skeleton_names'].tolist();parents=ref['skeleton_parents']
    local,application=apply_parameters(idle,names,parents,[b['name']for b in read_skeleton(face_raw)['bones']],selection,
        profile['face_parameters']['degrees_to_radians_float32'],face_indices=[b['merged_bone_index']for b in partial['bones']])
    face_world=world_pose(local,parents);world,body_application=body_world(face_world,parents,names,custom['body'],p['race'])
    model,_=body_world(face_world,parents,names,{**custom['body'],'height_scale':1.},p['race'])
    matrices=world@ref['skeleton_inverse_bind'];plan=face_shape_plan(p);result=copy.copy(source)
    result.pose={**source.pose,'source_assembly':dict(source.pose['source_assembly']),
        'customized_stance_geometry_bundle':dict(source.pose['customized_stance_geometry_bundle'])}
    meshes=[]
    for ci,component in enumerate(assembly['models']):
        by_index={m.source_mesh:m for m in originals.get(component['slot'],())}
        for mi,report in enumerate(component['meshes']):
            prefix=f'component_{ci}_mesh_{mi}_';index=report['source_mesh'];slot=component['slot']
            if slot in('Face','Hair'):
                raw=by_index[index];hidden=[f'atr_fv_{c}'for i,c in enumerate('abcdefg')if not p['facial_features']&(1<<i)]if slot=='Face'or p['race']==7 else[]
                visible,shape_report=resolve_face_mesh(raw,plan,hidden)if slot=='Face'else(resolve_mesh(raw,disabled_attributes=hidden),None)
                arrays[prefix+'indices']=visible.indices;report['disabled_facial_attributes']=hidden;report['face_mdl_shape_application']=shape_report
            positions,normals=skin_positions(ref[prefix+'positions'],ref[prefix+'normals'],ref[prefix+'weights'],ref[prefix+'joints'],ref[prefix+'palette_to_rig'],matrices)
            arrays[prefix+'positions']=positions;arrays[prefix+'normals']=normals
            mesh=next((m for m in source.meshes if m['slot']==slot and m['source_mesh']==index),None)
            if mesh is not None:meshes.append({**mesh,'posed_positions':positions,'indices':arrays[prefix+'indices']})
    arrays.update(skeleton_idle_local=idle,skeleton_idle_world=world_pose(idle,parents),skeleton_face_local=local,skeleton_face_world=face_world,
        skeleton_customized_model=model,skeleton_customized_world=world,skeleton_skinning_matrices=matrices)
    # Reference positions remain the original bind vertices. Only indices and
    # preset metadata are updated; skinning is always applied exactly once.
    reference=dict(ref)
    for key,value in arrays.items():
        if key.endswith('_indices'):reference[key]=value
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True);pose_path=directory/'pose.json'
    np.savez(directory/'reference.npz',**reference);np.savez(directory/'posed.npz',**arrays)
    assembly['preset']=new;assembly['face_mdl_shapes']=plan
    assembly['bind_geometry_bundle'].update(file='reference.npz',sha256=sha((directory/'reference.npz').read_bytes()))
    assembly_raw=json.dumps(assembly,ensure_ascii=False).encode();(directory/'assembly.json').write_bytes(assembly_raw)
    custom['skeletal_face']={**source.pose['customization']['skeletal_face'],'selection':selection,'application':application}
    custom['application']=body_application
    result.pose.update(preset=new,face_mdl_shapes=plan,models=assembly['models'],customization={**source.pose['customization'],**custom})
    result.pose['source_assembly'].update(path=str(directory/'assembly.json'),sha256=sha(assembly_raw),bundle_sha256=assembly['bind_geometry_bundle']['sha256'])
    result.pose['customized_stance_geometry_bundle'].update(file='posed.npz',sha256=sha((directory/'posed.npz').read_bytes()))
    pose_path.write_text(json.dumps(result.pose,ensure_ascii=False),encoding='utf8')
    result.meshes=meshes;result.skinning=matrices;result.bounds=np.vstack([m['posed_positions']for m in meshes])
    result.appearance={name:bytes.fromhex(custom[field]['data_hex'])for name,field in(('g_CustomizeParameter','customize_buffer'),('g_DecalColor','decal_color_buffer'))}
    result.identity={**source.identity,'preset_sha256':new['source_sha256'],'pose_report_sha256':sha(pose_path.read_bytes()),'reference_report_sha256':sha(assembly_raw)}
    result=character.hair_scenes.resolve(result,pose_path)
    from preset_static_character import PresetStaticCharacter
    from appearance_preview import clone_outfit
    candidate=PresetStaticCharacter.__new__(PresetStaticCharacter);candidate.base=result;candidate.pose_path=pose_path;candidate.hair_scenes=character.hair_scenes
    candidate.closed=False;candidate.outfit=clone_outfit(character,result,geometry_changed=True);candidate.preparation_identity={'live_pose':True};candidate.live_context=context
    if hasattr(character,'resource_hashes'):candidate.resource_hashes=dict(character.resource_hashes)
    return candidate
