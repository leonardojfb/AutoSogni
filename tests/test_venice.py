from pathlib import Path

import pytest

from app.venice.validation import (
    ModelProfile,
    REQUESTED_MODEL_IDS,
    build_multi_edit_payload,
    parse_model_catalog,
    validate_local_image,
    validate_prompt_and_images,
)


def _entry(model_id, name, constraints):
    return {"id": model_id, "model_spec": {"name": name, "constraints": constraints}}


def _catalog_constraints(max_images, limit, resolutions, ratios, default=""):
    constraints = {
        "maxInputImages": max_images,
        "promptCharacterLimit": limit,
        "aspectRatios": ratios,
    }
    if resolutions:
        constraints["resolutions"] = resolutions
    if default:
        constraints["defaultResolution"] = default
    return constraints


def _catalog():
    return {"data": [
        _entry("qwen-image-2-1-pro-edit", "Qwen Image 2.1 Pro", _catalog_constraints(
            10, 10000, ["1K", "2K"], ["auto", "1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"], "1K")),
        _entry("nano-banana-pro-edit", "Nano Banana Pro", _catalog_constraints(
            6, 32768, ["1K", "2K", "4K"], ["auto", "1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"], "1K")),
        _entry("seedream-v5-pro-edit", "Seedream V5 Pro", _catalog_constraints(
            6, 10000, ["1K", "2K"], ["auto", "1:1", "3:2", "16:9", "9:16", "2:3", "3:4", "4:3"], "1K")),
        _entry("flux-2-max-edit", "FLUX 2 Max", _catalog_constraints(
            6, 3000, [], ["auto", "1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"])),
        _entry("unrelated-model", "Other", {}),
    ]}


def _profile(model_id="qwen-image-2-1-pro-edit", **overrides):
    values = dict(id=model_id, label="Qwen", max_input_images=10,
                  prompt_character_limit=10000, resolutions=("1K", "2K"),
                  aspect_ratios=("auto", "1:1"), default_resolution="1K")
    values.update(overrides)
    return ModelProfile(**values)


def test_parse_model_catalog_keeps_only_requested_edit_models():
    profiles = parse_model_catalog(_catalog())
    assert tuple(profiles) == REQUESTED_MODEL_IDS
    assert [(p.label, p.max_input_images, p.prompt_character_limit, p.resolutions,
             p.aspect_ratios, p.default_resolution) for p in profiles.values()] == [
        ("Qwen Image 2.1 Pro", 10, 10000, ("1K", "2K"),
         ("auto", "1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"), "1K"),
        ("Nano Banana Pro", 6, 32768, ("1K", "2K", "4K"),
         ("auto", "1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"), "1K"),
        ("Seedream V5 Pro", 6, 10000, ("1K", "2K"),
         ("auto", "1:1", "3:2", "16:9", "9:16", "2:3", "3:4", "4:3"), "1K"),
        ("FLUX 2 Max", 6, 3000, (),
         ("auto", "1:1", "3:2", "16:9", "21:9", "9:16", "2:3", "3:4", "4:5"), ""),
    ]
    assert "unrelated-model" not in profiles


def test_payload_preserves_prompt_and_reference_order():
    assert build_multi_edit_payload(
        profile=_profile(), prompt="  Keep this exact text.  ",
        images=["base64-a", "base64-b", "base64-c"], aspect_ratio="1:1",
        resolution="2K", output_format="webp", safe_mode=False,
    ) == {
        "modelId": "qwen-image-2-1-pro-edit", "prompt": "  Keep this exact text.  ",
        "images": ["base64-a", "base64-b", "base64-c"], "aspect_ratio": "1:1",
        "resolution": "2K", "output_format": "webp", "safe_mode": False,
    }


def test_payload_omits_unset_optional_options():
    assert build_multi_edit_payload(profile=_profile(), prompt="Edit", images=["a"]) == {
        "modelId": "qwen-image-2-1-pro-edit", "prompt": "Edit", "images": ["a"],
        "output_format": "png", "safe_mode": True,
    }


@pytest.mark.parametrize("kwargs, message", [
    ({"prompt": "  "}, "Prompt"),
    ({"images": []}, "image"),
    ({"images": ["a"] * 11}, "at most 10"),
    ({"prompt": "x" * 10001}, "10000"),
    ({"aspect_ratio": "5:4"}, "aspect ratio"),
    ({"resolution": "4K"}, "resolution"),
    ({"output_format": "bmp"}, "Output format"),
])
def test_payload_rejects_invalid_options(kwargs, message):
    values = {"profile": _profile(), "prompt": "Edit", "images": ["a"]}
    values.update(kwargs)
    with pytest.raises(ValueError, match=message):
        build_multi_edit_payload(**values)


def test_validate_prompt_and_images_checks_prompt_count_and_files(tmp_path):
    path = tmp_path / "valid.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + (256).to_bytes(4, "big") * 2)
    validate_prompt_and_images(profile=_profile(), prompt="Edit", image_paths=[path])
    with pytest.raises(ValueError, match="at most 10"):
        validate_prompt_and_images(profile=_profile(), prompt="Edit", image_paths=[path] * 11)
    with pytest.raises(ValueError, match="Prompt"):
        validate_prompt_and_images(profile=_profile(), prompt="  ", image_paths=[path])


def test_validate_prompt_and_images_enforces_combined_pixel_cap(tmp_path):
    path = tmp_path / "valid.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + (5760).to_bytes(4, "big") * 2)
    with pytest.raises(ValueError, match="pixel"):
        validate_prompt_and_images(profile=_profile(), prompt="Edit", image_paths=[path, path])


@pytest.mark.parametrize("suffix,header", [
    ("png", b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + (256).to_bytes(4, "big") * 2),
    ("gif", b"GIF89a" + (256).to_bytes(2, "little") * 2 + b"\0" * 20),
    ("webp", b"RIFF" + (30).to_bytes(4, "little") + b"WEBP" + b"VP8X" + (10).to_bytes(4, "little") + b"\0" * 4 + (255).to_bytes(3, "little") * 2),
    ("jpg", b"\xff\xd8\xff\xc0\x00\x11\x08" + (256).to_bytes(2, "big") * 2 + b"\x03" + b"\0" * 9 + b"\xff\xd9"),
])
def test_validate_local_image_reads_dimensions(tmp_path, suffix, header):
    path = tmp_path / f"image.{suffix}"
    path.write_bytes(header)
    info = validate_local_image(path)
    assert (info.width, info.height) == (256, 256)


def test_validate_local_image_rejects_invalid_size_dimensions_and_headers(tmp_path):
    tiny = tmp_path / "tiny.png"
    tiny.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + (255).to_bytes(4, "big") * 2)
    with pytest.raises(ValueError, match="256"):
        validate_local_image(tiny)
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"bad")
    with pytest.raises(ValueError, match="image|header|dimensions"):
        validate_local_image(bad)
    with pytest.raises(ValueError, match="does not exist"):
        validate_local_image(tmp_path / "missing.png")
    unsupported = tmp_path / "image.bmp"
    unsupported.write_bytes(b"x")
    with pytest.raises(ValueError, match="PNG|JPEG|WebP|GIF"):
        validate_local_image(unsupported)


def test_validate_local_image_rejects_pixel_cap_and_25mb(tmp_path):
    large = tmp_path / "large.png"
    large.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + (5760).to_bytes(4, "big") + (5761).to_bytes(4, "big"))
    with pytest.raises(ValueError, match="pixel"):
        validate_local_image(large)
    too_big = tmp_path / "huge.png"
    with too_big.open("wb") as stream:
        stream.truncate(25 * 1024 * 1024)
    with pytest.raises(ValueError, match="25 MB"):
        validate_local_image(too_big)
