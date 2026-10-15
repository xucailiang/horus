"""Download pinned official engine releases with gh and verify SHA-256 before extracting."""

import hashlib
import platform
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

VERSIONS = {"hurl": "8.0.1", "k6": "2.3.0"}


def install(root: Path) -> None:
    system = platform.system()
    machine = platform.machine()
    arch = {"arm64": "aarch64", "aarch64": "aarch64",
            "x86_64": "x86_64", "AMD64": "x86_64"}[machine]
    target = {"Darwin": "apple-darwin", "Linux": "unknown-linux-gnu"}[system]
    downloads = root / "downloads"
    binary_dir = root / "bin"
    downloads.mkdir(parents=True, exist_ok=True)
    binary_dir.mkdir(parents=True, exist_ok=True)
    hurl = f"hurl-{VERSIONS['hurl']}-{arch}-{target}.tar.gz"
    k6_arch = "arm64" if arch == "aarch64" else "amd64"
    k6_os = "macos" if system == "Darwin" else "linux"
    suffix = "zip" if system == "Darwin" else "tar.gz"
    k6 = f"k6-v{VERSIONS['k6']}-{k6_os}-{k6_arch}.{suffix}"
    for name, repo, tag, asset, checksums in [
        ("hurl", "Orange-OpenSource/hurl", VERSIONS["hurl"], hurl, hurl + ".sha256"),
        ("k6", "grafana/k6", "v" + VERSIONS["k6"], k6, "k6-v2.3.0-checksums.txt"),
    ]:
        subprocess.run(["gh", "release", "download", tag, "--repo", repo,
                        "--pattern", asset, "--pattern", checksums, "--dir", str(downloads),
                        "--skip-existing"], check=True)
        lines = (downloads / checksums).read_text().splitlines()
        expected = next(line.split()[0] for line in lines
                        if len(line.split()) == 1 or line.split()[-1].lstrip("*") == asset)
        archive = downloads / asset
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Checksum mismatch: {asset}")
        if suffix == "zip" and name == "k6":
            with zipfile.ZipFile(archive) as zipped:
                content = zipped.read(next(n for n in zipped.namelist() if n.endswith("/k6")))
        else:
            with tarfile.open(archive) as tar:
                member = next(m for m in tar.getmembers() if m.name.endswith("/" + name))
                stream = tar.extractfile(member)
                if stream is None:
                    raise ValueError("Missing engine executable")
                content = stream.read()
        destination = binary_dir / name
        destination.write_bytes(content)
        destination.chmod(0o755)
        subprocess.run([str(destination), "--version" if name == "hurl" else "version"],
                       check=True)
    print(f"Verified engines installed in {binary_dir}")


if __name__ == "__main__":
    if shutil.which("gh") is None:
        raise SystemExit("Install GitHub CLI (gh) to fetch official engine releases")
    install(Path(__file__).resolve().parents[1] / ".tools")
