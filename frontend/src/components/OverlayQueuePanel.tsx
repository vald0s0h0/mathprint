// File d'attente de l'overlay : prochaine copie imprimée et choix de la reprise.
// L'ordinateur ne voit pas les feuilles sortir : seul le professeur sait où
// l'impression s'est arrêtée. Le panneau lui dit donc quoi regarder sur
// l'imprimante et lui laisse désigner l'élève à partir duquel reprendre.
import { Alert, Box, Paper, Stack, Text, UnstyledButton } from '@mantine/core'
import { useEffect, useRef } from 'react'

export type OverlayQueueEntry = {
  position: number; student: string; identified: boolean; pages: string
}

export type OverlayLastPrint = {
  status: string; error?: string | null; job_id?: string | null
  pass_side: 'all' | 'recto' | 'verso'; start_position: number | null
  sheets: number; first_student: string; last_student: string
  printer: string; sent_at: string
}

export type OverlayQueue = {
  blocked: string | null; total: number; adf_reversed: boolean
  assessment_duplex: boolean; entries: OverlayQueueEntry[]
  last_print: OverlayLastPrint | null
}

const SETTLED = ['submitted', 'uncertain']
const FAILED = ['failed', 'cancelled']

// Position proposée d'office. `null` : le dernier envoi a abouti, relancer sans
// choix explicite imprimerait une seconde fois sur des copies déjà corrigées.
export function defaultStartPosition(queue: OverlayQueue, manualDuplex: boolean): number | null {
  const last = queue.last_print
  if (!last) return 1
  if (FAILED.includes(last.status)) return last.start_position ?? null
  if (SETTLED.includes(last.status)) return manualDuplex && last.pass_side === 'recto' ? 1 : null
  return null
}

function sentTime(iso: string) {
  return new Date(iso).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' })
}

function sheetsLabel(count: number) {
  return `${count} feuille${count > 1 ? 's' : ''}`
}

// Un travail resté dans la file du système repartirait après le déblocage de
// l'imprimante : rien ne permet de l'annuler d'ici, seul le professeur le peut.
export function ResumeWarning() {
  return (
    <Alert color="orange" p={6}>
      <Text size="xs">
        <b>Avant de reprendre :</b> supprimez l’ancien travail de la file d’attente de
        l’imprimante sur l’ordinateur, sinon il imprimera en double.
      </Text>
    </Alert>
  )
}

function LastPrintNotice({ last }: { last: OverlayLastPrint }) {
  const range = `${sheetsLabel(last.sheets)}, de ${last.first_student} à ${last.last_student}`
  if (FAILED.includes(last.status)) {
    return (
      <Alert color="red" p="xs">
        <Text size="xs">
          L’envoi du {sentTime(last.sent_at)} a échoué ({range}), aucune feuille ne devrait être sortie.
          {last.error ? ` ${last.error}` : ''}
        </Text>
      </Alert>
    )
  }
  if (last.status === 'uncertain') {
    return (
      <Alert color="orange" p="xs">
        <Text size="xs">
          Envoi du {sentTime(last.sent_at)} à vérifier ({range}) : le poste a redémarré pendant
          l’envoi. Regardez la file d’attente de l’imprimante sur l’ordinateur avant de reprendre.
        </Text>
      </Alert>
    )
  }
  if (last.status !== 'submitted') return null
  return (
    <Alert color="blue" p="xs">
      <Text size="xs">
        Dernier envoi le {sentTime(last.sent_at)} : {range}.
        {last.pass_side === 'recto' && ' Passe recto.'}
        {last.pass_side === 'verso' && ' Passe verso.'}
      </Text>
    </Alert>
  )
}

export default function OverlayQueuePanel({ queue, start, onSelect, disabled, verso }: {
  queue: OverlayQueue
  start: number | null
  onSelect: (position: number) => void
  disabled: boolean
  verso?: boolean
}) {
  const listRef = useRef<HTMLDivElement>(null)
  const rowRefs = useRef(new Map<number, HTMLElement>())
  const next = start ? queue.entries[start - 1] : null
  // Une seule face par feuille : le numéro de page n'apprendrait rien.
  const pagesOf = (entry: OverlayQueueEntry) => queue.assessment_duplex ? entry.pages : ''

  useEffect(() => {
    // Défilement limité à la liste : la page derrière le panneau ne bouge pas.
    const list = listRef.current
    const row = start ? rowRefs.current.get(start) : null
    if (list && row) list.scrollTop = Math.max(0, row.offsetTop - list.clientHeight / 2)
  }, [start, queue.entries.length])

  return (
    <Stack gap={8}>
      {queue.last_print && <LastPrintNotice last={queue.last_print} />}

      <Paper withBorder p="xs" bg={next ? 'var(--mantine-primary-color-light)' : undefined}>
        {next ? (
          <>
            <Text size="xs" c="dimmed">Prochaine overlay imprimée</Text>
            <Text fw={700}>{next.student}</Text>
            <Text size="xs" c="dimmed">
              Feuille {next.position} sur {queue.total}{pagesOf(next) ? ` · ${pagesOf(next)}` : ''}
            </Text>
            <Text size="xs" mt={4}>
              {verso
                ? <>Retournez la pile d’un bloc, sans en modifier l’ordre : <b>{next.student}</b> doit être la première feuille prise par l’imprimante.</>
                : <>La copie de <b>{next.student}</b> doit être sur le dessus du bac : c’est la première que l’imprimante prendra.</>}
            </Text>
          </>
        ) : (
          <>
            <Text size="sm" fw={700}>À partir de quel élève reprendre ?</Text>
            <Text size="xs" mt={2}>
              L’ordinateur ne voit pas les feuilles sortir. Si l’impression s’est interrompue,
              lisez le nom sur la dernière overlay bien imprimée, puis cliquez sur l’élève suivant
              dans la liste.
            </Text>
          </>
        )}
      </Paper>

      <Box>
        <Text size="xs" fw={600} mb={4}>
          File d’attente · {sheetsLabel(queue.total)}
          <Text span size="xs" c="dimmed" fw={400}> · cliquez sur un élève pour reprendre</Text>
        </Text>
        <Box ref={listRef} role="listbox" aria-label="File d’attente des overlays"
          style={{
            maxHeight: 160, overflowY: 'auto', position: 'relative',
            border: '1px solid var(--mantine-color-default-border)', borderRadius: 4,
          }}>
          {queue.entries.map((entry) => {
            const selected = entry.position === start
            const skipped = start != null && entry.position < start
            return (
              <UnstyledButton key={entry.position} role="option" aria-selected={selected}
                disabled={disabled}
                ref={(element) => {
                  if (element) rowRefs.current.set(entry.position, element)
                  else rowRefs.current.delete(entry.position)
                }}
                onClick={() => onSelect(entry.position)}
                style={{
                  display: 'flex', gap: 8, alignItems: 'baseline', width: '100%',
                  padding: '3px 8px', fontSize: 13, opacity: skipped ? 0.5 : 1,
                  background: selected ? 'var(--mantine-primary-color-light)' : undefined,
                  fontWeight: selected ? 700 : 400,
                }}>
                <span style={{ width: 26, flexShrink: 0, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                  {entry.position}
                </span>
                <span style={{
                  flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  textDecoration: skipped ? 'line-through' : undefined,
                }}>
                  {entry.student}
                </span>
                <span style={{ flexShrink: 0, fontSize: 11, opacity: 0.7, fontWeight: 400 }}>
                  {skipped ? 'sautée' : selected ? 'prochaine' : pagesOf(entry)}
                </span>
              </UnstyledButton>
            )
          })}
        </Box>
        {queue.adf_reversed && (
          <Text size="xs" c="dimmed" mt={4}>
            L’ADF de ce profil inverse la pile : la file commence par la dernière copie scannée.
          </Text>
        )}
      </Box>
    </Stack>
  )
}
