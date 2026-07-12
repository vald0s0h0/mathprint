// Aperçu d'une figure géométrique paramétrée — même PNG qu'à l'impression.
import { Loader } from '@mantine/core'
import { useEffect, useState } from 'react'
import { getToken } from '../api'

interface FigurePreviewProps {
  exerciseId?: string
  figureJson?: Record<string, any> | null
  maxWidth?: number
}

export default function FigurePreview({ exerciseId, figureJson, maxWidth = 260 }: FigurePreviewProps) {
  const [imageSrc, setImageSrc] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)

  const figureKey = figureJson ? JSON.stringify(figureJson) : null

  useEffect(() => {
    if (!figureKey && !exerciseId) return
    let cancelled = false
    let objectUrl: string | null = null
    setLoading(true)
    setError(false)

    const headers: Record<string, string> = {}
    const token = getToken()
    if (token) headers['Authorization'] = `Bearer ${token}`

    const req = figureKey
      ? fetch('/api/content/figures/render', {
          method: 'POST',
          headers: { ...headers, 'Content-Type': 'application/json' },
          body: JSON.stringify({ figure_json: JSON.parse(figureKey) }),
        })
      : fetch(`/api/content/exercises/${exerciseId}/figure.png`, { headers })

    req
      .then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.blob() })
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setImageSrc(objectUrl)
      })
      .catch(() => { if (!cancelled) setError(true) })
      .finally(() => { if (!cancelled) setLoading(false) })

    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [figureKey, exerciseId])

  if ((!figureKey && !exerciseId) || error) return null
  if (loading) return <Loader size="xs" />
  if (!imageSrc) return null

  return (
    <img src={imageSrc} alt="figure"
      style={{ maxWidth, maxHeight: 180, objectFit: 'contain', display: 'block' }} />
  )
}

export { FigurePreview }
