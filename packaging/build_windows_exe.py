"""Build a native local launcher and a complete per-user Windows installer."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
NSIS_VERSION='3.11'
NSIS_SHA256='c7d27f780ddb6cffb4730138cd1591e841f4b7edb155856901cdf5f214394fa1'


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def prepare_nsis(work):
    work=Path(work);work.mkdir(parents=True,exist_ok=True)
    archive=work/'nsis-3.11.zip';folder=work/'nsis-3.11'
    if not archive.is_file() or sha(archive)!=NSIS_SHA256:
        failures=[]
        for url in ('https://downloads.sourceforge.net/project/nsis/NSIS%203/3.11/nsis-3.11.zip',
                    'https://mirror.nju.edu.cn/macports/distfiles/nsis/nsis-3.11.zip'):
            try:
                with urllib.request.urlopen(url,timeout=90) as response:content=response.read(8_000_000)
                if hashlib.sha256(content).hexdigest()!=NSIS_SHA256:raise ValueError('NSIS checksum mismatch')
                archive.write_bytes(content);break
            except Exception as exc:failures.append(type(exc).__name__)
        else:raise RuntimeError('Cannot retrieve the pinned NSIS compiler: '+str(failures))
    if not (folder/'makensis.exe').is_file():
        with zipfile.ZipFile(archive) as source:
            for info in source.infolist():
                target=(work/info.filename).resolve()
                if not target.is_relative_to(work.resolve()):raise ValueError('Unsafe compiler archive')
            source.extractall(work)
    return folder


def create_icon(work):
    icon=ROOT/'assets/ui/chick-head.ico'
    if not icon.is_file():raise RuntimeError('The shared frontal-head application icon is missing.')
    return icon


def quote_nsis(value):return str(value).replace('$','$$').replace('"','$\\"')


def write_file_manifests(work,files):
    add=[];remove=[];parents=set()
    for relative,source in sorted(files.items()):
        parts=Path(relative).parts
        if not parts or parts[0]=='data' or '..' in parts or Path(relative).is_absolute():raise ValueError('Invalid static manifest')
        directory=str(Path(relative).parent).replace('/','\\')
        add += ['SetOutPath "$INSTDIR'+('\\'+quote_nsis(directory) if directory!='.' else '')+'"',
                'File "/oname='+quote_nsis(Path(relative).name)+'" "'+quote_nsis(source)+'"']
        remove += ['Delete "$INSTDIR\\'+quote_nsis(relative.replace('/','\\'))+'"']
        parent=Path(relative).parent
        while parent!=Path('.'):
            parents.add(parent);parent=parent.parent
    remove += ['RMDir "$INSTDIR\\'+quote_nsis(str(p).replace('/','\\'))+'"' for p in sorted(parents,key=lambda p:len(p.parts),reverse=True)]
    install=work/'install-files.nsh';uninstall=work/'remove-files.nsh'
    install.write_text('\n'.join(add),encoding='utf-8-sig');uninstall.write_text('\n'.join(remove),encoding='utf-8-sig')
    return install,uninstall


def main():
    from westie_agent import __version__
    parser=argparse.ArgumentParser()
    parser.add_argument('--package',type=Path,default=ROOT/'Release'/('v'+__version__)/'MihuaAgent')
    parser.add_argument('--output',type=Path,default=ROOT/'Release'/('v'+__version__)/'交付文件')
    parser.add_argument('--report',type=Path,default=ROOT/'docs/release-deployment-check.json')
    args=parser.parse_args()
    package=args.package.resolve();output=args.output.resolve()
    release=(ROOT/'Release').resolve()
    if not package.is_relative_to(release) or not output.is_relative_to(release):
        raise ValueError('发布输入与输出必须位于本工程 Release 目录。')
    metadata=json.loads((package/'build-info.json').read_text(encoding='utf-8'))
    version=metadata['version']
    assert re.fullmatch(r'\d+\.\d+\.\d+',version)
    assert metadata['vocabulary_mode']=='spelling' and not metadata['legacy_paper_cards']
    assert metadata['focus_seconds_per_coin'] is None and metadata['focus_completion_bonus']==0
    assert (package/'MihuaAgent.exe').is_file()
    assert not any('before-' in p.name or '.before-' in p.name or '.pending.' in p.name
                   for p in package.iterdir() if p.is_file())
    output.mkdir(parents=True,exist_ok=True)
    work=ROOT/'build'/('windows-exe-v'+version);work.mkdir(parents=True,exist_ok=True)
    compiler=prepare_nsis(ROOT/'build/windows-exe');icon=create_icon(work)
    gcc=shutil.which('gcc');windres=shutil.which('windres')
    if not gcc or not windres:raise RuntimeError('A MinGW Windows compiler is required on the development machine.')
    for name in ('功能介绍.txt','快速开始.txt'):
        source=ROOT/'docs/deployment'/name
        assert source.is_file()
        shutil.copy2(source,package/name)
        shutil.copy2(source,output/name)
    shutil.copy2(ROOT/'USER_GUIDE.txt',package/'使用说明.txt')
    resource=work/'launcher.rc'
    icon_path=str(icon).replace('\\','/')
    numeric=version.replace('.',',')+',0'
    resource.write_text('1 ICON "'+icon_path+'"\n'+(
        '1 VERSIONINFO\nFILEVERSION '+numeric+'\nPRODUCTVERSION '+numeric+'\n'+
        'BEGIN\n BLOCK "StringFileInfo"\n BEGIN\n  BLOCK "080404B0"\n  BEGIN\n'+
        '   VALUE "ProductName", "米花\\0"\n'+
        '   VALUE "FileDescription", "米花桌面助手\\0"\n'+
        '   VALUE "FileVersion", "'+version+'\\0"\n'+
        '  END\n END\n BLOCK "VarFileInfo"\n BEGIN\n'+
        '  VALUE "Translation", 0x804, 1200\n END\nEND\n'),encoding='utf-8')
    obj=work/'launcher-resource.o';native=package/'米花.exe'
    subprocess.run([windres,'--codepage=65001',str(resource),str(obj)],check=True)
    subprocess.run([gcc,'-municode','-mwindows','-static','-s','-O2','-Wall','-Wextra',
                    str(ROOT/'packaging/launcher.c'),str(obj),'-o',str(native),'-luser32',
                    '-Wl,--dynamicbase,--nxcompat'],check=True)
    copying=compiler/'COPYING'
    (package/'licenses').mkdir(exist_ok=True)
    shutil.copy2(copying,package/'licenses/NSIS-COPYING.txt')
    files={p.relative_to(package).as_posix():p for p in package.rglob('*')
           if p.is_file() and p.relative_to(package).parts[0]!='data'}
    add,remove=write_file_manifests(work,files)
    setup=output/('米花安装程序-v'+version+'.exe')
    subprocess.run([str(compiler/'makensis.exe'),'/V2','/INPUTCHARSET','UTF8',
        '/DVERSION='+version,'/DOUTPUT='+str(setup),'/DICON='+str(icon),'/DPACKAGE='+str(package),
        '/DFILES='+str(add),'/DREMOVE_FILES='+str(remove),str(ROOT/'packaging/installer.nsi')],check=True)
    portable=output/('米花-免安装版-v'+version+'.zip')
    zip_root='米花-v'+version
    with zipfile.ZipFile(portable,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in sorted(package.rglob('*')):
            if not path.is_file():continue
            relative=path.relative_to(package)
            if relative.parts[0]=='data' and relative.as_posix()!='data/settings.json':continue
            archive.write(path,Path(zip_root)/relative)
    report={'version':version,'launcher':str(native),'launcher_sha256':sha(native),
        'launcher_bytes':native.stat().st_size,'launcher_target':str(package/'MihuaAgent.exe'),
        'installer':str(setup),'installer_sha256':sha(setup),'installer_bytes':setup.stat().st_size,
        'portable_zip':str(portable),'portable_zip_sha256':sha(portable),
        'portable_zip_bytes':portable.stat().st_size,'portable_zip_root':zip_root,
        'package':str(package),'output':str(output),'work':str(work),
        'compiler':'NSIS '+NSIS_VERSION,'compiler_archive_sha256':NSIS_SHA256,
        'static_files':{k:sha(v) for k,v in files.items()},'defaults_sha256':sha(package/'data/settings.json'),
        'data_bundled':['data/settings.json'],'installation_scope':'current user',
        'python_installation_required':False,'model_api_key_included':False,'tested':False}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'SHA256SUMS.txt').write_text(
        sha(setup)+'  '+setup.name+'\n'+sha(portable)+'  '+portable.name+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='static_files'},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
