// Image servie par une route protégée (Authorization: Bearer …).
// Une balise <img src> ne peut PAS porter d'en-tête d'authentification : les
// crops/figures Indigo et les vignettes de pages du manuel (routes admin)
// renvoyaient donc 401 et ne s'affichaient pas. On récupère l'image en fetch
// authentifié, puis on l'affiche via une URL d'objet (blob), révoquée au démontage.
import { Box } from '@mantine/core'
import { useEffect, useState } from 'react'
import { getToken } from '../api'

export default function AuthImg({ src, alt, style, reloadKey }: {
  src: string | null | undefined
  alt?: string
  style?: React.CSSProperties
  reloadKey?: number | string      // force un re-fetch (ex. après recadrage figure)
}) {
  const [url, setUrl] = useState<string | null>(null)
  const [err, setErr] = useState(false)

  useEffect(() => {
    if (!src) { setUrl(null); setErr(false); return }
    let cancelled = false
    let obj: string | null = null
    setErr(false); setUrl(null)
    const token = getToken()
    // cache-buster quand reloadKey change (ex. après recadrage +/-) : force des
    // octets frais même si le fichier a le même chemin (sinon 304 = image figée).
    const fetchUrl = reloadKey != null
      ? src + (src.includes('?') ? '&' : '?') + '_r=' + encodeURIComponent(String(reloadKey))
      : src
    fetch(fetchUrl, { cache: 'no-store', headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.blob() })
      .then((b) => { if (cancelled) return; obj = URL.createObjectURL(b); setUrl(obj) })
      .catch(() => { if (!cancelled) setErr(true) })
    return () => { cancelled = true; if (obj) URL.revokeObjectURL(obj) }
  }, [src, reloadKey])

  if (err)
    return <Box style={{ ...style, fontSize: 10, color: 'var(--mantine-color-red-6)' }}>image indisponible</Box>
  if (!url)
    return <Box style={{ ...style, minHeight: 24, background: 'var(--mantine-color-gray-1)', borderRadius: 4 }} />
  return <img src={url} alt={alt} style={style} />
}
