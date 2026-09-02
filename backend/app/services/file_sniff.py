"""Reconnaissance de fichiers déposés par signature d'octets (magic bytes) —
jamais le Content-Type déclaré par le client/l'expéditeur. Partagé par le
dépôt de scans (routers.scans) et la relève automatique par mail
(services.mail_intake) : mêmes formats acceptés partout (§5b : PDF, JPEG,
PNG, HEIC)."""


def sniff_file(content: bytes) -> tuple[str, str] | None:
    """(extension, mime) reconnus, ou None si le format n'est pas supporté."""
    if content.startswith(b"%PDF-"):
        return ".pdf", "application/pdf"
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if len(content) >= 12 and content[4:8] == b"ftyp" and content[8:12] in (
            b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"):
        return ".heic", "image/heic"
    return None
