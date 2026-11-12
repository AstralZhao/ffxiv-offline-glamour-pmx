"""User-managed local version; Gitee release tags define remote versions."""
import json
import re
from app_paths import paths,is_release

REPOSITORY='kilislan-wei/ffxiv_offline_glamour'
RELEASES='https://gitee.com/'+REPOSITORY+'/releases/'
API='https://gitee.com/api/v5/repos/'+REPOSITORY+'/releases'


def version_key(value):
    match=re.fullmatch(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-beta\.(0|[1-9][0-9]*))?',value)if isinstance(value,str)else None
    if match is None:raise ValueError('版本必须为 v主版本.次版本.修订号，测试版使用 -beta.数字')
    major,minor,patch,beta=match.groups()
    return int(major),int(minor),int(patch),int(beta is None),int(beta or 0)


def is_beta(value):
    return version_key(value)[3]==0


def current_version():
    p=paths()
    declared=json.loads((p.program/'docs/client-version.json').read_bytes())['version']
    version_key(declared)
    return declared


def manual_updates():
    """A customized portable build must not install the upstream edition."""
    return json.loads((paths().program/'docs/client-version.json').read_bytes()).get('update_policy')=='manual'


def distribution_label():
    return json.loads((paths().program/'docs/client-version.json').read_bytes()).get('edition_label','')


def update_notice():
    if manual_updates():
        return 'PMX 便携版采用手动更新，不安装上游普通版。请关闭软件后解压新版 PMX 包，再迁移“用户数据”；使用离线资源包时同时迁移“resources”。'
    return '开发版请通过 Git 更新源码，不覆盖开发目录。'


def can_install():
    # A source checkout is never overwritten by the release updater.
    p=paths()
    return p.program!=p.root and is_release(p.program)and not manual_updates()
