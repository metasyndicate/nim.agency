"""Image generation worker for `prompt`. Runs under sd-scripts' venv python
(etc/library.json prompt.python), importing sd-scripts' offline model loader and
LoRA network from prompt.sd_scripts. sd-scripts' own gen_img.py only uses CUDA or
CPU; this runs on Apple's mps device when available.

stdin:  {"settings": {sd_scripts, ckpt, lora, dtype, steps, guidance, size},
         "out_dir": str, "jobs": [{"file", "prompt", "negative", "weight", "seed"}],
         "sheet": {"file", "rows": [[file, ...], ...], "labels": [str, ...]}}
stdout: [{"file", "seconds", "blank"}]       progress goes to stderr
"""

import json
import sys
import time
from pathlib import Path

import torch
from PIL import Image, ImageDraw


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def main() -> None:
    req = json.load(sys.stdin)
    s = req["settings"]
    sys.path.insert(0, s["sd_scripts"])
    from diffusers import DPMSolverMultistepScheduler
    from transformers import CLIPTokenizer

    from library import model_util
    from networks import lora as lora_net

    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    dtype = {"float16": torch.float16, "bfloat16": torch.bfloat16}.get(s["dtype"], torch.float32)
    t0 = time.monotonic()
    log(f"loading {Path(s['ckpt']).name} on {device} ({s['dtype']})")
    text_encoder, vae, unet = model_util.load_models_from_stable_diffusion_checkpoint(False, s["ckpt"], "cpu", dtype)
    tokenizer = CLIPTokenizer.from_pretrained("openai/clip-vit-large-patch14", local_files_only=True)
    network = None
    if s.get("lora"):
        network, weights = lora_net.create_network_from_weights(1.0, s["lora"], vae, text_encoder, unet,
                                                                for_inference=True)
        network.apply_to(text_encoder, unet)
        network.load_state_dict(weights, False)
        network.to(device, dtype=dtype)
    # sd-scripts' own attention kernels return NaN on mps; train_lora.sh uses --sdpa for the same reason
    unet.set_use_memory_efficient_attention(False, False)
    unet.set_use_sdpa(True)
    text_encoder.to(device, dtype=dtype).eval()
    unet.to(device, dtype=dtype).eval()
    vae.to(device, dtype=torch.float32).eval()  # half-precision VAE decode is unreliable on mps
    log(f"loaded in {time.monotonic() - t0:.0f}s")

    sched = DPMSolverMultistepScheduler(beta_start=0.00085, beta_end=0.012, beta_schedule="scaled_linear",
                                        num_train_timesteps=1000)

    @torch.no_grad()
    def embed(text: str) -> torch.Tensor:
        ids = tokenizer(text, padding="max_length", max_length=tokenizer.model_max_length,
                        truncation=True, return_tensors="pt").input_ids.to(device)
        return text_encoder(ids)[0]

    @torch.no_grad()
    def generate(job: dict) -> Image.Image:
        if network is not None:
            network.set_multiplier(job["weight"])
        cond = torch.cat([embed(job["negative"]), embed(job["prompt"])])
        size = s["size"] // 8
        gen = torch.Generator("cpu").manual_seed(job["seed"])  # mps has no seeded randn
        latents = torch.randn((1, 4, size, size), generator=gen).to(device, dtype=dtype)
        sched.set_timesteps(s["steps"], device=device)
        latents = latents * sched.init_noise_sigma
        for t in sched.timesteps:
            x = sched.scale_model_input(torch.cat([latents] * 2), t)
            noise = unet(x, t, encoder_hidden_states=cond).sample
            uncond, text = noise.chunk(2)
            latents = sched.step(uncond + s["guidance"] * (text - uncond), t, latents).prev_sample
        img = vae.decode(latents.float() / 0.18215).sample
        img = ((img[0].clamp(-1, 1) + 1) * 127.5).round().byte().permute(1, 2, 0).cpu().numpy()
        return Image.fromarray(img)

    out_dir = Path(req["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for n, job in enumerate(req["jobs"], 1):
        t1 = time.monotonic()
        img = generate(job)
        img.save(out_dir / job["file"])
        secs = round(time.monotonic() - t1, 1)
        blank = img.getextrema() in (((0, 0),) * 3, ((255, 255),) * 3)
        results.append({"file": job["file"], "seconds": secs, "blank": blank})
        log(f"[{n}/{len(req['jobs'])}] {job['file']}  weight {job['weight']}  {secs}s"
            + ("  BLANK" if blank else ""))

    sheet = req.get("sheet")
    if sheet:
        rows, labels = sheet["rows"], sheet["labels"]
        cell, pad = s["size"] // 2, 24
        grid = Image.new("RGB", (cell * len(rows[0]), (cell + pad) * len(rows)), "white")
        draw = ImageDraw.Draw(grid)
        for r, row in enumerate(rows):
            for c, f in enumerate(row):
                grid.paste(Image.open(out_dir / f).resize((cell, cell)), (c * cell, r * (cell + pad) + pad))
            draw.text((4, r * (cell + pad) + 6), labels[r][: cell * len(row) // 7], fill="black")
        grid.save(out_dir / sheet["file"])
    json.dump(results, sys.stdout)


if __name__ == "__main__":
    main()
