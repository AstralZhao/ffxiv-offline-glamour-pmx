"""User-managed local version; Gitee release tags define remote versions."""
import json
import re
from app_paths import paths,is_release

REPOSITORY='kilislan-wei/ffxiv_offline_glamour'
RELEASES='https://gitee.com/'+REPOSITORY+'/releases/'
API='https://gitee.com/api/v5/repos/'+REPOSITORY+'/releases'


def version_key(value):
    if not isinstance(value,str) or not re.fullmatch(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)',value):
        raise ValueError('版本必须为 v主版本.次版本.修订号')
    return tuple(map(int,value[1:].split('.')))


def current_version():
    p=paths()
    declared=json.loads((p.program/'docs/client-version.json').read_bytes())['version']
    version_key(declared)
    return declared


def can_install():
    # A source checkout is never overwritten by the release updater.
    p=paths()
    return p.program!=p.root and is_release(p.program)
