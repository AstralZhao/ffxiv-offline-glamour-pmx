"""Deterministic runtime dependency closure shared by packaging and caches."""
import ast
import json
import os
from pathlib import Path
from runtime_identity import compiled_manifest
ROOT=Path(__file__).resolve().parents[1]

def native_static_assets():
    manifest=ROOT/'artifacts/native/static-hair-kernel.json';source=ROOT/'tools/static_hair_kernel.cpp'
    if not manifest.is_file():return [source]if source.is_file()else[]
    record=json.loads(manifest.read_bytes());name=record.get('file','static-hair-kernel.dll')
    if Path(name).name!=name or Path(name).suffix!='.dll':raise ValueError('Invalid native helper filename')
    dll=manifest.with_name(name)
    import hashlib
    if hashlib.sha256(source.read_bytes()).hexdigest()!=record['source_sha256']or hashlib.sha256(dll.read_bytes()).hexdigest()!=record['dll_sha256']:
        raise ValueError('Native helper/source identity differs')
    return [source,manifest,dll]


def application_files():
    compiled=compiled_manifest()
    if compiled is not None:
        return ([ROOT/p for p in sorted(compiled['modules'])],
                [ROOT/p for p in sorted(compiled['profiles'])])
    inventory={str(p.relative_to(ROOT)).replace('\\','/'):(p.stat().st_size,p.stat().st_mtime_ns,p.stat().st_ctime_ns)
        for folder in('tools','demo')for p in(ROOT/folder).glob('*.py')}
    signature=json.dumps(inventory,sort_keys=True);path=__import__('app_paths').paths().cache/'runtime-closure-cache.json'
    try:
        record=json.loads(path.read_bytes())
        if record['version']==1 and record['inventory']==signature:
            modules=[(ROOT/p).resolve() for p in record['modules']];profiles=[(ROOT/p).resolve() for p in record['profiles']]
            if (all(p.is_relative_to(ROOT)and p.is_file()for p in modules+profiles)
                and all(str(p.relative_to(ROOT)).replace('\\','/')in inventory for p in modules)
                and all(p.parent==ROOT/'docs'and p.suffix=='.json'for p in profiles)):return modules,profiles
    except(OSError,ValueError,KeyError,TypeError):pass
    modules,profiles=_application_files()
    record={'version':1,'inventory':signature,'modules':[str(p.relative_to(ROOT)).replace('\\','/')for p in modules],
        'profiles':[str(p.relative_to(ROOT)).replace('\\','/')for p in profiles]}
    temporary=path.with_suffix('.'+str(os.getpid())+'.tmp')
    try:
        path.parent.mkdir(parents=True,exist_ok=True);temporary.write_text(json.dumps(record));os.replace(temporary,path)
    except OSError:pass
    finally:
        try:temporary.unlink(missing_ok=True)
        except OSError:pass
    return modules,profiles

def _application_files():
    modules={path.stem:path for folder in('tools','demo')for path in(ROOT/folder).glob('*.py')}
    workers={'preset_character','prepare_fixed_stance','prepare_preset_customization'}
    diagnostic_only={'material_input_snapshot','draw_contract_audit','game_gpu_probe','material_color_pair','scene_pass_snapshot',
        'scene_pass_replay','character_color','actor_identity_audit','runtime_contract_probe','viewport_capture','render_validation_case'}
    class RuntimeSyntax(ast.NodeTransformer):
        def visit_FunctionDef(self,node):return None if node.name=='main'else self.generic_visit(node)
    pending=['launch',*workers];selected=set();profiles=set()
    while pending:
        name=pending.pop()
        if name in selected:continue
        selected.add(name);source=modules[name];tree=ast.parse(source.read_text(encoding='utf8'))
        if source.parent.name=='tools'and name not in workers:tree=RuntimeSyntax().visit(tree)
        for node in ast.walk(tree):
            names=[]
            if isinstance(node,ast.Import):names=[entry.name.split('.')[0]for entry in node.names]
            elif isinstance(node,ast.ImportFrom)and node.module:names=[node.module.split('.')[0]]
            elif isinstance(node,ast.Constant)and isinstance(node.value,str):
                literal=node.value
                if literal in modules:names=[literal]
                elif literal.endswith('.py')and Path(literal).stem in modules:names=[Path(literal).stem]
                if literal.endswith('.json'):
                    path=ROOT/'docs'/Path(literal).name
                    if path.is_file()and path.name not in('active-render-case.json','stage-three-acceptance.json'):profiles.add(path)
            pending.extend(value for value in names if value in modules and value not in selected and value not in diagnostic_only)
    return [modules[name]for name in sorted(selected)],sorted(profiles)
