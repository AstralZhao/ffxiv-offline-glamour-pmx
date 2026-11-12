"""Additional installed CN legacy gear programs for the Demo only.

The historical representative renderer's shader recognizer remains unchanged.
These original programs share its normal/class/scalar-occlusion MRT layout;
the alternate legacy coverage-in-normal.w producer remains incompatible.
"""
import hashlib

from material_shader_probe import selector
from shader_input_profiles import program_plan
from static_material_inputs import static_pass,shader_crc,ELEMENTS
from material_stage_feasibility import signature
from material_shader_probe import disassemble

LEGACY_VERTEX='50bea09655aca55b4a93f1e03bdc02c0104a385ac5e52e974a2238906470604b'
LEGACY_GEAR_PRODUCERS={
    False:frozenset(('636362c38b6e3c0099fe4deb30ace08f457c887e34f47664dbe60ec2d7c50eab',
        'b1324bec89813b11e0b58d139a7b29e38480e9306ed2fc6a08bf62f42b0d8b3d',
        '0a8ad43cb04a41cce522326d755134d5f994acf04b046f468ae37f6c69fcb7a6')),
    True:frozenset(('622d561d9303dc71a532a35eb2cb2a4b68b09eccd4cd81d88ec33715e62e556b',
        '8221d73f229205cd829d4cb984a77d2a2cf0dbe75282af5a960c2e8dbc4d2988',
        '28e610e5d2662ede23c6d38eac095e56c51a19d413cb01a598b4e9b2eedbcc11'))}


def equipment_pass(package,material_keys,influences=4,role='material',*,alpha_clip=False):
    try:return static_pass(package,material_keys,influences,role,alpha_clip=alpha_clip)
    except ValueError as error:
        if str(error)!='Static preview color pass is absent or ambiguous' or role!='material' or influences!=4:raise
        original_error=error
    # Same original package key/subview resolution as the existing planner.
    # Additional acceptance is exact code identity, not material/item ID or
    # arbitrary "has five outputs" matching. Native alpha-clip choice stays
    # explicit; never replace the original PS with a generated program.
    overrides={shader_crc('DrawOffscreen'):shader_crc('DrawOffscreenOn'),
        shader_crc('TransformView'):shader_crc('TransformViewSkin')}
    if alpha_clip:overrides[shader_crc('ApplyAlphaClip')]=shader_crc('ApplyAlphaClipOn')
    groups=[];used={}
    for group in('system','scene','material'):
        values=[]
        for key in package['keys'][group]:
            identifier=int(key['id'],16);default=int(key['default'],16)
            value=(overrides if group=='scene'else material_keys if group=='material'else{}).get(identifier,default)
            values.append(value);used[f'{group}:{identifier:08x}']=f'{value:08x}'
        groups.append(selector(values))
    candidates={}
    for subview in sorted({tuple(node['keys'][-2:])for node in package['nodes']}):
        selected=selector(groups+[selector(subview)])
        if selected not in package['selectors']:continue
        node_id=package['selectors'][selected]
        for pass_id,vi,pi,hs,ds,gs in package['nodes'][node_id]['passes']:
            if(hs,ds,gs)!=(0xffffffff,)*3:continue
            vs=package['stages']['vertex'][vi];ps=package['stages']['pixel'][pi]
            identity=(hashlib.sha256(vs['bytecode']).hexdigest(),hashlib.sha256(ps['bytecode']).hexdigest())
            if identity[0]!=LEGACY_VERTEX or identity[1]not in LEGACY_GEAR_PRODUCERS[bool(alpha_clip)]:continue
            if identity not in candidates:
                vertex=program_plan(vs,vi,'vertex');pixel=program_plan(ps,pi,'pixel')
                if pixel['output_slots']!=list(range(5)):raise ValueError('Legacy gear output layout changed')
                if not any(r['name']=='g_JointMatrixArray'and r['stride']==48 for r in vertex['resources']):
                    raise ValueError('Legacy gear joint storage changed')
                inputs=signature(disassemble(vs['bytecode']),False)
                actual={(r['name'],r['index'])for r in inputs if r['system']=='NONE'}
                if not actual<={(name,0)for name,_,_ in ELEMENTS}or('POSITION',0)not in actual:
                    raise ValueError('Legacy gear vertex layout changed')
                for stage in(vertex,pixel):stage['five_mrt_capture_eligible']=stage.pop('eligible')
                candidates[identity]={'node':node_id,'pass_id':pass_id,'vertex':vertex,'pixel':pixel,
                    'selector':f'{selected:08x}','keys':used,'subview_values':list(subview),'aliases':[],
                    'static_preview_eligible':True,'influences':4,'role':'material','vertex_inputs':inputs,
                    'material_output_layout':{'normal_xyz_class_w':0,'occlusion_x_roughness_y_material_z':1,
                        'base_color_rgb':2,'motion_xy':3,'geometric_normal_xyz':4,'legacy_scalar_occlusion':True,
                        'class_write':'mov o0.w, l(0.172549)','source':'exact_installed_CN_legacy_gear_program'}}
            candidates[identity]['aliases'].append({'node':node_id,'pass_id':pass_id,'subview_values':list(subview)})
    if len(candidates)!=1:raise original_error
    return next(iter(candidates.values()))
