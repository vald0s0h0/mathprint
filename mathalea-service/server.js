// Service HTTP MathALÉA headless (conteneur "mathalea" du cahier des charges §11.1).
//
//   GET  /health              -> { status, mathaleaVersion }
//   GET  /catalog?grade=5e    -> [{ ref, grade, title, file, amcType, interactifType }]
//   POST /generate            -> { ref, seed, nbQuestions? , sup?... }
//        -> { ref, titre, consigne, questions[], corrections[], expected[] }
//
// Déterminisme : Math.random est remplacé par seedrandom(seed) avant chaque
// génération (les exercices MathALÉA utilisent Math.random via outils.randint).
import { createServer } from 'node:http'
import { readFileSync, readdirSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import seedrandom from 'seedrandom'

const MATHALEA_ROOT = resolve(process.env.MATHALEA_ROOT || '../mathalea')
const PORT = Number(process.env.PORT || 8123)
const GRADES = ['6e', '5e', '4e', '3e']
const mathaleaVersion = JSON.parse(
  readFileSync(join(MATHALEA_ROOT, 'package.json'), 'utf8')).version

// ---- stubs DOM globaux (les modules exercices sont écrits pour le navigateur)
globalThis.window = {
  location: { href: 'http://localhost/', search: '' },
  addEventListener() {}, notify() {},
}
globalThis.document = {
  getElementById: () => null, querySelector: () => null,
  querySelectorAll: () => [], createElement: () => ({ style: {}, classList: { add() {} } }),
  addEventListener() {},
}
globalThis.localStorage = { getItem: () => null, setItem() {} }

// ---- catalogue : parse statique des fichiers (pas d'import massif)
function buildCatalog() {
  const catalog = []
  for (const grade of GRADES) {
    const dir = join(MATHALEA_ROOT, 'src/js/exercices', grade)
    let files = []
    try { files = readdirSync(dir).filter((f) => f.endsWith('.js')) } catch { continue }
    for (const f of files) {
      const src = readFileSync(join(dir, f), 'utf8')
      const titre = src.match(/export const titre\s*=\s*(['"`])(.*?)\1/s)?.[2]
      const ref = src.match(/export const ref\s*=\s*['"`](.*?)['"`]/)?.[1]
        ?? f.replace(/\.js$/, '')
      if (!titre) continue
      catalog.push({
        ref,
        grade,
        title: titre.replace(/\\'/g, "'"),
        file: `${grade}/${f}`,
        amcType: src.match(/export const amcType\s*=\s*['"`](.*?)['"`]/)?.[1] ?? null,
        interactifType: src.match(/export const interactifType\s*=\s*['"`](.*?)['"`]/)?.[1] ?? null,
      })
    }
  }
  return catalog
}
const CATALOG = buildCatalog()
const BY_REF = new Map(CATALOG.map((e) => [e.ref, e]))

let contextModule = null
async function getContext() {
  if (!contextModule) {
    contextModule = await import(
      pathToFileURL(join(MATHALEA_ROOT, 'src/js/modules/context.js')).href)
  }
  return contextModule
}

function serializeExpected(autoCorrection) {
  // autoCorrection[i].reponse.valeur : nombre, string, FractionX {num, den}...
  return (autoCorrection || []).map((ac) => {
    const rep = ac?.reponse
    if (!rep || rep.valeur === undefined) return null
    const vals = Array.isArray(rep.valeur) ? rep.valeur : [rep.valeur]
    return {
      format: rep.param?.formatInteractif ?? null,
      values: vals.map((v) => {
        if (v === null || v === undefined) return null
        if (typeof v === 'number' || typeof v === 'string') return v
        if (typeof v === 'object' && 'num' in v && 'den' in v)
          return { fraction: [Number(v.num), Number(v.den)] }
        return String(v.toString?.() ?? v)
      }),
    }
  })
}

async function generate({ ref, seed = 1, nbQuestions = 1, params = {} }) {
  const entry = BY_REF.get(ref)
  if (!entry) throw new Error(`ref inconnu : ${ref}`)
  const { setOutputLatex } = await getContext()
  setOutputLatex() // sortie LaTeX : pas de HTML interactif

  Math.random = seedrandom(`mathprint-${ref}-${seed}`)
  const mod = await import(
    pathToFileURL(join(MATHALEA_ROOT, 'src/js/exercices', entry.file)).href)
  const ex = new mod.default()
  ex.nbQuestions = nbQuestions
  ex.interactif = false
  for (const [k, v] of Object.entries(params)) {
    if (['sup', 'sup2', 'sup3', 'sup4', 'correctionDetaillee'].includes(k)) ex[k] = v
  }
  ex.nouvelleVersion(0)
  return {
    ref,
    mathaleaVersion,
    titre: mod.titre ?? ex.titre,
    consigne: ex.consigne ?? '',
    questions: ex.listeQuestions ?? [],
    corrections: ex.listeCorrections ?? [],
    expected: serializeExpected(ex.autoCorrection),
    amcType: entry.amcType,
  }
}

function json(res, code, data) {
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' })
  res.end(JSON.stringify(data))
}

createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x')
  try {
    if (req.method === 'GET' && url.pathname === '/health') {
      return json(res, 200, { status: 'ok', mathaleaVersion, exercises: CATALOG.length })
    }
    if (req.method === 'GET' && url.pathname === '/catalog') {
      const grade = url.searchParams.get('grade')
      return json(res, 200, grade ? CATALOG.filter((e) => e.grade === grade) : CATALOG)
    }
    if (req.method === 'POST' && url.pathname === '/generate') {
      let body = ''
      for await (const chunk of req) body += chunk
      const result = await generate(JSON.parse(body || '{}'))
      return json(res, 200, result)
    }
    json(res, 404, { error: 'not found' })
  } catch (e) {
    json(res, 500, { error: String(e.message || e) })
  }
}).listen(PORT, () => {
  console.log(`mathalea-service v${mathaleaVersion} sur :${PORT} — ${CATALOG.length} exercices`)
})
