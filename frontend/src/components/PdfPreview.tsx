// Modale d'aperçu PDF avec navigation entre copies (élève médian / plus
// facile / plus difficile, précédent/suivant) — §3.1 étape 5, §9.3.
import { Badge, Button, Group, Modal, Select, Text } from '@mantine/core'
import { useEffect, useMemo, useState } from 'react'
import { api, getToken } from '../api'

export type PreviewEntry = {
  copy_id: string
  student: string
  level: number
  pages: number
  role: string | null
}

export function PdfFrame({ src, height = '70vh' }: { src: string; height?: string }) {
  // le PDF passe par fetch avec le token puis blob URL (l'iframe n'envoie pas nos en-têtes)
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let revoke: string | null = null
    setUrl(null)
    setFailed(false)
    fetch(src, { headers: { Authorization: `Bearer ${getToken()}` } })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(`${r.status}`))))
      .then((b) => {
        revoke = URL.createObjectURL(b)
        setUrl(revoke)
      })
      .catch(() => setFailed(true))
    return () => {
      if (revoke) URL.revokeObjectURL(revoke)
    }
  }, [src])
  // ne jamais rester bloqué sur « Chargement… » : un 404 (overlay pas encore
  // généré, lot non finalisé) donne un message explicite
  if (failed) return (
    <Text c="dimmed" p="md">
      Aperçu non disponible — ce document n'existe pas encore (le lot n'est
      peut-être pas corrigé ni finalisé).
    </Text>
  )
  if (!url) return <Text c="dimmed" p="md">Chargement du PDF…</Text>
  return <iframe src={url} style={{ width: '100%', height, border: 'none' }} title="Aperçu PDF" />
}

export default function PdfPreviewModal({
  assessmentId, opened, onClose, initialMode = 'copy',
}: {
  assessmentId: string | null; opened: boolean; onClose: () => void
  // vue ouverte par défaut : « review » (copie + overlay) pour relire une
  // correction avant impression, « copy » pour prévisualiser un sujet.
  initialMode?: 'copy' | 'batch' | 'overlay' | 'review'
}) {
  const [entries, setEntries] = useState<PreviewEntry[]>([])
  const [idx, setIdx] = useState(0)
  const [mode, setMode] = useState<'copy' | 'batch' | 'overlay' | 'review'>(initialMode)

  useEffect(() => {
    if (opened) setMode(initialMode)
  }, [opened, initialMode])

  useEffect(() => {
    if (opened && assessmentId) {
      api.get<PreviewEntry[]>(`/api/assessments/${assessmentId}/preview`)
        .then((e) => {
          setEntries(e)
          const median = e.findIndex((x) => x.role === 'médiane')
          setIdx(median >= 0 ? median : 0)
        })
        .catch(() => setEntries([]))
    }
  }, [opened, assessmentId])

  const cur = entries[idx]
  const src = useMemo(() => {
    if (!assessmentId) return ''
    if (mode === 'batch') return `/api/assessments/${assessmentId}/files/subject_batch.pdf`
    if (mode === 'overlay') return `/api/assessments/${assessmentId}/files/correction_overlay.pdf`
    if (mode === 'review') return `/api/assessments/${assessmentId}/files/correction_review.pdf`
    return cur ? `/api/assessments/${assessmentId}/copies/${cur.copy_id}/pdf` : ''
  }, [assessmentId, mode, cur])

  function jumpToRole(role: string) {
    const i = entries.findIndex((e) => e.role === role)
    if (i >= 0) { setIdx(i); setMode('copy') }
  }

  return (
    <Modal opened={opened} onClose={onClose} size="90%" title="Aperçu PDF">
      <Group justify="space-between" mb="xs" wrap="wrap">
        <Group gap="xs">
          <Button size="xs" variant={mode === 'copy' ? 'filled' : 'light'}
            onClick={() => setMode('copy')}>Par copie</Button>
          <Button size="xs" variant={mode === 'batch' ? 'filled' : 'light'}
            onClick={() => setMode('batch')}>Lot complet</Button>
          <Button size="xs" variant={mode === 'overlay' ? 'filled' : 'light'}
            onClick={() => setMode('overlay')}>Overlay correction</Button>
          <Button size="xs" variant={mode === 'review' ? 'filled' : 'light'}
            onClick={() => setMode('review')}>Copie + Overlay</Button>
        </Group>
        {mode === 'copy' && (
          <Group gap="xs">
            <Button size="xs" variant="light" onClick={() => jumpToRole('plus facile')}>Plus facile</Button>
            <Button size="xs" variant="light" onClick={() => jumpToRole('médiane')}>Médiane</Button>
            <Button size="xs" variant="light" onClick={() => jumpToRole('plus difficile')}>Plus difficile</Button>
            <Button size="xs" disabled={idx <= 0} onClick={() => setIdx(idx - 1)}>←</Button>
            <Select size="xs" w={230} value={cur?.copy_id ?? null}
              data={entries.map((e, i) => ({
                value: e.copy_id,
                label: `${e.student} (niv. ${e.level})${e.role ? ` — ${e.role}` : ''}`,
              }))}
              onChange={(v) => { const i = entries.findIndex((e) => e.copy_id === v); if (i >= 0) setIdx(i) }} />
            <Button size="xs" disabled={idx >= entries.length - 1} onClick={() => setIdx(idx + 1)}>→</Button>
          </Group>
        )}
      </Group>
      {mode === 'copy' && cur && (
        <Group gap="xs" mb="xs">
          <Text size="sm" fw={600}>{cur.student}</Text>
          <Badge size="sm" color="grape">niveau {cur.level}/10</Badge>
          {cur.role && <Badge size="sm" color="blue">{cur.role}</Badge>}
          <Text size="xs" c="dimmed">{cur.pages} page(s)</Text>
        </Group>
      )}
      {src && <PdfFrame src={src} />}
    </Modal>
  )
}
