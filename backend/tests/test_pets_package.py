"""宠物包解析（parse_pet_package）单元测试 — 不依赖 DB/MinIO。"""

import io
import json
import zipfile

import pytest
from PIL import Image

from aio_agent_platform.pets.package import (
    PetPackageError,
    parse_pet_package,
)


def _make_spritesheet(cols: int, rows: int, fw: int = 192, fh: int = 208) -> bytes:
    """生成测试精灵图：每行一个纯色块帧（行尾留一个空帧检测 row_frames）。"""
    im = Image.new("RGBA", (cols * fw, rows * fh), (0, 0, 0, 0))
    for r in range(rows):
        for c in range(cols - 1):  # 最后一列留空
            for x in range(c * fw + 80, c * fw + 110):
                for y in range(r * fh + 90, r * fh + 120):
                    im.putpixel((x, y), (255, 0, 0, 255))
    buf = io.BytesIO()
    im.save(buf, format="WEBP", lossless=True)
    return buf.getvalue()


def _make_zip(
    manifest: dict | None,
    sheet: bytes | None = None,
    sheet_name: str = "spritesheet.webp",
    prefix: str = "mypet/",
    extra_files: dict[str, bytes] | None = None,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if manifest is not None:
            zf.writestr(f"{prefix}pet.json", json.dumps(manifest))
        if sheet is not None:
            zf.writestr(f"{prefix}{sheet_name}", sheet)
        for name, data in (extra_files or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


VALID_MANIFEST = {
    "id": "mypet",
    "displayName": "My Pet",
    "description": "test pet",
    "spritesheetPath": "spritesheet.webp",
}


def test_parse_valid_codex_package():
    sheet = _make_spritesheet(cols=8, rows=3)
    parsed = parse_pet_package(_make_zip(VALID_MANIFEST, sheet))
    assert parsed.name == "mypet"
    assert parsed.display_name == "My Pet"
    assert (parsed.frame_width, parsed.frame_height) == (192, 208)
    assert (parsed.col_count, parsed.row_count) == (8, 3)
    # 每行最后一列留空 → 有效帧数 7
    assert parsed.row_frames == [7, 7, 7]
    # manifest 原文保留
    assert parsed.manifest["id"] == "mypet"


def test_parse_missing_pet_json():
    with pytest.raises(PetPackageError, match=r"pet\.json"):
        parse_pet_package(_make_zip(None, _make_spritesheet(8, 1)))


def test_parse_missing_spritesheet():
    with pytest.raises(PetPackageError, match="spritesheet"):
        parse_pet_package(_make_zip(VALID_MANIFEST, None))


def test_parse_missing_id():
    bad = {**VALID_MANIFEST, "id": ""}
    with pytest.raises(PetPackageError, match="id"):
        parse_pet_package(_make_zip(bad, _make_spritesheet(8, 1)))


def test_parse_display_name_fallback_to_id():
    manifest = {"id": "mypet", "spritesheetPath": "spritesheet.webp"}
    parsed = parse_pet_package(_make_zip(manifest, _make_spritesheet(8, 1)))
    assert parsed.display_name == "mypet"


def test_parse_zip_slip_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil/pet.json", json.dumps(VALID_MANIFEST))
    with pytest.raises(PetPackageError, match="路径"):
        parse_pet_package(buf.getvalue())


def test_parse_bad_zip():
    with pytest.raises(PetPackageError, match="zip"):
        parse_pet_package(b"not a zip at all")


def test_parse_oversized_zip():
    with pytest.raises(PetPackageError, match="大小"):
        parse_pet_package(b"x" * (11 * 1024 * 1024))


def test_parse_undetectable_grid():
    # 宽不能被 8 整除且不符合 192x208 约定
    im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    buf = io.BytesIO()
    im.save(buf, format="WEBP", lossless=True)
    with pytest.raises(PetPackageError, match="网格"):
        parse_pet_package(_make_zip(VALID_MANIFEST, buf.getvalue()))


def test_parse_non_standard_but_divisible_grid():
    # 8 列可整除、行高接近帧宽 → 走 fallback 推断
    im = Image.new("RGBA", (1024, 384), (0, 0, 0, 0))  # 128x128 帧, 8x3
    for y in range(50, 80):
        for x in range(50, 80):
            im.putpixel((x, y), (255, 0, 0, 255))
    buf = io.BytesIO()
    im.save(buf, format="WEBP", lossless=True)
    parsed = parse_pet_package(_make_zip(VALID_MANIFEST, buf.getvalue()))
    assert (parsed.frame_width, parsed.frame_height) == (128, 128)
    assert (parsed.col_count, parsed.row_count) == (8, 3)


def test_parse_root_level_pet_json():
    """pet.json 在 zip 根目录（无子目录前缀）也可解析。"""
    parsed = parse_pet_package(
        _make_zip(VALID_MANIFEST, _make_spritesheet(8, 2), prefix="")
    )
    assert parsed.name == "mypet"
    assert parsed.row_count == 2
