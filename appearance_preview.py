"""Exact material-only preview updates; geometric changes use normal preparation."""
import copy,json,sqlite3
from pathlib import Path
from preset_character import import_preset
from preset_customization import parameters,sha

COLORS={'skin_color','hair_color','highlight_color','highlight_flags','right_eye_color',
        'left_eye_color','facial_feature_color','face_paint_color','face_paint'}

def clone_outfit(character,scene,*,geometry_changed=False):
    outfit=copy.copy(character.outfit);outfit.base=scene;outfit.pack=scene.pack
    outfit.database=sqlite3.connect((Path(__file__).resolve().parents[1]/'artifacts/cn-data/catalog.sqlite').as_uri()+'?mode=ro',uri=True)
    outfit.database.row_factory=sqlite3.Row
    if geometry_changed:
        from collections import OrderedDict
        outfit.prepared={};outfit.head_parts=None;outfit.attachments=OrderedDict()
    return outfit

def material_only(old,new):
    changed={k for k in new if new[k]!=old[k]}
    allowed=set(COLORS)
    if old['race']!=7:allowed.add('lip_color_or_fur_pattern')
    if old['race']in(1,5):allowed.add('ear_muscle_tail_size')
    if (old['mouth']&127)==(new['mouth']&127):allowed.add('mouth')
    return changed<=allowed

def material_preview(character,path):
    original=character.base;new=import_preset(path);p=new['parameters']
    if not material_only(original.pose['preset']['parameters'],p):return None
    profile=json.loads((Path(__file__).resolve().parents[1]/'docs/cn-customization-profile.json').read_bytes())
    cmp=original.pack.read(profile['cmp_path'])
    if sha(cmp)!=profile['cmp_sha256']:raise ValueError('Appearance palette source changed')
    result=copy.copy(original);result.pose={**original.pose,'customization':dict(original.pose['customization'])};result.pose['preset']=new
    custom=parameters(cmp,p)
    for name,value in custom.items():result.pose['customization'][name]=value
    result.appearance={name:bytes.fromhex(custom[field]['data_hex'])for name,field in
        (('g_CustomizeParameter','customize_buffer'),('g_DecalColor','decal_color_buffer'))}
    result.identity={**original.identity,'preset_sha256':new['source_sha256'],
        'material_only_preview_sha256':sha(json.dumps(p,sort_keys=True).encode())}
    # Neither existing scenes nor the saved preset are mutated. A fresh
    # outfit owner binds updated constants to all equipment and accessories.
    from preset_static_character import PresetStaticCharacter
    candidate=PresetStaticCharacter.__new__(PresetStaticCharacter)
    candidate.pose_path=character.pose_path;candidate.hair_scenes=character.hair_scenes
    candidate.base=result;candidate.closed=False
    candidate.outfit=clone_outfit(character,result)
    candidate.preparation_identity={'material_only':True,'source':character.preparation_identity}
    if hasattr(character,'resource_hashes'):candidate.resource_hashes=dict(character.resource_hashes)
    if hasattr(character,'live_context'):candidate.live_context=character.live_context
    return candidate
