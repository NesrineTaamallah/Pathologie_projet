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
const pool = require('../config/db');

async function importFile(filePath) {
  // Lecture caractère par caractère pour gérer correctement les champs
  // multi-lignes (définitions contenant des retours à la ligne entre guillemets)
  const raw = fs.readFileSync(filePath, 'utf8').replace(/^\uFEFF/, '');

  const rows = [];
  let row = [];
  let cur = '';
  let inQuotes = false;
  let i = 0;
  const len = raw.length;

  while (i < len) {
    const c = raw[i];
    if (inQuotes) {
      if (c === '"') {
        if (raw[i + 1] === '"') { cur += '"'; i += 2; continue; }
        inQuotes = false; i++; continue;
      }
      cur += c; i++; continue;
    }
    if (c === '"') { inQuotes = true; i++; continue; }
    if (c === ',') { row.push(cur); cur = ''; i++; continue; }
    if (c === '\r') { i++; continue; }
    if (c === '\n') {
      row.push(cur); cur = '';
      rows.push(row); row = [];
      i++; continue;
    }
    cur += c; i++;
  }
  if (cur.length > 0 || row.length > 0) { row.push(cur); rows.push(row); }

  if (rows.length === 0) return;
  const header = rows[0].map((h) => h.trim());
  let buffer = [];
  let count = 0;

  const flush = async () => {
    if (buffer.length === 0) return;
    const values = [];
    const placeholders = [];
    buffer.forEach((r, idx) => {
      const base = idx * 7;
      placeholders.push(
        `($${base + 1}, $${base + 2}, $${base + 3}, $${base + 4}, $${base + 5}, $${base + 6}, $${base + 7})`
      );
      values.push(
        (r.chapter || '').slice(0, 10) || null,
        (r.code || '').slice(0, 20) || null,
        r.title,
        (r.classKind || '').slice(0, 20) || null,
        (r.parent_code || '').slice(0, 20) || null,
        r.uri || null,
        r.definition || null
      );
    });
    const sql = `
      INSERT INTO cim11_codes (chapter, code, title, class_kind, parent_code, uri, definition)
      VALUES ${placeholders.join(',')}
    `;
    await pool.query(sql, values);
    count += buffer.length;
    buffer = [];
  };

  for (let r = 1; r < rows.length; r++) {
    const cols = rows[r];
    if (cols.length === 1 && cols[0].trim() === '') continue; // ligne vide
    const rowObj = {};
    header.forEach((h, idx) => { rowObj[h] = cols[idx]; });
    if (!rowObj.title) continue;
    buffer.push(rowObj);
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