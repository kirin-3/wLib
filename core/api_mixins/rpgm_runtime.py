# SPDX-License-Identifier: GPL-3.0-or-later
"""RPG Maker RTP and dependency installs into Wine prefixes."""
from __future__ import annotations

import os
import ssl
import threading
from contextlib import suppress
from typing import cast

from core.host_platform import (
    get_rtp_dir,
    host_command,
    host_which,
    is_linux,
    unsupported_on_host,
)
from core.api_mixins.base import ApiBase
from core.api_mixins.types import (
    ProgressStatus,
    URLResponseContext,
)

RTP_DOWNLOADS_PAGE_URL = "https://www.rpgmakerweb.com/run-time-package"
KOMODO_RTP_DOWNLOAD_HOSTS = {"dl.komodo.jp"}
# dl.komodo.jp omits its intermediate. Since its May 2026 renewal the leaf is issued by
# "Sectigo Public Server Authentication CA DV R36"; the older "Sectigo RSA Domain
# Validation Secure Server CA" stays in case the host switches back.
KOMODO_SECTIGO_INTERMEDIATE_CERT_PEM = """-----BEGIN CERTIFICATE-----
MIIGTDCCBDSgAwIBAgIQOXpmzCdWNi4NqofKbqvjsTANBgkqhkiG9w0BAQwFADBf
MQswCQYDVQQGEwJHQjEYMBYGA1UEChMPU2VjdGlnbyBMaW1pdGVkMTYwNAYDVQQD
Ey1TZWN0aWdvIFB1YmxpYyBTZXJ2ZXIgQXV0aGVudGljYXRpb24gUm9vdCBSNDYw
HhcNMjEwMzIyMDAwMDAwWhcNMzYwMzIxMjM1OTU5WjBgMQswCQYDVQQGEwJHQjEY
MBYGA1UEChMPU2VjdGlnbyBMaW1pdGVkMTcwNQYDVQQDEy5TZWN0aWdvIFB1Ymxp
YyBTZXJ2ZXIgQXV0aGVudGljYXRpb24gQ0EgRFYgUjM2MIIBojANBgkqhkiG9w0B
AQEFAAOCAY8AMIIBigKCAYEAljZf2HIz7+SPUPQCQObZYcrxLTHYdf1ZtMRe7Yeq
RPSwygz16qJ9cAWtWNTcuICc++p8Dct7zNGxCpqmEtqifO7NvuB5dEVexXn9RFFH
12Hm+NtPRQgXIFjx6MSJcNWuVO3XGE57L1mHlcQYj+g4hny90aFh2SCZCDEVkAja
EMMfYPKuCjHuuF+bzHFb/9gV8P9+ekcHENF2nR1efGWSKwnfG5RawlkaQDpRtZTm
M64TIsv/r7cyFO4nSjs1jLdXYdz5q3a4L0NoabZfbdxVb+CUEHfB0bpulZQtH1Rv
38e/lIdP7OTTIlZh6OYL6NhxP8So0/sht/4J9mqIGxRFc0/pC8suja+wcIUna0HB
pXKfXTKpzgis+zmXDL06ASJf5E4A2/m+Hp6b84sfPAwQ766rI65mh50S0Di9E3Pn
2WcaJc+PILsBmYpgtmgWTR9eV9otfKRUBfzHUHcVgarub/XluEpRlTtZudU5xbFN
xx/DgMrXLUAPaI60fZ6wA+PTAgMBAAGjggGBMIIBfTAfBgNVHSMEGDAWgBRWc1hk
lfmSGrASKgRieaFAFYghSTAdBgNVHQ4EFgQUaMASFhgOr872h6YyV6NGUV3LBycw
DgYDVR0PAQH/BAQDAgGGMBIGA1UdEwEB/wQIMAYBAf8CAQAwHQYDVR0lBBYwFAYI
KwYBBQUHAwEGCCsGAQUFBwMCMBsGA1UdIAQUMBIwBgYEVR0gADAIBgZngQwBAgEw
VAYDVR0fBE0wSzBJoEegRYZDaHR0cDovL2NybC5zZWN0aWdvLmNvbS9TZWN0aWdv
UHVibGljU2VydmVyQXV0aGVudGljYXRpb25Sb290UjQ2LmNybDCBhAYIKwYBBQUH
AQEEeDB2ME8GCCsGAQUFBzAChkNodHRwOi8vY3J0LnNlY3RpZ28uY29tL1NlY3Rp
Z29QdWJsaWNTZXJ2ZXJBdXRoZW50aWNhdGlvblJvb3RSNDYucDdjMCMGCCsGAQUF
BzABhhdodHRwOi8vb2NzcC5zZWN0aWdvLmNvbTANBgkqhkiG9w0BAQwFAAOCAgEA
YtOC9Fy+TqECFw40IospI92kLGgoSZGPOSQXMBqmsGWZUQ7rux7cj1du6d9rD6C8
ze1B2eQjkrGkIL/OF1s7vSmgYVafsRoZd/IHUrkoQvX8FZwUsmPu7amgBfaY3g+d
q1x0jNGKb6I6Bzdl6LgMD9qxp+3i7GQOnd9J8LFSietY6Z4jUBzVoOoz8iAU84OF
h2HhAuiPw1ai0VnY38RTI+8kepGWVfGxfBWzwH9uIjeooIeaosVFvE8cmYUB4TSH
5dUyD0jHct2+8ceKEtIoFU/FfHq/mDaVnvcDCZXtIgitdMFQdMZaVehmObyhRdDD
4NQCs0gaI9AAgFj4L9QtkARzhQLNyRf87Kln+YU0lgCGr9HLg3rGO8q+Y4ppLsOd
unQZ6ZxPNGIfOApbPVf5hCe58EZwiWdHIMn9lPP6+F404y8NNugbQixBber+x536
WrZhFZLjEkhp7fFXf9r32rNPfb74X/U90Bdy4lzp3+X1ukh1BuMxA/EEhDoTOS3l
7ABvc7BYSQubQ2490OcdkIzUh3ZwDrakMVrbaTxUM2p24N6dB+ns2zptWCva6jzW
r8IWKIMxzxLPv5Kt3ePKcUdvkBU/smqujSczTzzSjIoR5QqQA6lN1ZRSnuHIWCvh
JEltkYnTAH41QJ6SAWO66GrrUESwN/cgZzL4JLEqz1Y=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIGEzCCA/ugAwIBAgIQfVtRJrR2uhHbdBYLvFMNpzANBgkqhkiG9w0BAQwFADCB
iDELMAkGA1UEBhMCVVMxEzARBgNVBAgTCk5ldyBKZXJzZXkxFDASBgNVBAcTC0pl
cnNleSBDaXR5MR4wHAYDVQQKExVUaGUgVVNFUlRSVVNUIE5ldHdvcmsxLjAsBgNV
BAMTJVVTRVJUcnVzdCBSU0EgQ2VydGlmaWNhdGlvbiBBdXRob3JpdHkwHhcNMTgx
MTAyMDAwMDAwWhcNMzAxMjMxMjM1OTU5WjCBjzELMAkGA1UEBhMCR0IxGzAZBgNV
BAgTEkdyZWF0ZXIgTWFuY2hlc3RlcjEQMA4GA1UEBxMHU2FsZm9yZDEYMBYGA1UE
ChMPU2VjdGlnbyBMaW1pdGVkMTcwNQYDVQQDEy5TZWN0aWdvIFJTQSBEb21haW4g
VmFsaWRhdGlvbiBTZWN1cmUgU2VydmVyIENBMIIBIjANBgkqhkiG9w0BAQEFAAOC
AQ8AMIIBCgKCAQEA1nMz1tc8INAA0hdFuNY+B6I/x0HuMjDJsGz99J/LEpgPLT+N
TQEMgg8Xf2Iu6bhIefsWg06t1zIlk7cHv7lQP6lMw0Aq6Tn/2YHKHxYyQdqAJrkj
eocgHuP/IJo8lURvh3UGkEC0MpMWCRAIIz7S3YcPb11RFGoKacVPAXJpz9OTTG0E
oKMbgn6xmrntxZ7FN3ifmgg0+1YuWMQJDgZkW7w33PGfKGioVrCSo1yfu4iYCBsk
Haswha6vsC6eep3BwEIc4gLw6uBK0u+QDrTBQBbwb4VCSmT3pDCg/r8uoydajotY
uK3DGReEY+1vVv2Dy2A0xHS+5p3b4eTlygxfFQIDAQABo4IBbjCCAWowHwYDVR0j
BBgwFoAUU3m/WqorSs9UgOHYm8Cd8rIDZsswHQYDVR0OBBYEFI2MXsRUrYrhd+mb
+ZsF4bgBjWHhMA4GA1UdDwEB/wQEAwIBhjASBgNVHRMBAf8ECDAGAQH/AgEAMB0G
A1UdJQQWMBQGCCsGAQUFBwMBBggrBgEFBQcDAjAbBgNVHSAEFDASMAYGBFUdIAAw
CAYGZ4EMAQIBMFAGA1UdHwRJMEcwRaBDoEGGP2h0dHA6Ly9jcmwudXNlcnRydXN0
LmNvbS9VU0VSVHJ1c3RSU0FDZXJ0aWZpY2F0aW9uQXV0aG9yaXR5LmNybDB2Bggr
BgEFBQcBAQRqMGgwPwYIKwYBBQUHMAKGM2h0dHA6Ly9jcnQudXNlcnRydXN0LmNv
bS9VU0VSVHJ1c3RSU0FBZGRUcnVzdENBLmNydDAlBggrBgEFBQcwAYYZaHR0cDov
L29jc3AudXNlcnRydXN0LmNvbTANBgkqhkiG9w0BAQwFAAOCAgEAMr9hvQ5Iw0/H
ukdN+Jx4GQHcEx2Ab/zDcLRSmjEzmldS+zGea6TvVKqJjUAXaPgREHzSyrHxVYbH
7rM2kYb2OVG/Rr8PoLq0935JxCo2F57kaDl6r5ROVm+yezu/Coa9zcV3HAO4OLGi
H19+24rcRki2aArPsrW04jTkZ6k4Zgle0rj8nSg6F0AnwnJOKf0hPHzPE/uWLMUx
RP0T7dWbqWlod3zu4f+k+TY4CFM5ooQ0nBnzvg6s1SQ36yOoeNDT5++SR2RiOSLv
xvcRviKFxmZEJCaOEDKNyJOuB56DPi/Z+fVGjmO+wea03KbNIaiGCpXZLoUmGv38
sbZXQm2V0TP2ORQGgkE49Y9Y3IBbpNV9lXj9p5v//cWoaasm56ekBYdbqbe4oyAL
l6lFhd2zi+WJN44pDfwGF/Y4QA5C5BIG+3vzxhFoYt/jmPQT2BVPi7Fp2RBgvGQq
6jG35LWjOhSbJuMLe/0CjraZwTiXWTb2qHSihrZe68Zk6s+go/lunrotEbaGmAhY
LcmsJWTyXnW0OMGuf1pGg+pRyrbxmRE1a6Vqe8YAsOf4vmSyrcjC8azjUeqkk+B5
yOGBQMkKW+ESPMFgKuOXwIlCypTPRpgSabuY0MLTDXJLR27lk8QyKGOHQ+SwMj4K
00u/I5sUKUErmgQfky3xxzlIPK1aEn8=
-----END CERTIFICATE-----
"""


class RpgmRuntimeMixin(ApiBase):
    def _create_download_ssl_context(
        self, url: str, *, include_komodo_intermediate: bool = False
    ) -> ssl.SSLContext:
        import urllib.parse

        cafile = (os.environ.get("SSL_CERT_FILE") or "").strip()
        if cafile and not os.path.isfile(cafile):
            cafile = ""

        context = ssl.create_default_context(cafile=cafile or None)

        hostname = (urllib.parse.urlparse(url).hostname or "").lower()
        if include_komodo_intermediate and hostname in KOMODO_RTP_DOWNLOAD_HOSTS:
            context.load_verify_locations(cadata=KOMODO_SECTIGO_INTERMEDIATE_CERT_PEM)

        return context

    def _is_tls_verification_error(self, error: BaseException) -> bool:
        import ssl
        import urllib.error

        if isinstance(error, ssl.SSLCertVerificationError):
            return True

        if isinstance(error, urllib.error.URLError):
            reason = error.reason
            if isinstance(reason, ssl.SSLCertVerificationError):
                return True

        return "CERTIFICATE_VERIFY_FAILED" in str(error)

    def _open_url_with_targeted_tls_fallback(
        self, request: object, timeout: int = 30
    ) -> URLResponseContext:
        import urllib.error
        import urllib.parse
        import urllib.request

        request_obj: urllib.request.Request | str = cast(
            urllib.request.Request | str, request
        )
        request_url = str(getattr(request_obj, "full_url", request_obj))
        hostname = (urllib.parse.urlparse(request_url).hostname or "").lower()
        default_context = self._create_download_ssl_context(request_url)

        try:
            return cast(
                URLResponseContext,
                urllib.request.urlopen(
                    request_obj, context=default_context, timeout=timeout
                ),
            )
        except urllib.error.URLError as error:
            if hostname not in KOMODO_RTP_DOWNLOAD_HOSTS:
                raise

            if not self._is_tls_verification_error(error):
                raise

            print(
                f"Retrying {request_url} with bundled Sectigo intermediate certificate after TLS verification failure."
            )
            fallback_context = self._create_download_ssl_context(
                request_url, include_komodo_intermediate=True
            )
            return cast(
                URLResponseContext,
                urllib.request.urlopen(
                    request_obj, context=fallback_context, timeout=timeout
                ),
            )

    def _format_rtp_download_error(self, url: str, error: BaseException) -> str:
        import urllib.parse

        hostname = (urllib.parse.urlparse(url).hostname or "").lower()
        if hostname in KOMODO_RTP_DOWNLOAD_HOSTS and self._is_tls_verification_error(
            error
        ):
            return (
                "download failed because the official RPG Maker file host is serving an incomplete TLS certificate chain. "
                f"wLib retried with the required Sectigo intermediate certificate but the download still could not be verified. "
                f"Download it manually from {RTP_DOWNLOADS_PAGE_URL}."
            )

        return f"download failed ({error})"

    def _get_rtp_packages(self) -> list[dict[str, object]]:
        return [
            {
                "name": "VX Ace",
                "url": "https://dl.komodo.jp/rpgmakerweb/run-time-packages/RPGVXAce_RTP.zip",
                "filename": "vxace_rtp.zip",
                "sentinels": (
                    "drive_c/windows/system32/RGSS300.dll",
                    "drive_c/windows/system32/RGSS301.dll",
                    "drive_c/Program Files/Common Files/Enterbrain/RGSS3/RPGVXAce",
                    "drive_c/Program Files (x86)/Common Files/Enterbrain/RGSS3/RPGVXAce",
                ),
            },
            {
                "name": "VX",
                "url": "https://dl.komodo.jp/rpgmakerweb/run-time-packages/vx_rtp102e.zip",
                "filename": "vx_rtp.zip",
                "sentinels": (
                    "drive_c/windows/system32/RGSS202E.dll",
                    "drive_c/Program Files/Common Files/Enterbrain/RGSS2/RPGVX",
                    "drive_c/Program Files (x86)/Common Files/Enterbrain/RGSS2/RPGVX",
                ),
            },
            {
                "name": "XP",
                "url": "https://dl.komodo.jp/rpgmakerweb/run-time-packages/xp_rtp104e.exe",
                "filename": "xp_rtp104e.exe",
                "sentinels": (
                    "drive_c/windows/system32/RGSS104E.dll",
                    "drive_c/Program Files/Common Files/Enterbrain/RGSS/Standard",
                    "drive_c/Program Files (x86)/Common Files/Enterbrain/RGSS/Standard",
                ),
            },
            {
                "name": "2003",
                "url": "https://dl.komodo.jp/rpgmakerweb/run-time-packages/rpg2003_rtp_installer.zip",
                "filename": "rpg2003_rtp.zip",
                "sentinels": (
                    "drive_c/Program Files/Common Files/ASCII/RPG2003",
                    "drive_c/Program Files (x86)/Common Files/ASCII/RPG2003",
                    "drive_c/Program Files/Common Files/ASCII/RPG2003RTP",
                    "drive_c/Program Files (x86)/Common Files/ASCII/RPG2003RTP",
                    "drive_c/Program Files/Common Files/Enterbrain/RTP/2003",
                    "drive_c/Program Files (x86)/Common Files/Enterbrain/RTP/2003",
                    "drive_c/users/{user}/AppData/Roaming/KADOKAWA/Common/RPG Maker 2003 RTP",
                ),
            },
        ]

    def _runtime_sentinel_exists(
        self, resolved_prefix: str, relative_path: str
    ) -> bool:
        if "{user}" not in relative_path:
            return os.path.exists(os.path.join(resolved_prefix, relative_path))

        users_dir = os.path.join(resolved_prefix, "drive_c", "users")
        if not os.path.isdir(users_dir):
            return False

        for user_name in os.listdir(users_dir):
            candidate = os.path.join(
                resolved_prefix, relative_path.replace("{user}", user_name)
            )
            if os.path.exists(candidate):
                return True

        return False

    def _verify_rtp_packages(self, resolved_prefix: str) -> dict[str, bool]:
        verified: dict[str, bool] = {}
        for package in self._get_rtp_packages():
            package_name = str(package["name"])
            sentinels = cast(tuple[str, ...], package["sentinels"])
            verified[package_name] = any(
                self._runtime_sentinel_exists(resolved_prefix, relative_path)
                for relative_path in sentinels
            )
        return verified

    def _rtps_installed_in_prefix(self, resolved_prefix: str) -> bool:
        package_status = self._verify_rtp_packages(resolved_prefix)
        return bool(package_status) and all(package_status.values())

    def _dlls_installed_in_prefix(self, resolved_prefix: str) -> bool:
        sentinels = (
            "drive_c/windows/Fonts/arial.ttf",
            "drive_c/windows/system32/d3dcompiler_47.dll",
            "drive_c/windows/system32/msvcr120.dll",
            "drive_c/windows/system32/vcruntime140.dll",
            "drive_c/windows/Microsoft.NET/Framework/v4.0.30319",
        )
        return all(
            os.path.exists(os.path.join(resolved_prefix, relative_path))
            for relative_path in sentinels
        )

    def install_rpgmaker_dependencies(
        self, prefix_path: str | None = None, proton_path: str | None = None
    ) -> dict[str, object]:
        """
        Runs winetricks to install the common dependencies needed for RPGMaker/Unity visual novels.
        """
        if not is_linux():
            return unsupported_on_host("Winetricks dependency installation")

        import subprocess

        try:
            env, target = self._build_runtime_install_env(prefix_path, proton_path)
        except (OSError, ValueError) as exc:
            return {
                "success": False,
                "error": f"Could not prepare the Wine prefix: {exc}",
            }
        try:
            winetricks_command, env = self._winetricks_command(target, env)
        except (OSError, ValueError) as exc:
            return {"success": False, "error": str(exc)}

        verbs = [
            "corefonts",
            "d3dcompiler_42",
            "d3dcompiler_43",
            "d3dcompiler_47",
            "d3dx9",
            "d3dx11_42",
            "d3dx11_43",
            "quartz",
            "directshow",
            "wmp9",
            "dotnetdesktop8",
            "vcrun2013",
            "vcrun2022",
            "dotnet40",
            "dotnet45",
            "dotnet46",
            "dotnet461",
            "dotnet462",
            "dotnet472",
            "allfonts",
            "cjkfonts",
            "consolas",
            "unifont",
            "vcrun6",
            "vcrun2010",
            "dotnetcoredesktop3",
        ]
        command = [*winetricks_command, "-q", *verbs]
        print(
            f"Running winetricks command in {env.get('WINEPREFIX')}: {' '.join(command)}"
        )
        from core.game_sandbox import normalize_prefix

        deps_prefix = normalize_prefix(env.get("WINEPREFIX", ""))

        with self._status_lock:
            if self._prefix_tool_running():
                return {"success": False, "error": "Close the open Wine prefix tool first."}
            running_title = self.launcher.game_using_prefix(deps_prefix)
            if running_title:
                return {"success": False, "error": f"Close {running_title} first: it is running on this Wine prefix."}
            # winetricks verbs run wineserver -k/-w, which would break a parallel install.
            if (
                self._deps_install_status["running"]
                or self._rtp_install_status["running"]
            ):
                return {
                    "success": False,
                    "error": "Another install is already running. Wait for it to finish.",
                }
            self._deps_install_status: ProgressStatus = {
                "running": True,
                "done": 0,
                "total": len(verbs),
                "current": verbs[0],
                "error": "",
            }
            self._deps_install_prefix: str = deps_prefix

        # We run this in a background thread so we don't block the UI returning 'success' immediately
        # (Winetricks downloads huge MS packages that take longer than the IPC timeout)
        def _install_deps():
            try:
                for i, verb in enumerate(verbs):
                    with self._status_lock:
                        self._deps_install_status["done"] = i
                        self._deps_install_status["current"] = verb
                    print(f"Installing winetricks verb {i + 1}/{len(verbs)}: {verb}")
                    result = subprocess.run(
                        host_command([*winetricks_command, "-q", verb], env=env),
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                    if result.returncode != 0:
                        raise RuntimeError(
                            f"Winetricks failed while installing '{verb}' (exit code {result.returncode})."
                        )
                with self._status_lock:
                    self._deps_install_status["done"] = len(verbs)
                    self._deps_install_status["current"] = ""
                    self._deps_install_status["running"] = False
                    self._deps_install_status["error"] = ""
                print(
                    f"Finished installing winetricks dependencies for {target['resolved_prefix']}."
                )
            except Exception as e:
                with self._status_lock:
                    self._deps_install_status["running"] = False
                    self._deps_install_status["current"] = ""
                    self._deps_install_status["error"] = str(e)
                print(f"Winetricks encountered an error: {e}")

        threading.Thread(target=_install_deps, daemon=True).start()
        return {"success": True}

    def install_rpgmaker_rtp(
        self,
        prefix_path: str | None = None,
        proton_path: str | None = None,
        game_id: object = None,
    ) -> dict[str, object]:
        """
        Downloads and installs RPG Maker VX Ace, VX, XP, and 2003 RTPs into the configured wine prefix.
        Currently downloads the official zips and triggers their setup.exe silently.
        """
        if not is_linux():
            return unsupported_on_host("Wine-prefix RPG Maker RTP installation")

        import shutil
        import subprocess
        import urllib.request
        import zipfile

        try:
            env, target = self._build_runtime_install_env(
                prefix_path, proton_path, game_id
            )
        except (OSError, ValueError) as exc:
            return {
                "success": False,
                "error": f"Could not prepare the Wine prefix: {exc}",
            }
        rtps = self._get_rtp_packages()

        runner_command: list[str]
        if target["is_proton"]:
            from core.umu import build_umu_invocation

            try:
                command, env = build_umu_invocation(
                    "placeholder.exe",
                    prefix=target["base_prefix"],
                    runner=target["proton_path"],
                    environment=env,
                )
                runner_command = command[:-1]
            except (OSError, ValueError) as exc:
                return {"success": False, "error": str(exc)}
        else:
            wine_binary = host_which("wine")
            if not wine_binary:
                return {
                    "success": False,
                    "error": "Wine is not installed. Install Wine first, then try again.",
                }
            runner_command = [wine_binary]

        # We run this in a background thread so we don't block the UI returning 'success' immediately
        def _install_rtps():
            rtp_dir = get_rtp_dir()
            os.makedirs(rtp_dir, exist_ok=True)

            failures: list[str] = []

            for i, rtp in enumerate(rtps):
                rtp_name = str(rtp["name"])
                with self._status_lock:
                    self._rtp_install_status["done"] = i
                    self._rtp_install_status["current"] = rtp_name
                download_path = os.path.join(rtp_dir, str(rtp["filename"]))
                extract_path = os.path.join(rtp_dir, rtp_name.replace(" ", "_"))

                print(f"Downloading {rtp_name} RTP...")
                # Earlier builds could leave an empty file behind after a failed download.
                if (
                    not os.path.isfile(download_path)
                    or os.path.getsize(download_path) == 0
                ):
                    part_path = f"{download_path}.part"
                    try:
                        req = urllib.request.Request(
                            str(rtp["url"]), headers={"User-Agent": "Mozilla/5.0"}
                        )
                        with (
                            self._open_url_with_targeted_tls_fallback(
                                req, timeout=30
                            ) as response,
                            open(part_path, "wb") as out_file,
                        ):
                            shutil.copyfileobj(response, out_file)
                        os.replace(part_path, download_path)
                    except Exception as e:
                        formatted_error = self._format_rtp_download_error(
                            str(rtp["url"]), e
                        )
                        failures.append(f"{rtp_name}: {formatted_error}")
                        print(f"Failed to download {rtp_name} RTP: {formatted_error}")
                        continue

                setup_exe = None

                if download_path.endswith(".exe"):
                    setup_exe = download_path
                else:
                    print(f"Extracting {rtp_name} RTP...")
                    if not os.path.exists(extract_path):
                        staging_path = f"{extract_path}.part"
                        try:
                            shutil.rmtree(staging_path, ignore_errors=True)
                            with zipfile.ZipFile(download_path, "r") as zf:
                                zf.extractall(staging_path)
                            os.replace(staging_path, extract_path)
                        except Exception as e:
                            # Start clean next time: re-download and re-extract.
                            shutil.rmtree(staging_path, ignore_errors=True)
                            with suppress(OSError):
                                os.remove(download_path)
                            failures.append(f"{rtp_name}: extract failed ({e})")
                            print(f"Failed to extract {rtp_name} RTP: {e}")
                            continue

                    # Find the setup executable inside the extracted folder
                    for root, _dirs, files in os.walk(extract_path):
                        for f in files:
                            if f.lower().endswith(".exe"):
                                setup_exe = os.path.join(root, f)
                                break
                        if setup_exe:
                            break

                if setup_exe is None:
                    # Likely a half-extracted folder from an earlier build; redo it next time.
                    shutil.rmtree(extract_path, ignore_errors=True)
                    failures.append(f"{rtp_name}: installer executable not found")
                    continue

                print(f"Installing {rtp_name} RTP silently in prefix...")
                setup_exe_str = str(setup_exe)
                command = [*runner_command, setup_exe_str, "/S", "/v/qn"]
                try:
                    _ = subprocess.run(
                        host_command(command, env=env),
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=120,
                        check=True,
                    )
                    if not self._verify_rtp_packages(target["resolved_prefix"]).get(
                        rtp_name, False
                    ):
                        failures.append(
                            f"{rtp_name}: required runtime files were not found in the prefix after install"
                        )
                    else:
                        print(f"Finished installing {rtp_name} RTP.")
                except Exception as e:
                    failures.append(f"{rtp_name}: install failed ({e})")
                    print(f"Wine failed to run {rtp_name} setup: {e}")
                    # A silently truncated download would otherwise be reused
                    # forever; force the next attempt to download it again.
                    if os.path.abspath(str(setup_exe)) == os.path.abspath(
                        download_path
                    ):
                        with suppress(OSError):
                            os.remove(download_path)

            final_error = ""
            if failures:
                final_error = "; ".join(failures)

            if not final_error and not self._rtps_installed_in_prefix(
                target["resolved_prefix"]
            ):
                final_error = "Required RTP files were not found in the target prefix after installation."

            with self._status_lock:
                self._rtp_install_status["done"] = len(rtps)
                self._rtp_install_status["current"] = ""
                self._rtp_install_status["running"] = False
                self._rtp_install_status["error"] = final_error

            if final_error:
                print(
                    f"RTP installation did not verify successfully for {target['resolved_prefix']}: {final_error}"
                )
            else:
                print(
                    f"All RTPs installed successfully in {target['resolved_prefix']}."
                )

        def _download_and_install():
            # A failure before the per-package loop (for example the RTP cache
            # folder being unwritable) would otherwise leave the status stuck on
            # running forever and block every later installer.
            try:
                _install_rtps()
            except Exception as exc:
                message = str(exc) or "RTP installation failed."
                with self._status_lock:
                    self._rtp_install_status["running"] = False
                    self._rtp_install_status["current"] = ""
                    self._rtp_install_status["error"] = message
                print(f"RTP installation stopped unexpectedly: {message}")

        from core.game_sandbox import normalize_prefix

        rtp_prefix = normalize_prefix(env.get("WINEPREFIX", ""))
        with self._status_lock:
            if self._prefix_tool_running():
                return {"success": False, "error": "Close the open Wine prefix tool first."}
            running_title = self.launcher.game_using_prefix(rtp_prefix)
            if running_title:
                return {"success": False, "error": f"Close {running_title} first: it is running on this Wine prefix."}
            if (
                self._deps_install_status["running"]
                or self._rtp_install_status["running"]
            ):
                return {
                    "success": False,
                    "error": "Another install is already running. Wait for it to finish.",
                }
            self._rtp_install_prefix: str = rtp_prefix
            self._rtp_install_status: ProgressStatus = {
                "running": True,
                "done": 0,
                "total": len(rtps),
                "current": str(rtps[0]["name"]),
                "error": "",
            }
        threading.Thread(target=_download_and_install, daemon=True).start()
        return {"success": True}

    def get_install_status(
        self, prefix_path: str | None = None, proton_path: str | None = None
    ) -> dict[str, object]:
        """Returns the current status of background DLL and RTP installs, plus whether they've previously completed."""
        if not is_linux():
            # The frontend types deps/rtps as full progress payloads, so report
            # idle installers here instead of empty objects.
            return {
                "success": False,
                "error": "Wine/Proton runtime installers are only available on Linux.",
                "code": "unsupported_platform",
                "deps": {
                    "running": False,
                    "done": 0,
                    "total": 0,
                    "current": "",
                    "error": "",
                },
                "rtps": {
                    "running": False,
                    "done": 0,
                    "total": 0,
                    "current": "",
                    "error": "",
                },
                "dlls_installed": False,
                "rtps_installed": False,
            }

        with self._status_lock:
            deps_status = dict(
                getattr(
                    self,
                    "_deps_install_status",
                    {
                        "running": False,
                        "done": 0,
                        "total": 0,
                        "current": "",
                        "error": "",
                    },
                )
            )
            rtp_status = dict(
                getattr(
                    self,
                    "_rtp_install_status",
                    {
                        "running": False,
                        "done": 0,
                        "total": 0,
                        "current": "",
                        "error": "",
                    },
                )
            )

        target = self._resolve_runtime_install_target(prefix_path, proton_path)

        return {
            "success": True,
            "deps": deps_status,
            "rtps": rtp_status,
            "dlls_installed": self._dlls_installed_in_prefix(target["resolved_prefix"]),
            "rtps_installed": self._rtps_installed_in_prefix(target["resolved_prefix"]),
        }
