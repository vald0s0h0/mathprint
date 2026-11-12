"""Apple Vision : VNRecognizeTextRequest, sans correction linguistique.

Précis, fr-FR, `usesLanguageCorrection=False`, jusqu'à 3 candidats avec leur
confiance. Pas de score forcé. Désactivé automatiquement hors macOS.
"""
import io
import sys
import time

from .base import Crop, Engine, Read


def available() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        import Vision  # noqa: F401
        return True
    except ImportError:
        return False


def reading_order(boxes: list[tuple[float, float, float, float]]) -> list[int]:
    """Indices des boîtes (x, y, w, h normalisés, origine en bas à gauche) dans l'ordre de
    lecture : deux boîtes dont les bandes verticales se recouvrent de plus de la moitié de la
    plus petite sont sur la même ligne (gauche à droite) ; sinon la plus haute d'abord."""
    idx = sorted(range(len(boxes)), key=lambda i: -(boxes[i][1] + boxes[i][3] / 2))
    lines: list[list[int]] = []
    for i in idx:
        x, y, w, h = boxes[i]
        for line in lines:
            ref = boxes[line[0]]
            overlap = min(y + h, ref[1] + ref[3]) - max(y, ref[1])
            if overlap > 0.5 * min(h, ref[3]):
                line.append(i)
                break
        else:
            lines.append([i])
    return [i for line in lines for i in sorted(line, key=lambda j: boxes[j][0])]


def _reading_order_key(observations):
    boxes = []
    for obs in observations:
        b = obs.boundingBox()
        boxes.append((float(b.origin.x), float(b.origin.y), float(b.size.width), float(b.size.height)))
    rank = {i: r for r, i in enumerate(reading_order(boxes))}
    ids = {id(o): rank[k] for k, o in enumerate(observations)}
    return lambda obs: ids[id(obs)]


class AppleVisionEngine(Engine):
    name, family, backend, device = "apple_vision", "apple", "vision", "ane"
    weights = "system:VNRecognizeTextRequest"
    supports_score = False

    def __init__(self, cfg: dict | None = None):
        self.version = f"macos-{__import__('platform').mac_ver()[0]}"

    def read(self, crops: list[Crop]) -> list[Read]:
        import Vision
        from Foundation import NSData
        out = []
        for crop in crops:
            buf = io.BytesIO()
            crop.image.convert("RGB").save(buf, format="PNG")
            data = NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))
            req = Vision.VNRecognizeTextRequest.alloc().init()
            req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
            req.setRecognitionLanguages_(["fr-FR"])
            req.setUsesLanguageCorrection_(False)        # jamais de correction : lire fidèlement
            handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, {})
            t0 = time.time()
            ok, err = handler.performRequests_error_([req], None)
            if not ok:
                raise RuntimeError(f"Vision: {err}")
            texts, confs, alts = [], [], []
            # Vision ne garantit pas l'ordre de ses observations : « 12 » lu en deux boîtes
            # pourrait sortir « 2 1 ». Ordre de lecture : ligne (haut d'abord) puis gauche à droite.
            # boundingBox est normalisé, origine en bas à gauche.
            results = list(req.results() or [])
            obs_sorted = sorted(results, key=_reading_order_key(results))
            for i, obs in enumerate(obs_sorted):
                cands = obs.topCandidates_(3)
                if not cands:
                    continue
                texts.append(str(cands[0].string()))
                confs.append(float(cands[0].confidence()))
                if i == 0:
                    alts = [{"text": str(c.string()), "confidence": float(c.confidence())} for c in cands]
            raw = " ".join(texts)
            out.append(Read(raw_text=raw, illegible=not raw.strip(),
                            confidence_raw=min(confs) if confs else None,
                            alternatives=alts, latency_ms=(time.time() - t0) * 1000))
        return out
