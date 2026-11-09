// Copies tardives : chaque nouvel enchaînement scanné sur un sujet déjà corrigé est
// un job à part (backend services.late_copies), avec sa ligne et son overlay.
import { Alert, Box, Button, Group, Modal, Stack, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { Scale } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import AuthImg from './AuthImg'
import { type Segment } from './SegmentBar'

export type LateJob = {
  wave: number; created_at: string | null
  stage: 'conflict' | 'error' | 'ocr_review' | 'review' | 'processing' | 'print' | 'distribute' | 'done'
  students: string[]; sheets: number; conflicts: number
  // pipeline visuel propre à ce lot, et nombre de pages scannées
  segments: Segment[]; page_count: number
  pending_ocr: number; pending_reviews: number
  overlay_printed: boolean; overlay_distributed: boolean
}

type Conflict = {
  id: string; wave: number; student: string; page_no: number
  existing_note: string | null; existing_blank: boolean
  existing_image: string | null; new_image: string
}

const STAGE: Record<LateJob['stage'], { label: string; color: string }> = {
  conflict: { label: 'double correction à résoudre', color: 'orange' },
  error: { label: 'bloqué', color: 'red' },
  ocr_review: { label: 'lecture à vérifier', color: 'blue' },
  review: { label: 'à corriger', color: 'orange' },
  processing: { label: 'correction en cours', color: 'blue' },
  print: { label: 'overlay à imprimer', color: 'green' },
  distribute: { label: 'à rendre', color: 'teal' },
  done: { label: 'terminé', color: 'gray' },
}

export function lateJobNames(job: LateJob) {
  const shown = job.students.slice(0, 3).join(', ')
  return job.students.length > 3 ? `${shown} +${job.students.length - 3}` : shown
}

export function lateJobDate(job: LateJob) {
  return job.created_at
    ? new Date(job.created_at).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' })
    : ''
}

function SheetImage({ src, alt }: { src: string | null; alt: string }) {
  if (!src) return <Text size="xs" c="dimmed">Aperçu indisponible</Text>
  return (
    <AuthImg src={src} alt={alt} style={{
      width: '100%', border: '1px solid var(--mantine-color-default-border)',
      borderRadius: 4, display: 'block',
    }} />
  )
}

export function LateConflictButton({ batchId, count, onResolved, size = 'xs' }: {
  batchId: string; count: number; onResolved: () => void; size?: string
}) {
  const [opened, setOpened] = useState(false)
  const [conflicts, setConflicts] = useState<Conflict[]>([])
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    const rows = await api.get<Conflict[]>(`/api/scans/batches/${batchId}/conflicts`)
    setConflicts(rows)
    return rows
  }, [batchId])

  useEffect(() => {
    if (opened) {
      load().catch((e) => notifications.show({ color: 'red', message: (e as Error).message }))
    }
  }, [opened, load])

  async function resolve(conflict: Conflict, action: 'keep_old' | 'keep_new') {
    setBusy(true)
    try {
      await api.post(`/api/scans/batches/${batchId}/conflicts/${conflict.id}/resolve`, { action })
      notifications.show({
        color: 'green',
        message: action === 'keep_new'
          ? `${conflict.student} : la nouvelle copie part en correction`
          : `${conflict.student} : la note actuelle est conservée`,
      })
      const rest = await load()
      onResolved()
      if (!rest.length) setOpened(false)
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setBusy(false)
    }
  }

  const current = conflicts[0]
  return (
    <>
      <Button size={size as never} color="orange" leftSection={<Scale size={14} />}
        onClick={() => setOpened(true)}>
        Résoudre{count > 1 ? ` (${count})` : ''}
      </Button>
      <Modal opened={opened} onClose={() => setOpened(false)} size="xl"
        title={current ? `Double correction — ${current.student}` : 'Double correction'}>
        {current ? (
          <Stack gap="sm">
            <Alert color="orange" p="xs">
              <Text size="sm">
                Une nouvelle copie de <b>{current.student}</b> vient d’être déposée, alors que cet
                élève a déjà {current.existing_note
                  ? <>la note <b>{current.existing_note}</b></>
                  : 'une copie corrigée'}.
                {current.existing_blank && ' L’ancienne copie semble vide.'}
                {' '}Rien n’a été modifié : à vous de choisir.
              </Text>
            </Alert>
            <Group grow align="flex-start" gap="sm">
              <Box>
                <Text size="sm" fw={600} mb={4}>
                  Copie déjà corrigée{current.existing_note ? ` · ${current.existing_note}` : ''}
                </Text>
                <SheetImage src={current.existing_image} alt="Copie déjà corrigée" />
              </Box>
              <Box>
                <Text size="sm" fw={600} mb={4}>Nouvelle copie déposée</Text>
                <SheetImage src={current.new_image} alt="Nouvelle copie déposée" />
              </Box>
            </Group>
            <Group justify="flex-end" gap="xs">
              <Button variant={current.existing_blank ? 'default' : 'filled'} loading={busy}
                onClick={() => resolve(current, 'keep_old')}>
                Garder {current.existing_note ? `la note ${current.existing_note}` : 'la copie corrigée'}
              </Button>
              <Button variant={current.existing_blank ? 'filled' : 'default'} color="orange" loading={busy}
                onClick={() => resolve(current, 'keep_new')}>
                Remplacer et corriger la nouvelle copie
              </Button>
            </Group>
            <Text size="xs" c="dimmed">
              Remplacer efface l’ancienne note de cet élève ; elle reste consignée dans le journal.
              {conflicts.length > 1 && ` Encore ${conflicts.length - 1} après celle-ci.`}
            </Text>
          </Stack>
        ) : (
          <Text size="sm" c="dimmed">Aucune double correction à résoudre.</Text>
        )}
      </Modal>
    </>
  )
}
