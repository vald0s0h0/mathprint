// Loader Node : remplace @cortex-js/compute-engine (build CJS sans export
// ComputeEngine) par un stub. Dans gestionInteractif.setReponse, le moteur ne
// sert qu'à une vérification de forme (warning) ; la réponse attendue stockée
// dans autoCorrection[i].reponse.valeur reste la valeur brute de l'exercice.
const STUBS = {
  '@cortex-js/compute-engine': `
    export class ComputeEngine {
      parse(s) { return { canonical: String(s), simplify: () => ({ latex: String(s) }) } }
      box() { return { simplify: () => ({ latex: '' }) } }
    }
    export default { ComputeEngine }
  `,
}

export async function resolve(specifier, context, nextResolve) {
  if (STUBS[specifier]) {
    return { url: `stub:${specifier}`, shortCircuit: true }
  }
  return nextResolve(specifier, context)
}

export async function load(url, context, nextLoad) {
  if (url.startsWith('stub:')) {
    const name = url.slice(5)
    return { format: 'module', source: STUBS[name], shortCircuit: true }
  }
  return nextLoad(url, context)
}
