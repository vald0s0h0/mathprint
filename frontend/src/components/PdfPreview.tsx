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
  useEffect(() => {
    let revoke: string | null = null
    fetch(src, { headers: { Authorization: `Bearer ${getToken()}` } })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(`${r.status}`))))
      .then((b) => {
        revoke = URL.createObjectURL(b)
        setUrl(revoke)
      })
      .catch(() => setUrl(null))
    return () => {
      if (revoke) URL.revokeObjectURL(revoke)
    }
  }, [src])
  if (!url) return <Text c="dimmed" p="md">Chargement du PDF…</Text>
  return <iframe src={url} style={{ width: '100%', height, border: 'none' }} title="Aperçu PDF" />
}

export default function PdfPreviewModal({
  assessmentId, opened, onClose,
}: { assessmentId: string | null; opened: boolean; onClose: () => void }) {
  const [entries, setEntries] = useState<PreviewEntry[]>([])
  const [idx, setIdx] = useState(0)
  const [mode, setMode] = useState<'copy' | 'batch' | 'overlay'>('copy')

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
