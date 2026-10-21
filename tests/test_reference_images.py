"""参考图统一预处理：缩放、格式转换、透明通道处理、缓存失效。"""
from __future__ import annotations

import io

from PIL import Image

from charvar import reference_images


def _png_bytes(width: int, height: int, mode: str = "RGB") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, (width, height), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _rgba_png_bytes(width: int, height: int) -> bytes:
    """带真透明的 PNG（左上角不透明，其余透明）。"""
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    image.paste((200, 100, 50, 255), (0, 0, width, height // 2))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_prepare_downsamples_to_max_side(tmp_path):
    source = tmp_path / "big.png"
    source.write_bytes(_png_bytes(3000, 2000))
    target = reference_images.prepare_reference(source, tmp_path / "out.jpg")
    with Image.open(target) as image:
        assert max(image.size) == reference_images.MAX_SIDE
        assert image.format == "JPEG"


def test_prepare_keeps_small_images_as_is(tmp_path):
    """原图本来就小 → 不放大（放大只会劣化）。"""
    source = tmp_path / "small.png"
    source.write_bytes(_png_bytes(512, 512))
    target = reference_images.prepare_reference(source, tmp_path / "out.jpg")
    with Image.open(target) as image:
        assert image.size == (512, 512)


def test_prepare_flattens_alpha_onto_white(tmp_path):
    """透明区域必须白底合成 —— 直接转 RGB 会变黑，污染参考图。"""
    source = tmp_path / "alpha.png"
    source.write_bytes(_rgba_png_bytes(200, 200))
    target = reference_images.prepare_reference(source, tmp_path / "out.jpg")
    with Image.open(target) as image:
        rgb = image.convert("RGB")
        # 下半部分原本全透明 → 合成后应是白（不是黑）
        red, green, blue = rgb.getpixel((100, 180))
        assert min(red, green, blue) > 200, f"透明区域应为白底，实际 {(red, green, blue)}"


def test_prepare_shrinks_png_size(tmp_path):
    """PNG 2048×1152 → JPEG 长边 1536，体积应显著变小。"""
    source = tmp_path / "sheet.png"
    source.write_bytes(_png_bytes(2048, 1152))
    target = reference_images.prepare_reference(source, tmp_path / "out.jpg")
    assert target.stat().st_size < source.stat().st_size


def test_prepared_reference_reuses_cache(tmp_path):
    source = tmp_path / "a.png"
    source.write_bytes(_png_bytes(3000, 2000))
    subject_dir = tmp_path / "subject"
    first = reference_images.prepared_reference(source, subject_dir, "front")
    stamp = first.with_suffix(".stamp")
    assert first.exists() and stamp.exists()

    # 源文件没变 → 直接复用缓存（mtime/size 未变）
    second = reference_images.prepared_reference(source, subject_dir, "front")
    assert second == first
    assert second.stat().st_mtime_ns == first.stat().st_mtime_ns


def test_prepared_reference_refreshes_when_source_changes(tmp_path):
    source = tmp_path / "a.png"
    source.write_bytes(_png_bytes(3000, 2000))
    subject_dir = tmp_path / "subject"
    first = reference_images.prepared_reference(source, subject_dir, "front")
    first_size = first.stat().st_size

    # 换了源图（尺寸和内容都变）→ 缓存失效，重新生成
    source.write_bytes(_png_bytes(1000, 1000))
    second = reference_images.prepared_reference(source, subject_dir, "front")
    with Image.open(second) as image:
        assert image.size == (1000, 1000)
    assert second.stat().st_size != first_size


def test_prepared_reference_falls_back_to_source_on_error(tmp_path):
    """处理失败时退回原文件（不能因为压缩失败就整个生图流程挂掉）。"""
    source = tmp_path / "broken.png"
    source.write_bytes(b"not an image")
    subject_dir = tmp_path / "subject"
    result = reference_images.prepared_reference(source, subject_dir, "front")
    assert result == source
