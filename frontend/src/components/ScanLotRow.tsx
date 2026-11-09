// Une ligne = un lot de scan d'une correction. Le lot principal et les lots déposés
// après la validation s'affichent à l'identique : seule la taille (pages) les distingue.
// Chaque lot a son pipeline, son aperçu et sa prochaine action ; ce qui est propre à
// la correction entière (titre, absents) reste dans l'en-tête de la carte.
import { Badge, Button, Checkbox, Group, Menu, Text, Tooltip } from '@mantine/core'
import { Eye, PencilLine, RefreshCw, ScanLine, Trash2 } from 'lucide-react'
import ActionsMenu from './ActionsMenu'
import { LateConflictButton } from './LateJobs'
import PrintButton from './PrintButton'
import SegmentBar, { type Segment } from './SegmentBar'

export type LotStage =
  'awaiting' | 'processing' | 'error' | 'ocr_review' | 'review' | 'conflict' | 'ready'

export type Lot = {
  wave: number
  stage: LotStage
  segments: Segment[]
  pages: number
  /** Sujet imprimé recto-verso. */
  duplex: boolean
  pendingOcr: number
  pendingReviews: number
  conflicts: number
  overlayReady: boolean
  printed: boolean
  distributed: boolean
  stale: boolean
  rebuilding: boolean
  /** Élèves du lot (nommés à côté du pipeline s'il y en a moins de 4). */
  students: string[]
  /** Dépôt et pages du lot (infobulle du badge). */
  info: string
}

export type LotHandlers = {
  batchId: string
  assessmentId: string
  /** Réponses DeepSeek indisponibles (lot principal seulement). */
  pendingLlm?: number
  onPreview: () => void
  onOcr: () => void
  onCorrect: (scope: 'flagged' | 'all') => void
  onRetry: () => void
  onRetryLlm?: () => void
  onRefine: () => void
  onRegenerate: () => void
  onRecalc?: () => void
  onReset?: () => void
  onDistributed: (value: boolean) => void
  onChanged: () => void
}

// largeur de « 35 copies » ; l'emplacement R/V est réservé même en recto
const LOT_W = 92
export const DUPLEX_W = 44

export default function ScanLotRow({ lot, h }: { lot: Lot; h: LotHandlers }) {
  const ready = lot.stage === 'ready'
  const n = lot.students.length
  // Rend 5 cellules de la grille de la carte (voir Corrections) : taille du lot,
  // pipeline, « Distribué », élèves, actions — alignées d'une ligne à l'autre.
  return (
    <>
      <Tooltip label={lot.info} disabled={!lot.info} multiline maw={320}>
        <Group gap={4} wrap="nowrap" data-testid={`scan-lot-${lot.wave}`}>
          <Badge size="lg" variant="light" color="indigo" radius="sm" w={LOT_W}
            style={{ fontVariantNumeric: 'tabular-nums' }}>
            {lot.stage === 'awaiting' ? '—' : `${n} copie${n > 1 ? 's' : ''}`}
          </Badge>
          <div style={{ width: DUPLEX_W }}>
            {lot.duplex && lot.stage !== 'awaiting' && (
              <Badge size="lg" variant="filled" color="red" radius="sm" px={6}>R/V</Badge>
            )}
          </div>
        </Group>
      </Tooltip>

      {lot.stage === 'awaiting' ? (
        <Text size="xs" c="dimmed" style={{ gridColumn: '2 / 5' }}>
          En attente de scan — déposez-le dans le <b>bac à sable</b> en haut de page.
        </Text>
      ) : (
        <>
          <SegmentBar segments={lot.segments} />
          <div>
            {ready && (
              <Checkbox size="xs" label="Distribué"
                disabled={!lot.overlayReady || !lot.printed}
                checked={lot.distributed}
                onChange={(e) => h.onDistributed(e.target.checked)} />
            )}
          </div>
          <Text size="xs" c="dimmed" title={lot.students.join(', ')}
            style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0 }}>
            {n > 0 && n < 4 ? lot.students.join(', ') : ''}
          </Text>
        </>
      )}

      {lot.stage !== 'awaiting' ? (
        <Group gap="xs" wrap="nowrap" justify="flex-end">
          <Button size="xs" variant="subtle" leftSection={<Eye size={14} />}
            disabled={lot.wave > 0 && !lot.overlayReady}
            onClick={h.onPreview}>
            Aperçu
          </Button>

          {lot.stage === 'conflict' && (
            <LateConflictButton batchId={h.batchId} count={lot.conflicts} onResolved={h.onChanged} />
          )}
          {lot.stage === 'processing' && (
            <Button size="xs" variant="light" loading disabled>Correction en cours…</Button>
          )}
          {lot.stage === 'ocr_review' && (
            <Button size="xs" color="blue" leftSection={<ScanLine size={14} />} onClick={h.onOcr}>
              OCRiser ({lot.pendingOcr})
            </Button>
          )}
          {lot.stage === 'error' && (
            <Button size="xs" color="orange" leftSection={<RefreshCw size={14} />} onClick={h.onRetry}>
              Relancer
            </Button>
          )}
          {lot.stage === 'review' && (
            <Button size="xs" color="orange" leftSection={<ScanLine size={14} />}
              onClick={() => h.onCorrect('flagged')}>
              Corriger les copies ({lot.pendingReviews})
            </Button>
          )}
          {ready && lot.overlayReady && lot.stale && (
            <Tooltip label="Des corrections affinées ne sont pas encore sur l'overlay : régénérez-le avant d'imprimer">
              <Button size="xs" color="orange" leftSection={<RefreshCw size={14} />}
                loading={lot.rebuilding} onClick={h.onRegenerate}>
                Régénérer l'overlay
              </Button>
            </Tooltip>
          )}
          {ready && lot.overlayReady && !lot.stale && (
            <PrintButton assessmentId={h.assessmentId} file="correction_overlay.pdf"
              wave={lot.wave} label="Imprimer l'overlay" />
          )}

          <ActionsMenu>
            {ready && lot.overlayReady && !lot.distributed && (
              <Tooltip position="left" label="Relire chaque copie avec son overlay et corriger une note en modifiant la réponse de l'élève">
                <Menu.Item leftSection={<PencilLine size={14} />} onClick={h.onRefine}>
                  Affiner
                </Menu.Item>
              </Tooltip>
            )}
            {(ready || lot.stage === 'error') && (
              <Menu.Item leftSection={<ScanLine size={14} />} onClick={() => h.onCorrect('all')}>
                Corriger
              </Menu.Item>
            )}
            {ready && h.onRecalc && (
              <Tooltip position="left" label="Recalculer les notes et régénérer les copies corrigées après un ajustement">
                <Menu.Item leftSection={<RefreshCw size={14} />} onClick={h.onRecalc}>
                  Recalculer les notes
                </Menu.Item>
              </Tooltip>
            )}
            {lot.stage === 'processing' && (
              <Tooltip position="left" label="Si la correction semble bloquée, relancez-la">
                <Menu.Item leftSection={<RefreshCw size={14} />} onClick={h.onRetry}>
                  Relancer
                </Menu.Item>
              </Tooltip>
            )}
            {lot.stage === 'review' && (h.pendingLlm ?? 0) > 0 && h.onRetryLlm && (
              <Tooltip position="left" label="Retenter uniquement les verdicts DeepSeek indisponibles, sans refaire l’OCR">
                <Menu.Item leftSection={<RefreshCw size={14} />} onClick={h.onRetryLlm}>
                  Relancer l’IA ({h.pendingLlm})
                </Menu.Item>
              </Tooltip>
            )}
            {h.onReset && lot.stage !== 'processing' && (
              <Tooltip position="left" label="Effacer cette correction et re-scanner depuis zéro">
                <Menu.Item color="red" leftSection={<Trash2 size={14} />} onClick={h.onReset}>
                  Effacer
                </Menu.Item>
              </Tooltip>
            )}
          </ActionsMenu>
        </Group>
      ) : <div />}
    </>
  )
}
