// Filtres de la barre du haut : chaque zone de l'application n'affiche que ce qui
// la concerne, et les onglets du professeur que ce qu'il enseigne.
import { Group, SegmentedControl, Select } from '@mantine/core'
import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { api } from '../api'
import { CYCLE_LABELS, CYCLES, useAppState, type Cycle } from '../state/AppState'

const ALL_LABEL = 'Tout'
// Au-delà, la barre de boutons ne tient plus dans l'en-tête.
const MAX_CYCLE_BUTTONS = 4

function cycleOptions(cycles: Cycle[]) {
  return [...cycles.map((c) => ({ value: c, label: CYCLE_LABELS[c] || c })), { value: 'all', label: ALL_LABEL }]
}

function TeachingFilter() {
  const { cycle, setCycle, subject, setSubject, teaching } = useAppState()
  const data = cycleOptions(teaching.cycles)
  return (
    <Group gap="xs" wrap="nowrap">
      {teaching.cycles.length > MAX_CYCLE_BUTTONS && (
        <Select aria-label="Cycle" size="xs" w={120} allowDeselect={false} data={data} value={cycle}
          onChange={(v) => setCycle((v || 'all') as Cycle)} />
      )}
      {teaching.cycles.length > 1 && teaching.cycles.length <= MAX_CYCLE_BUTTONS && (
        <SegmentedControl aria-label="Cycle" size="xs" data={data} value={cycle}
          onChange={(v) => setCycle(v as Cycle)} />
      )}
      {teaching.subjects.length > 1 && (
        <Select aria-label="Matière" size="xs" w={180} allowDeselect={false} value={subject}
          data={teaching.subjects.map((s) => ({ value: s.id, label: s.name }))}
          onChange={(v) => setSubject(v || 'math')} />
      )}
    </Group>
  )
}

function SchoolFilter() {
  const { school, setSchool, schools } = useAppState()
  return (
    <Select aria-label="Établissement" size="xs" w={260} placeholder="Tous les établissements"
      clearable searchable value={school} onChange={setSchool}
      data={schools.map((s) => ({ value: s.id, label: s.name }))} />
  )
}

type Option = { id: string; name: string }

function CatalogFilter() {
  const { catalog, setCatalog } = useAppState()
  const [programs, setPrograms] = useState<Option[]>([])
  const [subjects, setSubjects] = useState<Option[]>([])

  useEffect(() => {
    api.get<{ programs: Option[]; subjects: Option[] }>('/api/operations/catalog')
      .then((c) => { setPrograms(c.programs); setSubjects(c.subjects) })
      .catch(() => {})
  }, [])

  return (
    <Group gap="xs" wrap="nowrap">
      <SegmentedControl aria-label="Programme" size="xs" value={catalog.program}
        onChange={(program) => setCatalog({ program })}
        data={[...programs.map((p) => {
          const label = p.name.replace(/^Programme\s+/, '')
          return { value: p.id, label: label.charAt(0).toUpperCase() + label.slice(1) }
        }), { value: 'all', label: ALL_LABEL }]} />
      <Select aria-label="Cycle" size="xs" w={120} allowDeselect={false} data={cycleOptions(CYCLES)}
        value={catalog.cycle} onChange={(v) => setCatalog({ cycle: (v || 'all') as Cycle })} />
      <Select aria-label="Matière" size="xs" w={180} allowDeselect={false} value={catalog.subject}
        data={subjects.map((s) => ({ value: s.id, label: s.name }))}
        onChange={(v) => setCatalog({ subject: v || 'math' })} />
    </Group>
  )
}

export default function HeaderFilters() {
  const { pathname } = useLocation()
  if (pathname === '/parametres') return null
  if (pathname === '/etablissements') return <SchoolFilter />
  if (pathname === '/exercices') return <CatalogFilter />
  return <TeachingFilter />
}
