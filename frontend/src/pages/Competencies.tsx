// Référentiels par niveau — affichage hiérarchisé domaine (H1) > chapitre
// (H2) > compétences (H3). Le référentiel suit le cycle global. L'ID court
// (ex. A1.1, repris de la numérotation du sommaire) est affiché à côté du
// libellé de compétence — un libellé isolé (ex. "Automatismes") ne suffit
// pas à savoir de quoi il s'agit sans son chapitre.
import {
  Badge, Group, ScrollArea, Stack, Text, TextInput, Title,
} from '@mantine/core'
import { Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import CompetencyHierarchy from '../components/CompetencyHierarchy'
import GradeSelectionRequired from '../components/GradeSelectionRequired'
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
    if (cycle === 'all') {
      setSel(null)
      setTree([])
      return
    }
    const fw = frameworks.find((f) => f.grade_level === cycle)
    setSel(fw?.id ?? null)
    setTree([])
  }, [frameworks, cycle])

  useEffect(() => {
    if (!sel) return
    let current = true
    api.get<Domain[]>(`/api/competencies/tree?framework_id=${sel}`)
      .then((domains) => { if (current) setTree(domains) })
    return () => { current = false }
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
  const hierarchy = useMemo(() => filtered.map((domain) => ({
    key: domain.code || domain.name,
    code: domain.code,
    name: domain.name,
    chapters: domain.chapters.map((chapter) => ({
      key: `${domain.code}/${chapter.code || chapter.name}`,
      code: chapter.code,
      name: chapter.name,
      rows: chapter.competencies,
    })),
  })), [filtered])

  if (cycle === 'all') return <GradeSelectionRequired title="Compétences" />

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
        </Group>
      </Group>

      <ScrollArea h="calc(100vh - 180px)">
        <CompetencyHierarchy domains={hierarchy}
          getRowKey={(competency) => competency.id}
          getShortId={(competency) => competency.short_id || competency.code}
          getLabel={(competency) => competency.label} />
      </ScrollArea>
    </Stack>
  )
}
