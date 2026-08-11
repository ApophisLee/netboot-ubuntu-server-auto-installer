#!/usr/bin/env python3
"""Update main.ipxe to the latest supported Ubuntu LTS available in netboot.xyz."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ENDPOINTS_URL = (
    "https://raw.githubusercontent.com/netbootxyz/netboot.xyz/"
    "development/endpoints.yml"
)
RELEASES_URL = (
    "https://cloud-images.ubuntu.com/releases/streams/v1/"
    "com.ubuntu.cloud:released:download.json"
)
USER_AGENT = "netboot-ubuntu-server-auto-installer-updater/1.0"
VERSION_PATTERN = re.compile(r"^\d{2}\.\d{2}(?:\.\d+)?$")
CODENAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
ENDPOINT_PATH_PATTERN = re.compile(
    r"^/ubuntu-squash/releases/download/([A-Za-z0-9._-]+)/$"
)


class UpdateError(RuntimeError):
    """Raised when upstream metadata or the local iPXE file is inconsistent."""


@dataclass(frozen=True)
class Release:
    version: str
    codename: str
    amd64_tag: str
    arm64_tag: str
    flavor: str = "netboot"


def _yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def parse_endpoints(document: str) -> list[dict[str, str]]:
    """Parse the flat endpoint records needed from netboot.xyz's YAML file."""
    endpoint_pattern = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*$")
    field_pattern = re.compile(r"^    ([A-Za-z0-9_-]+):\s*(.*?)\s*$")
    endpoints: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for line in document.splitlines():
        endpoint_match = endpoint_pattern.match(line)
        if endpoint_match:
            if current is not None:
                endpoints.append(current)
            current = {"name": endpoint_match.group(1)}
            continue

        if current is None:
            continue

        field_match = field_pattern.match(line)
        if field_match:
            current[field_match.group(1)] = _yaml_scalar(field_match.group(2))

    if current is not None:
        endpoints.append(current)

    return endpoints


def _tag_from_path(path: str) -> str:
    match = ENDPOINT_PATH_PATTERN.fullmatch(path)
    if not match:
        raise UpdateError(f"unsupported netboot.xyz endpoint path: {path}")
    return match.group(1)


def _version_key(version: str) -> tuple[int, ...]:
    if not VERSION_PATTERN.fullmatch(version):
        raise UpdateError(f"invalid Ubuntu version: {version}")
    return tuple(int(part) for part in version.split("."))


def candidate_releases(document: str) -> list[Release]:
    grouped: dict[tuple[str, str, str], dict[str, dict[str, str]]] = {}

    for endpoint in parse_endpoints(document):
        if endpoint.get("os") != "ubuntu":
            continue
        if endpoint.get("flavor") not in {"netboot", "netboot-hwe"}:
            continue
        if endpoint.get("arch") not in {"amd64", "arm64"}:
            continue

        version = endpoint.get("version", "")
        codename = endpoint.get("codename", "")
        path = endpoint.get("path", "")
        if not VERSION_PATTERN.fullmatch(version):
            continue
        if not CODENAME_PATTERN.fullmatch(codename):
            continue
        series = ".".join(version.split(".")[:2])
        flavor = endpoint["flavor"]
        expected_name = (
            f"ubuntu-netboot-{series}-{endpoint['arch']}"
            if flavor == "netboot"
            else f"ubuntu-netboot-hwe-{series}-{endpoint['arch']}"
        )
        if endpoint.get("name") != expected_name:
            continue
        if endpoint.get("kernel") != expected_name:
            raise UpdateError(f"unexpected kernel name for endpoint {expected_name}")
        tag = _tag_from_path(path)
        if not re.fullmatch(rf"{re.escape(version)}-[0-9a-f]{{8}}", tag):
            raise UpdateError(f"unexpected release tag for endpoint {expected_name}: {tag}")

        key = (version, codename, flavor)
        arch = endpoint["arch"]
        existing = grouped.setdefault(key, {}).get(arch)
        if existing is not None and existing.get("path") != path:
            raise UpdateError(
                f"ambiguous {arch} endpoints for Ubuntu {version} ({codename})"
            )
        grouped[key][arch] = endpoint

    releases: list[Release] = []
    for (version, codename, flavor), architectures in grouped.items():
        if set(architectures) != {"amd64", "arm64"}:
            continue
        releases.append(
            Release(
                version=version,
                codename=codename,
                amd64_tag=_tag_from_path(architectures["amd64"]["path"]),
                arm64_tag=_tag_from_path(architectures["arm64"]["path"]),
                flavor=flavor,
            )
        )

    flavor_priority = {"netboot": 1, "netboot-hwe": 0}
    return sorted(
        releases,
        key=lambda release: (
            _version_key(release.version),
            flavor_priority[release.flavor],
        ),
        reverse=True,
    )


def latest_supported_lts(document: str) -> tuple[str, str]:
    try:
        products = json.loads(document)["products"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise UpdateError("invalid Canonical released image stream") from error
    if not isinstance(products, dict):
        raise UpdateError("Canonical released image stream has invalid products")

    product_pattern = re.compile(
        r"^com\.ubuntu\.cloud:server:(\d{2}\.\d{2}):amd64$"
    )
    supported: list[tuple[str, str]] = []
    for product_id, product in products.items():
        match = product_pattern.fullmatch(product_id)
        if match is None or not isinstance(product, dict):
            continue
        series = match.group(1)
        codename = product.get("release", "")
        if product.get("os") != "ubuntu" or product.get("arch") != "amd64":
            continue
        if product.get("version") != series or product.get("supported") is not True:
            continue
        if product.get("release_title") != f"{series} LTS":
            continue
        if not CODENAME_PATTERN.fullmatch(codename):
            raise UpdateError(f"invalid Ubuntu codename in released stream: {codename}")
        supported.append((series, codename))

    if not supported:
        raise UpdateError("Canonical released image stream has no supported Ubuntu LTS")
    return max(supported, key=lambda release: _version_key(release[0]))


def release_urls(release: Release) -> tuple[list[str], list[str]]:
    iso_urls = [
        (
            f"https://releases.ubuntu.com/{release.codename}/"
            f"ubuntu-{release.version}-live-server-amd64.iso"
        ),
        (
            "https://cdimage.ubuntu.com/releases/"
            f"{release.version}/release/"
            f"ubuntu-{release.version}-live-server-arm64.iso"
        ),
    ]
    asset_urls = [
        (
            "https://github.com/netbootxyz/ubuntu-squash/releases/download/"
            f"{release.amd64_tag}/{asset}"
        )
        for asset in ("vmlinuz", "initrd")
    ]
    asset_urls.extend(
        (
            "https://github.com/netbootxyz/ubuntu-squash/releases/download/"
            f"{release.arm64_tag}/{asset}"
        )
        for asset in ("vmlinuz", "initrd")
    )
    return iso_urls, asset_urls


def url_is_available(url: str, attempts: int = 3) -> bool:
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": USER_AGENT},
    )

    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return 200 <= response.status < 400
        except urllib.error.HTTPError as error:
            if 400 <= error.code < 500 and error.code != 429:
                return False
        except (TimeoutError, urllib.error.URLError):
            pass

        if attempt + 1 < attempts:
            time.sleep(2**attempt)

    return False


def select_latest_release(
    endpoints_document: str,
    releases_document: str,
    probe: Callable[[str], bool] = url_is_available,
) -> Release:
    series, codename = latest_supported_lts(releases_document)
    releases = [
        release
        for release in candidate_releases(endpoints_document)
        if release.codename == codename
        and ".".join(release.version.split(".")[:2]) == series
    ]
    if not releases:
        raise UpdateError(
            f"netboot.xyz has no paired amd64/arm64 endpoints for Ubuntu {series}"
        )

    release = releases[0]
    iso_urls, asset_urls = release_urls(release)
    unavailable_isos = [url for url in iso_urls if not probe(url)]
    if unavailable_isos:
        raise UpdateError(
            "latest supported Ubuntu LTS has unavailable official ISOs: "
            + ", ".join(unavailable_isos)
        )
    unavailable_assets = [url for url in asset_urls if not probe(url)]
    if unavailable_assets:
        raise UpdateError(
            "latest supported Ubuntu LTS has unavailable netboot assets: "
            + ", ".join(unavailable_assets)
        )
    return release


def current_version(document: str) -> str:
    versions = set(re.findall(r"(?m)^set version_number (\S+)$", document))
    if len(versions) != 1:
        raise UpdateError("main.ipxe must contain one consistent version_number")
    version = versions.pop()
    _version_key(version)
    return version


def update_main_ipxe(document: str, release: Release) -> str:
    if not CODENAME_PATTERN.fullmatch(release.codename):
        raise UpdateError(f"invalid Ubuntu codename: {release.codename}")
    expected_tag = re.compile(rf"^{re.escape(release.version)}-[0-9a-f]{{8}}$")
    for tag in (release.amd64_tag, release.arm64_tag):
        if not expected_tag.fullmatch(tag):
            raise UpdateError(f"invalid netboot.xyz release tag: {tag}")

    selector_match = re.search(r"(?m)^set ubuntu_version ([a-z][a-z0-9-]*)$", document)
    if selector_match is None:
        raise UpdateError("main.ipxe is missing a valid ubuntu_version selector")
    old_codename = selector_match.group(1)

    updated, selector_count = re.subn(
        r"(?m)^set ubuntu_version [a-z][a-z0-9-]*$",
        f"set ubuntu_version {release.codename}",
        document,
        count=1,
    )
    if selector_count != 1:
        raise UpdateError("failed to update the ubuntu_version selector")

    for arch, tag in (
        ("amd64", release.amd64_tag),
        ("arm64", release.arm64_tag),
    ):
        block_pattern = re.compile(
            rf"(?m)^:{re.escape(old_codename)}_{arch}\n"
            r"set kernel_url \$\{live_endpoint\}/ubuntu-squash/releases/download/"
            r"[A-Za-z0-9._-]+/\n"
            r"set codename [a-z][a-z0-9-]*\n"
            r"set version_number \d{2}\.\d{2}(?:\.\d+)?$"
        )
        replacement = (
            f":{release.codename}_{arch}\n"
            "set kernel_url ${live_endpoint}/ubuntu-squash/releases/download/"
            f"{tag}/\n"
            f"set codename {release.codename}\n"
            f"set version_number {release.version}"
        )
        updated, block_count = block_pattern.subn(replacement, updated, count=1)
        if block_count != 1:
            raise UpdateError(f"failed to update the {arch} Ubuntu boot block")

    return updated


def fetch_document(url: str, name: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8")
    except (TimeoutError, urllib.error.URLError) as error:
        raise UpdateError(f"failed to download {name}: {error}") from error


def write_github_output(path: str | None, release: Release, changed: bool) -> None:
    if path is None:
        return
    output = Path(path)
    with output.open("a", encoding="utf-8") as stream:
        stream.write(f"changed={'true' if changed else 'false'}\n")
        stream.write(f"version={release.version}\n")
        stream.write(f"codename={release.codename}\n")
        stream.write(f"amd64_tag={release.amd64_tag}\n")
        stream.write(f"arm64_tag={release.arm64_tag}\n")


def run(args: argparse.Namespace) -> int:
    main_ipxe = Path(args.main_ipxe)
    original = main_ipxe.read_text(encoding="utf-8")
    installed_version = current_version(original)
    endpoints = fetch_document(args.endpoints_url, "netboot.xyz endpoints")
    releases = fetch_document(args.releases_url, "Canonical released image stream")
    release = select_latest_release(endpoints, releases)

    if _version_key(release.version) < _version_key(installed_version):
        raise UpdateError(
            f"refusing to downgrade Ubuntu {installed_version} to {release.version}"
        )

    updated = update_main_ipxe(original, release)
    changed = updated != original
    if changed:
        main_ipxe.write_text(updated, encoding="utf-8")

    write_github_output(args.github_output, release, changed)
    status = "updated" if changed else "already current"
    print(
        f"Ubuntu {release.version} ({release.codename}) is {status}; "
        f"amd64={release.amd64_tag}, arm64={release.arm64_tag}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main-ipxe", default="main.ipxe")
    parser.add_argument("--endpoints-url", default=ENDPOINTS_URL)
    parser.add_argument("--releases-url", default=RELEASES_URL)
    parser.add_argument("--github-output")
    args = parser.parse_args()

    try:
        return run(args)
    except (OSError, UpdateError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
