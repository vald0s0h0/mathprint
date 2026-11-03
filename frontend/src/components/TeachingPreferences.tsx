// Professeur autonome : son programme et sa matière se choisissent ici, ses cycles
// se déduisent des classes qu'il crée (backend services.education.teaching_scope).
import { Button, Card, Select, Stack, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { Save } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import { useAppState, type Teaching } from '../state/AppState'
import { options, showError, type Catalog } from './ClassEditor'

export default function TeachingPreferences({ programId, subjectId }: {
  programId: string | null; subjectId: string | null
}) {
  const { setTeaching } = useAppState()
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [program, setProgram] = useState(programId)
  const [subject, setSubject] = useState(subjectId)
  const [saving, setSaving] = useState(false)

  useEffect(() => { api.get<Catalog>('/api/operations/catalog').then(setCatalog).catch(showError) }, [])

  async function save() {
    setSaving(true)
    try {
      setTeaching(await api.put<Teaching>('/api/auth/me/teaching', { program_id: program, subject_id: subject }))
      notifications.show({ color: 'green', message: 'Programme et matière enregistrés' })
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
        <Text size="xs" c="dimmed">Vos cycles se déduisent automatiquement des classes que vous créez.</Text>
        <Button size="xs" onClick={save} loading={saving} leftSection={<Save size={14} />}
          disabled={!program || !subject}>
          Enregistrer
        </Button>
      </Stack>
    </Card>
  )
}
