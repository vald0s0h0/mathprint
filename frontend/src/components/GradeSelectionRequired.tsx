import { Alert, Stack, Title } from '@mantine/core'
import { AlertTriangle } from 'lucide-react'

export const GRADE_SELECTION_MESSAGE =
  'Sélectionne une classe (6ᵉ, 5ᵉ, 4ᵉ ou 3ᵉ) avec le sélecteur en haut de la page pour voir ses compétences.'

export default function GradeSelectionRequired({ title }: { title: string }) {
  return (
    <Stack>
      <Title order={2}>{title}</Title>
      <Alert color="blue" icon={<AlertTriangle size={16} />}>
        {GRADE_SELECTION_MESSAGE}
      </Alert>
    </Stack>
  )
}
