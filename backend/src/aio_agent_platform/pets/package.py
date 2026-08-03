"""Codex 宠物包解析与校验。

真实格式（2026-08-02 用 ~/.codex/pets 下 5 个真实包校准）：
- pet.json: {id, displayName, description?, spritesheetPath, spriteVersionNumber?, kind?}
- 精灵图: RGBA WebP/PNG，规则网格均分，每行一个动画、每列一帧。
  实测 Codex 约定为 192x208 px/帧、8 列；行数不限。行尾可能存在 alpha 全 0 的空帧。
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field

from PIL import Image

MAX_ZIP_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_UNCOMPRESSED_SIZE = 10 * 1024 * 1024
MAX_FILES = 32
MAX_SPRITESHEET_DIM = 4096
MAX_FRAMES = 512
ALLOWED_IMAGE_EXT = {".webp", ".png"}

# Codex 实测约定帧尺寸，优先按此切分
CODEX_FRAME = (192, 208)


class PetPackageError(ValueError):
    """宠物包校验失败（信息可直接返回给用户）。"""


@dataclass
class ParsedPetPackage:
    manifest: dict
    name: str
    display_name: str
    description: str | None
    kind: str | None
    spritesheet_bytes: bytes
    spritesheet_ext: str
    frame_width: int
    frame_height: int
    col_count: int
    row_count: int
    row_frames: list[int] = field(default_factory=list)  # 每行非空帧数


def _detect_grid(width: int, height: int) -> tuple[int, int]:
    """推断 (frame_width, frame_height)。优先 Codex 约定，否则按 8 列 + 近似方形行高。"""
    cw, ch = CODEX_FRAME
    if width % cw == 0 and height % ch == 0:
        return cw, ch
    if width % 8 == 0:
        fw = width // 8
        best: tuple[int, int] | None = None
        for rows in range(1, 65):
            if height % rows != 0:
                continue
            fh = height // rows
            if fh <= 0 or fh > MAX_SPRITESHEET_DIM:
                continue
            ratio = abs(fh - fw) / fw
            if ratio < 0.25 and (best is None or abs(fh - fw) < abs(best[1] - fw)):
                best = (fw, fh)
        if best:
            return best
    raise PetPackageError(
        f"无法识别精灵图网格（{width}x{height}）：宽度需能被 8 整除，或符合 192x208 的 Codex 帧约定"
    )


def _count_row_frames(im: Image.Image, fw: int, fh: int, cols: int, rows: int) -> list[int]:
    """每行从右向左找最后一个含非透明像素的帧，得到每行有效帧数。"""
    alpha = im.getchannel("A")
    bbox_data = alpha.load()
    if bbox_data is None:  # pragma: no cover - Pillow 仅在异常图像上返回 None
        return [1] * rows
    counts: list[int] = []
    for r in range(rows):
        last = 0
        for c in range(cols):
            x0, y0 = c * fw, r * fh
            # 抽样检测：步长 8px，足够判断帧是否为空
            nonempty = False
            for y in range(y0, y0 + fh, 8):
                for x in range(x0, x0 + fw, 8):
                    if bbox_data[x, y] > 8:
                        nonempty = True
                        break
                if nonempty:
                    break
            if nonempty:
                last = c + 1
        counts.append(max(last, 1))
    return counts


def parse_pet_package(zip_bytes: bytes) -> ParsedPetPackage:
    """解析并校验宠物包 zip。失败抛 PetPackageError。"""
    if len(zip_bytes) > MAX_ZIP_SIZE:
        raise PetPackageError(f"压缩包超过大小限制（{MAX_ZIP_SIZE // 1024 // 1024}MB）")

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise PetPackageError("不是有效的 zip 文件") from e

    entries = [i for i in zf.infolist() if not i.is_dir() and not i.filename.endswith(".DS_Store")]
    if not entries:
        raise PetPackageError("压缩包为空")
    if len(entries) > MAX_FILES:
        raise PetPackageError(f"压缩包文件数超过限制（{MAX_FILES}）")
    total = sum(i.file_size for i in entries)
    if total > MAX_UNCOMPRESSED_SIZE:
        raise PetPackageError(f"解压后总大小超过限制（{MAX_UNCOMPRESSED_SIZE // 1024 // 1024}MB）")

    # 路径安全（防 zip slip）
    for i in entries:
        parts = i.filename.replace("\\", "/").split("/")
        if any(p in ("", ".", "..") for p in parts) or i.filename.startswith("/"):
            raise PetPackageError(f"非法文件路径: {i.filename}")

    # 定位 pet.json：根目录或唯一一级子目录下
    manifest_info = next(
        (i for i in entries if i.filename.replace("\\", "/").rstrip("/").split("/")[-1] == "pet.json"),
        None,
    )
    if manifest_info is None:
        raise PetPackageError("缺少 pet.json 清单文件")
    prefix = manifest_info.filename[: -len("pet.json")]

    try:
        manifest = json.loads(zf.read(manifest_info).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PetPackageError("pet.json 不是合法的 JSON") from e
    if not isinstance(manifest, dict):
        raise PetPackageError("pet.json 必须是 JSON 对象")

    name = manifest.get("id")
    if not isinstance(name, str) or not name.strip():
        raise PetPackageError("pet.json 缺少必需字段 id")
    name = name.strip()
    display_name = manifest.get("displayName")
    if not isinstance(display_name, str) or not display_name.strip():
        display_name = name
    description = manifest.get("description")
    if description is not None and not isinstance(description, str):
        description = None
    kind = manifest.get("kind")
    if kind is not None and not isinstance(kind, str):
        kind = None

    spritesheet_path = manifest.get("spritesheetPath")
    if not isinstance(spritesheet_path, str) or not spritesheet_path.strip():
        raise PetPackageError("pet.json 缺少必需字段 spritesheetPath")
    spritesheet_path = spritesheet_path.strip()
    ext = "." + spritesheet_path.rsplit(".", 1)[-1].lower() if "." in spritesheet_path else ""
    if ext not in ALLOWED_IMAGE_EXT:
        raise PetPackageError(f"精灵图仅支持 {sorted(ALLOWED_IMAGE_EXT)}，当前: {ext or '无扩展名'}")

    sheet_name = prefix + spritesheet_path
    sheet_info = next((i for i in entries if i.filename == sheet_name), None)
    if sheet_info is None:
        raise PetPackageError(f"找不到精灵图文件: {spritesheet_path}")
    sheet_bytes = zf.read(sheet_info)

    try:
        im = Image.open(io.BytesIO(sheet_bytes))
        im.load()
    except Exception as e:
        raise PetPackageError("精灵图不是有效的图片文件") from e
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    w, h = im.size
    if w > MAX_SPRITESHEET_DIM or h > MAX_SPRITESHEET_DIM:
        raise PetPackageError(f"精灵图尺寸超过限制（{MAX_SPRITESHEET_DIM}x{MAX_SPRITESHEET_DIM}）")

    fw, fh = _detect_grid(w, h)
    cols, rows = w // fw, h // fh
    if cols * rows > MAX_FRAMES:
        raise PetPackageError(f"帧数超过限制（{MAX_FRAMES}）")

    return ParsedPetPackage(
        manifest=manifest,
        name=name,
        display_name=display_name.strip(),
        description=description,
        kind=kind,
        spritesheet_bytes=sheet_bytes,
        spritesheet_ext=ext,
        frame_width=fw,
        frame_height=fh,
        col_count=cols,
        row_count=rows,
        row_frames=_count_row_frames(im, fw, fh, cols, rows),
    )
