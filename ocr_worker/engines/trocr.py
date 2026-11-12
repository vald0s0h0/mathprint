"""TrOCR large manuscrit adapté au français (VisionEncoderDecoderModel).

Une ligne à la fois : il ne reçoit jamais de zone multiligne entière. En
`num_line`, un LogitsProcessor interdit les tokens hors alphabet. Constat du
banc (lot 0) : il perd la virgule décimale avec une confiance maximale ; il
n'est donc qu'observateur dans le plan par défaut.
"""
import time

from .base import (MAX_NEW_TOKENS, NUM_CHARSET, Crop, Engine, Read, ensure_device, free_memory,
                   logprob_stats, looks_suspect, set_determinism)

_DTYPES = {"fp16": "float16", "bf16": "bfloat16", "fp32": "float32"}


class TrocrEngine(Engine):
    name, family = "trocr", "encdec"
    supports_score = True

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.repo = cfg.get("model", "agomberto/trocr-large-handwritten-fr")
        self.processor_repo = cfg.get("processor", "microsoft/trocr-large-handwritten")
        self.dtype_name = cfg.get("dtype", "fp16")
        self.backend = cfg.get("backend", "mps")
        self.device = self.backend if self.backend in ("mps", "cuda", "cpu") else "cpu"
        self.weights = self.repo
        self._allowed: dict[str, list[int]] = {}
        self.model = self.proc = None

    def load(self) -> None:
        import torch
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel
        ensure_device(self.device)
        set_determinism(0)
        self.dtype = getattr(torch, _DTYPES[self.dtype_name])
        self.proc = TrOCRProcessor.from_pretrained(self.processor_repo)
        # .bin chargé avec torch >= 2.6 (weights_only par défaut)
        from pathlib import Path
        local_st = Path(self.repo).is_dir() and (Path(self.repo) / "model.safetensors").exists()   # modèle affiné local
        self.model = VisionEncoderDecoderModel.from_pretrained(
            self.repo, dtype=self.dtype, use_safetensors=True if local_st else False).to(self.device).eval()
        import transformers
        self.version = f"transformers-{transformers.__version__}"

    def unload(self) -> None:
        self.model = self.proc = None
        free_memory()

    def _allowed_ids(self, charset: str) -> list[int]:
        if charset not in self._allowed:
            tok = self.proc.tokenizer
            ids = [i for i in range(len(tok)) if (t := tok.decode([i])) and all(c in charset for c in t)]
            ids += [x for x in (tok.eos_token_id, tok.bos_token_id, tok.pad_token_id) if x is not None]
            self._allowed[charset] = sorted(set(ids))
        return self._allowed[charset]

    def _pixels(self, crop: Crop):
        return self.proc(images=crop.image.convert("RGB"), return_tensors="pt").pixel_values.to(
            self.device, self.dtype)

    def read(self, crops: list[Crop]) -> list[Read]:
        import torch
        from transformers import LogitsProcessor, LogitsProcessorList

        class Allowed(LogitsProcessor):
            def __init__(self, ids):
                self.ids = torch.tensor(ids)

            def __call__(self, input_ids, scores):
                mask = torch.full_like(scores, float("-inf"))
                mask[:, self.ids.to(scores.device)] = 0
                return scores + mask

        out = []
        for crop in crops:
            procs = None
            if crop.zone_class == "num_line":
                procs = LogitsProcessorList([Allowed(self._allowed_ids(crop.charset or NUM_CHARSET))])
            t0 = time.time()
            with torch.no_grad():
                g = self.model.generate(
                    self._pixels(crop), max_new_tokens=MAX_NEW_TOKENS.get(crop.zone_class, 64),
                    num_beams=1, do_sample=False, output_scores=True, return_dict_in_generate=True,
                    logits_processor=procs)
            ts = self.model.compute_transition_scores(g.sequences, g.scores, normalize_logits=True)[0].float().cpu()
            raw = self.proc.batch_decode(g.sequences, skip_special_tokens=True)[0]
            mean, low = logprob_stats(ts.tolist())
            out.append(Read(raw_text=raw, illegible=(not raw.strip()) or looks_suspect(raw),
                            logprob_mean=mean, logprob_min=low, latency_ms=(time.time() - t0) * 1000))
        return out

    def score(self, crop: Crop, candidates: list[str]) -> list[float] | None:
        """Log-probabilité forcée : somme des log-softmax sur les labels du candidat."""
        import torch
        tok = self.proc.tokenizer
        start = self.model.config.decoder_start_token_id
        pv = self._pixels(crop)
        out = []
        with torch.no_grad():
            for cand in candidates:
                labels = tok(cand, return_tensors="pt").input_ids.to(self.device)     # <s> ... </s>
                dec_in = torch.cat([torch.tensor([[start]], device=self.device), labels[:, :-1]], 1)
                logits = self.model(pixel_values=pv, decoder_input_ids=dec_in).logits.float()
                lp = torch.log_softmax(logits, -1).gather(-1, labels.unsqueeze(-1)).sum()
                out.append(float(lp))
        return out
