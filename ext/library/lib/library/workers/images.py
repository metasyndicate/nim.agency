"""Image worker for `init`. Runs under an interpreter that has Pillow + OpenCV
(etc/library.json init.python), separate from the stdlib-only engine.

stdin:  {"settings": {...}, "jobs": [{"id", "src", "out_dir"}]}
stdout: [{"id", "original": [w, h], "size": [w, h], "faces": [[x,y,w,h]],
          "outputs": [{"file", "variant", "width", "height"}], "error"?}]

Per job: decode (HEIC via macOS sips), apply EXIF rotation, flatten to RGB,
downscale so the long side <= max_side, save <id>.png, detect frontal faces,
and with exactly one face save a square <id>-face.png around it.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


def load(src: Path) -> Image.Image:
    if src.suffix.lower() in (".heic", ".heif"):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "x.png"
            subprocess.run(["sips", "-s", "format", "png", str(src), "--out", str(out)],
                           check=True, capture_output=True)
            img = Image.open(out)
            img.load()
    else:
        img = Image.open(src)
        img.load()
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def faces(img: Image.Image) -> list[list[int]]:
    gray = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)
    side = max(32, int(min(img.size) * 0.05))
    found = CASCADE.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(side, side))
    boxes = [[int(v) for v in f] for f in found]
    # Haar fires on texture (foliage, prints). A real face in the same frame as
    # others is rarely under a third of the largest one's width.
    biggest = max((b[2] for b in boxes), default=0)
    return sorted((b for b in boxes if b[2] >= biggest / 3), key=lambda b: -b[2])


def face_crop(img: Image.Image, box: list[int], scale: float):
    """Square crop around one face, face in the upper-middle; None if it would
    cover most of the frame anyway."""
    w, h = img.size
    x, y, fw, fh = box
    side = int(min(max(fw, fh) * scale, w, h))
    if side >= 0.8 * min(w, h):
        return None
    cx, cy = x + fw / 2, y + fh / 2 + side * 0.12
    left = int(min(max(cx - side / 2, 0), w - side))
    top = int(min(max(cy - side / 2, 0), h - side))
    return img.crop((left, top, left + side, top + side))


def run(job: dict, s: dict) -> dict:
    out = {"id": job["id"]}
    try:
        img = load(Path(job["src"]))
        out["original"] = list(img.size)
        ratio = s["max_side"] / max(img.size)
        if ratio < 1:
            img = img.resize((round(img.width * ratio), round(img.height * ratio)), Image.LANCZOS)
        out["size"] = list(img.size)
        d = Path(job["out_dir"])
        d.mkdir(parents=True, exist_ok=True)
        img.save(d / f"{job['id']}.png")
        outputs = [{"file": f"{job['id']}.png", "variant": "full",
                    "width": img.width, "height": img.height}]
        found = faces(img)
        out["faces"] = found
        face_file = d / f"{job['id']}-face.png"
        crop = face_crop(img, found[0], s["face_crop_scale"]) if s["face_crop"] and len(found) == 1 else None
        if crop is not None:
            if crop.width < s["face_crop_min"]:
                crop = crop.resize((s["face_crop_min"], s["face_crop_min"]), Image.LANCZOS)
            crop.save(face_file)
            outputs.append({"file": face_file.name, "variant": "face",
                            "width": crop.width, "height": crop.height})
        elif face_file.exists():
            face_file.unlink()
        out["outputs"] = outputs
    except Exception as e:  # report per image; never abort the batch
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def main() -> None:
    req = json.load(sys.stdin)
    json.dump([run(j, req["settings"]) for j in req["jobs"]], sys.stdout)


if __name__ == "__main__":
    main()
