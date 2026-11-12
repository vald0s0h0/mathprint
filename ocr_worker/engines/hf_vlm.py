"""Base commune des moteurs vision-langage (transformers sur MPS/CUDA).

Décodage glouton (température 0, graine fixée), `enable_thinking=False`, texte
brut renvoyé intact. `score` = passage forcé du candidat après le prompt.
"""
import time

from .base import (MAX_NEW_TOKENS, Crop, Engine, Read, ensure_device, free_memory, logprob_stats,
                   looks_suspect, set_determinism)

_DTYPES = {"fp16": "float16", "bf16": "bfloat16", "fp32": "float32"}


class HFVisionLM(Engine):
    supports_score = True
    prompt = ""
    processor_kwargs: dict = {}          # ex. fix_mistral_regex pour LightOnOCR-2

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.repo = cfg.get("model", self.default_repo)
        self.dtype_name = cfg.get("dtype", "bf16")
        self.backend = cfg.get("backend", "mps")
        self.device = self.backend if self.backend in ("mps", "cuda", "cpu") else "cpu"
        self.weights = self.repo
        self.model = self.proc = None

    # sous-classes : classes transformers à utiliser (AutoModelForImageTextToText
    # charge une mauvaise classe pour LightOnOCR-2 : classe explicite obligatoire)
    default_repo = ""

    def _classes(self):
        raise NotImplementedError

    def load(self) -> None:
        import torch
        import transformers
        ensure_device(self.device)
        set_determinism(0)
        self.dtype = getattr(torch, _DTYPES[self.dtype_name])
        model_cls, proc_cls = self._classes()
        self.proc = proc_cls.from_pretrained(self.repo, **self.processor_kwargs)
        self.model = model_cls.from_pretrained(self.repo, dtype=self.dtype).to(self.device).eval()
        self.version = f"transformers-{transformers.__version__}"

    def unload(self) -> None:
        self.model = self.proc = None
        free_memory()

    def _inputs(self, crop: Crop):
        content = [{"type": "image", "image": crop.image.convert("RGB")}]
        if self.prompt:
            content.append({"type": "text", "text": self.prompt})
        inputs = self.proc.apply_chat_template(
            [{"role": "user", "content": content}], add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt", enable_thinking=False).to(self.device)
        if "pixel_values" in inputs:
            inputs["pixel_values"] = inputs["pixel_values"].to(self.dtype)
        return inputs

    def read(self, crops: list[Crop]) -> list[Read]:
        import torch
        out = []
        for crop in crops:
            inputs = self._inputs(crop)
            t0 = time.time()
            with torch.no_grad():
                g = self.model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS.get(crop.zone_class, 64),
                                        do_sample=False, output_scores=True, return_dict_in_generate=True)
            seq = g.sequences[:, inputs["input_ids"].shape[1]:]
            ts = self.model.compute_transition_scores(g.sequences, g.scores, normalize_logits=True)[0].float().cpu()
            raw = self.proc.decode(seq[0], skip_special_tokens=True)
            mean, low = logprob_stats(ts[: seq.shape[1]].tolist())
            out.append(Read(raw_text=raw, illegible=(not raw.strip()) or looks_suspect(raw),
                            logprob_mean=mean, logprob_min=low, latency_ms=(time.time() - t0) * 1000))
        return out

    def score(self, crop: Crop, candidates: list[str]) -> list[float] | None:
        import torch
        tok = self.proc.tokenizer
        eos = self.model.generation_config.eos_token_id
        eos = eos[0] if isinstance(eos, (list, tuple)) else eos
        inputs = self._inputs(crop)
        prompt_ids = inputs["input_ids"]
        n = prompt_ids.shape[1]
        extra = {k: v for k, v in inputs.items() if k not in ("input_ids", "attention_mask")}
        out = []
        with torch.no_grad():
            for cand in candidates:
                cids = tok(cand, add_special_tokens=False, return_tensors="pt").input_ids.to(self.device)
                tgt = torch.cat([cids, torch.tensor([[eos]], device=self.device)], 1)
                full = torch.cat([prompt_ids, tgt], 1)
                kw = dict(extra)
                for k, v in extra.items():            # *_token_type_ids : zéros sur le candidat
                    if k.endswith("token_type_ids"):
                        kw[k] = torch.cat([v, torch.zeros_like(tgt)], 1)
                logits = self.model(input_ids=full, attention_mask=torch.ones_like(full), **kw).logits
                lp = torch.log_softmax(logits[0, n - 1:-1].float(), -1).gather(-1, tgt[0].unsqueeze(-1)).sum()
                out.append(float(lp))
        return out
