"""FLUX.2 [klein] 4B, deployed on Modal: draws the first pictures (those without `edit_of`).

  uv run modal deploy "pipeline/03 - draw/klein.py"     # app klein-imagegen, class Klein; again after any change here
  uv run modal app stop -y klein-imagegen

This file runs on Modal only. The loop calls the deployed app through call_deployed.py (`draw`), which builds the
prompts and writes the pictures; state pictures are edits, drawn by Qwen-Image-Edit (qwen_edit.py).
"""

import io

import modal

MODEL = "black-forest-labs/FLUX.2-klein-4B"
STEPS = 4
MINUTES = 60

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install(
        "torch==2.9.1",
        "diffusers==0.42.1",
        "transformers==5.22.0",
        "accelerate",
        "huggingface-hub[hf_xet]",
        "pillow",
    )
    .env({"HF_HUB_CACHE": "/cache", "HF_XET_HIGH_PERFORMANCE": "1"})
)
app = modal.App("klein-imagegen", image=image)

with image.imports():
    import torch
    from diffusers import Flux2KleinPipeline
    from PIL import Image


@app.cls(
    gpu=["L4", "A10"],  # L4 first, A10 when L4 is out of capacity
    scaledown_window=3 * MINUTES,  # the loop's next step reuses a warm container; little idle
    timeout=20 * MINUTES,
    volumes={"/cache": modal.Volume.from_name("huggingface-cache")},
    secrets=[modal.Secret.from_name("hf-secret")],
)
class Klein:
    @modal.enter()
    def load(self):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.pipe = Flux2KleinPipeline.from_pretrained(MODEL, torch_dtype=torch.bfloat16).to(self.device)

    @modal.method()
    def generate(self, prompt: str, width: int, height: int, ref: bytes | None, fmt: str, seed: int = 0, steps: int = 4) -> bytes:
        out = self.pipe(
            image=Image.open(io.BytesIO(ref)).convert("RGB") if ref else None,
            prompt=prompt,
            width=width,
            height=height,
            num_inference_steps=steps,
            guidance_scale=1.0,
            generator=torch.Generator(self.device).manual_seed(seed),
        ).images[0]
        buf = io.BytesIO()
        out.save(buf, format=fmt)
        return buf.getvalue()

