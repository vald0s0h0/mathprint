import { Button, Group, Stack, Text, Textarea } from '@mantine/core'
import MathText from './MathText'

export default function FeedbackEditor({ options, value, onChange }: {
  options: string[]; value: string; onChange: (value: string) => void
}) {
  return <Stack gap={6}>
    <Text size="xs" fw={600} tt="uppercase">Micro-correction</Text>
    {options.length > 0 && <Group gap="xs">
      {options.map((option) => <Button key={option} size="xs" variant={value === option ? 'filled' : 'light'}
        h="auto" py={6} styles={{ label: { whiteSpace: 'normal', textAlign: 'left' } }}
        onClick={() => onChange(option)}><MathText text={option} /></Button>)}
    </Group>}
    <Textarea label="Message personnalisé" placeholder="Écrivez une micro-correction ou choisissez une proposition"
      autosize minRows={2} value={value} onChange={(e) => onChange(e.currentTarget.value)} />
  </Stack>
}
