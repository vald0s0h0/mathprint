const katex = require(process.argv[2] + '/node_modules/katex')
const data = require(process.argv[3])
let n = 0, bad = 0
for (const fw of data.frameworks) for (const d of fw.domains) for (const ch of d.chapters) for (const c of ch.competencies) {
  for (const text of [c.label]) {
    const parts = text.split('$')
    if (parts.length % 2 === 0) { bad++; console.log('DOLLAR IMPAIR', c.code, text); continue }
    for (let i = 1; i < parts.length; i += 2) {
      n++
      try { katex.renderToString(parts[i], { throwOnError: true }) }
      catch (e) { bad++; console.log('KATEX', c.code, parts[i], e.message) }
    }
  }
}
console.log('formules', n, 'erreurs', bad)
