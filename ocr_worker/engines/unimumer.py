"""Uni-MuMER (Qwen3.5-2B) : reconnaissance d'expressions manuscrites, sortie LaTeX.

Bon sur les nombres et les expressions, inutilisable en texte (épelé lettre par
lettre) : le plan par défaut ne l'emploie qu'en `num_line` et `math_line`.
"""
from .hf_vlm import HFVisionLM

PROMPT = ("I have an image of a handwritten mathematical expression. "
          "Please write out the expression of the formula in the image using LaTeX format.")


class UnimumerEngine(HFVisionLM):
    name, family = "unimumer", "qwen35"
    default_repo = "phxember/Uni-MuMER-Qwen3.5-2B"
    prompt = PROMPT

    def _classes(self):
        import transformers
        return transformers.Qwen3_5ForConditionalGeneration, transformers.AutoProcessor
