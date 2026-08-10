const { trouverMeilleurCodeCim11, enregistrerMatchCim11 } = require('./cim11Matcher');



const CHAMPS_CIM11 = {
  epr_etiologie: [
    'detail_lesion_structurelle',
    'detail_gene_mute',
    'detail_maladie_metabolique',
    'detail_facteur_infectieux',
    'detail_maladie_auto_immune',
  ],
};


async function coderEntitesCim11(tables, opts = {}) {
  if (!tables) return tables;
  const { pseudonyme = null, persister = false } = opts;

  for (const [tableName, champs] of Object.entries(CHAMPS_CIM11)) {
    const contenu = tables[tableName];
    if (!contenu) continue;

    const occurrences = Array.isArray(contenu) ? contenu : [contenu];

    for (const occ of occurrences) {
      if (!occ || typeof occ !== 'object') continue;

      for (const champ of champs) {
        const valeur = occ[champ];
        if (!valeur || valeur === 'null' || valeur === 'NA') continue;

        try {
          const match = await trouverMeilleurCodeCim11(valeur);
          occ[`${champ}_cim11_code`] = match ? match.code : null;
          occ[`${champ}_cim11_titre`] = match ? match.titre : null;
          occ[`${champ}_cim11_score`] = match ? match.score : null;

          if (persister && pseudonyme && match) {
            await enregistrerMatchCim11({ pseudonyme, termeSource: valeur, match });
          }
        } catch (err) {
          console.error(`Erreur codage CIM-11 (${tableName}.${champ} = "${valeur}") :`, err.message);
          occ[`${champ}_cim11_code`] = null;
          occ[`${champ}_cim11_titre`] = null;
          occ[`${champ}_cim11_score`] = null;
        }
      }
    }
  }

  return tables;
}

module.exports = { coderEntitesCim11, CHAMPS_CIM11 };
