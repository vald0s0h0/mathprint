"""Lot 0: download the candidate OCR weights (no optimizer/rng/training_args)."""
from huggingface_hub import snapshot_download

TROCR_PATTERNS = ["pytorch_model.bin", "config.json", "generation_config.json",
                  "tokenizer*", "vocab.json", "merges.txt", "special_tokens_map.json",
                  "preprocessor_config.json", "added_tokens.json", "sentencepiece*"]
MODELS = {
    "agomberto/trocr-large-handwritten-fr": TROCR_PATTERNS,
    "microsoft/trocr-large-handwritten": ["preprocessor_config.json", "tokenizer*", "vocab.json",
                                           "merges.txt", "special_tokens_map.json"],
    "phxember/Uni-MuMER-Qwen3.5-2B": None,
    "lightonai/LightOnOCR-2-1B": None,
    "lightonai/LightOnOCR-3-4B": None,
}
for repo, pats in MODELS.items():
    print("==", repo, flush=True)
    p = snapshot_download(repo, allow_patterns=pats)
    print(p, flush=True)
