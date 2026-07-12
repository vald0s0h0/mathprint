// Couleur de maîtrise partagée (Élèves, tableau de compétences de l'assistant sujet).
export function masteryColor(m: number) {
  return m > 0.6 ? 'green' : m > 0.3 ? 'yellow' : 'red'
}
