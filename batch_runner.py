"""Process every image in input/ through the Runpod Serverless endpoint."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ENDPOINT_ID = "cnk2cvyik6amg3"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
TERMINAL_FAILURES = {"FAILED", "CANCELLED", "TIMED_OUT"}
MAX_IMAGE_BYTES = 16 * 1024 * 1024


def api_request(url: str, api_key: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST" if body is not None else "GET",
    )
    try:
        with urlopen(request, timeout=60) as response:
            return json.load(response)
    except HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", errors="replace")
        raise RuntimeError(f"Runpod HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Runpod connection error: {exc.reason}") from exc


def job_error(value: object) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    if isinstance(value, dict):
        return str(value.get("error_message") or value.get("message") or value.get("error_type") or value)
    return str(value)


def process_image(path: Path, output_dir: Path, api_key: str, endpoint_id: str, mode: str, force: bool, poll: int) -> str:
    data = path.read_bytes()
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("input image must be between 1 byte and 16 MiB")

    digest = hashlib.sha256(data).hexdigest()
    state_dir = output_dir / ".runpod_jobs"
    state_dir.mkdir(exist_ok=True)
    state_path = state_dir / f"{path.name}.json"
    base_url = f"https://api.runpod.ai/v2/{endpoint_id}"

    job_id = None
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state.get("sha256") == digest and state.get("endpoint_id") == endpoint_id and state.get("mode") == mode and state.get("force") == force:
            job_id = state.get("job_id")

    if job_id is None:
        result = api_request(
            f"{base_url}/run",
            api_key,
            {"input": {"image_base64": base64.b64encode(data).decode("ascii"), "mode": mode, "force": force}},
        )
        job_id = result["id"]
        state_path.write_text(json.dumps({"sha256": digest, "endpoint_id": endpoint_id, "mode": mode, "force": force, "job_id": job_id}))
        print(f"  submitted job {job_id}", flush=True)
    else:
        print(f"  resuming job {job_id}", flush=True)

    while True:
        result = api_request(f"{base_url}/status/{job_id}", api_key)
        status = result.get("status")
        if status == "COMPLETED":
            output = result["output"]
            image = base64.b64decode(output["image_base64"], validate=True)
            if not image or len(image) > MAX_IMAGE_BYTES:
                raise ValueError("Runpod returned an empty or oversized image")
            target = output_dir / path.name
            temporary = output_dir / f".{path.name}.tmp"
            temporary.write_bytes(image)
            temporary.replace(target)
            state_path.unlink()
            return str(target)
        if status in TERMINAL_FAILURES:
            state_path.unlink()
            raise RuntimeError(f"job {job_id} {status}: {job_error(result.get('error', 'no error detail'))}")
        if status not in {"IN_QUEUE", "IN_PROGRESS"}:
            raise RuntimeError(f"job {job_id} returned unexpected status: {status}")
        time.sleep(poll)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("input"))
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--endpoint-id", default=os.environ.get("RUNPOD_ENDPOINT_ID", ENDPOINT_ID))
    parser.add_argument("--mode", choices=("all", "visible", "metadata"), default="all")
    parser.add_argument("--force", action="store_true", help="force invisible watermark removal")
    parser.add_argument("--poll", type=int, default=5, help="seconds between status checks")
    parser.add_argument("--limit", type=int, help="process at most this many pending images")
    parser.add_argument("--dry-run", action="store_true", help="list files without calling Runpod")
    args = parser.parse_args()

    if args.poll < 1:
        parser.error("--poll must be at least 1 second")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if not args.input_dir.is_dir():
        parser.error(f"input directory does not exist: {args.input_dir}")
    images = sorted(path for path in args.input_dir.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS)
    if not images:
        parser.error(f"no PNG, JPEG, or WebP images found in {args.input_dir}")

    pending = [path for path in images if not (args.output_dir / path.name).exists()]
    print(f"Found {len(images)} images; {len(images) - len(pending)} already complete; {len(pending)} pending.")
    if args.limit is not None:
        pending = pending[: args.limit]
        print(f"Processing up to {args.limit} pending image(s).")
    if args.dry_run:
        for path in pending:
            print(path)
        return 0

    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        parser.error("set RUNPOD_API_KEY in your environment")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    failed = []
    completed = 0
    try:
        for number, path in enumerate(pending, 1):
            print(f"[{number}/{len(pending)}] {path.name}", flush=True)
            try:
                target = process_image(path, args.output_dir, api_key, args.endpoint_id, args.mode, args.force, args.poll)
                print(f"  saved {target}", flush=True)
                completed += 1
            except (ValueError, KeyError, RuntimeError, OSError, binascii.Error) as exc:
                print(f"  FAILED: {exc}", flush=True)
                failed.append(path.name)
                print("Stopping after the first failure. Fix the endpoint, then run this command again.", flush=True)
                break
    except KeyboardInterrupt:
        print("\nStopped. Run the same command to resume.")
        return 130

    print(f"Done: {completed} saved, {len(failed)} failed, {len(pending) - completed - len(failed)} not attempted.")
    if failed:
        print("Failed files: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
