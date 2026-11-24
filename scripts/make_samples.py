"""Generate three original sample videos with a fictional creator.

Each one stresses a different extraction path:
  narrated   - the knowledge is in speech
  onscreen   - the knowledge is only in on-screen text (no speech)
  demo       - speech and visuals each carry facts the other lacks
"""
from __future__ import annotations

import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "samples"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
W, H, FPS = 720, 1280, 12
FONT = "/System/Library/Fonts/Helvetica.ttc"


@dataclass
class Scene:
    kicker: str
    title: str
    body: str = ""
    seconds: float = 5.0


VIDEOS = {
    "narrated": {
        "bg": (24, 26, 24),
        "accent": (226, 240, 120),
        "voice": (
            "White midsoles turn yellow because the foam and rubber oxidize. "
            "Sunlight and heat speed that up, so never store your pairs on a windowsill. "
            "Keep them in a cool, dark closet. "
            "Stuff them with acid-free tissue, because newspaper ink can transfer onto the lining. "
            "And skip chlorine bleach. It can make yellowing worse over time. "
            "Use mild soap and a soft brush instead."
        ),
        "scenes": [
            Scene("MARA VOSS", "Why white midsoles turn yellow"),
            Scene("THE CAUSE", "Oxidation"),
            Scene("STORAGE", "Cool. Dark. Closed."),
            Scene("STUFFING", "Tissue, not newspaper"),
            Scene("CLEANING", "Skip the bleach"),
        ],
    },
    "onscreen": {
        "bg": (46, 32, 25),
        "accent": (244, 176, 112),
        "voice": None,
        "scenes": [
            Scene("MARA VOSS", "Suede rescue in 4 moves", "Water stain? Do not panic.", 3.5),
            Scene("MOVE 1", "Let it dry", "Air dry 24 hours. No hair dryer.", 5),
            Scene("MOVE 2", "Crepe brush", "Brush the whole panel to lift the nap.", 5),
            Scene("MOVE 3", "White vinegar", "Dab on a cloth. Blot. Never rub.", 5),
            Scene("MOVE 4", "Steam, 10 seconds", "Hold the kettle 15 cm away, then brush one direction.", 6),
        ],
    },
    "demo": {
        "bg": (22, 34, 44),
        "accent": (130, 214, 232),
        "voice": (
            "Here is how I deep clean knit sneakers. "
            "Take the laces and insoles out first. "
            "Mix your solution using the ratio on screen. "
            "Work in small circles with a soft brush, from the toe to the heel. "
            "Blot with a microfiber towel, then let them dry for the time shown here."
        ),
        "scenes": [
            Scene("MARA VOSS", "Deep clean knit sneakers"),
            Scene("PREP", "Laces and insoles out"),
            Scene("SOLUTION", "1 tsp soap", "per 1 cup of cool water"),
            Scene("TECHNIQUE", "Small circles", "Toe to heel"),
            Scene("DRY", "12 hours", "Stuffed, away from direct heat"),
        ],
    },
}


def wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])


def draw_shoe(draw: ImageDraw.ImageDraw, x: float, y: float, color, sole) -> None:
    upper = [(x, y + 120), (x + 30, y + 30), (x + 120, y), (x + 190, y + 66), (x + 300, y + 112),
             (x + 372, y + 150), (x + 382, y + 196), (x, y + 196)]
    draw.polygon(upper, fill=color)
    draw.rounded_rectangle((x - 8, y + 190, x + 392, y + 236), radius=20, fill=sole)
    for i in range(4):
        px = x + 140 + i * 34
        draw.line((px, y + 38 + i * 19, px + 30, y + 70 + i * 19), fill=sole, width=6)


def frame(scene: Scene, t: float, progress: float, bg, accent) -> Image.Image:
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    kicker, title_font, body_font = (ImageFont.truetype(FONT, s) for s in (30, 72, 40))
    d.rectangle((48, 72, 48 + (W - 96) * progress, 78), fill=accent)
    d.text((48, 118), scene.kicker, font=kicker, fill=accent)
    y = 190
    for line in wrap(d, scene.title, title_font, W - 96):
        d.text((48, y), line, font=title_font, fill=(246, 244, 236))
        y += 84
    y += 22
    for line in wrap(d, scene.body, body_font, W - 96):
        d.text((48, y), line, font=body_font, fill=(208, 206, 196))
        y += 54
    bob = math.sin(t * 2.2) * 9
    glow = tuple(min(255, c + 22) for c in bg)
    d.ellipse((60, 690, 660, 1180), fill=glow)
    draw_shoe(d, 160 + math.sin(t) * 12, 830 + bob, accent, (246, 244, 236))
    return img


def build(name: str, spec: dict) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / "audio.aiff"
        if spec["voice"]:
            subprocess.run(["say", "-v", "Samantha", "-r", "165", "-o", str(audio), spec["voice"]], check=True)
            out = subprocess.run([FFMPEG, "-i", str(audio)], capture_output=True, text=True).stderr
            stamp = out.split("Duration: ")[1].split(",")[0]
            h, m, s = stamp.split(":")
            total = int(h) * 3600 + int(m) * 60 + float(s) + 0.6
            weight = total / len(spec["scenes"])
            for scene in spec["scenes"]:
                scene.seconds = weight
        total = sum(s.seconds for s in spec["scenes"])

        input_audio = (
            ["-i", str(audio)]
            if spec["voice"]
            else ["-f", "lavfi", "-t", f"{total:.2f}", "-i",
                  "sine=frequency=196:sample_rate=44100,tremolo=f=2:d=0.7,volume=0.08"]
        )
        cmd = [FFMPEG, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0", *input_audio,
               "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-crf", "24",
               "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart",
               str(OUT / f"{name}.mp4")]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        elapsed = 0.0
        for scene in spec["scenes"]:
            for i in range(int(scene.seconds * FPS)):
                now = elapsed + i / FPS
                proc.stdin.write(frame(scene, now, now / total, spec["bg"], spec["accent"]).tobytes())
            elapsed += scene.seconds
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg failed for {name}")
    print(f"{name}: {total:.1f}s")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for key, value in VIDEOS.items():
        build(key, value)
