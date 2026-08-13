# Composants tiers du connecteur

La version Windows inclut **SumatraPDF 3.6.1**, utilisé uniquement comme
moteur local d'impression PDF. SumatraPDF est distribué sous licence GPLv3.

- Projet : https://github.com/sumatrapdfreader/sumatrapdf
- Version : `3.6.1rel` (`16c59fd`)
- Licence et sources correspondantes : jointes à chaque GitHub Release du
  connecteur sous le nom `SumatraPDF-3.6.1-source.zip`.
- SHA-256 archive binaire :
  `98b33a518d42986856d225064b0cd2d3643ecf78cbf84ab873d26cc51877a544`.
- SHA-256 archive source :
  `67bcd66f9d25fa5338ef7c4d882ce7597dc94083b5d7fe4d44da392640c8dec1`.

MathPrint ne modifie pas SumatraPDF et le lance comme processus séparé avec
des arguments documentés (`noscale`, `paper=A4`, `simplex`/`duplexlong`).
