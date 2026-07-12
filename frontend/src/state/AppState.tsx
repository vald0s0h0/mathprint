// État global léger : filtre Cycle (le professeur travaille par cycle, jamais
// plusieurs à la fois) + mode mock (pilote l'affichage des outils de démo).
//
// Vocabulaire (partout dans l'UI) :
// - CYCLE  : niveau scolaire 6e / 5e / 4e / 3e (grade_level) ;
// - CLASSE : groupe d'élèves d'un même cycle (ex. 5eA) ;
// - NIVEAU : niveau pédagogique 1-10 d'un élève, privé professeur.
import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { api, getToken } from '../api'

export type Cycle = '6e' | '5e' | '4e' | '3e' | 'all'
export const CYCLES: Cycle[] = ['6e', '5e', '4e', '3e']

type AppState = {
  cycle: Cycle
  setCycle: (c: Cycle) => void
  /** true si l'élément (classe, sujet, lot…) appartient au cycle filtré */
  matches: (grade?: string | null) => boolean
  mockMode: boolean
  refreshSystem: () => void
}

const Ctx = createContext<AppState>({
  cycle: 'all', setCycle: () => {}, matches: () => true,
  mockMode: false, refreshSystem: () => {},
})

export function AppStateProvider({ children }: { children: React.ReactNode }) {
  const [cycle, setCycleRaw] = useState<Cycle>(() => {
    const saved = localStorage.getItem('mathprint_cycle')
    return (saved === '6e' || saved === '5e' || saved === '4e' || saved === '3e') ? saved : 'all'
  })
  const [mockMode, setMockMode] = useState(false)

  const setCycle = useCallback((c: Cycle) => {
    setCycleRaw(c)
    localStorage.setItem('mathprint_cycle', c)
  }, [])

  const matches = useCallback(
    (grade?: string | null) => cycle === 'all' || !grade || grade === cycle,
    [cycle],
  )

  const refreshSystem = useCallback(() => {
    if (!getToken()) return
    api.get<Record<string, { enabled?: boolean }>>('/api/settings/system')
      .then((s) => setMockMode(!!s.mock_mode?.enabled))
      .catch(() => {})
  }, [])

  useEffect(refreshSystem, [refreshSystem])

  return (
    <Ctx.Provider value={{ cycle, setCycle, matches, mockMode, refreshSystem }}>
      {children}
    </Ctx.Provider>
  )
}

export const useAppState = () => useContext(Ctx)
