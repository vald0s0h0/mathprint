// Part d'exercices guidés selon le niveau de l'élève et le réglage du professeur.
// Miroir exact de backend student_history.guide_ratio : test_sheet_engine vérifie
// que les deux donnent le même résultat. Aucune dépendance : le test exécute ce
// fichier avec node.

export const DEFAULT_GUIDE_REACH = 25

// réglage, dernier niveau entièrement guidé, premier niveau sans guide
const CURVE = [[0, 1, 3], [25, 3, 6], [100, 9, 11]] as const

export function guideRatio(level: number, reach: number): number {
  const r = Math.max(0, Math.min(100, reach))
  const [lo, hi] = r <= CURVE[1][0] ? [CURVE[0], CURVE[1]] : [CURVE[1], CURVE[2]]
  const t = (r - lo[0]) / (hi[0] - lo[0])
  const full = lo[1] + (hi[1] - lo[1]) * t
  const none = lo[2] + (hi[2] - lo[2]) * t
  if (level <= full) return 1
  if (level >= none) return 0
  return (none - level) / (none - full)
}
