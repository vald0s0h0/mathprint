// Étape Compétences de l'assistant « Créer un sujet » : tableau complet des
// compétences du niveau (même hiérarchie/ordre que l'onglet Compétences), une
// colonne de maîtrise moyenne par classe du niveau, et ce que la banque
// contient pour chaque ligne. Le professeur coche des compétences, pas des
// exercices : en automatique la plateforme choisit, en manuel l'étape Mise en
// page propose les exercices de ces compétences (et les problèmes de leurs
// chapitres).
import {
  Accordion, Badge, Checkbox, Group, Progress, ScrollArea, Stack, Table, Text,
  TextInput, Tooltip,
} from '@mantine/core'
import { Search } from 'lucide-react'
import { Fragment, useEffect, useMemo, useState } from 'react'
import { api } from '../../api'
import { masteryColor } from '../../utils/mastery'

type ClassRef = { id: string; name: string }
type CompRow = {
  id: string; code: string; short_id: string; label: string
  mastery_by_class: Record<string, number | null>; exercise_count: number
}
type ChapterGroup = { code: string; name: string; problem_count: number; competencies: CompRow[] }
type DomainGroup = { code: string; name: string; chapters: ChapterGroup[] }
export type Matrix = { classes: ClassRef[]; domains: DomainGroup[] }

export default function CompetencyMatrixStep({
  gradeLevel, classId, selected, onChange, onMatrix,
}: {
  gradeLevel?: string; classId?: string | null
  selected: string[]; onChange: (ids: string[]) => void
  // la matrice chargée, remontée au parent (comptage des problèmes des
  // chapitres cochés, récapitulatif)
  onMatrix?: (m: Matrix) => void
}) {
  const [matrix, setMatrix] = useState<Matrix>({ classes: [], domains: [] })
  const [search, setSearch] = useState('')

  useEffect(() => {
    if (!gradeLevel) return
    api.get<Matrix>(`/api/assessments/competency-matrix?grade_level=${gradeLevel}`)
      .then((m) => { setMatrix(m); onMatrix?.(m) })
    // onMatrix est un rappel du parent : ne relance pas la requête
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gradeLevel])

  const sel = new Set(selected)
  function toggle(ids: string[], checked: boolean) {
    const rest = selected.filter((x) => !ids.includes(x))
    onChange(checked ? [...rest, ...ids] : rest)
  }

  // la classe du sujet d'abord : c'est SA maîtrise qui compte pour choisir
  const classes = useMemo(() => [...matrix.classes].sort((a, b) =>
    Number(b.id === classId) - Number(a.id === classId)), [matrix.classes, classId])

  const q = search.trim().toLowerCase()
  const domains = useMemo(() => matrix.domains.map((d) => ({
    ...d,
    chapters: d.chapters.map((ch) => ({
      ...ch,
      competencies: q ? ch.competencies.filter((c) =>
        `${c.short_id} ${c.code} ${c.label} ${ch.name}`.toLowerCase().includes(q))
        : ch.competencies,
    })).filter((ch) => ch.competencies.length > 0),
  })).filter((d) => d.chapters.length > 0), [matrix.domains, q])

  const total = matrix.domains.reduce(
    (n, d) => n + d.chapters.reduce((m, ch) => m + ch.competencies.length, 0), 0)

  return (
    <Stack gap="xs" style={{ flex: 1, minHeight: 0 }}>
      <Group justify="space-between" wrap="nowrap">
        <TextInput size="xs" w={280} placeholder="Filtrer les compétences…"
          leftSection={<Search size={13} />} value={search}
          onChange={(e) => setSearch(e.currentTarget.value)} />
        <Text size="xs" c="dimmed">
          {total} compétence(s) en {gradeLevel ?? '…'}
        </Text>
      </Group>
      <ScrollArea style={{ flex: 1 }} type="auto">
        <Accordion multiple variant="separated" radius="md"
          key={`${gradeLevel}-${matrix.domains.length}-${q}`}
          defaultValue={domains.map((d) => d.code)}>
          {domains.map((d) => {
            const inDomain = d.chapters.flatMap((ch) => ch.competencies)
            const nSel = inDomain.filter((c) => sel.has(c.id)).length
            return (
              <Accordion.Item key={d.code} value={d.code}>
                <Accordion.Control>
                  <Group gap="xs">
                    <Text fw={650} size="sm">{d.name}</Text>
                    {nSel > 0 && <Badge size="xs" variant="filled">{nSel}</Badge>}
                  </Group>
                </Accordion.Control>
                <Accordion.Panel>
                  <Table verticalSpacing={4} horizontalSpacing="xs" highlightOnHover
                    style={{ minWidth: 300 + classes.length * 72 }}>
                    <Table.Thead>
                      <Table.Tr>
                        <Table.Th style={{ minWidth: 300 }}>Compétence</Table.Th>
                        <Table.Th style={{ width: 80, textAlign: 'center', whiteSpace: 'nowrap' }}>Banque</Table.Th>
                        {classes.map((c) => (
                          <Table.Th key={c.id} style={{ width: 72, textAlign: 'center' }}>
                            <Text size="xs" fw={c.id === classId ? 700 : 500}
                              c={c.id === classId ? undefined : 'dimmed'}>{c.name}</Text>
                          </Table.Th>
                        ))}
                      </Table.Tr>
                    </Table.Thead>
                    <Table.Tbody>
                      {d.chapters.map((ch) => {
                        const usable = ch.competencies.filter((c) => c.exercise_count > 0)
                        const allOn = usable.length > 0 && usable.every((c) => sel.has(c.id))
                        const someOn = usable.some((c) => sel.has(c.id))
                        return (
                          <Fragment key={ch.code}>
                            <Table.Tr style={{ background: 'var(--mantine-color-default-hover)' }}>
                              <Table.Td colSpan={2 + classes.length} py={6}>
                                <Group justify="space-between" wrap="nowrap">
                                  <Checkbox size="xs" checked={allOn}
                                    indeterminate={someOn && !allOn}
                                    disabled={usable.length === 0}
                                    onChange={(e) => toggle(usable.map((c) => c.id), e.currentTarget.checked)}
                                    label={<Text size="xs" fw={700} tt="uppercase" c="dimmed">
                                      {ch.code} {ch.name}</Text>} />
                                  {ch.problem_count > 0 && (
                                    <Tooltip label="Problèmes et énigmes de ce chapitre, proposés dès qu'une de ses compétences est cochée">
                                      <Badge size="xs" variant="light" color="orange">
                                        {ch.problem_count} problème{ch.problem_count > 1 ? 's' : ''}
                                      </Badge>
                                    </Tooltip>
                                  )}
                                </Group>
                              </Table.Td>
                            </Table.Tr>
                            {ch.competencies.map((c) => {
                              const empty = c.exercise_count === 0
                              return (
                                <Table.Tr key={c.id} style={empty ? { opacity: 0.5 } : undefined}>
                                  <Table.Td>
                                    <Checkbox size="xs" checked={sel.has(c.id)}
                                      disabled={empty && !sel.has(c.id)}
                                      label={<Text size="sm">
                                        {c.short_id && <Text span c="dimmed" mr={6}>{c.short_id}</Text>}
                                        {c.label}</Text>}
                                      onChange={(e) => toggle([c.id], e.currentTarget.checked)} />
                                  </Table.Td>
                                  <Table.Td style={{ textAlign: 'center' }}>
                                    {empty ? (
                                      <Tooltip label="Aucun exercice publié pour cette compétence">
                                        <Text size="xs" c="dimmed">—</Text>
                                      </Tooltip>
                                    ) : (
                                      <Badge size="xs" variant="light" color="gray">
                                        {c.exercise_count} ex.
                                      </Badge>
                                    )}
                                  </Table.Td>
                                  {classes.map((cls) => {
                                    const m = c.mastery_by_class[cls.id]
                                    return (
                                      <Table.Td key={cls.id} style={{ textAlign: 'center' }}>
                                        {m == null ? (
                                          <Text size="xs" c="dimmed">—</Text>
                                        ) : (
                                          <Group gap={4} justify="center" wrap="nowrap">
                                            <Progress value={m * 100} size={6} w={28} color={masteryColor(m)} />
                                            <Text size="xs" c="dimmed">{Math.round(m * 100)}%</Text>
                                          </Group>
                                        )}
                                      </Table.Td>
                                    )
                                  })}
                                </Table.Tr>
                              )
                            })}
                          </Fragment>
                        )
                      })}
                    </Table.Tbody>
                  </Table>
                </Accordion.Panel>
              </Accordion.Item>
            )
          })}
          {domains.length === 0 && (
            <Text size="sm" c="dimmed" ta="center" py="xl">
              {q ? 'Aucune compétence ne correspond à ce filtre.'
                : 'Aucun référentiel de compétences pour ce niveau.'}
            </Text>
          )}
        </Accordion>
      </ScrollArea>
    </Stack>
  )
}
