// Diaporama de correction au tableau, en plein écran par-dessus l'application.
// La page vient de l'API (services/slideshow.py), en un seul fichier autonome — la même
// que « Exporter diaporama ». Elle est récupérée en fetch authentifié puis affichée dans
// un cadre ; sa croix ✕ envoie `kn-slideshow-close` pour revenir à l'onglet Correction.
import { useEffect, useRef, useState } from 'react'
import { Button, Loader, Stack, Text } from '@mantine/core'
import { ApiError, download, getToken } from '../api'

export const slideshowUrl = (batchId: string) => `/api/scans/batches/${batchId}/slideshow.html`

export function exportSlideshow(batchId: string) {
  return download(`${slideshowUrl(batchId)}?download=1`, 'diaporama.html')
}

export default function SlideshowOverlay({ batchId, onClose }: { batchId: string | null; onClose: () => void }) {
  const [src, setSrc] = useState<string | null>(null)
  const [error, setError] = useState('')
  const frame = useRef<HTMLIFrameElement>(null)

  useEffect(() => {
    if (!batchId) return
    let url: string | null = null
    let cancelled = false
    setSrc(null); setError('')
    const token = getToken()
    fetch(slideshowUrl(batchId), { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then(async (res) => {
        if (!res.ok) {
          let msg = ''
          try { msg = (await res.json())?.detail || '' } catch { /* corps non-JSON */ }
          throw new ApiError(res.status, msg || `Erreur ${res.status}`)
        }
        return res.blob()
      })
      .then((blob) => { if (cancelled) return; url = URL.createObjectURL(blob); setSrc(url) })
      .catch((e) => { if (!cancelled) setError((e as Error).message) })
    return () => { cancelled = true; if (url) URL.revokeObjectURL(url) }
  }, [batchId])

  useEffect(() => {
    if (!batchId) return
    const onMsg = (e: MessageEvent) => { if (e.data?.type === 'kn-slideshow-close') onClose() }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !src) onClose() }
    window.addEventListener('message', onMsg)
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('message', onMsg); window.removeEventListener('keydown', onKey) }
  }, [batchId, onClose, src])

  if (!batchId) return null
  return (
    <div data-testid="slideshow-overlay"
      style={{ position: 'fixed', inset: 0, zIndex: 1000, background: '#f6f7f9', display: 'flex' }}>
      {src ? (
        <iframe ref={frame} src={src} title="Diaporama de correction" allow="fullscreen"
          onLoad={() => frame.current?.contentWindow?.focus()}
          style={{ border: 0, width: '100%', height: '100%' }} />
      ) : (
        <Stack align="center" justify="center" gap="sm" style={{ flex: 1 }}>
          {error ? (
            <>
              <Text c="red" fw={600}>Diaporama indisponible : {error}</Text>
              <Button variant="light" onClick={onClose}>Revenir</Button>
            </>
          ) : (
            <>
              <Loader />
              <Text c="dimmed">Préparation du diaporama : cartes, copies et résultats…</Text>
              <Button variant="subtle" color="gray" onClick={onClose}>Annuler</Button>
            </>
          )}
        </Stack>
      )}
    </div>
  )
}
