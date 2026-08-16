// Assistant « Créer mon sujet » : le professeur compose LUI-MÊME son sujet.
//
// Quatre étapes, dans cet ordre parce que chacune conditionne la suivante :
//   1. Sujet      — classe, type, pages, et surtout le mode de GUIDES (il change
//                   la hauteur des cartes, donc la place disponible) ;
//   2. Compétences— ce qui détermine les exercices proposés (et, via leur
//                   chapitre, les problèmes) ;
//   3. Variantes  — aucune, anti-triche (N sujets équivalents) ou par niveau
//                   (exactement 3 : facile / moyen / difficile) ;
//   4. Mise en page — le glisser-déposer, variante par variante.
//
// Pas de sujet individuel ici : uniquement des sujets COMMUNS à toute la classe,
// éventuellement déclinés en variantes. C'est la différence de fond avec la
// « Création automatique », qui personnalise copie par copie.
import {
  Alert, Badge, Button, Card, Center, Divider, Group, Modal, NumberInput, Radio,
  ScrollArea, Select, Stack, Stepper, Text, TextInput, ThemeIcon, Tooltip,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import {
  AlertTriangle, Copy as CopyIcon, EyeOff, Layers, PencilRuler, Plus, Printer,
  Shuffle, Trash2, Users,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../../api'
import CompetencyMatrixStep from './CompetencyMatrixStep'
import LayoutBoard, {
  emptyLayout, layoutCount, overfullColumns, resizeLayout,
  type Layout, type Metrics, type PoolItem,
} from './LayoutBoard'

type Cls = { id: string; name: string; grade_level: string }
type Pool = {
  exercises: PoolItem[]; problems: PoolItem[]
  chapters: { code: string; name: string }[]; metrics: Metrics
}
type Variant = { key: string; label: string; layout: Layout }

const NOTE_BASES = ['5', '10', '20']
const LEVEL_KEYS = ['facile', 'moyen', 'difficile'] as const
const LEVEL_LABELS: Record<string, string> = {
  facile: 'Facile', moyen: 'Moyen', difficile: 'Difficile',
}
const MAX_VARIANTS = 6

// Les trois politiques de guide. Un « guide » est l'aide courte
// d'auto-correction attaché à chaque exercice/problème.
const GUIDE_OPTIONS = [
  {
    value: 'overlay', icon: Printer, title: '1. Overlay après correction',
    desc: "L'espace du guide est réservé sous chaque carte ; il ne s'imprime "
      + "sur l'overlay que si l'exercice est faux (niveaux 4 à 10).",
  },
  {
    value: 'print_fragile', icon: Users, title: '2. Imprimés pour les niveaux 1 à 4',
    desc: 'Active aussi l\'option 1 : les élèves de niveau 1 à 4 reçoivent '
      + 'le guide dès le sujet ; les autres le reçoivent en overlay si besoin.',
  },
  {
    value: 'none', icon: EyeOff, title: 'Aucun guide',
    desc: "L'espace réservé au guide disparaît : les cartes sont plus compactes "
      + 'et il tient nettement plus d\'exercices par page.',
  },
]

const VARIANT_OPTIONS = [
  {
    value: 'none', icon: Layers, title: 'Un seul sujet',
    desc: 'Toute la classe reçoit exactement la même feuille.',
  },
  {
    value: 'anticheat', icon: Shuffle, title: 'Variantes anti-triche',
    desc: 'Plusieurs sujets équivalents, distribués en tourniquet : deux voisins '
      + "n'ont pas la même feuille.",
  },
  {
    value: 'level', icon: PencilRuler, title: 'Variantes par niveau',
    desc: 'Trois sujets — facile, moyen, difficile — attribués automatiquement '
      + "d'après le niveau de chaque élève.",
  },
]

function ChoiceCard({ selected, icon: Icon, title, desc, onClick }: {
  selected: boolean; icon: typeof Layers; title: string; desc: string
  onClick: () => void
}) {
  return (
    <Card withBorder padding="sm" onClick={onClick}
      style={{
        cursor: 'pointer', flex: 1, minWidth: 220,
        borderColor: selected ? 'var(--mantine-color-indigo-6)' : undefined,
        borderWidth: selected ? 2 : 1,
        background: selected ? 'var(--mantine-color-indigo-light)' : undefined,
      }}>
      <Group gap="xs" wrap="nowrap" align="flex-start">
        <ThemeIcon size="md" variant={selected ? 'filled' : 'light'} color="indigo">
          <Icon size={16} />
        </ThemeIcon>
        <Stack gap={2}>
          <Text size="sm" fw={600}>{title}</Text>
          <Text size="xs" c="dimmed">{desc}</Text>
        </Stack>
      </Group>
    </Card>
  )
}

export default function ManualWizard({ opened, classes, onClose, onCreated }: {
  opened: boolean; classes: Cls[]; onClose: () => void; onCreated: () => void
}) {
  const [step, setStep] = useState(0)
  // étape 1
  const [classId, setClassId] = useState<string | null>(null)
  const [type, setType] = useState('training')
  const [noteBase, setNoteBase] = useState('20')
  const [title, setTitle] = useState('')
  const [pages, setPages] = useState(1)
  const [guides, setGuides] = useState('overlay')
  // étape 2
  const [competencyIds, setCompetencyIds] = useState<string[]>([])
  // étape 3
  const [variantKind, setVariantKind] = useState('none')
  const [variants, setVariants] = useState<Variant[]>(
    [{ key: 'A', label: 'Sujet unique', layout: emptyLayout(1) }])
  const [current, setCurrent] = useState(0)
  // étape 4
  const [pool, setPool] = useState<Pool | null>(null)
  const [loadingPool, setLoadingPool] = useState(false)
  const [busy, setBusy] = useState(false)

  const grade = classes.find((c) => c.id === classId)?.grade_level

  const reset = useCallback(() => {
    setStep(0); setClassId(null); setType('training'); setNoteBase('20')
    setTitle(''); setPages(1); setGuides('overlay'); setCompetencyIds([])
    setVariantKind('none'); setCurrent(0); setPool(null)
    setVariants([{ key: 'A', label: 'Sujet unique', layout: emptyLayout(1) }])
  }, [])

  function close() { reset(); onClose() }

  // le nombre de pages pilote la taille de CHAQUE plan de variante
  useEffect(() => {
    setVariants((vs) => vs.map((v) => ({ ...v, layout: resizeLayout(v.layout, pages) })))
  }, [pages])

  // changement de politique de variantes : on recompose la liste en gardant les
  // plans déjà posés (le 1er sert de base — c'est presque toujours ce qu'on veut).
  function applyVariantKind(kind: string) {
    setVariantKind(kind)
    setCurrent(0)
    setVariants((vs) => {
      const base = vs[0]?.layout ?? emptyLayout(pages)
      if (kind === 'none') return [{ key: 'A', label: 'Sujet unique', layout: base }]
      if (kind === 'level') {
        return LEVEL_KEYS.map((k, i) => ({
          key: k, label: LEVEL_LABELS[k],
          layout: vs[i]?.layout ?? resizeLayout(base, pages),
        }))
      }
      const n = Math.max(2, vs.length)
      return Array.from({ length: n }, (_, i) => ({
        key: String.fromCharCode(65 + i), label: `Variante ${String.fromCharCode(65 + i)}`,
        layout: vs[i]?.layout ?? emptyLayout(pages),
      }))
    })
  }

  function addVariant() {
    setVariants((vs) => {
      if (vs.length >= MAX_VARIANTS) return vs
      const k = String.fromCharCode(65 + vs.length)
      return [...vs, { key: k, label: `Variante ${k}`, layout: emptyLayout(pages) }]
    })
  }

  function removeVariant(i: number) {
    setVariants((vs) => {
      if (vs.length <= 2) return vs
      const kept = vs.filter((_, j) => j !== i).map((v, j) => ({
        ...v, key: String.fromCharCode(65 + j),
        label: `Variante ${String.fromCharCode(65 + j)}`,
      }))
      setCurrent((c) => Math.min(c, kept.length - 1))
      return kept
    })
  }

  function duplicateInto(target: number, from: number) {
    setVariants((vs) => vs.map((v, i) => (i === target
      ? { ...v, layout: vs[from].layout.map((p) => p.map((c) => [...c])) } : v)))
  }

  // le pool ne dépend que des compétences (et du nombre de pages, pour la
  // géométrie des colonnes) : chargé à l'entrée dans l'étape mise en page.
  useEffect(() => {
    if (!opened || step < 3 || !competencyIds.length) return
    setLoadingPool(true)
    api.get<Pool>(`/api/assessments/manual/pool?competency_ids=${competencyIds.join(',')}`
      + `&pages=${pages}`)
      .then((p) => {
        setPool(p)
        // Décocher une compétence retire ses exercices du pool : les cartes
        // déjà posées qui en venaient doivent disparaître des plans, sinon
        // elles restent affichées sans contenu et la génération échouerait.
        const known = new Set([...p.exercises, ...p.problems].map((e) => e.id))
        let dropped = 0
        setVariants((vs) => vs.map((v) => ({
          ...v,
          layout: v.layout.map((page) => page.map((col) => col.filter((id) => {
            if (known.has(id)) return true
            dropped += 1
            return false
          }))),
        })))
        if (dropped) {
          notifications.show({
            color: 'orange',
            message: `${dropped} exercice(s) retiré(s) des pages : leur compétence `
              + "n'est plus cochée.",
          })
        }
      })
      .catch((e) => notifications.show({ color: 'red', message: (e as Error).message }))
      .finally(() => setLoadingPool(false))
  }, [opened, step, competencyIds, pages])

  const placed = variants.reduce((n, v) => n + layoutCount(v.layout), 0)
  const emptyVariants = variants.filter((v) => layoutCount(v.layout) === 0)
  const overfull = useMemo(() => {
    if (!pool) return []
    const byId = new Map(([...pool.exercises, ...pool.problems]).map((e) => [e.id, e]))
    return variants.flatMap((v) => overfullColumns(v.layout, byId, pool.metrics, guides)
      .map((c) => `${v.label} — ${c}`))
  }, [pool, variants, guides])

  async function createAndGenerate() {
    setBusy(true)
    try {
      const created = await api.post<{ id: string }>('/api/assessments', {
        class_id: classId, type, title: title || 'Sans titre', pages,
        note_base: Number(noteBase),
      })
      await api.post(`/api/assessments/${created.id}/manual-plan`, {
        competency_ids: competencyIds, guides, variant_kind: variantKind,
        variants: variants.map((v) => ({
          key: v.key, label: v.label,
          items: v.layout.flatMap((page, p) => page.flatMap((col, c) =>
            col.map((exercise_id, rank) => ({ exercise_id, page: p, col: c, rank })))),
        })),
      })
      await api.post(`/api/assessments/${created.id}/generate`, { font_size: 10 })
      notifications.show({ color: 'blue', message: 'Sujet en file de génération' })
      onCreated()
      close()
    } catch (e) {
      notifications.show({ color: 'red', message: (e as Error).message })
    } finally {
      setBusy(false)
    }
  }

  const variantHint = variantKind === 'level'
    ? `Vous composez la variante « ${LEVEL_LABELS[variants[current]?.key] ?? ''} » : `
      + 'elle ira aux élèves de ce niveau.'
    : variantKind === 'anticheat'
      ? `Vous composez la ${variants[current]?.label} : elle sera distribuée à un `
        + 'élève sur ' + variants.length + '.'
      : 'Toute la classe recevra cette feuille.'
  const guideSummary = guides === 'print_fragile'
    ? 'Options 1 et 2 — sujet niveaux 1 à 4 + overlay si besoin'
    : GUIDE_OPTIONS.find((o) => o.value === guides)!.title

  return (
    <Modal opened={opened} onClose={close} size="calc(100vw - 3rem)" centered
      title={<Text fw={650}>Créer mon sujet</Text>}
      styles={{
        content: { height: 'calc(100vh - 3rem)', display: 'flex', flexDirection: 'column' },
        body: { flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' },
      }}>
      <Stepper active={step} onStepClick={setStep} allowNextStepsSelect={false}
        size="sm" style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}
        styles={{ content: { flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' } }}>

        {/* ----------------------------------------------------- 1. sujet */}
        <Stepper.Step label="Sujet" description="Classe, pages, guides">
          <ScrollArea style={{ flex: 1 }}>
            <Stack my="xl" mx="auto" maw={860} w="100%">
              <Group grow align="flex-start">
                <Select label="Classe" required value={classId} onChange={setClassId}
                  placeholder={classes.length ? 'Choisir une classe'
                    : 'Aucune classe — créez-en une dans Élèves'}
                  data={classes.map((c) => ({ value: c.id, label: `${c.name} (${c.grade_level})` }))} />
                <TextInput label="Titre" placeholder="ex. Fractions — semaine 12"
                  value={title} onChange={(e) => setTitle(e.target.value)} />
              </Group>
              <Group grow align="flex-start">
                <Radio.Group label="Type" value={type} onChange={setType}>
                  <Group mt="xs">
                    <Radio value="training" label="Entraînement" />
                    <Radio value="control" label="Contrôle noté" />
                  </Group>
                </Radio.Group>
                <NumberInput label="Nombre de pages" value={pages} min={1} max={6}
                  description="1 = recto seul, 2 = recto/verso"
                  onChange={(v) => setPages(Math.max(1, Math.min(6, Number(v) || 1)))} />
              </Group>
              <Radio.Group label="Base de scoring" value={noteBase} onChange={setNoteBase}
                description={type === 'control'
                  ? "Le résultat est ramené à cette base à la correction."
                  : "Le score sert au suivi mais n'est pas imprimé sur la copie."}>
                <Group mt="xs">
                  {NOTE_BASES.map((b) => <Radio key={b} value={b} label={`/${b}`} />)}
                </Group>
              </Radio.Group>
              <Divider my="xs" />
              <div>
                <Text size="sm" fw={600}>Guides</Text>
                <Text size="xs" c="dimmed" mb="xs">
                  Chaque exercice et chaque problème porte un guide élève court
                  d'auto-correction. Ce choix décide de la place qu'il occupe sur
                  la feuille — il vaut pour tout le sujet.
                </Text>
                <Group align="stretch" gap="xs">
                  {GUIDE_OPTIONS.map((o) => (
                    <ChoiceCard key={o.value} selected={guides === o.value
                      || (guides === 'print_fragile' && o.value === 'overlay')}
                      icon={o.icon} title={o.title} desc={o.desc}
                      onClick={() => setGuides(o.value)} />
                  ))}
                </Group>
              </div>
              <Button mt="sm" w={200} onClick={() => setStep(1)} disabled={!classId}>
                Continuer
              </Button>
            </Stack>
          </ScrollArea>
        </Stepper.Step>

        {/* ----------------------------------------------- 2. compétences */}
        <Stepper.Step label="Compétences" description="Le périmètre du sujet">
          <ScrollArea style={{ flex: 1 }}>
            <Stack my="xl" mx="auto" maw={1100} w="100%" gap="xs">
              <Text size="xs" c="dimmed">
                Les exercices proposés à l'étape suivante viendront de ces
                compétences. Les <b>problèmes</b>, eux, portent sur un chapitre
                entier : cocher une seule compétence suffit à proposer tous les
                problèmes de son chapitre.
              </Text>
              <CompetencyMatrixStep gradeLevel={grade} selected={competencyIds}
                onChange={setCompetencyIds} />
              <Button w={200} onClick={() => setStep(2)} disabled={!competencyIds.length}>
                Continuer
              </Button>
            </Stack>
          </ScrollArea>
        </Stepper.Step>

        {/* -------------------------------------------------- 3. variantes */}
        <Stepper.Step label="Variantes" description="Un ou plusieurs sujets">
          <ScrollArea style={{ flex: 1 }}>
            <Stack my="xl" mx="auto" maw={860} w="100%">
              <Group align="stretch" gap="xs">
                {VARIANT_OPTIONS.map((o) => (
                  <ChoiceCard key={o.value} selected={variantKind === o.value}
                    icon={o.icon} title={o.title} desc={o.desc}
                    onClick={() => applyVariantKind(o.value)} />
                ))}
              </Group>
              {variantKind === 'anticheat' && (
                <Card withBorder padding="sm">
                  <Group justify="space-between">
                    <Text size="sm">
                      {variants.length} variantes — un élève sur {variants.length} reçoit
                      chacune d'elles.
                    </Text>
                    <Group gap="xs">
                      <Button size="xs" variant="light" leftSection={<Plus size={13} />}
                        onClick={addVariant} disabled={variants.length >= MAX_VARIANTS}>
                        Ajouter une variante
                      </Button>
                      <Button size="xs" variant="subtle" color="red"
                        leftSection={<Trash2 size={13} />}
                        onClick={() => removeVariant(variants.length - 1)}
                        disabled={variants.length <= 2}>
                        Retirer la dernière
                      </Button>
                    </Group>
                  </Group>
                </Card>
              )}
              {variantKind === 'level' && (
                <Alert color="blue" p="xs">
                  Trois variantes à composer : <b>Facile</b> (élèves de niveau 1 à 4),
                  <b> Moyen</b> (5 à 7) et <b>Difficile</b> (8 à 10). Chaque élève reçoit
                  automatiquement celle qui correspond à son niveau.
                </Alert>
              )}
              <Button w={200} onClick={() => setStep(3)}>Composer les pages</Button>
            </Stack>
          </ScrollArea>
        </Stepper.Step>

        {/* ------------------------------------------------ 4. mise en page */}
        <Stepper.Step label="Mise en page" description="Glisser-déposer">
          <Stack gap="xs" mt="sm" style={{ flex: 1, minHeight: 0 }}>
            <Group justify="space-between" wrap="nowrap">
              <Group gap={6} wrap="nowrap">
                {variants.map((v, i) => (
                  <Button key={v.key} size="compact-sm"
                    variant={i === current ? 'filled' : 'default'}
                    onClick={() => setCurrent(i)}>
                    {v.label}
                    <Badge ml={6} size="xs" variant="white" color="gray"
                      circle={false}>{layoutCount(v.layout)}</Badge>
                  </Button>
                ))}
                {variantKind !== 'none' && current > 0 && (
                  <Tooltip label={`Recopier le plan de « ${variants[current - 1].label} »`}>
                    <Button size="compact-sm" variant="subtle"
                      leftSection={<CopyIcon size={13} />}
                      onClick={() => duplicateInto(current, current - 1)}>
                      Dupliquer
                    </Button>
                  </Tooltip>
                )}
              </Group>
              <Text size="xs" c="dimmed">{variantHint}</Text>
            </Group>
            {loadingPool && <Text size="sm" c="dimmed">Chargement des exercices…</Text>}
            {pool && (
              <div style={{ flex: 1, minHeight: 0 }}>
                <LayoutBoard
                  pool={pool.exercises} problems={pool.problems}
                  metrics={pool.metrics} guides={guides} pages={pages}
                  layout={variants[current]?.layout ?? emptyLayout(pages)}
                  onChange={(l) => setVariants((vs) =>
                    vs.map((v, i) => (i === current ? { ...v, layout: l } : v)))} />
              </div>
            )}
            <Group justify="flex-end">
              <Button onClick={() => setStep(4)} disabled={placed === 0}>
                Continuer
              </Button>
            </Group>
          </Stack>
        </Stepper.Step>

        {/* ------------------------------------------------- 5. génération */}
        <Stepper.Step label="Génération" description="Vérifier et lancer">
          <ScrollArea style={{ flex: 1 }}>
            <Center mih="calc(100vh - 220px)">
            <Stack my="xl" maw={620} w="100%">
              <Card withBorder padding="sm">
                <Stack gap={6}>
                  <Row k="Classe" v={classes.find((c) => c.id === classId)?.name ?? '—'} />
                  <Row k="Sujet" v={`${title || 'Sans titre'} — ${type === 'control'
                    ? `contrôle noté /${noteBase}` : `entraînement scoré /${noteBase}`}`} />
                  <Row k="Pages" v={`${pages} page(s)`} />
                  <Row k="Guides" v={guideSummary} />
                  <Row k="Variantes" v={variantKind === 'none' ? 'sujet unique'
                    : `${variants.length} · ${variantKind === 'level'
                      ? 'par niveau' : 'anti-triche'}`} />
                  <Row k="Exercices placés" v={`${placed} au total`} />
                </Stack>
              </Card>
              {emptyVariants.length > 0 && (
                <Alert color="orange" p="xs" icon={<AlertTriangle size={15} />}>
                  {emptyVariants.map((v) => v.label).join(', ')} : aucune carte placée.
                  Ces variantes ne seront pas générées — revenez à l'étape précédente
                  pour les composer.
                </Alert>
              )}
              {overfull.length > 0 && (
                <Alert color="orange" p="xs" icon={<AlertTriangle size={15} />}>
                  Colonnes trop chargées : {overfull.join(' ; ')}. Les cartes en trop
                  glisseront dans la colonne suivante à l'impression.
                </Alert>
              )}
              <Text size="xs" c="dimmed">
                La génération tourne en file de fond : la fenêtre se ferme aussitôt,
                le sujet apparaît dans la liste dès qu'il est prêt.
              </Text>
              <Button onClick={createAndGenerate} loading={busy} disabled={placed === 0}>
                Générer le sujet
              </Button>
            </Stack>
            </Center>
          </ScrollArea>
        </Stepper.Step>
      </Stepper>
    </Modal>
  )
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <Group justify="space-between" wrap="nowrap">
      <Text size="sm" c="dimmed">{k}</Text>
      <Text size="sm" fw={550} ta="right">{v}</Text>
    </Group>
  )
}
