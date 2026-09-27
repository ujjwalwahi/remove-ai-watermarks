# Runpod Serverless endpoint

The [worker image](../Dockerfile) installs this repository's image
processing package and starts a queue-based Runpod Serverless handler. The
endpoint accepts PNG, JPEG, or WebP image bytes as base64 JSON and returns the
cleaned image in the same format. It supports `all` (default), `visible`, and
`metadata` modes. The `all` mode runs the library's visible, invisible, and
metadata pipeline; the invisible stage runs only when there is local evidence
or when you set `force` to `true`.

## Build and deploy

Build a Linux x86-64 image and push it to a registry Runpod can access:

```bash
docker build --platform linux/amd64 -t YOUR_REGISTRY/remove-ai-watermarks:runpod .
docker push YOUR_REGISTRY/remove-ai-watermarks:runpod
```

You can also import this GitHub repository directly in the Runpod Serverless
console. Select the `main` branch and set **Dockerfile Path** to `Dockerfile`.
The handler is the root-level `handler.py`; it starts the worker with
`runpod.serverless.start`.

For either deployment route, select the **Queue** endpoint type with an NVIDIA
GPU. Give the container disk room for the image, Python dependencies, and
downloaded model weights. Diffusion model loading can be slow on the first
request. Set `HF_TOKEN` as an endpoint environment variable if a chosen model
requires access.

For `all` mode, attach a network volume with at least 150 GB free and set
endpoint environment variables `HF_HOME=/runpod-volume/huggingface` and
`XDG_CACHE_HOME=/runpod-volume/.cache`. Runpod mounts an attached Serverless
network volume at `/runpod-volume`. The Qwen-Image-2512 and Z-Image-Turbo
model repositories alone contain roughly 91 GB of weights, with additional
ControlNet and LoRA weights plus download and offload working space. Without a
volume, the image uses the worker's ephemeral `/root/.cache`; a failed model
download can surface as a Hugging Face Xet "Background writer channel closed"
error. Check the worker logs and available disk space if that happens. Increase
the endpoint's execution timeout above the default 600 seconds for the first
model download; 3600 seconds is a starting point, then tune it after a
successful job.

## Send a request

The JSON envelope is `{"input": {...}}`. `image_base64` can be plain base64 or a
base64 data URL. Input and output image bytes each have a 16 MiB limit.

```json
{
  "input": {
    "mode": "all",
    "image_base64": "BASE64_IMAGE_BYTES",
    "force": false,
    "backend": "cv2",
    "sensitivity": "auto",
    "cpu_offload": false
  }
}
```

To call the deployed endpoint from Python:

```python
import base64
import os
import time
from pathlib import Path

import requests

image = Path("input.png").read_bytes()
response = requests.post(
    "https://api.runpod.ai/v2/YOUR_ENDPOINT_ID/run",
    headers={"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}"},
    json={"input": {"mode": "all", "image_base64": base64.b64encode(image).decode(), "force": True}},
    timeout=30,
)
response.raise_for_status()
job_id = response.json()["id"]
while True:
    status_response = requests.get(
        f"https://api.runpod.ai/v2/YOUR_ENDPOINT_ID/status/{job_id}",
        headers={"Authorization": f"Bearer {os.environ['RUNPOD_API_KEY']}"},
        timeout=30,
    )
    status_response.raise_for_status()
    result = status_response.json()
    if result["status"] == "COMPLETED":
        break
    if result["status"] in {"FAILED", "CANCELLED", "TIMED_OUT"}:
        raise RuntimeError(result)
    time.sleep(5)

Path("clean.png").write_bytes(base64.b64decode(result["output"]["image_base64"]))
print(result["output"]["invisible_status"])
```

The output also reports `visible_status`, `invisible_status`, and
`metadata_status` for `all` mode. An invisible status of `no-signal` means no
regeneration ran; it is not a clean-image verdict. For a quick image, `/runsync`
can return the output directly. Use `/run` for diffusion jobs that may take
longer than the synchronous wait period.

## Process a folder

From the repository root, set your Runpod API key and run:

```bash
export RUNPOD_API_KEY="your-api-key"
python3 batch_runner.py
```

The script submits each PNG, JPEG, or WebP file in `input/` as a separate `/run`
job, waits for it, and saves the result with the same filename in `output/`.
It skips completed files when restarted and resumes an in-progress job using
the ID saved under `output/.runpod_jobs/`. It stops after a failed job so an
endpoint problem does not fail the rest of the folder. Use `--limit 1` to test
one image, `--dry-run` to list pending files, `--mode visible` or
`--mode metadata` to select a stage, and
`--endpoint-id` for a different endpoint. Processing is sequential, so a large
folder can take a while.

Invalid input and processing failures cause a failed Runpod job. The handler
cleans up per-job temporary files. It does not accept remote URLs or video files.

See Runpod's [Serverless quickstart](https://docs.runpod.io/serverless/quickstart)
for endpoint deployment and [request guide](https://docs.runpod.io/serverless/endpoints/send-requests)
for `/run`, `/runsync`, and `/status` behavior.
