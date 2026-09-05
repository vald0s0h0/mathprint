// Modale globale des scans non identifiés (QR/fiduciels illisibles) : affichée
// au démarrage de l'app et dès qu'un nouveau problème apparaît, quelle que soit
// la page courante. La page garde sa PLACE dans le lot (overlay « Non
// identifié », cf. services.pipeline.build_overlays) pour ne jamais décaler les
// copies physiques suivantes à l'impression — mais elle reste sans élève tant
// que le professeur ne la relie pas, ou ne confirme pas que c'est une erreur de
// scan. Les doublons ne remontent jamais ici : rien à décider, aucune perte.
import {
  Badge, Button, Group, Modal, NumberInput, Select, Stack, Text,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { ChevronLeft, ChevronRight, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, getToken } from '../api'
import AuthImg from './AuthImg'

type ScanProblem = {
  id: string; batch_id: string; source_index: number
  assessment_id: string; assessment_title: string
  class_id: string | null; class_name: string
  warnings: string[]; image: string
}
type ClassRow = { id: string; name: string }
type AssessmentRow = { id: string; title: string; class_id: string }
type StudentRow = { id: string; name: string }

const WARNING_LABELS: Record<string, string> = {
  main_qr_missing_or_invalid: 'QR introuvable',
  qr_hmac_invalid: 'QR corrompu',
  page_from_other_assessment: 'signée pour un autre sujet',
  not_enough_markers: 'repères de coin manquants',
  reprojection_error_high: 'page déformée',
}

export default function ScanProblemsModal() {
  const [problems, setProblems] = useState<ScanProblem[]>([])
  const [manuallyClosed, setManuallyClosed] = useState(false)
  const [idx, setIdx] = useState(0)
  const prevIdsRef = useRef<Set<string>>(new Set())

  const [classes, setClasses] = useState<ClassRow[]>([])
  const [assessments, setAssessments] = useState<AssessmentRow[]>([])
  const [students, setStudents] = useState<StudentRow[]>([])
  const [classId, setClassId] = useState<string | null>(null)
  const [assessmentId, setAssessmentId] = useState<string | null>(null)
  const [studentId, setStudentId] = useState<string | null>(null)
  const [pageNo, setPageNo] = useState<number>(1)
  const [confirmDismiss, setConfirmDismiss] = useState(false)
  const [busy, setBusy] = useState(false)

  const poll = useCallback(() => {
    api.get<ScanProblem[]>('/api/scans/problems').then((rows) => {
      const ids = new Set(rows.map((r) => r.id))
      // Un nouveau problème (jamais vu) rouvre la modale même si elle avait
      // été fermée — un problème déjà vu ne la force pas à se rouvrir en boucle.
      if ([...ids].some((id) => !prevIdsRef.current.has(id))) setManuallyClosed(false)
      prevIdsRef.current = ids
      setProblems(rows)
      setIdx((i) => Math.min(i, Math.max(0, rows.length - 1)))
    }).catch(() => {})
  }, [])

  useEffect(() => {
    if (!getToken()) return
    poll()
    const t = setInterval(poll, 15000)
    return () => clearInterval(t)
  }, [poll])

  useEffect(() => {
    if (!getToken()) return
    api.get<ClassRow[]>('/api/classes').then(setClasses).catch(() => {})
    api.get<AssessmentRow[]>('/api/assessments').then(setAssessments).catch(() => {})
  }, [])

  const current = problems[idx] ?? null

  // Repli par défaut sur le sujet/classe où la page a atterri : c'est presque
  // toujours le bon, seul l'élève manque. Le professeur peut les corriger tous
  // les deux si le dépôt en vrac a mélangé plusieurs sujets.
  useEffect(() => {
    if (!current) return
    setClassId(current.class_id)
    setAssessmentId(current.assessment_id)
    setStudentId(null)
    setPageNo(1)
    setConfirmDismiss(false)
  }, [current?.id])

  useEffect(() => {
    if (!classId) { setStudents([]); return }
    api.get<StudentRow[]>(`/api/classes/${classId}/students`).then(setStudents).catch(() => {})
  }, [classId])

  const assessmentsForClass = assessments.filter((a) => a.class_id === classId)

  function removeCurrentAndAdvance() {
    setProblems((rows) => rows.filter((r) => r.id !== current?.id))
  }

  async function handleLink() {
    if (!current || !assessmentId || !studentId) return
    setBusy(true)
    try {
      await api.post(`/api/scans/scanned-pages/${current.id}/resolve`, {
        action: 'link', assessment_id: assessmentId, student_id: studentId, page_no: pageNo,
      })
      notifications.show({ color: 'green', message: 'Page reliée — nouvelle tentative de lecture en cours' })
      removeCurrentAndAdvance()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setBusy(false)
    }
  }

  async function handleDismiss() {
    if (!current) return
    setBusy(true)
    try {
      await api.post(`/api/scans/scanned-pages/${current.id}/resolve`, { action: 'dismiss' })
      notifications.show({ color: 'gray', message: 'Page retirée du lot (erreur de scan)' })
      removeCurrentAndAdvance()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setBusy(false)
      setConfirmDismiss(false)
    }
  }

  const opened = problems.length > 0 && !manuallyClosed
  if (!opened || !current) return null

  return (
    <Modal opened={opened} onClose={() => setManuallyClosed(true)} size="lg"
      title={<Text fw={650}>Page de scan non identifiée</Text>}>
      <Stack gap="sm">
        <Group justify="space-between" wrap="nowrap">
          <Text size="sm" c="dimmed">
            Le QR ou les repères de coin sont illisibles sur cette page. Elle
            garde sa place dans le lot pour ne jamais décaler les copies
            suivantes à l'impression des corrections.
          </Text>
          {problems.length > 1 && (
            <Group gap={4} wrap="nowrap">
              <Button size="xs" variant="subtle" px={6}
                disabled={idx === 0} onClick={() => setIdx((i) => i - 1)}>
                <ChevronLeft size={16} />
              </Button>
              <Text size="xs" c="dimmed">{idx + 1}/{problems.length}</Text>
              <Button size="xs" variant="subtle" px={6}
                disabled={idx === problems.length - 1} onClick={() => setIdx((i) => i + 1)}>
                <ChevronRight size={16} />
              </Button>
            </Group>
          )}
        </Group>

        <Group align="flex-start" wrap="nowrap" gap="md">
          <AuthImg src={current.image} alt="Page non identifiée"
            style={{ width: 240, maxWidth: '40%', border: '1px solid var(--mantine-color-gray-4)',
              borderRadius: 4, objectFit: 'contain' }} />
          <Stack gap={6} style={{ flex: 1, minWidth: 0 }}>
            <Text size="xs" c="dimmed">
              Lot d'origine : {current.assessment_title} — {current.class_name}
            </Text>
            {current.warnings.length > 0 && (
              <Group gap={4}>
                {current.warnings.map((w) => (
                  <Badge key={w} size="xs" variant="light" color="orange">
                    {WARNING_LABELS[w] ?? w}
                  </Badge>
                ))}
              </Group>
            )}
            <Select label="Classe" size="sm" data={classes.map((c) => ({ value: c.id, label: c.name }))}
              value={classId} onChange={(v) => { setClassId(v); setAssessmentId(null); setStudentId(null) }} />
            <Select label="Sujet" size="sm" disabled={!classId}
              data={assessmentsForClass.map((a) => ({ value: a.id, label: a.title }))}
              value={assessmentId} onChange={(v) => { setAssessmentId(v); setStudentId(null) }} />
            <Select label="Élève" size="sm" searchable disabled={!assessmentId}
              placeholder="Reconnaître l'élève sur l'aperçu"
              data={students.map((s) => ({ value: s.id, label: s.name }))}
              value={studentId} onChange={setStudentId} />
            <NumberInput label="N° de page (sujet à plusieurs pages)" size="sm"
              min={1} value={pageNo} onChange={(v) => setPageNo(Number(v) || 1)} />
          </Stack>
        </Group>

        <Group justify="space-between" mt="xs">
          {!confirmDismiss ? (
            <Button variant="subtle" color="red" size="sm" leftSection={<Trash2 size={14} />}
              onClick={() => setConfirmDismiss(true)}>
              Erreur de scan — supprimer
            </Button>
          ) : (
            <Group gap="xs">
              <Text size="xs" c="red">Cette page ne sera jamais imprimée. Confirmer ?</Text>
              <Button size="xs" color="red" onClick={handleDismiss} loading={busy}>Confirmer</Button>
              <Button size="xs" variant="subtle" onClick={() => setConfirmDismiss(false)}>Annuler</Button>
            </Group>
          )}
          <Button onClick={handleLink} disabled={!assessmentId || !studentId} loading={busy}>
            Relier à cet élève
          </Button>
        </Group>
      </Stack>
    </Modal>
  )
}
