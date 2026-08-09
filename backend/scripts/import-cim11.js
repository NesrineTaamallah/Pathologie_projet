/**
 * Import des codes CIM-11 depuis les fichiers CSV exportés (icd11_tous_chapitres*.csv)
 * vers la table cim11_codes.
 *
 * Usage :
 *   node backend/scripts/import-cim11.js chemin/vers/icd11_tous_chapitres.csv chemin/vers/icd11_tous_chapitres__1_.csv
 *
 * Les fichiers peuvent être fournis en autant de morceaux que nécessaire
 * (le script fait un UPSERT sur le code + titre pour éviter les doublons).
 */
const fs = require('fs');
const path = require('path');
const readline = require('readline');
const pool = require('../config/db');

// Petit parseur CSV tolérant aux guillemets et virgules internes
function parseCsvLine(line) {
  const out = [];
  let cur = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inQuotes) {
      if (c === '"') {
        if (line[i + 1] === '"') { cur += '"'; i++; } else { inQuotes = false; }
      } else {
        cur += c;
      }
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ',') {
      out.push(cur);
      cur = '';
    } else {
      cur += c;
    }
  }
  out.push(cur);
  return out;
}

async function importFile(filePath) {
  const rl = readline.createInterface({
    input: fs.createReadStream(filePath, { encoding: 'utf8' }),
    crlfDelay: Infinity,
  });

  let header = null;
  let buffer = [];
  let count = 0;

  const flush = async () => {
    if (buffer.length === 0) return;
    const values = [];
    const placeholders = [];
    buffer.forEach((row, idx) => {
      const base = idx * 7;
      placeholders.push(
        `($${base + 1}, $${base + 2}, $${base + 3}, $${base + 4}, $${base + 5}, $${base + 6}, $${base + 7})`
      );
      values.push(row.chapter, row.code || null, row.title, row.classKind, row.parent_code || null, row.uri || null, row.definition || null);
    });
    const sql = `
      INSERT INTO cim11_codes (chapter, code, title, class_kind, parent_code, uri, definition)
      VALUES ${placeholders.join(',')}
    `;
    await pool.query(sql, values);
    count += buffer.length;
    buffer = [];
  };

  for await (const line of rl) {
    if (!line.trim()) continue;
    const cleaned = line.replace(/^\uFEFF/, ''); // enlève le BOM éventuel
    const cols = parseCsvLine(cleaned);
    if (!header) {
      header = cols.map((h) => h.trim());
      continue;
    }
    const row = {};
    header.forEach((h, i) => { row[h] = cols[i]; });
    if (!row.title) continue;
    buffer.push(row);
    if (buffer.length >= 500) await flush();
  }
  await flush();
  console.log(`  -> ${count} lignes importées depuis ${path.basename(filePath)}`);
}

async function main() {
  const files = process.argv.slice(2);
  if (files.length === 0) {
    console.error('Usage: node import-cim11.js fichier1.csv [fichier2.csv ...]');
    process.exit(1);
  }

  console.log('Nettoyage de la table cim11_codes avant import...');
  await pool.query('TRUNCATE cim11_codes RESTART IDENTITY CASCADE');

  for (const f of files) {
    console.log(`Import de ${f}...`);
    await importFile(f);
  }

  // Dédoublonnage de sécurité (au cas où un code apparaîtrait dans les deux fichiers)
  await pool.query(`
    DELETE FROM cim11_codes a USING cim11_codes b
    WHERE a.id > b.id
      AND a.code = b.code
      AND a.title = b.title
      AND a.code IS NOT NULL AND a.code <> ''
  `);

  const { rows } = await pool.query('SELECT count(*)::int AS n FROM cim11_codes');
  console.log(`Import terminé. ${rows[0].n} lignes en base.`);
  await pool.end();
}

main().catch((err) => {
  console.error('Erreur import CIM-11 :', err);
  process.exit(1);
});
