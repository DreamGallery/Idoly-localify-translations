#!/usr/bin/env python3
"""Install the release runtime in user space from checksum-verified official assets.

The first run resolves versions; bootstrap-lock.json and requirements.lock keep
subsequent runs on those versions. No account, signing, or model secrets are read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

SOLIS_REVISION = "a72ee470a4771858f7868a0963bece2e998a43f5"
SOLIS_URL = "https://github.com/vilebbit/SolisClient.git"
GOOGLE_REPOSITORY = "https://dl.google.com/android/repository/"
COMMAND_TOOLS_PACKAGE = "cmdline-tools;16.0"  # Compatible with the pinned JDK 17 runtime.
SDK_PACKAGES = ["platform-tools", "platforms;android-36", "build-tools;36.0.0",
                "ndk;26.3.11579264", "cmake;3.22.1"]
API_REQUIREMENTS = ["grpcio==1.70.0", "protobuf==5.29.3", "pycryptodome==3.23.0",
                    "requests==2.32.5", "sqlcipher3-binary==0.6.0"]


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha(path, algorithm="sha256"):
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def request(url):
    if not url.startswith("https://"):
        raise ValueError("Bootstrap downloads require HTTPS")
    headers = {"User-Agent": "idoly-release-bootstrap", "Accept": "application/json"}
    response = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120)
    if not response.geturl().startswith("https://"):
        response.close()
        raise ValueError("Official download redirected away from HTTPS")
    return response


def metadata(url, limit=32 * 1024 * 1024):
    with request(url) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Official metadata exceeds its size limit")
    return data


def github_asset(repository, pattern):
    api = "https://api.github.com/repos/" + repository + "/releases/latest"
    release = json.loads(metadata(api))
    assets = release["assets"]
    matches = [asset for asset in assets if re.fullmatch(pattern, asset["name"])]
    if len(matches) != 1:
        raise ValueError("Cannot select the official release archive for " + repository)
    asset = matches[0]
    checksum = asset.get("digest", "")
    if checksum and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", checksum):
        digest = checksum.split(":", 1)[1].lower()
    else:
        checksum_assets = [a for a in assets if a["name"] in
                           (asset["name"] + ".sha256", "sha256.sum", "checksums.txt") or
                           a["name"].endswith("_checksums.txt")]
        digest = None
        for check in checksum_assets:
            text = metadata(check["browser_download_url"]).decode("utf-8")
            for line in text.splitlines():
                parts = line.split()
                if parts and re.fullmatch("[0-9a-fA-F]{64}", parts[0]) and (
                        len(parts) == 1 and check["name"] == asset["name"] + ".sha256" or
                        len(parts) >= 2 and parts[-1].lstrip("*") == asset["name"]):
                    digest = parts[0].lower()
            if digest:
                break
        if not digest:
            raise ValueError("Official release has no usable SHA256 checksum: " + repository)
    return {"version": release["tag_name"], "url": asset["browser_download_url"],
            "algorithm": "sha256", "checksum": digest, "metadata_url": api}


def jdk_asset():
    api = ("https://api.adoptium.net/v3/assets/feature_releases/17/ga?"
           "architecture=x64&heap_size=normal&image_type=jdk&jvm_impl=hotspot&os=linux&page_size=1&project=jdk")
    releases = json.loads(metadata(api))
    package = releases[0]["binaries"][0]["package"]
    return {"version": releases[0]["release_name"], "url": package["link"],
            "algorithm": "sha256", "checksum": package["checksum"], "metadata_url": api}


def parse_android_packages(data):
    root = ET.fromstring(data)
    packages = {}
    for package in root.findall("remotePackage"):
        name = package.attrib["path"]
        if name != COMMAND_TOOLS_PACKAGE and name not in SDK_PACKAGES:
            continue
        # Production channels only; never install a preview under a stable path.
        channel = package.find("channelRef")
        if channel is not None and channel.attrib.get("ref") != "channel-0":
            continue
        revision = package.find("revision")
        version = ".".join(revision.findtext(part, "0") for part in ("major", "minor", "micro"))
        for archive in package.findall("archives/archive"):
            host = archive.findtext("host-os")
            if host not in (None, "linux"):
                continue
            complete = archive.find("complete")
            if complete is None:
                continue
            check = complete.find("checksum")
            url = complete.findtext("url")
            algorithm = check.attrib.get("type", "sha1").lower().replace("-", "")
            if algorithm not in ("sha1", "sha256"):
                raise ValueError("Unsupported checksum in Google SDK metadata")
            item = {"version": version, "url": GOOGLE_REPOSITORY + url,
                    "algorithm": algorithm, "checksum": check.text.strip().lower(),
                    "metadata_url": GOOGLE_REPOSITORY + "repository2-3.xml"}
            if name not in packages or tuple(map(int, version.split("."))) > tuple(map(int, packages[name]["version"].split("."))):
                packages[name] = item
    return packages


def resolve_lock(path):
    if path.exists():
        lock = json.loads(path.read_text())
        if lock.get("schema_version") != 1 or lock.get("platform") != "linux-x86_64":
            raise ValueError("Incompatible bootstrap lock")
        return lock
    print("Resolving official tool versions", flush=True)
    google = parse_android_packages(metadata(GOOGLE_REPOSITORY + "repository2-3.xml"))
    wanted = [COMMAND_TOOLS_PACKAGE, *SDK_PACKAGES]
    if any(name not in google for name in wanted):
        raise ValueError("Google metadata is missing a required SDK package")
    lock = {"schema_version": 1, "platform": "linux-x86_64", "python_version": platform.python_version(),
            "uv": github_asset("astral-sh/uv", r"uv-x86_64-unknown-linux-gnu\.tar\.gz"),
            "gh": github_asset("cli/cli", r"gh_[0-9.]+_linux_amd64\.tar\.gz"),
            "jdk": jdk_asset(), "android": {name: google[name] for name in wanted},
            "solis": {"url": SOLIS_URL, "commit": SOLIS_REVISION}}
    atomic_json(path, lock)
    return lock


def download(root, spec):
    algorithm, expected = spec["algorithm"], spec["checksum"]
    if algorithm not in ("sha1", "sha256") or not re.fullmatch(
            "[0-9a-f]{" + str(hashlib.new(algorithm).digest_size * 2) + "}", expected):
        raise ValueError("Malformed download checksum")
    target = root / "downloads" / (expected + ".archive")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and sha(target, algorithm) == expected:
        return target
    temporary = target.with_suffix(".partial")
    try:
        with request(spec["url"]) as response, temporary.open("wb") as output:
            size = 0
            while block := response.read(1024 * 1024):
                size += len(block)
                if size > 5 * 1024**3:
                    raise ValueError("Tool archive exceeds download limit")
                output.write(block)
        if sha(temporary, algorithm) != expected:
            raise ValueError("Official archive checksum mismatch")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def archive_target(root, name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or "\\" in name:
        raise ValueError("Archive path escapes installation directory")
    target = root.joinpath(*relative.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Archive path traverses a link outside installation directory")
    return target


def unpack(archive, destination):
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            entries = source.infolist()
            if sum(entry.file_size for entry in entries) > 12 * 1024**3:
                raise ValueError("Archive expands beyond installation limit")
            links = []
            seen = set()
            for entry in entries:
                target = archive_target(destination, entry.filename)
                if target in seen:
                    raise ValueError("Duplicate archive destination")
                seen.add(target)
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode):
                    if entry.file_size > 4096:
                        raise ValueError("Oversized archive symbolic link")
                    link = source.read(entry).decode("utf-8")
                    if (PurePosixPath(link).is_absolute() or "\\" in link or
                            not (target.parent / link).resolve().is_relative_to(destination.resolve())):
                        raise ValueError("Archive symbolic link escapes installation directory")
                    links.append((target, link))
                    continue
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as stream, target.open("wb") as output:
                    shutil.copyfileobj(stream, output)
                if mode & 0o111:
                    target.chmod(0o755)
            # Install links last, so no regular file can be written through a link.
            for target, link in links:
                if target.exists() or target.is_symlink():
                    raise ValueError("Archive symbolic link conflicts with a directory")
                if not (target.parent / link).resolve().is_relative_to(destination.resolve()):
                    raise ValueError("Archive link chain escapes installation directory")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(link)
    else:
        with tarfile.open(archive) as source:
            entries = source.getmembers()
            if sum(entry.size for entry in entries) > 12 * 1024**3:
                raise ValueError("Archive expands beyond installation limit")
            for entry in entries:
                archive_target(destination, entry.name)
            # Python 3.12's data filter rejects devices and links escaping destination.
            source.extractall(destination, members=entries, filter="data")


def install_archive(root, name, spec):
    destination = root / "packages" / name
    marker = destination / ".bootstrap-complete.json"
    if marker.is_file() and json.loads(marker.read_text()) == spec:
        return destination
    if destination.exists():
        raise ValueError("Existing incomplete/different install requires review: " + name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=name + "-", dir=destination.parent) as directory:
        stage = Path(directory)
        unpack(download(root, spec), stage)
        atomic_json(stage / ".bootstrap-complete.json", spec)
        stage.rename(destination)
    return destination


def executable(directory, name):
    matches = [path for path in directory.rglob(name) if path.is_file() and os.access(path, os.X_OK)]
    if len(matches) != 1:
        raise ValueError("Expected one installed executable: " + name)
    return matches[0]


def run(command, **kwargs):
    subprocess.run([str(part) for part in command], check=True, **kwargs)


def install_python(root, uv, toolkit, project, lock, lock_path):
    inputs = [toolkit / "requirements.txt", project / "tools/requirements-font.txt"]
    if any(not path.is_file() for path in inputs):
        raise ValueError("Toolkit/font requirements are missing")
    text = "\n".join(path.read_text() for path in inputs) + "\n" + "\n".join(API_REQUIREMENTS) + "\n"
    fingerprint = hashlib.sha256(text.encode()).hexdigest()
    requirements = root / "requirements.lock"
    if not requirements.exists() and lock.get("python_requirements_text"):
        requirements.write_text(lock["python_requirements_text"], encoding="utf-8")
    if requirements.exists():
        if lock.get("python_requirements_input_sha256") != fingerprint or lock.get("python_requirements_lock_sha256") != sha(requirements):
            raise ValueError("Python dependency inputs/lock changed; review before replacing the runtime lock")
    else:
        if "python_requirements_lock_sha256" in lock:
            raise ValueError("Locked Python requirements file is missing")
        source = root / "requirements.in"
        source.write_text(text, encoding="utf-8")
        run([uv, "pip", "compile", "--python", sys.executable, "--generate-hashes",
             "--no-emit-index-url", source, "-o", requirements], stdout=subprocess.DEVNULL)
        lock["python_requirements_input_sha256"] = fingerprint
        lock["python_requirements_lock_sha256"] = sha(requirements)
        lock["python_requirements_text"] = requirements.read_text(encoding="utf-8")
        atomic_json(lock_path, lock)
    venv = root / "venv"
    if not (venv / "pyvenv.cfg").exists():
        run([uv, "venv", "--python", sys.executable, venv])
    python = venv / "bin/python"
    run([uv, "pip", "sync", "--python", python, "--require-hashes", requirements])
    run([python, "-c", "import grpc, google.protobuf, Crypto, requests, UnityPy, fontTools; "
         "from sqlcipher3 import dbapi2 as sql; c=sql.connect(':memory:'); "
         "assert c.execute('pragma cipher_version').fetchone(); "
         "print('Python API, resource, font and SQLCipher imports passed')"])
    return python


def install_solis(root, lock):
    target = root / "solis-client"
    spec = lock["solis"]
    if not target.exists():
        with tempfile.TemporaryDirectory(prefix="solis-", dir=root) as temporary:
            stage = Path(temporary)
            run(["git", "init", "-q", stage])
            run(["git", "-C", stage, "remote", "add", "origin", spec["url"]])
            run(["git", "-C", stage, "fetch", "--depth=1", "origin", spec["commit"]])
            run(["git", "-C", stage, "checkout", "--detach", "FETCH_HEAD"])
            stage.rename(target)
    revision = subprocess.check_output(["git", "-C", str(target), "rev-parse", "HEAD"], text=True).strip()
    if revision != spec["commit"]:
        raise ValueError("Solis checkout differs from the pinned commit")
    return target


def install_android(root, lock, java_home):
    sdk = root / "android-sdk"
    sdk.mkdir(exist_ok=True)
    command_tools = install_archive(root, "android-command-tools", lock["android"][COMMAND_TOOLS_PACKAGE])
    sdkmanager = executable(command_tools, "sdkmanager")
    sdk_tools = sdk / "cmdline-tools/latest"
    sdk_tools.parent.mkdir(exist_ok=True)
    if not sdk_tools.exists():
        sdk_tools.symlink_to(sdkmanager.parent.parent, target_is_directory=True)
    env = dict(os.environ, JAVA_HOME=str(java_home), ANDROID_HOME=str(sdk), ANDROID_SDK_ROOT=str(sdk))
    env["PATH"] = str(java_home / "bin") + os.pathsep + env.get("PATH", "")
    # sdkmanager uses Google's standard licenses. Finite input avoids a shell
    # yes pipe/SIGPIPE and licenses are accepted only by this requested bootstrap.
    run([sdkmanager, "--sdk_root=" + str(sdk), "--licenses"], input="y\n" * 100, text=True, env=env)
    # Extract the exact archives from the saved Google metadata: sdkmanager's
    # versionless platform-tools would otherwise drift on the next bootstrap.
    for package in SDK_PACKAGES:
        folder = sdk.joinpath(*package.split(";"))
        spec = lock["android"][package]
        marker = folder / ".bootstrap-complete.json"
        if marker.is_file() and json.loads(marker.read_text()) == spec:
            continue
        if folder.exists():
            raise ValueError("Existing untracked Android package requires review: " + package)
        extracted = install_archive(root, "sdk-" + package.replace(";", "-"), spec)
        children = [path for path in extracted.iterdir() if path.name != ".bootstrap-complete.json"]
        if (extracted / "source.properties").is_file() or (extracted / "bin/cmake").is_file():
            payload = extracted  # Some Google CMake archives have no wrapper directory.
        elif len(children) == 1 and children[0].is_dir():
            payload = children[0]
        else:
            raise ValueError("Unexpected Android archive layout: " + package)
        folder.parent.mkdir(parents=True, exist_ok=True)
        # Both archive verification and unpacking finish before the SDK path appears.
        atomic_json(payload / ".bootstrap-complete.json", spec)
        folder.symlink_to(payload, target_is_directory=True)
    return sdk


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / ".local/share/idoly-release")
    parser.add_argument("--toolkit-dir", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    args = parser.parse_args()
    if sys.platform != "linux" or platform.machine() not in ("x86_64", "AMD64") or sys.version_info[:2] != (3, 12):
        parser.error("Bootstrap requires Linux x86_64 and Python 3.12")
    root = args.root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "bootstrap-lock.json"
    lock = resolve_lock(lock_path)
    uv = executable(install_archive(root, "uv", lock["uv"]), "uv")
    gh = executable(install_archive(root, "github-cli", lock["gh"]), "gh")
    java = executable(install_archive(root, "jdk17", lock["jdk"]), "java")
    java_home = java.parent.parent
    binaries = root / "bin"
    binaries.mkdir(exist_ok=True)
    for name, source in (("uv", uv), ("gh", gh), ("java", java)):
        link = binaries / name
        if link.is_symlink() and link.resolve() == source.resolve():
            continue
        if link.exists() or link.is_symlink():
            raise ValueError("Existing bootstrap executable path differs: " + name)
        link.symlink_to(source)
    python = install_python(root, uv, args.toolkit_dir.resolve(), args.project.resolve(), lock, lock_path)
    solis = install_solis(root, lock)
    run([python, "-c", "import sys; sys.path.insert(0,sys.argv[1]); "
         "import papi_grpc, papi_pb2, pmaster_pb2, ptransaction_pb2; print('Solis protocol imports passed')", solis])
    sdk = install_android(root, lock, java_home)
    run([java, "-version"])
    run([gh, "--version"])
    snippet = {"python": str(python), "api_python": str(python), "solis_dir": str(solis),
               "android_home": str(sdk), "java_home": str(java_home), "bin_dir": str(binaries)}
    atomic_json(root / "installation.json", snippet)
    print(json.dumps({"runtime_root": str(root), "runner_config_fields": snippet,
                      "path_entries": [str(binaries), str(java_home / "bin"), str(sdk / "platform-tools")],
                      "lock_file": str(lock_path)}, indent=2))


if __name__ == "__main__":
    main()
