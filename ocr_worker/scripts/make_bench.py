"""Lot 0 bench set: real teacher_ocr crops + synthetic handwriting-font crops.

Output: data/ocr_bench/{manifest.json, *.png}  (data/ is gitignored: student data stays local).
Usage: backend/.venv/bin/python ocr_worker/scripts/make_bench.py
"""
import json, random, shutil, sqlite3, glob
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "ocr_bench"
FONTS = ["/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",
         "/System/Library/Fonts/MarkerFelt.ttc",
         "/System/Library/Fonts/Supplemental/ChalkboardSE.ttc",
         "/System/Library/Fonts/Noteworthy.ttc"]
FONTS = [f for f in FONTS if Path(f).exists()]
INKS = [(25, 35, 110), (20, 20, 20), (30, 60, 140), (60, 40, 90)]
rnd = random.Random(42)
items = []

def classify_real():
    c = sqlite3.connect(f"file:{ROOT/'backend/mathprint.db'}?mode=ro", uri=True)
    rows = c.execute("select t.zone_id,t.text,t.raw_json,t.created_at from ocr_attempts t "
                     "join response_zones z on z.id=t.zone_id where t.provider='teacher_ocr' "
                     "and z.type in ('table_fill','short_text') order by t.created_at").fetchall()
    last = {}
    for zid, text, raw, ts in rows:
        last[zid] = (text, json.loads(raw))
    for zid, (text, raw) in last.items():
        base = glob.glob(str(ROOT / f"data/assessments/*/scans/derived/{zid}"))
        d = Path(glob.glob(str(ROOT / f"data/assessments/*/scans/derived/{zid}.png"))[0]).parent
        cells = raw.get("cells")
        if cells:
            for k, v in enumerate(cells):
                p = d / f"{zid}-c{k}.png"
                v = v.replace("\\(", "").replace("\\)", "").strip()
                if p.exists() and v:
                    items.append(dict(src="real", path=str(p), truth=v, zone_class="math_line" if ("^" in v or "\\" in v) else "num_line"))
        elif text.strip():
            items.append(dict(src="real", path=str(d / f"{zid}.png"), truth=text.strip(), zone_class="num_line"))

def paper(w, h):
    img = Image.new("RGB", (w, h), (238, 238, 236))
    px = img.load()
    for _ in range(w * h // 40):
        x, y = rnd.randrange(w), rnd.randrange(h)
        g = rnd.randint(-12, 6); px[x, y] = tuple(max(0, min(255, 238 + g)) for _ in range(3))
    return img

def render(text, k, size, ruled=False, w=None):
    font = ImageFont.truetype(rnd.choice(FONTS), size)
    d0 = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    tw = int(d0.textlength(text, font=font)) + 60
    h = int(size * 1.9)
    img = paper(w or max(tw, 220), h)
    d = ImageDraw.Draw(img)
    ink = rnd.choice(INKS)
    x = rnd.randint(14, 30); y = int(h * 0.18) + rnd.randint(-4, 6)
    for ch in text:   # per-char jitter = imperfect hand
        d.text((x, y + rnd.randint(-3, 3)), ch, font=font, fill=ink)
        x += d0.textlength(ch, font=font) + rnd.randint(-1, 3)
    if ruled:
        d.line([(0, h - 8), (img.width, h - 8)], fill=(170, 170, 190), width=1)
    img = img.rotate(rnd.uniform(-2.5, 2.5), fillcolor=(238, 238, 236), expand=True)
    return img.filter(ImageFilter.GaussianBlur(rnd.choice([0, 0.4, 0.8])))

def synth(cls, text, truth=None, size=None, ruled=False):
    k = len(items)
    img = render(text, k, size or rnd.randint(46, 66), ruled)
    p = OUT / f"s{k:03d}.png"; img.save(p)
    items.append(dict(src="synth", path=str(p), truth=truth or text, zone_class=cls))

def build():
    classify_real()
    nums = ["24", "7", "105", "1 000", "-4", "−12", "3,5", "1,79", "0,50", "6,0", "12,25", "0,05", "36", "250", "8",
            "99", "-0,3", "1,8", "15", "2,4", "47", "300", "0,125", "5,5", "81"]
    for t in nums:
        synth("num_line", t)
    for _ in range(40):
        synth("num_line", str(rnd.choice([rnd.randint(1, 999), round(rnd.uniform(0.01, 99), rnd.choice([1, 2])) ])).replace(".", ","))
    for t in ["3/6", "1/2", "5/12", "-3/4", "7/10", "x²", "2×3", "2²×3", "3x+5", "2x−7", "(x+2)(x−1)", "√16", "4x²", "a²+b²",
              "x=3", "−2/5", "(2x+1)²", "12−5x", "5×10³", "y=2x+1", "3(x−4)", "x²−9", "9/4", "2,5×4"]:
        synth("math_line", t)
    for t in ["Vrai", "Faux", "vrai", "le triangle est rectangle", "d'après Pythagore", "l'hypoténuse", "œuf", "ça marche",
              "x est solution", "d'où AB = 5 cm", "réponse : 12 élèves", "la fonction est affine", "Thalès", "proportionnelle",
              "on développe", "côté opposé", "égal à 7", "les droites sont parallèles", "carré", "Donc x = 4",
              "BC² = AB² + AC²", "il y a 3 cas", "Éric a 8 billes", "été"]:
        synth("text_line", t)
    for t in ["On calcule BC²", "BC² = 6² + 8²", "BC² = 100", "donc BC = 10 cm", "on développe (x+1)²",
              "x² + 2x + 1", "d'après le théorème", "les points sont alignés", "5x = 20", "x = 4",
              "le prix total est 36 €", "reste 14 €", "1 000 ÷ 8 = 125", "c'est faux car 7 est premier",
              "A = π × 3²", "A ≈ 28,3 cm²"]:
        synth("multiline_line", t, ruled=True)

if __name__ == "__main__":
    if OUT.exists(): shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    build()
    (OUT / "manifest.json").write_text(json.dumps(items, ensure_ascii=False, indent=1))
    from collections import Counter
    print(len(items), Counter((i["src"], i["zone_class"]) for i in items))
