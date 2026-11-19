// Une ligne = un lot de scan d'une correction. Le lot principal et les lots déposés
// après la validation s'affichent à l'identique : seule la taille (pages) les distingue.
// Chaque lot a son pipeline, son aperçu et sa prochaine action ; ce qui est propre à
// la correction entière (titre, absents) reste dans l'en-tête de la carte.
import { useState } from 'react'
import { Badge, Button, Group, Loader, Menu, Text, Tooltip } from '@mantine/core'
import { CloudOff, Download, Eye, PencilLine, Presentation, RefreshCw, Printer, ScanLine, Send, Trash2, Undo2 } from 'lucide-react'
import ActionsMenu from './ActionsMenu'
import { LateConflictButton } from './LateJobs'
import PrintButton from './PrintButton'
import SegmentBar, { STEP_COUNT, type Segment } from './SegmentBar'

export type LotStage =
  'awaiting' | 'processing' | 'error' | 'ocr_review' | 'review' | 'conflict' | 'ready'

/** Machine à états de la lecture sur le Mac (backend : ocr_cascade.local_progress).
 *  - mac_reading : le Mac lit (bleu animé) — rien à faire, on attend, on ne l'interrompt pas ;
 *  - mac_offline : worker muet depuis 2 min (gris) — la lecture reprendra à son retour ;
 *  - mac_overdue : attente au-delà du délai (orange) — au professeur de décider.
 *  Ensuite `ocr_review` (orange) : OCRiser, avec les propositions de tous les moteurs. */
export type MacState = 'mac_reading' | 'mac_offline' | 'mac_overdue'
export type OcrLocal = {
  decided: number; total: number; worker_online: boolean; mode?: 'cascade' | 'shadow'; state?: MacState
  waiting_hours?: number; timeout_hours?: number; overdue?: boolean
}

export function macStateOf(o: OcrLocal | null | undefined): MacState | null {
  if (!o) return null
  return o.state ?? (o.overdue ? 'mac_overdue' : o.worker_online ? 'mac_reading' : 'mac_offline')
}

/** Bouton d'état du Mac : spinner pendant la lecture, jamais cliquable (on n'interrompt pas). */
export function MacStatusButton({ ocr }: { ocr: OcrLocal }) {
  const state = macStateOf(ocr)
  const count = `${ocr.decided}/${ocr.total}`
  const waiting = ocr.mode === 'shadow' ? 'OCRiser s\'ouvrira avec leurs propositions dès la fin.' : 'La correction reprendra seule dès la fin.'
  const tip = state === 'mac_reading'
    ? `Les moteurs locaux du Mac lisent les écritures (lecture libre puis avec contexte). ${waiting} Rien à faire : la lecture n'est pas interrompue.`
    : state === 'mac_offline'
      ? `Le Mac n'a pas contacté le serveur depuis plus de 2 minutes (veille, réseau ?). La lecture reprendra à son retour, là où elle s'est arrêtée.`
      : `En attente du Mac depuis ${Math.round(ocr.waiting_hours ?? 0)} h (délai : ${ocr.timeout_hours ?? '?'} h).`
  const label = state === 'mac_reading' ? `Lecture sur le Mac ${count}`
    : state === 'mac_offline' ? `Mac hors ligne ${count}` : `Mac en retard ${count}`
  return (
    <Tooltip label={tip} multiline maw={300}>
      <Button size="xs" variant="light" color={state === 'mac_reading' ? 'blue' : 'gray'}
        leftSection={state === 'mac_reading' ? <Loader size={12} color="blue" /> : <CloudOff size={14} />}
        data-testid="ocr-local-status" data-state={state}
        style={{ cursor: 'default', fontVariantNumeric: 'tabular-nums' }}
        onClick={(e) => e.preventDefault()}>
        {label}
      </Button>
    </Tooltip>
  )
}

export type Lot = {
  wave: number
  stage: LotStage
  segments: Segment[]
  pages: number
  pendingOcr: number
  pendingReviews: number
  conflicts: number
  overlayReady: boolean
  printed: boolean
  distributed: boolean
  stale: boolean
  rebuilding: boolean
  /** Lecture sur le Mac (cascade, ou ombre avant OCRiser) : voir `MacState`. */
  ocrLocal?: OcrLocal | null
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
  /** Réponses « correction en attente » de la commande vague (lot principal seulement). */
  pendingLlmQueued?: number
  onPreview: () => void
  onOcr: () => void
  onCorrect: (scope: 'flagged' | 'all') => void
  onRetry: () => void
  /** Lot en attente du Mac depuis trop longtemps : le relire avec Mathpix. */
  onOcrFallback?: () => void
  onRetryLlm?: () => void
  onRefine: () => void
  onRegenerate: () => void
  onRecalc?: () => void
  onReset?: () => void
  onDistributed: (value: boolean) => void
  onChanged: () => void
  /** Diaporama de correction au tableau du sujet (lot principal seulement). */
  onSlideshow?: () => void
  onExportSlideshow?: () => void
}

// largeur de « 35 copies »
const LOT_W = 108

export default function ScanLotRow({ lot, h }: { lot: Lot; h: LotHandlers }) {
  const ready = lot.stage === 'ready'
  const [reprintOpen, setReprintOpen] = useState(false)
  // « Affiner » en bouton, à gauche de l'action principale, quand l'impression de l'overlay est
  // attendue ; l'aperçu est alors dans le menu ⋮ (comme dans les autres états)
  const printExpected = ready && lot.overlayReady && !lot.stale && !lot.printed
  const n = lot.students.length
  // « Distribué » n'est à faire qu'une fois l'overlay à jour et imprimé
  const distributable = ready && lot.overlayReady && !lot.stale && lot.printed && !lot.distributed
  // lecture sur le Mac : tant qu'il lit (ou revient), OCRiser attend ses propositions
  const mac = macStateOf(lot.ocrLocal)
  const macBusy = mac === 'mac_reading' || mac === 'mac_offline'
  // Rend 4 cellules de la grille de la carte (voir Corrections) : taille du lot,
  // pipeline (avec « Distribué »), élèves, actions — alignées d'une ligne à l'autre.
  return (
    <>
      <Tooltip label={lot.info} disabled={!lot.info} multiline maw={320}>
        <Badge size="lg" variant="light" color="indigo" radius="sm" tt="none" w={LOT_W} mr={16}
          data-testid={`scan-lot-${lot.wave}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
          {lot.stage === 'awaiting' ? '—' : `${n} copie${n > 1 ? 's' : ''}`}
        </Badge>
      </Tooltip>

      {lot.stage === 'awaiting' ? (
        <Text size="xs" c="dimmed" style={{ gridColumn: `2 / ${STEP_COUNT + 3}` }}>
          En attente de scan — déposez-le dans le <b>bac à sable</b> en haut de page.
        </Text>
      ) : (
        <>
          <SegmentBar segments={lot.segments} extra={{
            phase: 'distribute', label: 'Distribué',
            state: lot.distributed ? 'green' : distributable ? 'orange' : 'gray',
            // cliquable une fois l'overlay imprimé : on coche / décoche la distribution
            onClick: distributable || lot.distributed
              ? () => h.onDistributed(!lot.distributed) : undefined,
            hint: distributable || lot.distributed ? 'Cliquer pour marquer comme distribué aux élèves' : undefined,
          }} />
          <Text size="xs" c="dimmed" title={lot.students.join(', ')}
            style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0, marginLeft: 16 }}>
            {n > 0 && n < 4 ? lot.students.join(', ') : ''}
          </Text>
        </>
      )}

      {lot.stage !== 'awaiting' ? (
        <Group gap="xs" wrap="nowrap" justify="flex-end" ml={16}>
          {/* moment d'imprimer l'overlay : « Affiner » (relire / corriger) avant d'imprimer */}
          {printExpected && (
            <Button size="xs" variant="subtle" leftSection={<PencilLine size={14} />} onClick={h.onRefine}>
              Affiner
            </Button>
          )}

          {lot.stage === 'conflict' && (
            <LateConflictButton batchId={h.batchId} count={lot.conflicts} onResolved={h.onChanged} />
          )}
          {(lot.stage === 'processing' || lot.stage === 'ocr_review') && lot.ocrLocal && (
            <MacStatusButton ocr={lot.ocrLocal} />
          )}
          {lot.stage === 'processing' && mac === 'mac_overdue' && h.onOcrFallback && (
            <Tooltip multiline maw={300} label={`En attente du Mac depuis ${Math.round(lot.ocrLocal?.waiting_hours ?? 0)} h : relire ce lot avec Mathpix, comme avant la lecture locale`}>
              <Button size="xs" color="orange" leftSection={<RefreshCw size={14} />}
                onClick={h.onOcrFallback} data-testid="ocr-fallback">Lire avec Mathpix</Button>
            </Tooltip>
          )}
          {lot.stage === 'processing' && !lot.ocrLocal && (
            <Button size="xs" variant="light" loading disabled>Correction en cours…</Button>
          )}
          {/* OCRiser = intervention humaine (orange), une fois le Mac fini (ou en retard) */}
          {lot.stage === 'ocr_review' && !macBusy && (
            <Tooltip multiline maw={300} disabled={mac !== 'mac_overdue'}
              label="Le Mac n'a pas fini : les propositions de ses moteurs manqueront pour les cases qu'il n'a pas lues.">
              <Button size="xs" color="orange" leftSection={<ScanLine size={14} />} onClick={h.onOcr}
                data-testid="ocr-open">
                {lot.pendingOcr > 0 ? `OCRiser (${lot.pendingOcr})` : 'OCRiser : revoir et lancer la correction'}
              </Button>
            </Tooltip>
          )}
          {lot.stage === 'error' && (
            <Button size="xs" color="orange" leftSection={<RefreshCw size={14} />} onClick={h.onRetry}>
              Relancer
            </Button>
          )}
          {lot.stage === 'review' && (h.pendingLlmQueued ?? 0) > 0 && (
            <Tooltip multiline maw={300} label="Réponses rédigées en attente de la commande vague, lancée depuis le Mac. Aucune note n'est finale avant : vous pouvez aussi les corriger vous-même.">
              <Badge size="lg" variant="light" color="gray" radius="sm" tt="none" data-testid="llm-queued">
                Correction en attente ({h.pendingLlmQueued})
              </Badge>
            </Tooltip>
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
          {ready && lot.overlayReady && !lot.stale && !lot.printed && (
            <PrintButton assessmentId={h.assessmentId} file="correction_overlay.pdf"
              wave={lot.wave} label="Imprimer l'overlay" />
          )}
          {ready && !lot.overlayReady && (
            <Button size="xs" variant="light" loading disabled>Génération de l'overlay…</Button>
          )}
          {/* Étape « Distribué » à faire : son bouton, comme les autres étapes */}
          {distributable && (
            <Button size="xs" variant="light" leftSection={<Send size={14} />}
              onClick={() => h.onDistributed(true)}>
              Marquer distribué
            </Button>
          )}

          {ready && lot.overlayReady && !lot.stale && lot.printed && (
            <PrintButton assessmentId={h.assessmentId} file="correction_overlay.pdf"
              wave={lot.wave} label="Imprimer l'overlay" hiddenTrigger
              open={reprintOpen} onOpenChange={setReprintOpen} />
          )}
          <ActionsMenu>
            {lot.overlayReady && (
              <Menu.Item leftSection={<Eye size={14} />} onClick={h.onPreview}>Aperçu</Menu.Item>
            )}
            {ready && lot.overlayReady && !lot.stale && lot.printed && (
              <Menu.Item leftSection={<Printer size={14} />} onClick={() => setReprintOpen(true)}>
                Imprimer l'overlay
              </Menu.Item>
            )}
            {ready && lot.overlayReady && !lot.distributed && !printExpected && (
              <Tooltip position="left" label="Relire chaque copie avec son overlay et corriger une note en modifiant la réponse de l'élève">
                <Menu.Item leftSection={<PencilLine size={14} />} onClick={h.onRefine}>
                  Affiner
                </Menu.Item>
              </Tooltip>
            )}
            {ready && lot.distributed && (
              <Menu.Item leftSection={<Undo2 size={14} />} onClick={() => h.onDistributed(false)}>
                Annuler la distribution
              </Menu.Item>
            )}
            {(ready || lot.stage === 'review') && h.onSlideshow && (
              <Tooltip position="left" label="Corriger au tableau : exercices du plus facile au plus dur, résultats des élèves, correction">
                <Menu.Item leftSection={<Presentation size={14} />} onClick={h.onSlideshow}>
                  Lancer diaporama
                </Menu.Item>
              </Tooltip>
            )}
            {(ready || lot.stage === 'review') && h.onExportSlideshow && (
              <Tooltip position="left" label="Le diaporama en un seul fichier HTML, images comprises, à ouvrir sans connexion">
                <Menu.Item leftSection={<Download size={14} />} onClick={h.onExportSlideshow}>
                  Exporter diaporama
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
            {lot.stage === 'ocr_review' && macBusy && (
              <Tooltip position="left" multiline maw={280} label="Ouvrir OCRiser tout de suite, sans les propositions des moteurs du Mac pour les cases qu'il n'a pas encore lues. La lecture du Mac continue.">
                <Menu.Item leftSection={<ScanLine size={14} />} onClick={h.onOcr}>
                  OCRiser sans attendre le Mac
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
