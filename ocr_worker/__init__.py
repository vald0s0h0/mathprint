"""Worker OCR Kiro & Nina : lit les découpes du serveur avec des moteurs locaux.

Il parle au backend uniquement en HTTP (jeton dédié) et n'importe aucun
modèle tant qu'un moteur n'est pas chargé : `import ocr_worker` reste léger
et se teste sans torch.
"""
__version__ = "0.1.0"
