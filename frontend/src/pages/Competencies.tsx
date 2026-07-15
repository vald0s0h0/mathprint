// Référentiels par niveau — affichage hiérarchisé domaine (H1) > chapitre
// (H2) > compétences (H3). Le référentiel suit le cycle global. L'ID court
// (ex. A1.1, repris de la numérotation du sommaire) est affiché à côté du
// libellé de compétence — un libellé isolé (ex. "Automatismes") ne suffit
// pas à savoir de quoi il s'agit sans son chapitre.
import {
  Accordion, Badge, Group, ScrollArea, Select, Stack, Text, TextInput, Title,
} from '@mantine/core'
import { Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { useAppState } from '../state/AppState'

type Framework = { id: string; name: string; grade_level: string; version: string; status: string }
type Domain = {
  code: string; name: string
  chapters: { code: string; name: string; competencies: { id: string; code: string; short_id: string; label: string }[] }[]
}

export default function Competencies() {
  const [frameworks, setFrameworks] = useState<Framework[]>([])
  const [sel, setSel] = useState<string | null>(null)
  const [tree, setTree] = useState<Domain[]>([])
  const [filter, setFilter] = useState('')
  const { cycle } = useAppState()

  useEffect(() => {
    api.get<Framework[]>('/api/competencies/frameworks').then(setFrameworks)
  }, [])

  // le référentiel affiché suit le cycle filtré dans la barre du haut
  useEffect(() => {
    if (!frameworks.length) return
    const want = cycle === 'all' ? '5e' : cycle
    const fw = frameworks.find((f) => f.grade_level === want) ?? frameworks[0]
    if (fw) setSel(fw.id)
  }, [frameworks, cycle])

  useEffect(() => {
    if (sel) api.get<Domain[]>(`/api/competencies/tree?framework_id=${sel}`).then(setTree)
  }, [sel])

  const fw = frameworks.find((f) => f.id === sel)
  const filtered = useMemo(() => {
    if (!filter.trim()) return tree
    const q = filter.toLowerCase()
    return tree.map((d) => ({
      ...d,
      chapters: d.chapters.map((ch) => ({
        ...ch,
        competencies: ch.competencies.filter((c) => c.label.toLowerCase().includes(q)),
      })).filter((ch) => ch.competencies.length),
    })).filter((d) => d.chapters.length)
  }, [tree, filter])

  const total = tree.reduce((n, d) => n + d.chapters.reduce((m, ch) => m + ch.competencies.length, 0), 0)

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <div>
          <Title order={2}>Compétences</Title>
          {fw && (
            <Group gap="xs" mt={4}>
              <Badge variant="light" color={fw.status === 'published' ? 'green' : 'gray'} size="sm">
                v{fw.version} — {fw.status === 'published' ? 'publiée (immuable)' : fw.status}
              </Badge>
              <Text size="xs" c="dimmed">
                {total} objectifs d'apprentissage — {fw.grade_level === '6e'
                  ? 'cycle 3 (année 6e uniquement)' : 'cycle 4'}
              </Text>
            </Group>
          )}
        </div>
        <Group gap="xs">
          <TextInput size="xs" w={240} placeholder="Filtrer les objectifs…" value={filter}
            leftSection={<Search size={14} />}
            onChange={(e) => setFilter(e.target.value)} />
          {cycle === 'all' && (
            <Select size="xs" w={260} value={sel} onChange={setSel}
              data={frameworks.map((f) => ({ value: f.id, label: f.name }))} />
          )}
        </Group>
      </Group>

      <ScrollArea h="calc(100vh - 180px)">
        <Accordion multiple variant="separated" radius="md" key={`${sel}-${tree.length}`}
          defaultValue={tree.map((d) => d.code)}>
          {filtered.map((d) => (
            <Accordion.Item key={d.code} value={d.code}>
              <Accordion.Control>
                <Group gap="xs">
                  <Text fw={650} size="sm">{d.name}</Text>
                  <Text size="xs" c="dimmed">
                    {d.chapters.reduce((n, ch) => n + ch.competencies.length, 0)} objectifs
                  </Text>
                </Group>
              </Accordion.Control>
              <Accordion.Panel>
                <Stack gap="sm">
                  {d.chapters.map((ch) => (
                    <div key={ch.code}>
                      <Text size="xs" fw={700} c="dimmed" tt="uppercase" mb={4}>
                        {ch.code} {ch.name} ({ch.competencies.length})
                      </Text>
                      <Stack gap={2}>
                        {ch.competencies.map((c) => (
                          <Text key={c.id} size="sm" py={1} pl="sm"
                            style={{ borderLeft: '2px solid var(--mantine-color-default-border)' }}>
                            {c.short_id && <Text span c="dimmed" size="xs" mr={6}>{c.short_id}</Text>}
                            {c.label}
                          </Text>
                        ))}
                      </Stack>
                    </div>
                  ))}
                </Stack>
              </Accordion.Panel>
            </Accordion.Item>
          ))}
        </Accordion>
      </ScrollArea>
    </Stack>
  )
}
