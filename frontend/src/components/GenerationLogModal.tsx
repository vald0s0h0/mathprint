// Journal de génération d'un sujet (bouton « Voir log » de l'écran Sujets) :
// suit en direct jobs.log_text alimenté par le worker de fond — repère
// immédiatement l'étape en cours (banque LLM, copie n/N, PDF) ou la cause
// d'un échec, sans SSH sur le NAS.
import { Badge, Group, Modal, Progress, ScrollArea, Stack, Text } from '@mantine/core'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

type JobLog = {
  status: string
  progress: number
  progress_message: string | null
  error: string | null
  updated_at: string | null
  log: string
}

const STATUS_COLOR: Record<string, string> = {
  pending: 'yellow', running: 'orange', done: 'green', failed: 'red',
}

export default function GenerationLogModal({ assessmentId, title, onClose }: {
  assessmentId: string | null
  title?: string
  onClose: () => void
}) {
  const [data, setData] = useState<JobLog | null>(null)
  const viewport = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!assessmentId) {
      setData(null)
      return
    }
    let stop = false
    const load = () =>
      api.get<JobLog>(`/api/assessments/${assessmentId}/generation-log`)
        .then((d) => {
          if (stop) return
          setData(d)
          // suivre la fin du log comme un `tail -f`
          requestAnimationFrame(() => viewport.current?.scrollTo(
            { top: viewport.current.scrollHeight }))
        })
        .catch(() => { if (!stop) setData(null) })
    load()
    const t = setInterval(load, 3000)
    return () => { stop = true; clearInterval(t) }
  }, [assessmentId])

  return (
    <Modal opened={!!assessmentId} onClose={onClose} size="xl"
      title={<Text fw={650}>Journal de génération{title ? ` — ${title}` : ''}</Text>}>
      {!data ? (
        <Text size="sm" c="dimmed">Aucun journal disponible pour ce sujet.</Text>
      ) : (
        <Stack gap="xs">
          <Group gap="sm">
            <Badge variant="light" color={STATUS_COLOR[data.status] ?? 'gray'}>
              {data.status}
            </Badge>
            {data.progress_message && <Text size="sm">{data.progress_message}</Text>}
          </Group>
          {['pending', 'running'].includes(data.status) && (
            <Progress value={data.progress} animated size="sm" />
          )}
          {data.error && <Text size="sm" c="red">{data.error}</Text>}
          <ScrollArea h={380} viewportRef={viewport} type="auto"
            style={{ borderRadius: 8, border: '1px solid var(--mantine-color-default-border)' }}>
            <Text component="pre" size="xs" p="sm" m={0}
              style={{ fontFamily: 'ui-monospace, monospace', whiteSpace: 'pre-wrap' }}>
              {data.log || 'Le journal est vide pour l’instant — la génération démarre…'}
            </Text>
          </ScrollArea>
          <Text size="xs" c="dimmed">
            Actualisé toutes les 3 s{data.updated_at
              ? ` — dernière activité du worker : ${new Date(data.updated_at).toLocaleTimeString()}`
              : ''}
          </Text>
        </Stack>
      )}
    </Modal>
  )
}
