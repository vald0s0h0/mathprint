// État global léger : filtre Cycle (le professeur travaille par cycle, jamais
// plusieurs à la fois) + suivi des jobs de génération de fond.
//
// Vocabulaire (partout dans l'UI) :
// - CYCLE  : niveau scolaire 6e / 5e / 4e / 3e (grade_level) ;
// - CLASSE : groupe d'élèves d'un même cycle (ex. 5eA) ;
// - NIVEAU : niveau pédagogique 1-10 d'un élève, privé professeur.
import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { api, getToken } from '../api'

export type Cycle = '6e' | '5e' | '4e' | '3e' | '2nde' | '1re' | 'tle' | 'all'
export const CYCLES: Cycle[] = ['6e', '5e', '4e', '3e', '2nde', '1re', 'tle']

export type GenerationJob = {
  assessment_id: string; title: string; class_name: string
  status: string; progress: number
}

type AppState = {
  subject: string
  setSubject: (s: string) => void
  cycle: Cycle
  setCycle: (c: Cycle) => void
  /** true si l'élément (classe, sujet, lot…) appartient au cycle filtré */
  matches: (grade?: string | null) => boolean
  /** sujets en file de génération de fond (assistant, étape finale) */
  activeJobs: GenerationJob[]
}

const Ctx = createContext<AppState>({
  subject: 'math', setSubject: () => {}, cycle: 'all', setCycle: () => {}, matches: () => true, activeJobs: [],
})

export function AppStateProvider({ children }: { children: React.ReactNode }) {
  const [cycle, setCycleRaw] = useState<Cycle>(() => {
    const saved = localStorage.getItem('mathprint_cycle')
    return CYCLES.includes(saved as Cycle) ? saved as Cycle : 'all'
  })
  const [subject, setSubject] = useState('math')
  const [activeJobs, setActiveJobs] = useState<GenerationJob[]>([])

  const setCycle = useCallback((c: Cycle) => {
    setCycleRaw(c)
    localStorage.setItem('mathprint_cycle', c)
  }, [])

  const matches = useCallback(
    (grade?: string | null) => cycle === 'all' || !grade || grade === cycle,
    [cycle],
  )

  useEffect(() => {
    const poll = () => {
      if (!getToken()) { setActiveJobs([]); return }
      api.get<GenerationJob[]>('/api/assessments/jobs/active').then(setActiveJobs).catch(() => {})
    }
    poll()
    const t = setInterval(poll, 4000)
    return () => clearInterval(t)
  }, [])

  return (
    <Ctx.Provider value={{ cycle, setCycle, matches, activeJobs, subject, setSubject }}>
      {children}
    </Ctx.Provider>
  )
}

export const useAppState = () => useContext(Ctx)
