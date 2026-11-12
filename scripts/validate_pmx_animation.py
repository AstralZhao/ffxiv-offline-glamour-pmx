"""Manual v0.0.9 integration check; run with the prepared embedded Python."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.run_dev import TAG


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game',required=True,type=Path)
    parser.add_argument('--runtime',type=Path,default=ROOT/'local_runtime'/('dev-'+TAG))
    parser.add_argument('--output',type=Path,default=ROOT/'test_output/v009/animation')
    args=parser.parse_args()
    runtime=args.runtime.resolve();output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    game=args.game.resolve();os.chdir(runtime)
    sys.path[:0]=[str(runtime/'demo'),str(runtime/'tools')]
    import numpy as np
    from app import DemoEngine
    from pmx_export import AXES,build_model
    engine=DemoEngine(game,runtime/'defaults/test.dat',preset_directory=output/'presets')
    try:
        engine.ready.result()
        if engine.status!='ready':raise RuntimeError(engine.error)

        def validate():
            state=deepcopy(engine.last['state']);state['effects_visible']=False
            engine.render(state)
            reference=build_model(engine.last_scene,engine.character.outfit,rig='standard',scale=1)
            catalogue=engine.emote_catalog()
            dance=next(r for r in catalogue if r['key']=='emote/dance')
            facial=next(r for r in catalogue if r['facial'] and 'smile' in r['key'])
            state['expression']=facial['id']
            samples=[];previous=None
            for seconds in (.75,1.5):
                state['emote']={'id':dance['id'],'time':seconds}
                engine.render(state);scene=engine.last_scene
                if previous is not None:
                    assert scene is not previous
                    assert previous.skinning.tobytes()==previous_skin
                before=scene.skinning.tobytes()
                native=build_model(scene,engine.character.outfit,rig='native',scale=1)
                expected=[];stale=[]
                for mesh in scene.meshes:
                    if not len(mesh['indices']):continue
                    v=mesh['vertices'];points=np.column_stack((v['position'],np.ones(len(v))))
                    matrices=mesh.get('joint_matrices')
                    if matrices is None:matrices=scene.skinning[mesh['palette']]
                    positions=np.zeros((len(v),3))
                    for i in range(v['weights'].shape[1]):
                        active=v['weights'][:,i]>0
                        positions[active]+=np.einsum('nij,nj->ni',matrices[v['joints'][active,i]],points[active])[:,:3]*(v['weights'][active,i,None]/255.)
                    used=np.unique(mesh['indices'])
                    expected.append(positions[used]*AXES);stale.append(mesh['posed_positions'][used]*AXES)
                actual=np.concatenate([part.positions for part in native.parts]);gpu=np.concatenate(expected)
                error=float(np.abs(actual-gpu).max());assert error<2e-6,error
                stale_delta=float(np.linalg.norm(actual-np.concatenate(stale),axis=1).max())
                assert stale_delta>.01,stale_delta
                standard=build_model(scene,engine.character.outfit,rig='standard',scale=1)
                for a,b in zip(standard.parts,reference.parts):np.testing.assert_allclose(a.positions,b.positions,atol=1e-7)
                for a,b in zip(standard.bones,reference.bones):np.testing.assert_allclose(a.position,b.position,atol=1e-7)
                assert scene.skinning.tobytes()==before
                assert len(standard.morphs)==13 and sum(b.ik is not None for b in standard.bones)==4
                samples.append({'seconds':seconds,'gpu_position_error_m':error,'stale_position_difference_m':stale_delta})
                previous=scene;previous_skin=before
            native_result=engine.export_pmx(output/'animation_native.pmx',{'rig':'native','textures':False,'overwrite':True})
            standard_result=engine.export_pmx(output/'animation_standard.pmx',{'rig':'standard','textures':False,'overwrite':True})
            # A failed render must not replace the last successfully shown scene.
            last=engine.last_scene
            try:engine.render({**state,'emote':{'id':999999,'time':0.}})
            except (ValueError,NotImplementedError):pass
            else:raise AssertionError('Invalid emote unexpectedly accepted')
            assert engine.last_scene is last
            report={'runtime_version':json.loads((runtime/'docs/client-version.json').read_bytes())['version'],
                    'emote':dance['name'],'expression':facial['name'],'samples':samples,
                    'standard_reference_unchanged':True,'last_success_preserved':True,
                    'native':native_result,'standard':standard_result}
            (output/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
            print(json.dumps({k:v for k,v in report.items()if k not in('native','standard')},ensure_ascii=False),flush=True)
        engine.pool.submit(validate).result()
    finally:
        engine.pool.submit(engine.close_gpu).result();engine.pool.shutdown(wait=True)


if __name__=='__main__':main()
