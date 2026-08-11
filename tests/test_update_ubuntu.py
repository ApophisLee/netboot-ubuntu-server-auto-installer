import json
import unittest

from scripts.update_ubuntu import (
    Release,
    UpdateError,
    candidate_releases,
    current_version,
    latest_supported_lts,
    select_latest_release,
    update_main_ipxe,
)


ENDPOINTS = """\
endpoints:
  ubuntu-netboot-26.04-amd64:
    path: /ubuntu-squash/releases/download/26.04-aaaaaaaa/
    os: ubuntu
    version: '26.04'
    codename: resolute
    flavor: netboot
    kernel: ubuntu-netboot-26.04-amd64
    arch: amd64
  ubuntu-netboot-26.04-arm64:
    path: /ubuntu-squash/releases/download/26.04-bbbbbbbb/
    os: ubuntu
    version: '26.04'
    codename: resolute
    flavor: netboot
    kernel: ubuntu-netboot-26.04-arm64
    arch: arm64
  ubuntu-netboot-26.10-amd64:
    path: /ubuntu-squash/releases/download/26.10-cccccccc/
    os: ubuntu
    version: '26.10'
    codename: stonking
    flavor: netboot
    kernel: ubuntu-netboot-26.10-amd64
    arch: amd64
  ubuntu-netboot-26.10-arm64:
    path: /ubuntu-squash/releases/download/26.10-dddddddd/
    os: ubuntu
    version: '26.10'
    codename: stonking
    flavor: netboot
    kernel: ubuntu-netboot-26.10-arm64
    arch: arm64
"""

HWE_ENDPOINTS = """\
  ubuntu-netboot-hwe-26.04-amd64:
    path: /ubuntu-squash/releases/download/26.04-eeeeeeee/
    os: ubuntu
    version: '26.04'
    codename: resolute
    flavor: netboot-hwe
    kernel: ubuntu-netboot-hwe-26.04-amd64
    arch: amd64
  ubuntu-netboot-hwe-26.04-arm64:
    path: /ubuntu-squash/releases/download/26.04-ffffffff/
    os: ubuntu
    version: '26.04'
    codename: resolute
    flavor: netboot-hwe
    kernel: ubuntu-netboot-hwe-26.04-arm64
    arch: arm64
"""

RELEASES = json.dumps(
    {
        "products": {
            "com.ubuntu.cloud:server:24.04:amd64": {
                "arch": "amd64",
                "os": "ubuntu",
                "release": "noble",
                "release_title": "24.04 LTS",
                "supported": True,
                "version": "24.04",
            },
            "com.ubuntu.cloud:server:26.04:amd64": {
                "arch": "amd64",
                "os": "ubuntu",
                "release": "resolute",
                "release_title": "26.04 LTS",
                "supported": True,
                "version": "26.04",
            },
            "com.ubuntu.cloud:server:26.10:amd64": {
                "arch": "amd64",
                "os": "ubuntu",
                "release": "stonking",
                "release_title": "26.10",
                "supported": True,
                "version": "26.10",
            },
        }
    }
)

MAIN_IPXE = """\
#!ipxe
set live_endpoint https://github.com/netbootxyz
set ubuntu_version noble
goto ${ubuntu_version}_${os_arch}

:noble_amd64
set kernel_url ${live_endpoint}/ubuntu-squash/releases/download/24.04-amd64-tag/
set codename noble
set version_number 24.04.4
goto sub_boot
:noble_arm64
set kernel_url ${live_endpoint}/ubuntu-squash/releases/download/24.04-arm64-tag/
set codename noble
set version_number 24.04.4
goto sub_boot
"""


class UpdateUbuntuTests(unittest.TestCase):
    def test_candidates_are_sorted_by_version(self):
        releases = candidate_releases(ENDPOINTS)
        self.assertEqual([release.version for release in releases], ["26.10", "26.04"])

    def test_standard_netboot_is_preferred_when_hwe_has_the_same_version(self):
        releases = candidate_releases(ENDPOINTS + HWE_ENDPOINTS)
        resolute = [release for release in releases if release.version == "26.04"]
        self.assertEqual([release.flavor for release in resolute], ["netboot", "netboot-hwe"])

    def test_latest_supported_lts_excludes_interim_releases(self):
        self.assertEqual(latest_supported_lts(RELEASES), ("26.04", "resolute"))

    def test_selects_the_latest_supported_lts_even_when_interim_urls_exist(self):
        release = select_latest_release(ENDPOINTS, RELEASES, probe=lambda _url: True)
        self.assertEqual(release.version, "26.04")
        self.assertEqual(release.codename, "resolute")

    def test_point_release_is_preferred_within_the_supported_lts_series(self):
        point_release = (
            ENDPOINTS.replace("/26.04-aaaaaaaa/", "/26.04.1-aaaaaaaa/")
            .replace("/26.04-bbbbbbbb/", "/26.04.1-bbbbbbbb/")
            .replace("version: '26.04'", "version: 26.04.1", 2)
        )
        release = select_latest_release(point_release, RELEASES, probe=lambda _url: True)
        self.assertEqual(release.version, "26.04.1")

    def test_updates_both_architectures_and_is_idempotent(self):
        release = Release(
            version="26.04",
            codename="resolute",
            amd64_tag="26.04-22041617",
            arm64_tag="26.04-c15b14d8",
        )
        updated = update_main_ipxe(MAIN_IPXE, release)

        self.assertIn("set ubuntu_version resolute", updated)
        self.assertIn(":resolute_amd64", updated)
        self.assertIn(":resolute_arm64", updated)
        self.assertIn("26.04-22041617", updated)
        self.assertIn("26.04-c15b14d8", updated)
        self.assertEqual(current_version(updated), "26.04")
        self.assertEqual(update_main_ipxe(updated, release), updated)

    def test_unpaired_architecture_is_rejected(self):
        only_amd64 = ENDPOINTS.split("  ubuntu-netboot-26.04-arm64:", 1)[0]
        with self.assertRaises(UpdateError):
            select_latest_release(only_amd64, RELEASES, probe=lambda _url: True)

    def test_missing_asset_does_not_fall_back_to_an_older_lts(self):
        with self.assertRaises(UpdateError):
            select_latest_release(
                ENDPOINTS,
                RELEASES,
                probe=lambda url: "26.04-aaaaaaaa/vmlinuz" not in url,
            )

    def test_release_tag_must_match_the_declared_version(self):
        mismatched = ENDPOINTS.replace(
            "/26.04-aaaaaaaa/", "/24.04.4-aaaaaaaa/", 1
        )
        with self.assertRaises(UpdateError):
            candidate_releases(mismatched)


if __name__ == "__main__":
    unittest.main()
