// Grilles officielles (programmes cycles 3 et 4) — affichage compact et
// hiérarchisé : domaine > thème > objectifs, une ligne par compétence.
import {
  Accordion, Badge, Group, ScrollArea, Select, Stack, Text, TextInput, Title,
} from '@mantine/core'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'

type Framework = { id: string; name: string; grade_level: string; version: string; status: string }
type Domain = {
  code: string; name: string
  themes: { code: string; name: string; competencies: { id: string; code: string; label: string }[] }[]
}

export default function Competencies() {
  const [frameworks, setFrameworks] = useState<Framework[]>([])
  const [sel, setSel] = useState<string | null>(null)
  const [tree, setTree] = useState<Domain[]>([])
  const [filter, setFilter] = useState('')

  useEffect(() => {
    api.get<Framework[]>('/api/competencies/frameworks').then((f) => {
      setFrameworks(f)
      const fw5 = f.find((x) => x.grade_level === '5e') ?? f[0]
      if (fw5) setSel(fw5.id)
    })
  }, [])

  useEffect(() => {
    if (sel) api.get<Domain[]>(`/api/competencies/tree?framework_id=${sel}`).then(setTree)
  }, [sel])

  const fw = frameworks.find((f) => f.id === sel)
  const filtered = useMemo(() => {
    if (!filter.trim()) return tree
    const q = filter.toLowerCase()
    return tree.map((d) => ({
      ...d,
      themes: d.themes.map((t) => ({
        ...t,
        competencies: t.competencies.filter(
          (c) => c.label.toLowerCase().includes(q) || c.code.toLowerCase().includes(q)),
      })).filter((t) => t.competencies.length),
    })).filter((d) => d.themes.length)
  }, [tree, filter])

  const total = tree.reduce((n, d) => n + d.themes.reduce((m, t) => m + t.competencies.length, 0), 0)

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Title order={2}>Compétences</Title>
        <Group gap="xs">
          <TextInput size="xs" w={240} placeholder="Filtrer…" value={filter}
            onChange={(e) => setFilter(e.target.value)} />
          <Select size="xs" w={260} value={sel} onChange={setSel}
            data={frameworks.map((f) => ({ value: f.id, label: f.name }))} />
        </Group>
      </Group>
      {fw && (
        <Group gap="xs">
          <Badge color={fw.status === 'published' ? 'green' : 'gray'} size="sm">
            v{fw.version} — {fw.status === 'published' ? 'publiée (immuable)' : fw.status}
          </Badge>
          <Text size="xs" c="dimmed">
            {total} objectifs d'apprentissage — {fw.grade_level === '6e'
              ? 'cycle 3 (année 6e uniquement, hors primaire)' : 'cycle 4'}
          </Text>
        </Group>
      )}
      <ScrollArea h="calc(100vh - 190px)">
        <Accordion multiple variant="contained" defaultValue={filtered.map((d) => d.code)}>
          {filtered.map((d) => (
            <Accordion.Item key={d.code} value={d.code}>
              <Accordion.Control>
                <Group gap="xs">
                  <Badge variant="filled" size="sm">{d.code}</Badge>
                  <Text fw={600} size="sm">{d.name}</Text>
                  <Text size="xs" c="dimmed">
                    {d.themes.reduce((n, t) => n + t.competencies.length, 0)} objectifs
                  </Text>
                </Group>
              </Accordion.Control>
              <Accordion.Panel>
                <Stack gap="xs">
                  {d.themes.map((t) => (
                    <div key={t.code}>
                      <Text size="xs" fw={700} c="dimmed" tt="uppercase" mb={2}>
                        {t.name} ({t.competencies.length})
                      </Text>
                      {t.competencies.map((c) => (
                        <Group key={c.id} gap={8} wrap="nowrap" py={1}>
                          <Text size="xs" ff="monospace" c="dimmed" w={130} style={{ flexShrink: 0 }}>
                            {c.code}
                          </Text>
                          <Text size="sm" lineClamp={1} title={c.label}>{c.label}</Text>
                        </Group>
                      ))}
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
