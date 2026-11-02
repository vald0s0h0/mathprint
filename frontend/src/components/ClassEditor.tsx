import { Alert, Button, Group, Modal, Select, Stack, Textarea, TextInput } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useEffect, useState } from 'react'
import { api } from '../api'

export type Catalog = {
  countries: { id: string; name: string }[]; programs: { id: string; name: string }[]
  subjects: { id: string; name: string }[]; grades: { id: string; name: string }[]
  paths: { id: string; name: string; program_id: string; kind: string; grades: string[] }[]
}
export type SchoolRow = { id: string; name: string; country_id: string; program_ids: string[]; city: string; address: string; contact: string; notes: string; service_active: boolean; class_count: number }
export type TeacherRow = { id: string; name: string; email: string; phone: string; school_ids: string[]; service_mode: string; active: boolean; has_account: boolean }
export type ClassRow = { id: string; name: string; school_id: string; school_name: string; grade_level: string; program_id: string; series_id: string | null; school_year_id: string; school_year: string; student_count: number; archived: boolean }
export type YearRow = { id: string; label: string; active: boolean }
export type Teaching = { id: string; subject_id: string; subject_name: string; teacher_id: string; framework_id: string | null; groups: { id: string; name: string; path_id: string; student_ids: string[] }[] }
export const options = (rows: { id: string; name: string }[]) => rows.map(r => ({ value: r.id, label: r.name }))
export const showError = (e: unknown) => notifications.show({ color: 'red', message: (e as Error).message })

export default function ClassEditor({ opened, onClose, onSaved, schoolId, editing }: {
  opened: boolean; onClose: () => void; onSaved: () => void; schoolId?: string; editing?: ClassRow | null
}) {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [schools, setSchools] = useState<SchoolRow[]>([])
  const [teachers, setTeachers] = useState<TeacherRow[]>([])
  const [years, setYears] = useState<YearRow[]>([])
  const [role, setRole] = useState('')
  const [school, setSchool] = useState(''); const [program, setProgram] = useState('fr')
  const [grade, setGrade] = useState('6e'); const [series, setSeries] = useState<string | null>(null)
  const [teacher, setTeacher] = useState<string | null>(null); const [subject, setSubject] = useState('math')
  const [year, setYear] = useState<string | null>(null); const [name, setName] = useState('')
  const [students, setStudents] = useState(''); const [saving, setSaving] = useState(false)
  useEffect(() => {
    if (!opened) return
    setName(editing?.name || ''); setSchool(editing?.school_id || schoolId || '')
    setProgram(editing?.program_id || 'fr'); setGrade(editing?.grade_level || '6e'); setSeries(editing?.series_id || null)
    setTeacher(null); setSubject('math'); setStudents('')
    Promise.all([api.get<Catalog>('/api/operations/catalog'), api.get<SchoolRow[]>('/api/operations/schools'),
      api.get<YearRow[]>('/api/years'), api.get<{ role: string }>('/api/auth/me')]).then(([c, s, y, me]) => {
        setCatalog(c); setSchools(s); setYears(y); setRole(me.role)
        setYear(editing?.school_year_id || y.find(x => x.active)?.id || null)
        if (me.role !== 'teacher') api.get<TeacherRow[]>('/api/operations/teachers').then(setTeachers).catch(showError)
      }).catch(showError)
  }, [opened, schoolId, editing])
  const chosenSchool = schools.find(s => s.id === school)
  async function save() {
    setSaving(true)
    try {
      if (editing) await api.patch(`/api/classes/${editing.id}`, { name, grade_level: grade, program_id: program, series_id: series })
      else await api.post('/api/classes', { name, school_id: school, program_id: program, grade_level: grade,
        series_id: series, school_year_id: year, teacher_profile_id: teacher, subject_id: subject, students_text: students })
      onSaved(); onClose()
    } catch (e) { showError(e) } finally { setSaving(false) }
  }
  return <Modal opened={opened} onClose={onClose} title={editing ? 'Modifier la classe' : 'Nouvelle classe'} size="lg">
    <Stack>
      {schools.length === 0 && <Alert>Aucun établissement affecté. L’équipe Kironina doit d’abord rattacher votre fiche à un établissement.</Alert>}
      <Select label="Établissement" data={options(schools)} value={school || null} searchable disabled={!!editing}
        onChange={v => { setSchool(v || ''); setTeacher(null); const s = schools.find(x => x.id === v); setProgram(s?.program_ids[0] || 'fr'); setSeries(null) }} />
      <Group grow><TextInput label="Classe" placeholder="Ex. 3A" value={name} onChange={e => setName(e.currentTarget.value)} />
        <Select label="Année scolaire" disabled={!!editing} data={years.filter(y => y.active || y.id === year).map(y => ({ value: y.id, label: y.label }))} value={year} onChange={setYear} /></Group>
      <Group grow><Select label="Programme" data={options(catalog?.programs.filter(p => chosenSchool?.program_ids.includes(p.id)) || [])}
        value={program} onChange={v => { setProgram(v || 'fr'); setSeries(null) }} />
        <Select label="Cycle" data={options(catalog?.grades || [])} value={grade} onChange={v => { setGrade(v || '6e'); setSeries(null) }} /></Group>
      {['2nde', '1re', 'tle'].includes(grade) && <Select label="Série" placeholder={program === 'ci' ? 'Choisir une série' : 'Voie générale (sans série)'} clearable
        data={options(catalog?.paths.filter(p => p.kind === 'series' && p.program_id === program && p.grades.includes(grade)) || [])} value={series} onChange={setSeries} />}
      {!editing && <>
        <Select label="Matière" data={options(catalog?.subjects || [])} value={subject} onChange={v => setSubject(v || 'math')} />
        {role !== 'teacher' && <Select label="Professeur" searchable data={options(teachers.filter(t => t.active && t.school_ids.includes(school)))} value={teacher} onChange={setTeacher} />}
        <Textarea label="Élèves" description="Un nom complet par ligne, dans l’ordre de la classe." minRows={6} value={students} onChange={e => setStudents(e.currentTarget.value)} />
      </>}
      <Button onClick={save} loading={saving} disabled={!name.trim() || !school || !year || (!editing && role !== 'teacher' && !teacher)}>Enregistrer</Button>
    </Stack>
  </Modal>
}
