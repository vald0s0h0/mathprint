import { Box, Stack, Text, UnstyledButton } from '@mantine/core'
import { CYCLES } from '../state/AppState'

export type ClassChoice = { id: string; name: string; grade_level: string }

export function sortClassChoices<T extends ClassChoice>(classes: T[]): T[] {
  return [...classes].sort((a, b) => {
    const gradeDiff = CYCLES.indexOf(a.grade_level as typeof CYCLES[number])
      - CYCLES.indexOf(b.grade_level as typeof CYCLES[number])
    return gradeDiff || a.name.localeCompare(b.name, 'fr')
  })
}

export default function CompactClassSelector({ classes, value, onChange }: {
  classes: ClassChoice[]
  value: string | null
  onChange: (schoolClass: ClassChoice) => void
}) {
  const sorted = sortClassChoices(classes)
  const groups = CYCLES.map((grade) => ({
    grade,
    rows: sorted.filter((schoolClass) => schoolClass.grade_level === grade),
  })).filter((group) => group.rows.length > 0)

  return (
    <Box w={108} miw={108}>
      <Stack gap={8}>
        {groups.map((group) => (
          <Stack key={group.grade} gap={2}>
            <Text size="10px" fw={700} c="dimmed" tt="uppercase" px={5}>
              {group.grade}
            </Text>
            {group.rows.map((schoolClass) => {
              const selected = schoolClass.id === value
              return (
                <UnstyledButton key={schoolClass.id} onClick={() => onChange(schoolClass)}
                  title={schoolClass.name}
                  style={{
                    width: '100%', padding: '7px 8px', borderRadius: 7,
                    background: selected ? 'var(--mantine-primary-color-light)' : undefined,
                    color: selected ? 'var(--mantine-primary-color-filled)' : undefined,
                  }}>
                  <Text size="xs" fw={selected ? 700 : 500} truncate>
                    {schoolClass.name}
                  </Text>
                </UnstyledButton>
              )
            })}
          </Stack>
        ))}
        {groups.length === 0 && (
          <Text size="xs" c="dimmed" ta="center">Aucune classe</Text>
        )}
      </Stack>
    </Box>
  )
}
