"""Simulate a few training runs so the dashboard has something to show.

    uv run python examples/train_demo.py
    uv run pandm ui
"""

from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw, ImageOps

import pandm

PALETTES = [
    ((24, 26, 60), (139, 149, 246)),
    ((40, 18, 40), (239, 125, 155)),
    ((12, 40, 38), (72, 207, 173)),
    ((45, 30, 12), (245, 163, 95)),
]


def fake_sample(step: int, total: int, palette_idx: int) -> Image.Image:
    """A 'denoising' image: noise fades out as training progresses."""
    dark, bright = PALETTES[palette_idx % len(PALETTES)]
    base = ImageOps.colorize(Image.linear_gradient("L").rotate(45 - palette_idx * 30, expand=False), dark, bright)
    base = base.resize((256, 256))
    sigma = 90 * (1 - step / total) + 6
    noise = ImageOps.colorize(Image.effect_noise((256, 256), sigma), (0, 0, 0), (255, 255, 255))
    img = Image.blend(base, noise, alpha=max(0.04, 0.8 * (1 - step / total)))
    draw = ImageDraw.Draw(img)
    draw.rectangle([8, 224, 116, 246], fill=(0, 0, 0, 160))
    draw.text((14, 228), f"step {step}", fill=(235, 235, 240))
    return img


def simulate(project: str, name: str, lr: float, bs: int, palette_idx: int, steps: int = 400, seed: int = 0) -> None:
    rng = random.Random(seed)
    run = pandm.init(
        project=project,
        name=name,
        total_steps=steps,  # lets the dashboard estimate an ETA; progress tracks the step below
        config={"lr": lr, "batch_size": bs, "optimizer": "adamw", "model": "resnet18", "seed": seed},
    )
    base = 2.0 + rng.uniform(-0.2, 0.4)
    speed = lr * rng.uniform(800, 1400)
    for step in range(steps):
        progress = step / steps
        loss = base * math.exp(-speed * progress) + 0.08 + rng.gauss(0, 0.02) * (1.2 - progress)
        acc = 1 - 0.9 * math.exp(-3.5 * progress) + rng.gauss(0, 0.008)
        lr_t = lr * 0.5 * (1 + math.cos(math.pi * progress))  # cosine schedule
        run.log({"train/loss": loss, "train/acc": min(acc, 0.999), "lr": lr_t}, step=step)
        if step % 10 == 0:
            val_loss = loss + 0.12 + rng.gauss(0, 0.03)
            run.log({"val/loss": val_loss, "val/acc": min(acc - 0.04 + rng.gauss(0, 0.01), 0.999)}, step=step)
        if step % 80 == 0 or step == steps - 1:
            run.log_image("samples", fake_sample(step, steps, palette_idx), step=step, caption=f"epoch {step // 80}")
    run.finish()
    print(f"  ✓ {project}/{name}")


if __name__ == "__main__":
    print("simulating runs…")
    simulate("mnist-diffusion", "baseline", lr=1e-3, bs=64, palette_idx=0, seed=1)
    simulate("mnist-diffusion", "high-lr", lr=3e-3, bs=64, palette_idx=1, seed=2)
    simulate("mnist-diffusion", "big-batch", lr=1e-3, bs=256, palette_idx=2, seed=3)
    simulate("mnist-diffusion", "low-lr", lr=3e-4, bs=64, palette_idx=3, seed=4)
    simulate("llm-finetune", "lora-r8", lr=2e-4, bs=8, palette_idx=1, steps=300, seed=5)
    simulate("llm-finetune", "lora-r32", lr=2e-4, bs=8, palette_idx=2, steps=300, seed=6)
    print("done — run `pandm ui` to view")
