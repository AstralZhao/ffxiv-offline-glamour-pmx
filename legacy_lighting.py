"""Original CN null-scene direction input for legacy gear specular passes.

This fixed studio has no world-scene directional-light object. CN 281679
selects (1,1,1) for that case, then 3395a0 transforms its direction by the
current camera view and 2f9050 normalizes it. It is separate from the
camera-fixed mode0 ambient coefficients; do not rotate those coefficients.
"""
import hashlib
import json
from pathlib import Path

import numpy as np

from cn_offscreen_lighting import rva_bytes

PROFILE=Path(__file__).resolve().parents[1]/'docs/cn-legacy-light-direction.json'


def source_direction(pack):
    profile=json.loads(PROFILE.read_bytes())
    version=(pack.game_root/'game/ffxivgame.ver').read_text().strip()
    exe=(pack.game_root/'game/ffxiv_dx11.exe').read_bytes()
    if version!=profile['client_version']or hashlib.sha256(exe).hexdigest()!=profile['executable_sha256']:
        raise ValueError('Legacy light direction requires CN source review')
    for window in profile['windows']:
        raw=rva_bytes(exe,int(window['rva'],16),window['bytes'])
        if hashlib.sha256(raw).hexdigest()!=window['sha256']:
            raise ValueError('Legacy light direction source differs')
    scalar=np.frombuffer(rva_bytes(exe,int(profile['null_scene_scalar_rva'],16),4),dtype='<f4')[0]
    return np.full(3,scalar,dtype='<f4')


def camera_direction(source,view):
    source=np.asarray(source,dtype='<f4');view=np.asarray(view,dtype='<f4')
    if source.shape!=(3,)or view.shape!=(4,4)or not np.isfinite(source).all()or not np.isfinite(view).all():
        raise ValueError('Invalid legacy light direction frame')
    # Native row storage implements the same model->view direction map used
    # by this renderer's column matrices. Translation is excluded.
    vector=(source[1]*view[:3,1]+source[0]*view[:3,0])+source[2]*view[:3,2]
    length2=(vector[0]*vector[0]+vector[1]*vector[1])+vector[2]*vector[2]
    if length2<=np.float32(2**-23):vector=np.zeros(3,dtype='<f4')
    else:vector=vector*(np.float32(1)/np.sqrt(length2))
    # PS reads xyz only. Fourth lane is explicit unused upload padding.
    return np.r_[vector,np.float32(0)].astype('<f4').tobytes()
