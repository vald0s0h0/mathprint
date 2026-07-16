// Étape Exercices de l'assistant sujet : tableau complet des compétences du
// niveau (même hiérarchie/ordre que l'onglet Compétences), une colonne de
// maîtrise moyenne par classe du niveau. Le professeur coche des
// compétences, pas des exercices — l'application se charge de choisir/
// générer les exercices correspondants une fois le sujet mis en file.
import { Accordion, Badge, Checkbox, Group, Progress, ScrollArea, SegmentedControl, Stack, Table, Text } from '@mantine/core'
import { Fragment, useEffect, useState } from 'react'
import { api } from '../../api'
import { masteryColor } from '../../utils/mastery'

type ClassRef = { id: string; name: string }
type CompRow = { id: string; code: string; short_id: string; label: string; mastery_by_class: Record<string, number | null> }
type ChapterGroup = { code: string; name: string; competencies: CompRow[] }
type DomainGroup = { code: string; name: string; chapters: ChapterGroup[] }
type Matrix = { classes: ClassRef[]; domains: DomainGroup[] }

export default function CompetencyMatrixStep({
  gradeLevel, selected, onChange, source, onSourceChange,
}: {
  gradeLevel?: string; selected: string[]; onChange: (ids: string[]) => void
  source: string; onSourceChange: (source: string) => void
}) {
  const [matrix, setMatrix] = useState<Matrix>({ classes: [], domains: [] })

  useEffect(() => {
    if (!gradeLevel) return
    api.get<Matrix>(`/api/assessments/competency-matrix?grade_level=${gradeLevel}`).then(setMatrix)
  }, [gradeLevel])

  const sel = new Set(selected)
  function toggle(id: string, checked: boolean) {
    onChange(checked ? [...selected, id] : selected.filter((x) => x !== id))
  }

  const total = matrix.domains.reduce(
    (n, d) => n + d.chapters.reduce((m, ch) => m + ch.competencies.length, 0), 0)

  return (
    <Stack gap="xs">
      <Group justify="space-between" align="flex-end">
        <Stack gap={2}>
          <Text size="xs" c="dimmed" fw={600}>Source des exercices</Text>
          {/* Les deux sources lisent le manuel 5e : Sésamaths en ADAPTE les
              exercices, Gemini s'en sert de référence (programme, niveau) pour
              en CRÉER d'autres — pas de géométrie pour l'instant. */}
          <SegmentedControl size="xs" value={source} onChange={onSourceChange} data={[
            { value: 'auto', label: 'Automatique' },
            { value: 'mathalea', label: 'MathALÉA' },
            { value: 'sesamaths', label: 'Sésamaths (5e)' },
            { value: 'gemini', label: 'Gemini' },
          ]} />
        </Stack>
        <Badge variant="light">{selected.length} sélectionnée(s)</Badge>
      </Group>
      <Group justify="space-between">
        <Text size="xs" c="dimmed">
          {total} compétence(s) — {matrix.classes.length} classe(s) en {gradeLevel ?? '…'}
        </Text>
      </Group>
      <ScrollArea h={360}>
        <Accordion multiple variant="separated" radius="md"
          key={`${gradeLevel}-${matrix.domains.length}`}
          defaultValue={matrix.domains.map((d) => d.code)}>
          {matrix.domains.map((d) => (
            <Accordion.Item key={d.code} value={d.code}>
              <Accordion.Control>
                <Text fw={650} size="sm">{d.name}</Text>
              </Accordion.Control>
              <Accordion.Panel>
                <Table verticalSpacing={4} horizontalSpacing="xs"
                  style={{ minWidth: 260 + matrix.classes.length * 72 }}>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th style={{ minWidth: 260 }}>Compétence</Table.Th>
                      {matrix.classes.map((c) => (
                        <Table.Th key={c.id} style={{ width: 72, textAlign: 'center' }}>
                          {c.name}
                        </Table.Th>
                      ))}
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {d.chapters.map((ch) => (
                      <Fragment key={ch.code}>
                        <Table.Tr>
                          <Table.Td colSpan={1 + matrix.classes.length} pt={10}>
                            <Text size="xs" fw={700} c="dimmed" tt="uppercase">{ch.code} {ch.name}</Text>
                          </Table.Td>
                        </Table.Tr>
                        {ch.competencies.map((c) => (
                          <Table.Tr key={c.id}>
                            <Table.Td>
                              <Checkbox size="xs" checked={sel.has(c.id)}
                                label={<Text size="sm">{c.short_id && <Text span c="dimmed" mr={6}>{c.short_id}</Text>}{c.label}</Text>}
                                onChange={(e) => toggle(c.id, e.target.checked)} />
                            </Table.Td>
                            {matrix.classes.map((cls) => {
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
                        ))}
                      </Fragment>
                    ))}
                  </Table.Tbody>
                </Table>
              </Accordion.Panel>
            </Accordion.Item>
          ))}
        </Accordion>
      </ScrollArea>
    </Stack>
  )
}
