// Section « Lecture OCR multicouche » des Paramètres admin : mode, parts
// d'audit et d'échantillon, planches Mathpix, plan et seuils des moteurs.
// Le plan et les seuils sont du JSON validé par le serveur (422 si incohérent).
import { Badge, Button, Code, CopyButton, Group, NumberInput, Select, Stack, Switch, Text, Textarea } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { Save } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import SettingsSection from './SettingsSection'

type Values = Record<string, { value: any }>

const MODES = [
  { value: 'mathpix', label: 'Mathpix seul' },
  { value: 'shadow', label: 'Ombre : le local enregistre, Mathpix décide' },
  { value: 'cascade', label: 'Cascade : le local décide' },
]

export default function OcrEngineSettings({ system, onSaved }: { system: Values; onSaved: () => void }) {
  const v = (k: string, d: any) => system[k]?.value ?? d
  const [mode, setMode] = useState<string>('mathpix')
  const [audit, setAudit] = useState<number | string>(2)
  const [shadowAudit, setShadowAudit] = useState<number | string>(5)
  const [timeoutH, setTimeoutH] = useState<number | string>(6)
  const [sample, setSample] = useState<number | string>(5)
  const [accept, setAccept] = useState<number | string>(95)
  const [margin, setMargin] = useState<number | string>(2)
  const [montage, setMontage] = useState(false)
  const [contextPass, setContextPass] = useState(true)
  const [maxCrops, setMaxCrops] = useState<number | string>(12)
  const [flush, setFlush] = useState<number | string>(10)
  const [lease, setLease] = useState<number | string>(15)
  const [plan, setPlan] = useState('')
  const [thresholds, setThresholds] = useState('')
  const [saving, setSaving] = useState(false)
  const [newToken, setNewToken] = useState('')

  useEffect(() => {
    setMode(v('ocr_mode', 'mathpix'))
    setAudit(Math.round(Number(v('ocr_audit_share', 0.02)) * 100))
    setShadowAudit(Math.round(Number(v('ocr_shadow_audit_share', 0.05)) * 100))
    setTimeoutH(Number(v('ocr_local_timeout_hours', 6)))
    setSample(Math.round(Number(v('ocr_full_sample_share', 1)) * 100))
    setAccept(Math.round(Number(v('ocr_accept_confidence', 0.95)) * 100))
    setMargin(Number(v('ocr_score_margin', 2)))
    setMontage(Boolean(v('ocr_montage_enabled', false)))
    setContextPass(Boolean(v('ocr_context_enabled', true)))
    setMaxCrops(Number(v('ocr_montage_max_crops', 12)))
    setFlush(Number(v('ocr_montage_flush_minutes', 10)))
    setLease(Number(v('ocr_lease_minutes', 15)))
    setPlan(JSON.stringify(v('ocr_engine_plan', {}), null, 2))
    setThresholds(JSON.stringify(v('ocr_engine_thresholds', {}), null, 2))
  }, [system])

  async function save() {
    setSaving(true)
    try {
      const put = (key: string, value: any) => api.post('/api/settings/system', { key, value: { value } })
      let planJson: unknown, thresholdsJson: unknown
      try { planJson = JSON.parse(plan) } catch { throw new Error('Plan des moteurs : JSON invalide') }
      try { thresholdsJson = JSON.parse(thresholds) } catch { throw new Error('Seuils : JSON invalide') }
      await put('ocr_engine_plan', planJson)
      await put('ocr_engine_thresholds', thresholdsJson)
      await put('ocr_audit_share', Number(audit) / 100)
      await put('ocr_shadow_audit_share', Number(shadowAudit) / 100)
      await put('ocr_local_timeout_hours', Number(timeoutH))
      await put('ocr_full_sample_share', Number(sample) / 100)
      await put('ocr_accept_confidence', Number(accept) / 100)
      await put('ocr_score_margin', Number(margin))
      await put('ocr_montage_enabled', montage)
      await put('ocr_context_enabled', contextPass)
      await put('ocr_montage_max_crops', Number(maxCrops))
      await put('ocr_montage_flush_minutes', Number(flush))
      await put('ocr_lease_minutes', Number(lease))
      await put('ocr_mode', mode)   // en dernier : un refus plus haut ne change pas le mode
      notifications.show({ color: 'green', message: 'Lecture OCR enregistrée' })
      onSaved()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setSaving(false)
    }
  }

  const tokenInfo = system.ocr_worker_token_set as { value: boolean; source?: string } | undefined

  // Génère un jeton aléatoire, l'enregistre côté serveur et l'affiche UNE fois : il ne revient jamais.
  async function generateToken() {
    const bytes = crypto.getRandomValues(new Uint8Array(32))
    const token = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
    try {
      await api.post('/api/settings/system', { key: 'ocr_worker_token', value: { value: token } })
      setNewToken(token)
      onSaved()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    }
  }

  return (
    <SettingsSection title="Lecture OCR multicouche"
      badge={<Badge variant="light" color={mode === 'cascade' ? 'orange' : 'gray'}>{mode}</Badge>}
      description="Plusieurs moteurs locaux lisent chaque découpe, un consensus déterministe décide, Mathpix n'intervient qu'en dernier recours. En mode ombre, rien ne change pour les notes : la lecture locale est seulement enregistrée et comparée.">
      <Stack>
        <Select label="Mode" data={MODES} value={mode} onChange={(x) => setMode(x ?? 'mathpix')}
          allowDeselect={false} maw={420}
          description="Qui lit et décide : Mathpix seul, ombre (le Mac enregistre) ou cascade (le Mac décide, Mathpix en renfort)." />
        <Group align="flex-end">
          <NumberInput label="Contrôle en cascade" suffix=" %" min={0} max={100} w={160}
            description="Décisions automatiques relues par le professeur, pour mesurer les erreurs."
            value={audit} onChange={setAudit} />
          <NumberInput label="Contrôle en ombre" suffix=" %" min={0} max={100} w={160}
            description="Zones acceptées par Mathpix, relues pour juger le Mac." value={shadowAudit} onChange={setShadowAudit} />
          <NumberInput label="Alerte Mac absent" suffix=" h" min={1} w={150}
            description="Au-delà, le lot passe en « Mac en retard » : Mathpix proposé."
            value={timeoutH} onChange={setTimeoutH} />
          <NumberInput label="Échantillon complet" suffix=" %" min={0} max={100} w={170}
            description="Découpes lues par tous les moteurs après la décision, pour la mesure seulement."
            value={sample} onChange={setSample} />
          <NumberInput label="Confiance écrite à l'accord" suffix=" %" min={0} max={100} w={210}
            description="Confiance inscrite quand les moteurs s'accordent (sert au calcul des notes)."
            value={accept} onChange={setAccept} />
          <NumberInput label="Marge de score" suffix=" nats" min={0} max={50} step={0.5} w={160}
            description="Écart minimal entre deux scores pour trancher. Plus haut : moins d'automatique, moins d'erreurs."
            value={margin} onChange={setMargin} />
          <NumberInput label="Bail d'une tâche" suffix=" min" min={1} w={150}
            description="Durée de réservation d'une tâche par le Mac, avant reprise si le Mac s'arrête."
            value={lease} onChange={setLease} />
        </Group>
        <Switch label="Second passage avec le contexte de l'exercice" checked={contextPass}
          onChange={(e) => setContextPass(e.currentTarget.checked)}
          description="Chaque moteur qui le peut (plan : context_engines) relit la découpe avec l'énoncé et la réponse attendue, dans la même vague que sa lecture libre. Ce que le contexte souffle ne compte que si un moteur l'a aussi lu sans contexte, ou si Mathpix le confirme." />
        <Group align="flex-end">
          <Switch label="Planches Mathpix" checked={montage} onChange={(e) => setMontage(e.currentTarget.checked)}
            description="Plusieurs découpes sur une même image Mathpix (moins cher, mais risque de fusion)." />
          <NumberInput label="Découpes par planche" min={1} w={170} value={maxCrops} onChange={setMaxCrops}
            description="Nombre maximum de découpes sur une planche." />
          <NumberInput label="Envoi au plus tard" suffix=" min" min={1} w={170} value={flush} onChange={setFlush}
            description="Une planche partie après ce délai, même incomplète." />
        </Group>
        <Text size="xs" c="dimmed">
          Laisser les planches désactivées tant que le banc n'a pas montré 0 écart avec la lecture unitaire.
        </Text>
        <Textarea label="Plan des moteurs (JSON)" autosize minRows={6} maxRows={18}
          description="Par classe de zone : moteurs qui lisent, qui décident, qui observent seulement, et familles corrélées. Ne pas modifier sans banc."
          styles={{ input: { fontFamily: 'monospace', fontSize: 12 } }}
          value={plan} onChange={(e) => setPlan(e.currentTarget.value)} />
        <Textarea label="Seuils par moteur (JSON)" autosize minRows={4} maxRows={14}
          description="Confiance minimale par moteur pour qu'une lecture compte."
          styles={{ input: { fontFamily: 'monospace', fontSize: 12 } }}
          value={thresholds} onChange={(e) => setThresholds(e.currentTarget.value)} />
        <Group>
          <Button leftSection={<Save size={14} />} loading={saving} onClick={save}>Enregistrer</Button>
        </Group>
        <Stack gap={6} mt="xs">
          <Group gap="sm">
            <Text size="sm" fw={600}>Jeton du worker (Mac)</Text>
            <Badge variant="light" color={tokenInfo?.value ? 'green' : 'gray'}>
              {tokenInfo?.value ? `défini (${tokenInfo.source === 'env' ? 'variable d\'environnement' : 'réglage'})` : 'absent : API du worker fermée'}
            </Badge>
            <Button size="xs" variant="light" onClick={generateToken}
              disabled={tokenInfo?.source === 'env'}>{tokenInfo?.value ? 'Régénérer' : 'Générer'}</Button>
          </Group>
          {newToken && (
            <Group gap="xs">
              <Code>{newToken}</Code>
              <CopyButton value={newToken}>
                {({ copied, copy }) => <Button size="compact-xs" variant="subtle" onClick={copy}>{copied ? 'Copié' : 'Copier'}</Button>}
              </CopyButton>
              <Text size="xs" c="dimmed">Affiché une seule fois : à poser dans MATHPRINT_OCR_WORKER_TOKEN du worker.</Text>
            </Group>
          )}
        </Stack>
      </Stack>
    </SettingsSection>
  )
}
