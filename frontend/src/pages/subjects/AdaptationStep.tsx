// Étape Adaptation de l'assistant sujet : 3 options seulement, compatibles
// entraînement et contrôle.
import { Alert, Radio, Stack, Text } from '@mantine/core'

export const MODES = [
  { value: 'common', label: 'Commun', desc: 'Le même sujet pour toute la classe' },
  { value: 'common_variants', label: 'Variantes communes',
    desc: '3 variantes maximum pour toute la classe, distribuées au hasard (anti-copie)' },
  { value: 'individual', label: 'Individuel', desc: 'Difficulté adaptée et copie unique par élève' },
]

export default function AdaptationStep({
  mode, onChange, type,
}: { mode: string; onChange: (m: string) => void; type: string }) {
  return (
    <Stack mt="md">
      <Radio.Group label="Personnalisation des copies" value={mode} onChange={onChange}>
        <Stack mt="xs" gap="xs">
          {MODES.map((m) => (
            <Radio key={m.value} value={m.value}
              label={<span><Text component="span" fw={550} size="sm">{m.label}</Text>
                <Text component="span" size="xs" c="dimmed"> — {m.desc}</Text></span>} />
          ))}
        </Stack>
      </Radio.Group>
      {type === 'control' && mode !== 'common' && (
        <Alert color="orange">
          Règle d'équité : un contrôle personnalisé conserve un périmètre de compétences
          commun ; les notes brutes ne seront pas naïvement comparables.
        </Alert>
      )}
    </Stack>
  )
}
