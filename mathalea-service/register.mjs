// Hooks de chargement Node : substitue les modules navigateur-only de MathALÉA
// par des stubs inoffensifs pour l'exécution headless.
import { register } from 'node:module'
import { pathToFileURL } from 'node:url'

register('./stub-loader.mjs', pathToFileURL('./'))
