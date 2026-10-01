#!/usr/bin/env python3
"""Materialize explicitly supplied private bootstrap data without logging values."""
from __future__ import annotations
import argparse
import base64
import configparser
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import urllib.request
import zipfile

FONT_URL = 'https://github.com/DreamGallery/Idoly-localify-translations/releases/download/v0.2.0/idoly-localify-0.2.0.apk'
APK_SHA = 'a3b294b66263f9b913f60c9be6e290122e443213f20900d360c46d50b18b9437'
FONT_SHA = '1638f83e42a7f0e5c807fc4fdee0fb6469b95166c9b1668176d694bd5fb8a8ca'
FONT_ENTRY = 'assets/idoly-localify/resource-han-rounded.bundle'

def write_private(path: Path, data: bytes, preserve=False):
    if path.is_symlink(): raise ValueError('Private configuration path is a symlink')
    if preserve and path.exists():
        path.chmod(0o600)
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)

def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()

def configure(root: Path, toolkit: Path, encoded: str):
    package = json.loads(base64.b64decode(encoded, validate=True))
    if not isinstance(package, dict) or package.get('schema_version') != 1:
        raise ValueError('Unsupported bootstrap data schema')
    account = package['notice_account']
    if account.get('schema') != 1 or account.get('purpose') != 'dedicated_notice_collector':
        raise ValueError('Only a dedicated notice collector is accepted')
    installation = json.loads((root / 'installation.json').read_text())
    config = root / 'config'
    if config.is_symlink(): raise ValueError('Private configuration directory is a symlink')
    config.mkdir(parents=True, exist_ok=True)
    config.chmod(0o700)
    for field, filename in [('octo_settings','octo-settings.json'),
                            ('firebase_settings','firebase-settings.json'),
                            ('notice_account','collector-account.json')]:
        if not isinstance(package[field], dict): raise ValueError('Invalid bootstrap configuration')
        # Preserve refreshed tokens and operator-updated settings on reruns.
        write_private(config / filename, json_bytes(package[field]), preserve=True)
    keystore = base64.b64decode(package['keystore'], validate=True)
    key_path = config / 'idoly-localify.jks'
    if key_path.exists() and key_path.read_bytes() != keystore:
        raise ValueError('Existing signing key differs; refusing to change APK identity')
    write_private(key_path, keystore, preserve=True)
    signing = package['signing']
    expected = {'IDOLY_KEYSTORE_PASSWORD','IDOLY_KEY_ALIAS','IDOLY_KEY_PASSWORD'}
    if set(signing) != expected or any(not isinstance(v,str) or not v for v in signing.values()):
        raise ValueError('Invalid signing configuration')
    signing['IDOLY_KEYSTORE_FILE'] = str(key_path)
    write_private(config / 'signing.json', json_bytes(signing), preserve=True)
    settings = configparser.ConfigParser()
    settings.read(toolkit / 'config.example.ini')
    for key in ('FILE_KEY','FILE_IV','KR_FILE_KEY'):
        settings.set('Decryption settings',key,package['decryption'].get(key,''))
    buffer = io.StringIO()
    settings.write(buffer)
    write_private(config / 'toolkit.ini', buffer.getvalue().encode(), preserve=True)
    font = config / 'resource-han-rounded.bundle'
    if not font.exists():
        with urllib.request.urlopen(FONT_URL, timeout=120) as response:
            apk = response.read(128 * 1024 * 1024 + 1)
        if hashlib.sha256(apk).hexdigest() != APK_SHA:
            raise ValueError('Bootstrap APK digest mismatch')
        with zipfile.ZipFile(io.BytesIO(apk)) as archive:
            if archive.getinfo(FONT_ENTRY).file_size > 64 * 1024 * 1024:
                raise ValueError('Bootstrap font exceeds expected size')
            data = archive.read(FONT_ENTRY)
        if hashlib.sha256(data).hexdigest() != FONT_SHA: raise ValueError('Bootstrap font digest mismatch')
        write_private(font,data)
    if font.is_symlink() or hashlib.sha256(font.read_bytes()).hexdigest() != FONT_SHA:
        raise ValueError('Existing bootstrap font differs')
    value = {key:installation[key] for key in ('python','api_python','java_home','android_home','solis_dir','bin_dir')}
    value.update(work_dir=str(root/'cache/release'),toolkit_config=str(config/'toolkit.ini'),
        octo_settings=str(config/'octo-settings.json'),firebase_settings=str(config/'firebase-settings.json'),
        notice_account=str(config/'collector-account.json'),font_bundle=str(font),
        signing_env_file=str(config/'signing.json'),app_version='6.0.2',initialize_collector_day=True)
    path = config / 'runner.json'
    write_private(path,json_bytes(value),preserve=True)
    return path, installation

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path.home()/'.local/share/idoly-release')
    parser.add_argument('--toolkit-dir',type=Path,required=True)
    args=parser.parse_args()
    encoded=os.environ.pop('BOOTSTRAP_PRIVATE_B64','')
    if not encoded: raise ValueError('BOOTSTRAP_PRIVATE_B64 is not configured')
    path, installation=configure(args.root.resolve(),args.toolkit_dir.resolve(),encoded)
    if os.environ.get('GITHUB_PATH'):
        with open(os.environ['GITHUB_PATH'],'a') as stream:
            stream.write(installation['bin_dir']+'\n'+str(Path(installation['java_home'])/'bin')+'\n')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as stream: stream.write('config='+str(path)+'\n')
    print('Runner configuration ready: '+str(path))

if __name__ == '__main__':
    try: main()
    except Exception as error:
        # Never echo malformed credentials, token values or signing dictionaries.
        raise SystemExit('Private runner configuration failed ('+type(error).__name__+').') from None
