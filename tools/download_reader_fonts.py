"""Fetch original Adobe WOFF2 assets, validate them, and create local font files only."""
import hashlib
import io
import json
import uuid
from pathlib import Path
from urllib.request import urlopen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    ("source-han-serif-cn-vf.woff2", "source-han-serif", "SourceHanSerifCN-VF.otf.woff2"),
    ("source-han-sans-cn-vf.woff2", "source-han-sans", "SourceHanSansCN-VF.otf.woff2"),
]


def fetch(url, maximum):
    with urlopen(url, timeout=45) as response:
        if not response.geturl().startswith("https://raw.githubusercontent.com/adobe-fonts/"):
            raise ValueError("Unexpected download source")
        payload = response.read(maximum + 1)
    if not payload or len(payload) > maximum:
        raise ValueError("Empty or oversized asset")
    return payload


def main():
    directory = ROOT / "web/fonts"
    targets = [directory / name for name, _, _ in SOURCES] + [directory / "serif-LICENSE.txt", directory / "sans-LICENSE.txt", directory / "manifest.json"]
    if any(path.exists() for path in targets):
        raise ValueError("Existing font asset must not be overwritten")
    downloaded, metadata = {}, []
    for name, repository, filename in SOURCES:
        url = f"https://raw.githubusercontent.com/adobe-fonts/{repository}/release/Variable/WOFF2/OTF/Subset/{filename}"
        payload = fetch(url, 40 * 1024 * 1024)
        if payload[:4] != b"wOF2":
            raise ValueError("Expected original WOFF2 font")
        with TTFont(io.BytesIO(payload)) as font:
            cmap = font.getBestCmap()
            if len(cmap) < 6000 or not all(ord(char) in cmap for char in "中文译文载入日期保存经济利率债务美国万亿0123456789，。“”"):
                raise ValueError("Chinese font coverage is incomplete")
            axes = {axis.axisTag: [axis.minValue, axis.maxValue] for axis in font["fvar"].axes}
            family = font["name"].getDebugName(1)
            version = font["name"].getDebugName(5)
        license_url = f"https://raw.githubusercontent.com/adobe-fonts/{repository}/master/LICENSE.txt"
        license_data = fetch(license_url, 64 * 1024)
        if "SIL OPEN FONT LICENSE" not in license_data.decode("utf-8"):
            raise ValueError("Missing official license text")
        downloaded[name] = payload
        downloaded[("serif" if "serif" in repository else "sans") + "-LICENSE.txt"] = license_data
        metadata.append({"file": name, "family": family, "version": version, "source_url": url,
            "license_url": license_url, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload), "unicode_count": len(cmap), "axes": axes})
    # Keep raw public downloads in a new isolated directory before publishing the two verified fonts.
    stage = ROOT / "output" / f"font-download-{uuid.uuid4().hex}"
    stage.mkdir()
    for name, payload in downloaded.items():
        with (stage / name).open("xb") as stream:
            stream.write(payload)
    directory.mkdir(exist_ok=True)
    if not directory.resolve().is_relative_to(ROOT / "web"):
        raise ValueError("Font destination escaped project")
    for name, payload in downloaded.items():
        with (directory / name).open("xb") as stream:
            stream.write(payload)
        if (directory / name).read_bytes() != payload:
            raise ValueError("Published font differs from validated download")
    with (directory / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump({"fonts": metadata, "original_files_unmodified": True}, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"fonts": metadata, "staging": str(stage)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
