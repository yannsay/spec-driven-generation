"""Qwen-Image-Edit-2511 with the 8-step Lightning LoRA, deployed on Modal: the image loop's edits.

  uv run modal deploy "03 - draw/qwen_edit.py"     # app qwen-edit, class QwenEdit; again after any change here
  uv run modal app stop -y qwen-edit

The loop calls the deployed app through call_deployed.py. Draws at 1664x928, the model family's own 16:9 size, or
1328x1328 for a square picture (an item stays square).
"""

import io
import math
import time

import modal

MODEL = "Qwen/Qwen-Image-Edit-2511"
LORA = "lightx2v/Qwen-Image-Edit-2511-Lightning"
LORA_FILE = "Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors"
STEPS = 8
MINUTES = 60

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install(
        "torch==2.9.1",
        "torchvision==0.24.1",  # the pipeline's image processor needs it
        "diffusers==0.42.1",
        "transformers==5.22.0",
        "accelerate",
        "peft",  # to load the LoRA
        "huggingface-hub[hf_xet]",
        "pillow",
    )
    .env({"HF_HUB_CACHE": "/cache", "HF_XET_HIGH_PERFORMANCE": "1"})
)
app = modal.App("qwen-edit", image=image)

with image.imports():
    import torch
    from diffusers import FlowMatchEulerDiscreteScheduler, QwenImageEditPlusPipeline
    from PIL import Image

# The Lightning LoRA's scheduler, from the Qwen-Image-Lightning repository.
SCHEDULER = {
    "base_image_seq_len": 256,
    "base_shift": math.log(3),
    "invert_sigmas": False,
    "max_image_seq_len": 8192,
    "max_shift": math.log(3),
    "num_train_timesteps": 1000,
    "shift": 1.0,
    "shift_terminal": None,
    "stochastic_sampling": False,
    "time_shift_type": "exponential",
    "use_beta_sigmas": False,
    "use_dynamic_shifting": True,
    "use_exponential_sigmas": False,
    "use_karras_sigmas": False,
}


@app.cls(
    gpu=["A100-80GB", "H100"],  # same size of model as Qwen-Image 2512
    scaledown_window=3 * MINUTES,  # the loop's next step reuses a warm container; little idle
    max_containers=1,  # a wave's edits (sent at once) queue on one warm GPU, one cold start
    timeout=45 * MINUTES,  # the first run downloads the model
    volumes={"/cache": modal.Volume.from_name("huggingface-cache")},
    secrets=[modal.Secret.from_name("hf-secret")],
)
class QwenEdit:
    @modal.enter()
    def load(self):
        scheduler = FlowMatchEulerDiscreteScheduler.from_config(SCHEDULER)
        self.pipe = QwenImageEditPlusPipeline.from_pretrained(MODEL, scheduler=scheduler, torch_dtype=torch.bfloat16)
        self.pipe.load_lora_weights(LORA, weight_name=LORA_FILE)
        self.pipe.to("cuda")

    @modal.method()
    def edit(self, prompt: str, ref: bytes, fmt: str, seed: int = 0) -> tuple[bytes, str, float]:
        """The edited picture, the GPU's name, and the seconds the edit took on it."""
        t0 = time.time()
        src = Image.open(io.BytesIO(ref)).convert("RGB")
        width, height = (1328, 1328) if src.width == src.height else (1664, 928)  # an item stays square
        out = self.pipe(
            image=[src],
            prompt=prompt,
            negative_prompt=" ",
            width=width,
            height=height,
            num_inference_steps=STEPS,
            true_cfg_scale=1.0,  # Lightning LoRA: no classifier-free guidance
            guidance_scale=1.0,
            generator=torch.Generator("cuda").manual_seed(seed),
        ).images[0]
        buf = io.BytesIO()
        out.save(buf, format=fmt)
        return buf.getvalue(), torch.cuda.get_device_name(), round(time.time() - t0, 1)
