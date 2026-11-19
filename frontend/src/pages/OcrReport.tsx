// Page admin « Lecture OCR » : diagnostic de la lecture multicouche (moteurs locaux, consensus,
// Mathpix, professeur). Affiche le MÊME JSON que `scripts/ocr_report.py --json`.
// En tête : les erreurs passées en automatique, puis le panneau go / no-go par classe de zone.
import { Alert, Badge, Card, Code, Group, Loader, Select, Stack, Table, Text, Title } from '@mantine/core'
import { useEffect, useState } from 'react'
import { api } from '../api'
import SettingsSection from '../components/SettingsSection'

type Wilson = { n: number; k: number; rate: number | null; low: number | null; high: number | null }
type GoClass = {
  decisions: number; automatic: number; automatic_share: number | null; manual: number; manual_share: number | null
  mathpix_confirmed: number; with_truth: Wilson; audited: Wilson; go: boolean; audits_missing: number
}
type Report = {
  auto_errors: { count: number; shown: number; items: Record<string, string>[] }
  go_no_go: { criterion: string; by_class: Record<string, GoClass> }
  volume: { total: number; mix: Record<string, number>; mix_share: Record<string, number> }
  reasons: { decision: string; reason: string; count: number }[]
  engines: Record<string, Record<string, {
    family: string; reads: number; illegible_rate: number | null; accuracy: Wilson
    latency_p50_ms: number | null; latency_p95_ms: number | null; devices: string[]
  }>>
  pairs: { class: string; a: string; b: string; agree_rate: number | null; wrong_agree: number; both_readable: number }[]
  marginal: { crops: number; with_truth?: number; engines: Record<string, {
    decisions_changed: number; automatic_decisions_lost: number; share_changed: number
    automatic_decisions_gained?: number; auto_errors_with?: number; auto_errors_without?: number }> }
  stacks?: { crops: number; sources?: string[]; stacks: {
    sources: string[]; crops: number; auto: number; auto_share: number; auto_errors: number
    auto_error_rate: Wilson; oracle: Wilson }[] }
  context_gain?: { crops: number; engines: Record<string, {
    crops: number; fixed: number; broken: number; copied_expected: number; wrong_answers: number
    free_accuracy: Wilson; ctx_accuracy: Wilson; copy_rate: Wilson }> }
  mathpix: { cost_eur: number; crops_read_by_mathpix: number; crops_via_montage: number; copies: number; cost_per_copy_eur: number | null }
  shadow: { zones: number; compared_items: number; agree: number; agreement: Wilson | null }
  waiting: { waiting: number; oldest_hours: number; over_timeout: number; timeout_hours: number }
  settings: { mode: string; audit_share: number; shadow_audit_share: number; full_sample_share: number }
}

const pct = (x: number | null | undefined) => (x == null ? '—' : `${(100 * x).toFixed(2)} %`)

export default function OcrReport() {
  const [rep, setRep] = useState<Report | null>(null)
  const [days, setDays] = useState<string | null>('')
  const [mode, setMode] = useState<string | null>('')
  const [cls, setCls] = useState<string | null>('')
  const [err, setErr] = useState('')

  useEffect(() => {
    setRep(null); setErr('')
    const q = new URLSearchParams()
    if (days) q.set('since_days', days)
    if (mode) q.set('mode', mode)
    if (cls) q.set('zone_class', cls)
    api.get<Report>(`/api/ocr/report?${q.toString()}`).then(setRep).catch((e: Error) => setErr(e.message))
  }, [days, mode, cls])

  return (
    <Stack maw={1100}>
      <Group justify="space-between" align="flex-end">
        <Title order={3}>Lecture OCR</Title>
        <Group gap="sm">
          <Select size="xs" w={130} placeholder="Période" value={days} onChange={setDays} allowDeselect={false}
            data={[{ value: '', label: 'Tout' }, { value: '1', label: '24 h' }, { value: '7', label: '7 jours' }, { value: '30', label: '30 jours' }]} />
          <Select size="xs" w={130} value={mode} onChange={setMode} allowDeselect={false}
            data={[{ value: '', label: 'Tous modes' }, { value: 'shadow', label: 'Ombre' }, { value: 'cascade', label: 'Cascade' }]} />
          <Select size="xs" w={160} value={cls} onChange={setCls} allowDeselect={false}
            data={[{ value: '', label: 'Toutes classes' }, { value: 'num_line', label: 'Nombres' }, { value: 'math_line', label: 'Expressions' },
              { value: 'text_line', label: 'Texte' }, { value: 'multiline_line', label: 'Rédaction' }]} />
        </Group>
      </Group>
      {err && <Alert color="red">{err}</Alert>}
      {!rep && !err && <Loader size="sm" />}
      {rep && <>
        {rep.waiting.waiting > 0 && (
          <Alert color={rep.waiting.over_timeout ? 'orange' : 'blue'} title="Lots en attente du Mac">
            {rep.waiting.waiting} lot(s), le plus ancien depuis {rep.waiting.oldest_hours} h
            {rep.waiting.over_timeout ? ` (${rep.waiting.over_timeout} au-delà de ${rep.waiting.timeout_hours} h : « Lire avec Mathpix » est proposé sur le lot)` : ''}.
          </Alert>
        )}
        <SettingsSection title={`Erreurs passées en automatique : ${rep.auto_errors.count}`}
          tone={rep.auto_errors.count ? 'danger' : 'default'}
          description="Décision automatique que le professeur a corrigée ensuite. C'est la mesure qui compte : l'objectif est zéro."
          badge={<Badge variant="light" color="gray">mode {rep.settings.mode}</Badge>}>
          {rep.auto_errors.items.length === 0 ? <Text size="sm" c="dimmed">Aucune erreur automatique constatée.</Text> : (
            <Table fz="xs" withRowBorders>
              <Table.Thead><Table.Tr><Table.Th>Découpe</Table.Th><Table.Th>Classe</Table.Th><Table.Th>Décision</Table.Th>
                <Table.Th>Accepté</Table.Th><Table.Th>Professeur</Table.Th><Table.Th>Source</Table.Th></Table.Tr></Table.Thead>
              <Table.Tbody>{rep.auto_errors.items.slice(0, 50).map((e) => (
                <Table.Tr key={e.crop_id}><Table.Td><Code>{e.crop_id.slice(0, 8)}</Code></Table.Td><Table.Td>{e.zone_class}</Table.Td>
                  <Table.Td>{e.decision}</Table.Td><Table.Td><Code>{e.accepted_surface}</Code></Table.Td>
                  <Table.Td><Code>{e.truth_surface}</Code></Table.Td><Table.Td>{e.truth_source}</Table.Td></Table.Tr>))}</Table.Tbody>
            </Table>
          )}
        </SettingsSection>

        <SettingsSection title="Passage en cascade, par classe de zone" description={rep.go_no_go.criterion}>
          <Table fz="xs" withRowBorders>
            <Table.Thead><Table.Tr><Table.Th>Classe</Table.Th><Table.Th>Décisions</Table.Th><Table.Th>Automatiques</Table.Th>
              <Table.Th>Manuelles</Table.Th><Table.Th>Audits</Table.Th><Table.Th>Erreurs / audits</Table.Th><Table.Th>Borne haute</Table.Th><Table.Th /></Table.Tr></Table.Thead>
            <Table.Tbody>{Object.entries(rep.go_no_go.by_class).map(([c, g]) => (
              <Table.Tr key={c}><Table.Td>{c}</Table.Td><Table.Td>{g.decisions}</Table.Td><Table.Td>{pct(g.automatic_share)}</Table.Td>
                <Table.Td>{pct(g.manual_share)}</Table.Td><Table.Td>{g.audited.n}</Table.Td>
                <Table.Td>{g.audited.k} / {g.audited.n}</Table.Td><Table.Td>{pct(g.audited.high)}</Table.Td>
                <Table.Td>{g.go ? <Badge color="green">GO</Badge> : <Badge color="gray" variant="light">encore {g.audits_missing} audits</Badge>}</Table.Td></Table.Tr>))}</Table.Tbody>
          </Table>
          <Text size="xs" c="dimmed" mt="xs">
            Audit actuel : {pct(rep.settings.audit_share)} en cascade, {pct(rep.settings.shadow_audit_share)} en ombre.
            Les premières semaines en cascade, un audit à 10 % accélère la mesure.
          </Text>
        </SettingsSection>

        <SettingsSection title="Où vont les découpes" description={`${rep.volume.total} découpes jugées`}>
          <Group gap="xs">{Object.entries(rep.volume.mix).map(([k, v]) => (
            <Badge key={k} variant="light" size="lg">{k} : {v} ({pct(rep.volume.mix_share[k])})</Badge>))}</Group>
          <Table fz="xs" mt="sm" withRowBorders>
            <Table.Thead><Table.Tr><Table.Th>Nombre</Table.Th><Table.Th>Décision</Table.Th><Table.Th>Raison</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>{rep.reasons.slice(0, 12).map((r, i) => (
              <Table.Tr key={i}><Table.Td>{r.count}</Table.Td><Table.Td>{r.decision}</Table.Td><Table.Td><Code>{r.reason || '—'}</Code></Table.Td></Table.Tr>))}</Table.Tbody>
          </Table>
        </SettingsSection>

        <SettingsSection title="Moteurs" description="Exactitude contre la vérité du professeur (intervalle de Wilson), latence, apport marginal sur l'échantillon complet.">
          <Table fz="xs" withRowBorders>
            <Table.Thead><Table.Tr><Table.Th>Classe</Table.Th><Table.Th>Moteur</Table.Th><Table.Th>Lectures</Table.Th><Table.Th>Illisible</Table.Th>
              <Table.Th>Exactitude</Table.Th><Table.Th>p50 / p95</Table.Th><Table.Th>Changerait si retiré</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>{Object.entries(rep.engines).flatMap(([c, es]) => Object.entries(es).map(([eng, e]) => (
              <Table.Tr key={c + eng}><Table.Td>{c}</Table.Td><Table.Td>{eng} <Text span size="xs" c="dimmed">({e.devices.join(', ')})</Text></Table.Td>
                <Table.Td>{e.reads}</Table.Td><Table.Td>{pct(e.illegible_rate)}</Table.Td>
                <Table.Td>{pct(e.accuracy.rate)} <Text span size="xs" c="dimmed">n={e.accuracy.n}</Text></Table.Td>
                <Table.Td>{e.latency_p50_ms ?? '—'} / {e.latency_p95_ms ?? '—'} ms</Table.Td>
                <Table.Td>{rep.marginal.engines[eng] ? `${rep.marginal.engines[eng].decisions_changed} (${pct(rep.marginal.engines[eng].share_changed)})` : '—'}</Table.Td></Table.Tr>)))}</Table.Tbody>
          </Table>
          {rep.pairs.length > 0 && <>
            <Text size="sm" fw={600} mt="md">Paires de moteurs</Text>
            <Table fz="xs" withRowBorders>
              <Table.Thead><Table.Tr><Table.Th>Classe</Table.Th><Table.Th>Paire</Table.Th><Table.Th>Accord</Table.Th><Table.Th>Accords à tort</Table.Th></Table.Tr></Table.Thead>
              <Table.Tbody>{rep.pairs.map((p, i) => (
                <Table.Tr key={i}><Table.Td>{p.class}</Table.Td><Table.Td>{p.a} × {p.b}</Table.Td><Table.Td>{pct(p.agree_rate)}</Table.Td>
                  <Table.Td>{p.wrong_agree}</Table.Td></Table.Tr>))}</Table.Tbody>
            </Table>
          </>}
        </SettingsSection>

        <SettingsSection title="Le contexte améliore-t-il la lecture ?"
          description="Moteurs qui lisent deux fois (image seule, puis avec l'énoncé et la réponse attendue), sur les découpes relues par le professeur. « Recopie » : le passage avec contexte écrit la réponse attendue alors que l'élève a écrit autre chose — ce biais ne vote jamais seul (garde-fou).">
          {!rep.context_gain || Object.keys(rep.context_gain.engines).length === 0
            ? <Text size="sm" c="dimmed">Pas encore de découpe relue par le professeur avec les deux passages.</Text> : (
            <Table fz="xs" withRowBorders>
              <Table.Thead><Table.Tr><Table.Th>Moteur</Table.Th><Table.Th>Découpes</Table.Th><Table.Th>Exactitude libre</Table.Th>
                <Table.Th>Avec contexte</Table.Th><Table.Th>Réparées</Table.Th><Table.Th>Cassées</Table.Th><Table.Th>Recopie l'attendu</Table.Th></Table.Tr></Table.Thead>
              <Table.Tbody>{Object.entries(rep.context_gain.engines).map(([eng, g]) => (
                <Table.Tr key={eng}><Table.Td>{eng}</Table.Td><Table.Td>{g.crops}</Table.Td>
                  <Table.Td>{pct(g.free_accuracy.rate)}</Table.Td>
                  <Table.Td><Text span size="xs" fw={600} c={(g.ctx_accuracy.rate ?? 0) > (g.free_accuracy.rate ?? 0) ? 'teal' : undefined}>{pct(g.ctx_accuracy.rate)}</Text></Table.Td>
                  <Table.Td>{g.fixed}</Table.Td><Table.Td>{g.broken}</Table.Td>
                  <Table.Td>{g.copied_expected} / {g.wrong_answers}</Table.Td></Table.Tr>))}</Table.Tbody>
            </Table>
          )}
          {Object.keys(rep.marginal.engines).length > 0 && <>
            <Text size="sm" fw={600} mt="md">Sans cette source (échantillon complet, {rep.marginal.crops} découpes, {rep.marginal.with_truth ?? 0} relues)</Text>
            <Table fz="xs" withRowBorders>
              <Table.Thead><Table.Tr><Table.Th>Retiré</Table.Th><Table.Th>Décisions changées</Table.Th><Table.Th>Auto perdues</Table.Th>
                <Table.Th>Auto gagnées</Table.Th><Table.Th>Erreurs auto avec → sans</Table.Th></Table.Tr></Table.Thead>
              <Table.Tbody>{Object.entries(rep.marginal.engines).map(([name, m]) => (
                <Table.Tr key={name}><Table.Td>{name}</Table.Td><Table.Td>{m.decisions_changed} ({pct(m.share_changed)})</Table.Td>
                  <Table.Td>{m.automatic_decisions_lost}</Table.Td><Table.Td>{m.automatic_decisions_gained ?? 0}</Table.Td>
                  <Table.Td>{m.auto_errors_with ?? 0} → {m.auto_errors_without ?? 0}</Table.Td></Table.Tr>))}</Table.Tbody>
            </Table>
          </>}
        </SettingsSection>

        <SettingsSection title="Quelle pile retenir ?"
          description="Chaque combinaison de sources (passage libre « moteur », passage avec contexte « moteur+ctx ») rejouée par la fusion complète sur l'échantillon complet relu par le professeur. Les plus sûres d'abord (borne haute de l'erreur automatique), puis les plus automatiques. « Proposée » : la bonne lecture figure parmi les propositions de la pile.">
          {!rep.stacks || rep.stacks.stacks.length === 0
            ? <Text size="sm" c="dimmed">Pas encore de découpe de l'échantillon complet relue par le professeur.</Text> : (
            <Table fz="xs" withRowBorders>
              <Table.Thead><Table.Tr><Table.Th>Pile</Table.Th><Table.Th>Découpes</Table.Th><Table.Th>Automatiques</Table.Th>
                <Table.Th>Erreurs auto</Table.Th><Table.Th>Borne haute</Table.Th><Table.Th>Bonne lecture proposée</Table.Th></Table.Tr></Table.Thead>
              <Table.Tbody>{rep.stacks.stacks.slice(0, 25).map((x) => (
                <Table.Tr key={x.sources.join('|')}><Table.Td>{x.sources.join(' + ')}</Table.Td><Table.Td>{x.crops}</Table.Td>
                  <Table.Td>{pct(x.auto_share)}</Table.Td>
                  <Table.Td><Text span size="xs" c={x.auto_errors ? 'red' : undefined}>{x.auto_errors} / {x.auto}</Text></Table.Td>
                  <Table.Td>{pct(x.auto_error_rate.high)}</Table.Td><Table.Td>{pct(x.oracle.rate)}</Table.Td></Table.Tr>))}</Table.Tbody>
            </Table>
          )}
        </SettingsSection>

        <SettingsSection title="Mathpix et mode ombre">
          <Group gap="xl">
            <div><Text size="xs" c="dimmed">Dépense (lecture multicouche)</Text><Text fw={600}>{rep.mathpix.cost_eur.toFixed(3)} €</Text></div>
            <div><Text size="xs" c="dimmed">Par copie</Text><Text fw={600}>{rep.mathpix.cost_per_copy_eur == null ? '—' : `${rep.mathpix.cost_per_copy_eur.toFixed(4)} €`}</Text></div>
            <div><Text size="xs" c="dimmed">Découpes lues / via planche</Text><Text fw={600}>{rep.mathpix.crops_read_by_mathpix} / {rep.mathpix.crops_via_montage}</Text></div>
            <div><Text size="xs" c="dimmed">Ombre : local = Mathpix</Text>
              <Text fw={600}>{rep.shadow.compared_items ? `${rep.shadow.agree}/${rep.shadow.compared_items} (${pct(rep.shadow.agreement?.rate)})` : '—'}</Text></div>
          </Group>
        </SettingsSection>

        <Card withBorder padding="sm">
          <Text size="xs" c="dimmed" mb={4}>JSON complet (identique à <Code>scripts/ocr_report.py --json</Code>)</Text>
          <Code block style={{ maxHeight: 280, overflow: 'auto', fontSize: 11 }}>{JSON.stringify(rep, null, 1)}</Code>
        </Card>
      </>}
    </Stack>
  )
}
