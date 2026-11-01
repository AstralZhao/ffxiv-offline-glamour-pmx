"""Conservative appearance hints from authored CN model visibility metadata.

EQP describes coverage, not fabric, colour, ornament or artistic style. Only
positive source coverage is used; absent flags never imply a short silhouette.
No model rendering, game process or per-equipment tuning is required.
"""
from pathlib import Path
import hashlib

VERSION=1
EQP_PATH='chara/xls/equipmentparameter/equipmentparameter.eqp'


def coverage_tags(slot,value,origin='local_eqp'):
    if origin!='local_eqp':return []
    if slot=='Body':
        if not value&(1<<10):return ['全身装']
        if not value&(1<<9)or not value&(1<<8):return ['连体设计']
    elif slot=='Head':
        if value&(1<<40)and((value>>41)&7)==3:return ['遮面']
    elif slot=='Gloves':
        if value&(1<<25):return ['及肘设计']
        if value&(1<<26):return ['长筒设计']
    elif slot=='Feet':
        if value&(1<<33):return ['过膝设计']
        if value&(1<<34):return ['长筒设计']
    elif slot=='Legs'and not value&(1<<21):return ['连体设计']
    return []


def load_model_features(game,rows):
    from ff14_readonly import SqPack
    from cn_equipment import EquipmentParameters
    raw=SqPack(Path(game)).read(EQP_PATH);table=EquipmentParameters(raw)
    features={}
    for row in rows:
        key=(row['slot'],row['model_main']&65535)
        if key in features:continue
        if not key[1]:features[key]=[];continue
        value,origin=table.entry(key[1]);features[key]=coverage_tags(key[0],value,origin)
    return features,{'version':VERSION,'path':EQP_PATH,'sha256':hashlib.sha256(raw).hexdigest()}
