// Professeur autonome : son programme et sa matière se choisissent ici, ses cycles
// se déduisent des classes qu'il crée (backend services.education.teaching_scope).
import { Button, Card, Select, Stack, Text } from '@mantine/core'
import { Save } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import { options, showError, type Catalog } from './ClassEditor'

export default function TeachingPreferences({ programId, subjectId }: {
  programId: string | null; subjectId: string | null
}) {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [program, setProgram] = useState(programId)
  const [subject, setSubject] = useState(subjectId)
  const [saving, setSaving] = useState(false)

  useEffect(() => { api.get<Catalog>('/api/operations/catalog').then(setCatalog).catch(showError) }, [])

  async function save() {
    setSaving(true)
    try {
      await api.put('/api/auth/me/teaching', { program_id: program, subject_id: subject })
      try {
        const catalog = JSON.parse(localStorage.getItem('mathprint_catalog') || '{}')
        localStorage.setItem('mathprint_catalog', JSON.stringify({ ...catalog, program }))
      } catch { /* Le choix du catalogue reste facultatif. */ }
      window.location.reload()
    } catch (e) { showError(e) } finally { setSaving(false) }
  }

  return (
    <Card withBorder maw={420} mt="md">
      <Stack gap="xs">
        <Text fw={600} size="sm">Mon enseignement</Text>
        <Select label="Programme" allowDeselect={false} data={options(catalog?.programs || [])}
          value={program} onChange={setProgram} />
        <Select label="Matière" allowDeselect={false} data={options(catalog?.subjects || [])}
          value={subject} onChange={setSubject} />
        <Text size="xs" c="dimmed">Chaque programme conserve ses classes et ses sujets. Changez de programme ici pour passer de l’un à l’autre.</Text>
        <Button size="xs" onClick={save} loading={saving} leftSection={<Save size={14} />}
          disabled={!program || !subject}>
          Enregistrer
        </Button>
      </Stack>
    </Card>
  )
}
