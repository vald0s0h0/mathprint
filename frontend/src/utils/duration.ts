// Durées des référentiels (programme ivoirien) : heures officielles par leçon,
// minutes estimées par compétence. Partagé par Compétences et Exercices.

export function formatMinutes(minutes: number): string {
  if (minutes < 60) return `${minutes} min`
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return m ? `${h} h ${String(m).padStart(2, '0')}` : `${h} h`
}

export function formatHours(hours: number): string {
  return `${Number.isInteger(hours) ? hours : hours.toLocaleString('fr-FR')} h`
}

export const DURATION_HINT =
  'Durée prévue par le programme : heures officielles de la leçon (progression annuelle) réparties entre ses compétences'
