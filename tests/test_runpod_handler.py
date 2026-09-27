"""The Serverless adapter must preserve payloads and clean up each job."""

from __future__ import annotations

import base64
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import handler

PNG = b"\x89PNG\r\n\x1a\n" + b"test image payload"


class RunpodHandlerTests(unittest.TestCase):
    def test_rejects_invalid_input_before_processing(self) -> None:
        with self.assertRaisesRegex(ValueError, "base64"):
            handler.handler({"input": {"image_base64": "not valid base64"}})
        with self.assertRaisesRegex(ValueError, "mode"):
            handler.handler({"input": {"mode": "command", "image_base64": base64.b64encode(PNG).decode()}})
        with self.assertRaisesRegex(ValueError, "force"):
            handler.handler({"input": {"force": "true", "image_base64": base64.b64encode(PNG).decode()}})

    def test_all_mode_returns_image_and_removes_job_files(self) -> None:
        observed: list[Path] = []
        api = types.ModuleType("remove_ai_watermarks.api")

        def remove_all(source: Path, output: Path, **kwargs: object) -> SimpleNamespace:
            self.assertEqual(kwargs["force"], True)
            observed.extend((source, output))
            output.write_bytes(source.read_bytes())
            return SimpleNamespace(invisible="no-signal", visible_status="no_watermark", visible_label=None)

        api.remove_all = remove_all  # type: ignore[attr-defined]
        api.InvisibleOptions = lambda **kwargs: kwargs  # type: ignore[attr-defined]
        package = types.ModuleType("remove_ai_watermarks")
        package.__path__ = []  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"remove_ai_watermarks": package, "remove_ai_watermarks.api": api}):
            result = handler.handler(
                {"input": {"image_base64": base64.b64encode(PNG).decode(), "force": True}}
            )

        self.assertEqual(base64.b64decode(result["image_base64"]), PNG)
        self.assertEqual(result["invisible_status"], "no-signal")
        self.assertEqual(result["mime_type"], "image/png")
        self.assertTrue(all(not path.exists() for path in observed))


if __name__ == "__main__":
    unittest.main()
