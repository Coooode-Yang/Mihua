"""Build a clean Windows runtime and verify it away from personal installations."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def zip_tree(folder:Path,output:Path,include_models=True):
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in sorted(folder.rglob('*')):
            if not path.is_file():continue
            relative=path.relative_to(folder)
            model=include_models and relative.parts[:2]==('data','models')
            if relative.parts[0]=='data' and relative.as_posix()!='data/settings.json' and not model:continue
            if not include_models and relative.parts[:2]==('data','models'):continue
            archive.write(path,Path('MihuaAgent')/relative)


def validate_destination(output:Path):
    output=output.resolve()
    relative=output.relative_to(ROOT.resolve()).parts if output.is_relative_to(ROOT.resolve()) else ()
    if not relative or relative[0]!='Release':
        raise RuntimeError('发布输出必须位于本仓库的 Release 子目录。')
    package=(output/'MihuaAgent').resolve()
    if not package.is_relative_to(output):raise RuntimeError('发布目录超出预期范围。')
    if (package/'data').exists() and any(p.is_file() and p.name!='settings.json' for p in (package/'data').rglob('*')):
        raise RuntimeError('发布目录已有个人数据，请使用新的 Release 输出目录。')
    return output


def main():
    from westie_agent import __version__
    from westie_agent.file_conversion import FILEE_VERSION,FILEE_ARCHIVE_SHA256
    from westie_agent.paper_reader import ReaderGenerator
    from westie_agent.settings import Settings
    parser=argparse.ArgumentParser(description='构建米花 Windows x64 公开发布包')
    parser.add_argument('--output',type=Path,default=ROOT/'Release'/('v'+__version__))
    parser.add_argument('--no-archives',action='store_true')
    args=parser.parse_args()
    if os.name!='nt':raise RuntimeError('Windows 应用需要在 Windows 上构建。')
    output=validate_destination(args.output)
    engine=ROOT/'runtime/filee'/FILEE_VERSION
    manifest=engine/'bai-engine-manifest.json'
    if not manifest.is_file():raise RuntimeError('请先运行 packaging/prepare_filee.py 准备固定版本转换组件。')
    info=json.loads(manifest.read_text(encoding='utf-8'))
    if (info.get('archive_sha256')!=FILEE_ARCHIVE_SHA256 or info.get('version')!=FILEE_VERSION
            or info.get('cli_sha256')!=hashlib.sha256((engine/'filee-cli.exe').read_bytes()).hexdigest()):
        raise RuntimeError('转换组件版本或校验信息不匹配。')
    ReaderGenerator(ROOT).guidance()
    output.mkdir(parents=True,exist_ok=True)
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--onedir','--windowed','--name','MihuaAgent',
             '--icon',str(ROOT/'assets/ui/chick-head.ico'),'--distpath',str(output),
             '--workpath',str(ROOT/'build'/('pyinstaller-'+output.name)),
             '--specpath',str(ROOT/'build'/output.name),'--paths',str(ROOT),
             '--add-data',str(ROOT/'assets')+';assets','--collect-all','netpet_tools',
             '--collect-data','genai_prices','--recursive-copy-metadata','pydantic-ai-slim',
             '--copy-metadata','openai','--copy-metadata','PySide6',
             '--collect-data','pdfminer','--collect-all','pypdfium2_raw',
             '--hidden-import','pydantic_ai.models.ollama','--hidden-import','pydantic_ai.providers.ollama']
    ffi=Path(sys.base_prefix)/'Library/bin/ffi.dll'
    if ffi.is_file():command += ['--add-binary',str(ffi)+';.']
    subprocess.run(command+[str(ROOT/'app.py')],cwd=ROOT,check=True)
    package=output/'MihuaAgent'
    shutil.copytree(engine,package/'runtime/filee'/FILEE_VERSION,dirs_exist_ok=True)
    shutil.copytree(ROOT/'licenses',package/'licenses',dirs_exist_ok=True)
    for name in ('LICENSE','THIRD_PARTY_NOTICES.md','USER_GUIDE.txt'):
        shutil.copy2(ROOT/name,package/name)
    for distribution in importlib.metadata.distributions():
        name=distribution.metadata.get('Name','unknown')
        if name.lower() in {'pip','setuptools','pytest','pyinstaller','pyinstaller-hooks-contrib','iniconfig','pluggy'}:continue
        for file in distribution.files or []:
            if '.dist-info' in str(file) and ('license' in str(file).lower() or file.name.upper() in {'COPYING','NOTICE'}):
                source=Path(distribution.locate_file(file))
                if source.is_file():
                    destination=package/'licenses/python-components'/name/file.name
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(source,destination)
    Settings().save(package)
    metadata=dict(version=__version__,display_name='米花',python_bundled=True,
        model_backend='api',offline_model_included=False,network_guardian=False,network_diagnosis=False,
        vocabulary_mode='spelling',legacy_paper_cards=False,growth_system=False,wardrobe=False,
        focus_seconds_per_coin=None,focus_completion_bonus=0,iphone_sync=False,
        file_conversion=True,filee_version=FILEE_VERSION,application_icon='assets/ui/chick-head.ico',
        automatic_updates=False,pet_character='Kenney chick / 米花',pet_sizes=[140,190,240])
    (package/'build-info.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    env=dict(os.environ)
    for key in ('PYTHONHOME','PYTHONPATH','DEEPSEEK_API_KEY','OPENAI_API_KEY'):env.pop(key,None)
    env['QT_QPA_PLATFORM']='offscreen'
    checks={}
    for kind in ('chick','self','api','unified','attachments','reader','journal','quick-ask','sedentary','dialog-chrome','conversion'):
        report=output/('packaged-'+kind+'.json')
        flag='--self-test' if kind=='self' else '--'+kind+'-self-test'
        subprocess.run([str(package/'MihuaAgent.exe'),flag,'--self-test-output',str(report)],
                       cwd=output,env=env,check=True,timeout=180)
        value=json.loads(report.read_text(encoding='utf-8'))
        if not value.get('ok') or not value.get('frozen') or value.get('external_api_called'):
            raise RuntimeError('编译版检查未通过：'+kind)
        checks[kind]=True
    # Self-checks may create temporary default app records; public ZIPs still
    # include only default settings, and no user's data is copied into the build.
    if not args.no_archives:
        zip_tree(package,output/'MihuaAgent-api-win64.zip',include_models=False)
    print(json.dumps({'output':str(output),'checks':checks,'personal_data_bundled':False},ensure_ascii=False))


if __name__=='__main__':main()
