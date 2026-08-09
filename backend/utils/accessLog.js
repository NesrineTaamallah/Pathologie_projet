const pool = require('../config/db');


async function logAccess({ userId = null, action, success, req, sessionId = null }) {
  const ip = req?.ip ?? null;
  const userAgent = req?.headers?.['user-agent'] ?? null;
  const sid = sessionId ?? req?.user?.jti ?? null;

  // CORRECTIF (dates faussées dans les graphiques d'activité) : la colonne
  // created_at a DEFAULT now() côté PostgreSQL, donc si l'horloge SYSTÈME du
  // serveur de base de données est en retard (ex. VM/conteneur avec une date
  // système fausse), chaque nouveau log est horodaté avec le mauvais jour,
  // indépendamment de tout réglage de fuseau (AT TIME ZONE) fait côté lecture.
  // On fournit donc explicitement l'horodatage depuis l'horloge du serveur
  // applicatif Node (new Date(), correcte), plutôt que de laisser Postgres
  // appliquer son propre now() (potentiellement faux) à l'écriture.
  await pool.query(
    `INSERT INTO access_logs (user_id, action, success, ip_address, user_agent, session_id, created_at)
     VALUES ($1, $2, $3, $4, $5, $6, $7)`,
    [userId, action, success, ip, userAgent, sid, new Date()]
  );
}

module.exports = { logAccess };