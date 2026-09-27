"""Queue-based Runpod Serverless adapter for image watermark removal."""

from __future__ import annotations

import base64
import binascii
import tempfile
from pathlib import Path
from typing import Any

MAX_IMAGE_BYTES = 16 * 1024 * 1024
FORMATS = {
    "png": (".png", "image/png"),
    "jpeg": (".jpg", "image/jpeg"),
    "webp": (".webp", "image/webp"),
}


def _image_format(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    raise ValueError("image_base64 must contain a PNG, JPEG, or WebP image")


def _decode_image(value: Any) -> tuple[bytes, str]:
    if not isinstance(value, str) or not value:
        raise ValueError("input.image_base64 must be a nonempty base64 string")
    if value.startswith("data:"):
        header, separator, value = value.partition(",")
        if not separator or not header.endswith(";base64"):
            raise ValueError("image_base64 data URL must be base64 encoded")
    if len(value) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise ValueError("image_base64 exceeds the 16 MiB decoded image limit")
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("image_base64 is not valid base64") from exc
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("image_base64 must contain an image of at most 16 MiB")
    return data, _image_format(data)


def _choice(value: Any, name: str, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"input.{name} must be one of: {', '.join(sorted(allowed))}")
    return value


def handler(job: dict[str, Any]) -> dict[str, Any]:
    """Process one image and return base64 image bytes plus stage results."""
    request = job.get("input")
    if not isinstance(request, dict):
        raise ValueError("job.input must be an object")
    mode = _choice(request.get("mode", "all"), "mode", {"all", "visible", "metadata"})
    backend = _choice(request.get("backend", "cv2"), "backend", {"auto", "cv2"})
    sensitivity = _choice(request.get("sensitivity", "auto"), "sensitivity", {"auto", "strict"})
    force = request.get("force", False)
    if not isinstance(force, bool):
        raise ValueError("input.force must be a boolean")
    cpu_offload = request.get("cpu_offload", False)
    if not isinstance(cpu_offload, bool):
        raise ValueError("input.cpu_offload must be a boolean")
    image_bytes, image_format = _decode_image(request.get("image_base64"))
    suffix, mime_type = FORMATS[image_format]

    with tempfile.TemporaryDirectory(prefix="raiw-runpod-") as scratch:
        source = Path(scratch) / f"input{suffix}"
        output = Path(scratch) / f"output{suffix}"
        source.write_bytes(image_bytes)

        if mode == "visible":
            from remove_ai_watermarks.api import remove_visible_detailed

            report = remove_visible_detailed(
                source,
                output,
                backend=backend,
                sensitivity=sensitivity,
                write_noop=True,
            )
            result: dict[str, Any] = {
                "mode": mode,
                "visible_status": report.status,
                "visible_labels": list(report.labels),
            }
        elif mode == "metadata":
            from remove_ai_watermarks.metadata import strip_and_verify

            _, remaining = strip_and_verify(source, output)
            if remaining:
                raise RuntimeError(f"AI metadata markers survived: {', '.join(sorted(remaining))}")
            result = {"mode": mode, "metadata_status": "stripped"}
        else:
            from remove_ai_watermarks.api import InvisibleOptions, remove_all

            report = remove_all(
                source,
                output,
                backend=backend,
                sensitivity=sensitivity,
                force=force,
                invisible=InvisibleOptions(cpu_offload=cpu_offload),
            )
            if report.invisible == "unavailable":
                raise RuntimeError("invisible removal was requested but its CUDA runtime is unavailable")
            result = {
                "mode": mode,
                "visible_status": report.visible_status,
                "visible_label": report.visible_label,
                "invisible_status": report.invisible,
                "metadata_status": "stripped",
            }

        if not output.is_file():
            raise RuntimeError("image processing did not produce an output")
        processed = output.read_bytes()
        if len(processed) > MAX_IMAGE_BYTES:
            raise RuntimeError("output exceeds the 16 MiB response image limit")
        result.update({"image_base64": base64.b64encode(processed).decode("ascii"), "mime_type": mime_type})
        return result


if __name__ == "__main__":
    import runpod

    runpod.serverless.start({"handler": handler})
