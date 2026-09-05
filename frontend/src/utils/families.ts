// Mise en lignes des exercices de l'onglet Exercices, par FAMILLE.
//
// Un exercice source du manuel donne un TRIO — facile, base, difficile — dont
// les deux dérivés pointent la base par `derived_from_id`. Ces trois-là ne se
// relisent pas isolément : c'est la comparaison côte à côte qui dit si
// l'étayage du facile et l'exigence du difficile tiennent la route. Chaque trio
// occupe donc UNE ligne de trois colonnes, dans cet ordre, quitte à laisser une
// case vide si un dérivé manque — c'est l'alignement qui rend la lecture
// possible.
//
// Les exercices SANS dérivé (pipeline classique, ou trio incomplet réduit à un
// seul) ne méritent pas une ligne aux deux tiers vide : ils se regroupent par
// trois, comme avant.

/** Ordre d'affichage d'une famille : du plus étayé au plus exigeant. */
export const VARIANT_ORDER = ['facile', 'base', 'difficile'] as const

type FamilyMember = {
  id: string
  variant_kind?: string | null
  derived_from_id?: string | null
}

export function familyRows<T extends FamilyMember>(exercises: T[] | null): (T | null)[][] {
  if (!exercises?.length) return []
  const families = new Map<string, T[]>()
  for (const ex of exercises) {
    const root = ex.derived_from_id || ex.id
    const list = families.get(root)
    if (list) list.push(ex)
    else families.set(root, [ex])
  }
  const rows: (T | null)[][] = []
  const loose: T[] = []
  const flushLoose = () => {
    while (loose.length) {
      const chunk = loose.splice(0, 3)
      rows.push([chunk[0] ?? null, chunk[1] ?? null, chunk[2] ?? null])
    }
  }
  for (const family of families.values()) {
    if (family.length < 2) { loose.push(family[0]); continue }
    flushLoose()
    rows.push(VARIANT_ORDER.map((kind) =>
      family.find((e) => (e.variant_kind || 'base') === kind) ?? null))
  }
  flushLoose()
  return rows
}
