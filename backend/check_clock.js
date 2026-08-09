require('dotenv').config();
const pool = require('./config/db');

(async () => {
  try {
    const r = await pool.query(`
      SELECT
        now()                                   AS db_now_raw,
        now() AT TIME ZONE 'Africa/Tunis'        AS db_now_tunis,
        current_setting('TIMEZONE')              AS db_session_tz,
        (now() AT TIME ZONE 'Africa/Tunis')::date AS db_today_tunis
    `);
    console.log('Horloge Node (ce PC)      :', new Date().toString());
    console.log('Horloge PostgreSQL (brute):', r.rows[0].db_now_raw);
    console.log('PostgreSQL en heure Tunis :', r.rows[0].db_now_tunis);
    console.log('Fuseau de session Postgres:', r.rows[0].db_session_tz);
    console.log('"Aujourd\'hui" vu par la DB:', r.rows[0].db_today_tunis);
  } catch (e) {
    console.error('Erreur de connexion à la base :', e.message);
  } finally {
    await pool.end();
  }
})();
