// État global léger : filtres de la barre du haut + suivi des jobs de génération
// de fond. Chaque zone a son filtre (cf. components/HeaderFilters) :
// - onglets du professeur : cycles et matières qu'il enseigne (`cycle`, `subject`) ;
// - Établissements : l'établissement (`school`) ;
// - Exercices : tout le catalogue (`catalog`), sans effet sur les autres onglets.
//
// Vocabulaire (partout dans l'UI) :
// - CYCLE  : niveau scolaire 6e / 5e / 4e / 3e (grade_level) ;
// - CLASSE : groupe d'élèves d'un même cycle (ex. 5eA) ;
// - NIVEAU : niveau pédagogique 1-10 d'un élève, privé professeur.
import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { api, getToken } from '../api'

export type Cycle = '6e' | '5e' | '4e' | '3e' | '2nde' | '1re' | 'tle' | 'all'
export const CYCLES: Cycle[] = ['6e', '5e', '4e', '3e', '2nde', '1re', 'tle']
export const CYCLE_LABELS: Partial<Record<Cycle, string>> = { '2nde': 'Seconde', '1re': 'Première', tle: 'Terminale' }

export type GenerationJob = {
  assessment_id: string; title: string; class_name: string
  status: string; progress: number
}

/** Ce que l'utilisateur enseigne (backend services.education.teaching_scope). */
export type Teaching = { cycles: Cycle[]; subjects: { id: string; name: string }[]; programs: string[] }
export type CatalogFilter = { program: string; cycle: Cycle; subject: string }
export type SchoolOption = { id: string; name: string }

const NO_TEACHING: Teaching = { cycles: [], subjects: [], programs: [] }
const ALL_CATALOG: CatalogFilter = { program: 'all', cycle: 'all', subject: 'math' }

type AppState = {
  subject: string
  setSubject: (s: string) => void
  cycle: Cycle
  setCycle: (c: Cycle) => void
  /** true si l'élément (classe, sujet, lot…) appartient au cycle filtré */
  matches: (grade?: string | null) => boolean
  teaching: Teaching
  setTeaching: (t: Teaching) => void
  catalog: CatalogFilter
  setCatalog: (changes: Partial<CatalogFilter>) => void
  school: string | null
  setSchool: (id: string | null) => void
  schools: SchoolOption[]
  setSchools: (schools: SchoolOption[]) => void
  /** sujets en file de génération de fond (assistant, étape finale) */
  activeJobs: GenerationJob[]
}

const Ctx = createContext<AppState>({
  subject: 'math', setSubject: () => {}, cycle: 'all', setCycle: () => {}, matches: () => true,
  teaching: NO_TEACHING, setTeaching: () => {}, catalog: ALL_CATALOG, setCatalog: () => {},
  school: null, setSchool: () => {}, schools: [], setSchools: () => {}, activeJobs: [],
})

function savedCatalog(): CatalogFilter {
  try {
    return { ...ALL_CATALOG, ...JSON.parse(localStorage.getItem('mathprint_catalog') || '{}') }
  } catch {
    return ALL_CATALOG
  }
}

export function AppStateProvider({ children }: { children: React.ReactNode }) {
  const [chosenCycle, setChosenCycle] = useState<Cycle>(() => {
    const saved = localStorage.getItem('mathprint_cycle')
    return CYCLES.includes(saved as Cycle) ? saved as Cycle : 'all'
  })
  const [chosenSubject, setSubject] = useState('math')
  const [teaching, setTeaching] = useState<Teaching>(NO_TEACHING)
  const [catalog, setCatalogRaw] = useState<CatalogFilter>(savedCatalog)
  const [school, setSchool] = useState<string | null>(null)
  const [schools, setSchools] = useState<SchoolOption[]>([])
  const [activeJobs, setActiveJobs] = useState<GenerationJob[]>([])

  // Jamais de filtre hors de ce que l'utilisateur enseigne : un ancien choix
  // resté dans le navigateur (ex. 5e) viderait toutes ses pages sans recours.
  const { cycles, subjects } = teaching
  const cycle: Cycle = cycles.length === 1 ? cycles[0]
    : chosenCycle !== 'all' && !cycles.includes(chosenCycle) ? 'all' : chosenCycle
  const subject = subjects.length && !subjects.some((s) => s.id === chosenSubject)
    ? subjects[0].id : chosenSubject

  const setCycle = useCallback((c: Cycle) => {
    setChosenCycle(c)
    localStorage.setItem('mathprint_cycle', c)
  }, [])

  const setCatalog = useCallback((changes: Partial<CatalogFilter>) => {
    setCatalogRaw((current) => {
      const next = { ...current, ...changes }
      localStorage.setItem('mathprint_catalog', JSON.stringify(next))
      return next
    })
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
    <Ctx.Provider value={{
      cycle, setCycle, matches, activeJobs, subject, setSubject, teaching, setTeaching,
      catalog, setCatalog, school, setSchool, schools, setSchools,
    }}>
      {children}
    </Ctx.Provider>
  )
}

export const useAppState = () => useContext(Ctx)
