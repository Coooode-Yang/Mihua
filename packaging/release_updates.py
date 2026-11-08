"""Offline release administration. Never uploads files or prints private keys."""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import zipfile
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from westie_agent.updates import (UpdateConfig, UpdateError, canonical_json,
                                 file_sha256, read_json, verify_manifest, version_tuple)


def key_password():
    value = os.environ.get('MIHUA_UPDATE_KEY_PASSWORD')
    return value.encode('utf-8') if value else None


def generate_key(private_path: Path):
    private_path = private_path.resolve()
    if private_path.is_relative_to(ROOT):
        raise UpdateError('签名私钥必须保存在项目目录之外。')
    public_path = private_path.with_suffix('.public.txt')
    if private_path.exists() or public_path.exists():
        raise UpdateError('密钥文件已经存在，不会覆盖。')
    private_path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    password = key_password()
    encryption = serialization.BestAvailableEncryption(password) if password else serialization.NoEncryption()
    with private_path.open('xb') as output:
        output.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption))
    public_path.write_text(base64.b64encode(key.public_key().public_bytes_raw()).decode('ascii'), encoding='ascii')
    return public_path


def prepare(package: Path, private_path: Path, output: Path, notes=''):
    with zipfile.ZipFile(package) as archive:
        config = UpdateConfig(**read_json(archive.read('MihuaAgent/update-config.json'))).validate()
        version = read_json(archive.read('MihuaAgent/build-info.json'))['version']
        names = archive.namelist()
        if 'MihuaAgent/MihuaAgent.exe' not in names or 'MihuaAgent/MihuaUpdater.exe' not in names:
            raise UpdateError('软件包缺少程序或更新助手。')
        if any(name.startswith('MihuaAgent/data/') and not name.endswith('/')
               and name != 'MihuaAgent/data/settings.json' for name in names):
            raise UpdateError('发布包包含用户数据或模型，请使用干净的应用更新包。')
    version_tuple(version)
    if not config.enabled:
        raise UpdateError('软件包尚未配置更新仓库和公钥，请先配置后重新打包。')
    key = serialization.load_pem_private_key(private_path.read_bytes(), password=key_password())
    if not isinstance(key, Ed25519PrivateKey):
        raise UpdateError('签名密钥必须是 Ed25519。')
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode('ascii')
    if public != config.public_key:
        raise UpdateError('私钥与软件包内的验证公钥不匹配。')
    manifest = {'schema': 1, 'repository': config.repository, 'platform': 'windows-x64',
                'variant': config.variant, 'version': version, 'asset': package.name,
                'sha256': file_sha256(package), 'size': package.stat().st_size, 'notes': notes}
    envelope = {'manifest': manifest,
                'signature': base64.b64encode(key.sign(canonical_json(manifest))).decode('ascii')}
    raw = canonical_json(envelope)
    verify_manifest(raw, config)
    output.mkdir(parents=True, exist_ok=True)
    result = output / config.manifest_asset
    result.write_bytes(raw)
    return result


def main():
    parser = argparse.ArgumentParser(description='米花 GitHub Releases 更新发布工具（只生成本地文件）')
    commands = parser.add_subparsers(dest='command', required=True)
    keygen = commands.add_parser('keygen')
    keygen.add_argument('--private-key', type=Path, required=True)
    configure = commands.add_parser('configure')
    configure.add_argument('--repository', required=True)
    configure.add_argument('--public-key', type=Path, required=True)
    configure.add_argument('--output', type=Path, default=ROOT / 'update-config.json')
    release = commands.add_parser('prepare')
    release.add_argument('--package', type=Path, required=True)
    release.add_argument('--private-key', type=Path, required=True)
    release.add_argument('--output', type=Path, required=True)
    release.add_argument('--notes-file', type=Path)
    args = parser.parse_args()
    if args.command == 'keygen':
        print('公钥文件：' + str(generate_key(args.private_key)))
        print('私钥仅保存在指定位置，请备份并限制访问；不要上传 GitHub。')
    elif args.command == 'configure':
        config = UpdateConfig(args.repository, args.public_key.read_text(encoding='ascii').strip()).validate()
        args.output.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding='utf-8')
        print('更新配置：' + str(args.output.resolve()))
    else:
        notes = args.notes_file.read_text(encoding='utf-8') if args.notes_file else ''
        print('已生成签名更新信息：' + str(prepare(args.package, args.private_key, args.output, notes)))
        print('请将更新信息与对应 ZIP 一起上传到版本号匹配的 GitHub 正式 Release。')


if __name__ == '__main__':
    main()
