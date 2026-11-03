import { Alert, Stack, Title } from '@mantine/core'
import { AlertTriangle } from 'lucide-react'

export const GRADE_SELECTION_MESSAGE =
  'Choisis un cycle en haut de la page pour voir ses compétences.'

// `noClass` : aucun cycle à proposer tant que le professeur n'a pas créé de classe.
export default function GradeSelectionRequired({ title, noClass }: { title: string; noClass?: boolean }) {
  return (
    <Stack>
      <Title order={2}>{title}</Title>
      <Alert color="blue" icon={<AlertTriangle size={16} />}>
        {noClass ? 'Créez une classe dans l’onglet Élèves : ses compétences s’afficheront ici.' : GRADE_SELECTION_MESSAGE}
      </Alert>
    </Stack>
  )
}
