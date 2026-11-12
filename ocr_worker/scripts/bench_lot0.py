"""Lot 0 bench: one engine per process (clean peak memory). Greedy decoding, no language correction.

Usage: ocr_worker/.venv/bin/python ocr_worker/scripts/bench_lot0.py <engine> [--dtype bf16|fp16|fp32] [--limit N]
Engines: trocr, unimumer, lighton2, lighton3, apple_vision
Writes data/ocr_bench/results_<engine>.json
"""
import argparse, gc, json, resource, sys, time
from pathlib import Path
import torch
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "data" / "ocr_bench"
UNIMUMER_PROMPT = ("I have an image of a handwritten mathematical expression. "
                   "Please write out the expression of the formula in the image using LaTeX format.")
REPOS = {"trocr": "agomberto/trocr-large-handwritten-fr", "unimumer": "phxember/Uni-MuMER-Qwen3.5-2B",
         "lighton2": "lightonai/LightOnOCR-2-1B", "lighton3": "lightonai/LightOnOCR-3-4B"}
DT = {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": torch.float32}

def rss_gb(): return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9
def mps_gb():
    try: return torch.mps.driver_allocated_memory() / 1e9
    except Exception: return 0.0

PREP = "none"
def pad_line(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    if PREP == "up":      # upscale so the long side is ~1024 px
        k = max(1.0, 1024 / max(img.size)); img = img.resize((int(img.width * k), int(img.height * k)), Image.LANCZOS)
    elif PREP == "page":    # page-like: crop at ~half scale on a white 1190x1540 page, with a margin
        k = 0.5 if img.height > 60 else 1.0
        img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS)
        page = Image.new("RGB", (1190, 1540), "white"); page.paste(img, (120, 160)); img = page
    elif PREP == "canvas":  # white page, crop upscaled x3 at top-left
        img = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
        page = Image.new("RGB", (max(1000, img.width + 100), 900), "white"); page.paste(img, (50, 50)); img = page
    return img

def run_hf_vlm(engine, items, dtype, dev):
    from transformers import AutoProcessor, AutoModelForImageTextToText
    repo = REPOS[engine]
    t0 = time.time()
    import transformers
    if engine == "lighton2":
        proc = transformers.LightOnOcrProcessor.from_pretrained(repo)
        model = transformers.LightOnOcrForConditionalGeneration.from_pretrained(repo, dtype=dtype).to(dev).eval()
    elif engine in ("lighton3", "unimumer"):
        proc = AutoProcessor.from_pretrained(repo)
        model = transformers.Qwen3_5ForConditionalGeneration.from_pretrained(repo, dtype=dtype).to(dev).eval()
    else:
        proc = AutoProcessor.from_pretrained(repo)
        model = AutoModelForImageTextToText.from_pretrained(repo, dtype=dtype).to(dev).eval()
    load_s = time.time() - t0
    print(f"loaded {engine} in {load_s:.1f}s rss={rss_gb():.2f}GB mps={mps_gb():.2f}GB", flush=True)
    prompt = UNIMUMER_PROMPT if engine == "unimumer" else ""
    out = []
    for i, it in enumerate(items):
        img = pad_line(Image.open(it["path"]))
        content = [{"type": "image", "image": img}]
        if prompt: content.append({"type": "text", "text": prompt})
        inputs = proc.apply_chat_template([{"role": "user", "content": content}], add_generation_prompt=True,
                                          tokenize=True, return_dict=True, return_tensors="pt", enable_thinking=False).to(dev)
        if "pixel_values" in inputs: inputs["pixel_values"] = inputs["pixel_values"].to(dtype)
        t = time.time()
        with torch.no_grad():
            g = model.generate(**inputs, max_new_tokens=64, do_sample=False, output_scores=True,
                               return_dict_in_generate=True)
        if dev == "mps": torch.mps.synchronize()
        lat = (time.time() - t) * 1000
        seq = g.sequences[:, inputs["input_ids"].shape[1]:]
        ts = model.compute_transition_scores(g.sequences, g.scores, normalize_logits=True)[0].float().cpu()
        n = seq.shape[1]
        ts = ts[:n]
        raw = proc.decode(seq[0], skip_special_tokens=True)
        out.append(dict(raw=raw, lat_ms=lat, lp_mean=float(ts.mean()) if len(ts) else None,
                        lp_min=float(ts.min()) if len(ts) else None, ntok=int(n)))
        if i % 20 == 0: print(i, repr(raw), f"{lat:.0f}ms", flush=True)
    return out, load_s

def run_trocr(items, dtype, dev):
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel
    repo = REPOS["trocr"]
    t0 = time.time()
    proc = TrOCRProcessor.from_pretrained("microsoft/trocr-large-handwritten")
    model = VisionEncoderDecoderModel.from_pretrained(repo, dtype=dtype, use_safetensors=False).to(dev).eval()
    load_s = time.time() - t0
    print(f"loaded trocr in {load_s:.1f}s rss={rss_gb():.2f}GB mps={mps_gb():.2f}GB", flush=True)
    out = []
    for i, it in enumerate(items):
        img = Image.open(it["path"]).convert("RGB")
        pv = proc(images=img, return_tensors="pt").pixel_values.to(dev, dtype)
        t = time.time()
        with torch.no_grad():
            g = model.generate(pv, max_new_tokens=48, num_beams=1, do_sample=False, output_scores=True,
                               return_dict_in_generate=True)
        if dev == "mps": torch.mps.synchronize()
        lat = (time.time() - t) * 1000
        ts = model.compute_transition_scores(g.sequences, g.scores, normalize_logits=True)[0].float().cpu()
        raw = proc.batch_decode(g.sequences, skip_special_tokens=True)[0]
        out.append(dict(raw=raw, lat_ms=lat, lp_mean=float(ts.mean()), lp_min=float(ts.min()), ntok=int(len(ts))))
        if i % 20 == 0: print(i, repr(raw), f"{lat:.0f}ms", flush=True)
    return out, load_s

def run_apple(items):
    import Vision, Quartz
    from Foundation import NSData
    out = []
    for i, it in enumerate(items):
        data = NSData.dataWithContentsOfFile_(it["path"])
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        req.setRecognitionLanguages_(["fr-FR"])
        req.setUsesLanguageCorrection_(False)
        h = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, {})
        t = time.time()
        ok, err = h.performRequests_error_([req], None)
        lat = (time.time() - t) * 1000
        texts, confs = [], []
        for obs in (req.results() or []):
            c = obs.topCandidates_(1)
            if c: texts.append(str(c[0].string())); confs.append(float(c[0].confidence()))
        out.append(dict(raw=" ".join(texts), lat_ms=lat, conf=min(confs) if confs else None, lp_mean=None, lp_min=None, ntok=0))
        if i % 20 == 0: print(i, repr(out[-1]["raw"]), f"{lat:.0f}ms", flush=True)
    return out, 0.0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("engine"); ap.add_argument("--dtype", default="bf16"); ap.add_argument("--limit", type=int); ap.add_argument("--prep", default="none"); ap.add_argument("--tag", default="")
    a = ap.parse_args()
    global PREP; PREP = a.prep
    items = json.loads((BENCH / "manifest.json").read_text())
    if a.limit: items = items[:a.limit]
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    torch.manual_seed(0)
    dt = DT[a.dtype]
    t0 = time.time()
    if a.engine == "trocr": res, load_s = run_trocr(items, dt, dev)
    elif a.engine == "apple_vision": res, load_s = run_apple(items)
    else: res, load_s = run_hf_vlm(a.engine, items, dt, dev)
    for it, r in zip(items, res): r.update(path=it["path"], truth=it["truth"], zone_class=it["zone_class"], src=it["src"])
    import transformers
    meta = dict(engine=a.engine, dtype=a.dtype, device=dev, load_s=load_s, peak_rss_gb=rss_gb(), mps_gb=mps_gb(),
                transformers=transformers.__version__, torch=torch.__version__, total_s=time.time() - t0)
    (BENCH / f"results_{a.engine}{'' if a.dtype=='bf16' else '_'+a.dtype}{a.tag}.json").write_text(
        json.dumps(dict(meta=meta, results=res), ensure_ascii=False, indent=1))
    print(meta)

if __name__ == "__main__":
    main()
