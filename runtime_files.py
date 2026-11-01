"""Deterministic runtime dependency closure shared by packaging and caches."""
import ast
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def application_files():
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
