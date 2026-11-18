"""LightOnOCR-2-1B : vision Pixtral, modèle de langage Qwen3.

Invite vide (image seule) : la fiche du modèle précise que toute autre
instruction est hors distribution. Une sortie qui n'est pas une transcription
(`<figure>`, bloc de code, `\\boxed`...) est marquée illisible par `looks_suspect`.
La classe doit être explicite : `AutoModelForImageTextToText` charge une mauvaise
classe et produit du bruit.

Pas de passage avec contexte : mesuré le 2026-11-18 sur le banc (13 écritures
réelles, 20 synthétiques), le modèle transcrit l'écriture PUIS recopie
l'instruction elle-même (« 2Réponse manuscrite d'un élève... ») : 0 lecture juste
sur 33. C'est un modèle d'OCR pur, il lit tout le texte qu'on lui montre.
"""
from .hf_vlm import HFVisionLM


class LightonEngine(HFVisionLM):
    name, family = "lighton", "pixtral_qwen3"
    default_repo = "lightonai/LightOnOCR-2-1B"
    prompt = ""
    supports_context = False
    # le tokenizer Mistral livré avec le modèle a une regex fautive (avertissement transformers) : le
    # correctif ne change aucune des 142 lectures du banc, mais garantit la tokenisation des scores forcés
    processor_kwargs = {"fix_mistral_regex": True}

    def _classes(self):
        import transformers
        return transformers.LightOnOcrForConditionalGeneration, transformers.LightOnOcrProcessor
