const pool = require('../config/db');
const { generateTempPassword, hashPassword, verifyPassword } = require('../utils/passwordUtils');
const { sendTempPasswordEmail, sendDormantReminderEmail, sendCustomEmail, sendMfaGuideEmail } = require('../utils/mailer');
const { logAccess } = require('../utils/accessLog');

async function createUser(req, res) {
  const { email, role, adminPassword } = req.body; 
  const adminId = req.user.sub;

  let organizationName = req.body.organizationName ? String(req.body.organizationName).trim() : null;
  if (role === 'admin') {
    organizationName = null;
  } else if (!organizationName) {
    const label = role === 'clinicien' ? 'de la clinique/hôpital' : 'du laboratoire/institut';
    return res.status(400).json({ error: `Le nom ${label} est requis pour ce rôle.` });
  }

  try {
    
    if (role === 'admin') {
      if (!adminPassword) {
        return res.status(400).json({ error: 'Confirmez votre mot de passe pour créer un compte admin.' });
      }
      const selfResult = await pool.query('SELECT password_hash FROM users WHERE id = $1', [adminId]);
      const selfHash = selfResult.rows[0]?.password_hash;
      const passwordOk = selfHash && await verifyPassword(adminPassword, selfHash);
      if (!passwordOk) {
        await logAccess({ userId: adminId, action: 'CREATE_USER_STEPUP_AUTH_FAILED', success: false, req });
        return res.status(401).json({ error: 'Mot de passe incorrect. Création du compte admin annulée.' });
      }
    }

    const existing = await pool.query('SELECT id FROM users WHERE email = $1', [email]);
    if (existing.rows.length > 0) {
      return res.status(409).json({ error: 'Un compte existe déjà avec cet email.' });
    }

    const tempPassword = generateTempPassword();
    const passwordHash = await hashPassword(tempPassword);

    const result = await pool.query(
      `INSERT INTO users (email, password_hash, role, must_change_password, created_by, organization_name)
       VALUES ($1, $2, $3, true, $4, $5)
       RETURNING id, email, role, created_at, organization_name`,
      [email, passwordHash, role, adminId, organizationName]
    );
    const newUser = result.rows[0];

    
    try {
      await sendTempPasswordEmail(email, tempPassword, role);
    } catch (emailErr) {
      console.error('Échec envoi email pour', email, '-', emailErr.message);

      await pool.query('DELETE FROM users WHERE id = $1', [newUser.id]);

      await logAccess({ userId: adminId, action: `CREATE_USER_EMAIL_FAILED:${email}`, success: false, req });

      return res.status(502).json({
        error:
          "Le compte n'a pas pu être créé : l'envoi de l'email a échoué. " +
          'Réessayez dans quelques instants (rien n\'a été enregistré).',
      });
    }

    await logAccess({ userId: adminId, action: `CREATE_USER:${newUser.id}`, success: true, req });

    return res.status(201).json({
      message: 'Utilisateur créé, email envoyé avec le mot de passe temporaire.',
      user: newUser,
    });
  } catch (err) {
    console.error(err);
    return res.status(500).json({ error: 'Erreur serveur lors de la création du compte.' });
  }
}

async function listUsers(req, res) {
  try {
    const result = await pool.query(
      `SELECT id, email, role, must_change_password, is_2fa_enabled, created_at
       FROM users ORDER BY created_at DESC`
    );
    return res.json(result.rows);
  } catch (err) {
    console.error(err);
    return res.status(500).json({ error: 'Erreur serveur.' });
  }
}


async function resetTotp(req, res) {
  const { id } = req.params;
  const adminId = req.user.sub;

  
  if (id === adminId) {
    return res.status(403).json({
      error: "Vous ne pouvez pas réinitialiser votre propre 2FA. Demandez à un autre admin de le faire pour vous.",
    });
  }

  try {
    const result = await pool.query(
      `UPDATE users
       SET is_2fa_enabled = false, totp_secret = NULL
       WHERE id = $1
       RETURNING id, email, role`,
      [id]
    );

    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Utilisateur introuvable.' });
    }

    await logAccess({ userId: adminId, action: `RESET_2FA:${id}`, success: true, req });

    return res.json({
      message: `2FA réinitialisé pour ${result.rows[0].email}. L'utilisateur devra reconfigurer un nouveau QR code à sa prochaine connexion.`,
      user: result.rows[0],
    });
  } catch (err) {
    console.error(err);
    return res.status(500).json({ error: 'Erreur serveur.' });
  }
}

const TEMP_PASSWORD_VALIDITY_HOURS = 48;


async function listUsersDetailed(req, res) {
  try {
    const result = await pool.query(
      `SELECT id, email, role, organization_name, must_change_password, is_2fa_enabled,
              is_active, failed_login_attempts, locked_until,
              temp_password_created_at, last_login_at, created_at
       FROM users ORDER BY created_at DESC`
    );

    const now = Date.now();
    const users = result.rows.map((u) => {
      let tempPasswordStatus = null;
      if (u.must_change_password) {
        const createdAt = new Date(u.temp_password_created_at || u.created_at).getTime();
        const hoursElapsed = (now - createdAt) / (1000 * 60 * 60);
        tempPasswordStatus = hoursElapsed > TEMP_PASSWORD_VALIDITY_HOURS ? 'expired' : 'pending';
      }
      return { ...u, tempPasswordStatus };
    });

    return res.json(users);
  } catch (err) {
    console.error(err);
    return res.status(500).json({ error: 'Erreur serveur.' });
  }
}


async function resendTempPassword(req, res) {
  const { id } = req.params;
  const adminId = req.user.sub;

  try {
    const existing = await pool.query('SELECT id, email, role, password_hash FROM users WHERE id = $1', [id]);
    if (existing.rows.length === 0) {
      return res.status(404).json({ error: 'Utilisateur introuvable.' });
    }
    const user = existing.rows[0];
    const previousHash = user.password_hash;

    const tempPassword = generateTempPassword();
    const newHash = await hashPassword(tempPassword);

    await pool.query(
      `UPDATE users
       SET password_hash = $1, must_change_password = true, temp_password_created_at = now()
       WHERE id = $2`,
      [newHash, id]
    );

    try {
      await sendTempPasswordEmail(user.email, tempPassword, user.role);
    } catch (emailErr) {
      console.error('Échec renvoi email pour', user.email, '-', emailErr.message);

      
      await pool.query(
        `UPDATE users SET password_hash = $1 WHERE id = $2`,
        [previousHash, id]
      );

      await logAccess({ userId: adminId, action: `RESEND_TEMP_PASSWORD_EMAIL_FAILED:${user.email}`, success: false, req });

      return res.status(502).json({
        error: "L'envoi de l'email a échoué. Rien n'a été modifié, réessayez.",
      });
    }

    await logAccess({ userId: adminId, action: `RESEND_TEMP_PASSWORD:${id}`, success: true, req });

    return res.json({ message: `Nouveau mot de passe temporaire envoyé à ${user.email}.` });
  } catch (err) {
    console.error(err);
    return res.status(500).json({ error: 'Erreur serveur.' });
  }
}

async function unlockUser(req, res) {
  const { id } = req.params;
  const adminId = req.user.sub;

  try {
    const result = await pool.query(
      `UPDATE users SET failed_login_attempts = 0, locked_until = NULL
       WHERE id = $1 RETURNING id, email`,
      [id]
    );

    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Utilisateur introuvable.' });
    }

    await logAccess({ userId: adminId, action: `UNLOCK_ACCOUNT:${id}`, success: true, req });

    return res.json({ message: `Compte de ${result.rows[0].email} déverrouillé.` });
  } catch (err) {
    console.error(err);
    return res.status(500).json({ error: 'Erreur serveur.' });
  }
}

async function toggleActive(req, res) {
  const { id } = req.params;
  const adminId = req.user.sub;

  if (id === adminId) {
    return res.status(400).json({ error: 'Vous ne pouvez pas désactiver votre propre compte.' });
  }

  try {
    const result = await pool.query(
      `UPDATE users SET is_active = NOT is_active WHERE id = $1
       RETURNING id, email, is_active`,
      [id]
    );

    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Utilisateur introuvable.' });
    }

    const updated = result.rows[0];

    await logAccess({
      userId: adminId,
      action: `${updated.is_active ? 'REACTIVATE' : 'DEACTIVATE'}_ACCOUNT:${id}`,
      success: true,
      req,
    });

    return res.json({
      message: `Compte de ${updated.email} ${updated.is_active ? 'réactivé' : 'désactivé'}.`,
      user: updated,
    });
  } catch (err) {
    console.error(err);
    return res.status(500).json({ error: 'Erreur serveur.' });
  }
}


async function notifyDormantUsers(req, res) {
  const adminId = req.user.sub;

  try {
    const dormant = await pool.query(`
      SELECT id, email, role
      FROM users
      WHERE is_active = true
        AND last_login_at IS NOT NULL
        AND last_login_at < now() - interval '60 days'
    `);

    if (dormant.rows.length === 0) {
      return res.json({ sent: 0, failed: 0, failedEmails: [] });
    }

    let sent = 0;
    const failedEmails = [];

    for (const user of dormant.rows) {
      try {
        await sendDormantReminderEmail(user.email, user.role);
        sent += 1;
      } catch (emailErr) {
        console.error('Échec email rappel dormant pour', user.email, '-', emailErr.message);
        failedEmails.push(user.email);
      }
    }

    await logAccess({
      userId: adminId,
      action: `NOTIFY_DORMANT_USERS:${sent}/${dormant.rows.length}`,
      success: failedEmails.length === 0,
      req,
    });

    return res.json({ sent, failed: failedEmails.length, failedEmails });
  } catch (err) {
    console.error(err);
    return res.status(500).json({ error: "Erreur serveur lors de l'envoi des rappels." });
  }
}


async function retryFailedEmails(req, res) {
  const adminId = req.user.sub;

  try {
    const candidates = await pool.query(`
      SELECT u.id, u.email, u.role, u.password_hash
      FROM users u
      WHERE u.must_change_password = true
        AND EXISTS (
          SELECT 1 FROM access_logs al
          WHERE al.action = 'RESEND_TEMP_PASSWORD_EMAIL_FAILED:' || u.email
            AND al.created_at = (
              SELECT MAX(al2.created_at) FROM access_logs al2
              WHERE al2.action ~ ('^(CREATE_USER:' || u.id || '|RESEND_TEMP_PASSWORD:' || u.id || '|RESEND_TEMP_PASSWORD_EMAIL_FAILED:' || u.email || ')$')
            )
        )
    `);

    if (candidates.rows.length === 0) {
      return res.json({ sent: 0, failed: 0, failedEmails: [] });
    }

    let sent = 0;
    const failedEmails = [];

    for (const user of candidates.rows) {
      const previousHash = user.password_hash;
      const tempPassword = generateTempPassword();
      const newHash = await hashPassword(tempPassword);

      await pool.query(
        `UPDATE users
         SET password_hash = $1, must_change_password = true, temp_password_created_at = now()
         WHERE id = $2`,
        [newHash, user.id]
      );

      try {
        await sendTempPasswordEmail(user.email, tempPassword, user.role);
        await logAccess({ userId: adminId, action: `RESEND_TEMP_PASSWORD:${user.id}`, success: true, req });
        sent += 1;
      } catch (emailErr) {
        console.error('Échec renvoi email pour', user.email, '-', emailErr.message);
        await pool.query(`UPDATE users SET password_hash = $1 WHERE id = $2`, [previousHash, user.id]);
        await logAccess({ userId: adminId, action: `RESEND_TEMP_PASSWORD_EMAIL_FAILED:${user.email}`, success: false, req });
        failedEmails.push(user.email);
      }
    }

    return res.json({ sent, failed: failedEmails.length, failedEmails });
  } catch (err) {
    console.error(err);
    return res.status(500).json({ error: "Erreur serveur lors du renvoi des emails échoués." });
  }
}

const VALID_ROLES = ['admin', 'clinicien', 'chercheur', 'statisticien'];


async function sendCommunication(req, res) {
  const adminId = req.user.sub;
  const { recipientMode, role, userIds, subject, message } = req.body;

  const cleanSubject = typeof subject === 'string' ? subject.trim() : '';
  const cleanMessage = typeof message === 'string' ? message.trim() : '';

  if (!cleanSubject) {
    return res.status(400).json({ error: 'Le sujet est requis.' });
  }
  if (!cleanMessage) {
    return res.status(400).json({ error: 'Le message est requis.' });
  }

  try {
    let recipients;

    if (recipientMode === 'all') {
      recipients = await pool.query(`SELECT id, email FROM users WHERE is_active = true`);
    } else if (recipientMode === 'role') {
      if (!VALID_ROLES.includes(role)) {
        return res.status(400).json({ error: 'Rôle invalide.' });
      }
      recipients = await pool.query(
        `SELECT id, email FROM users WHERE is_active = true AND role = $1`,
        [role]
      );
    } else if (recipientMode === 'selected') {
      if (!Array.isArray(userIds) || userIds.length === 0) {
        return res.status(400).json({ error: 'Sélectionnez au moins un destinataire.' });
      }
      recipients = await pool.query(
        `SELECT id, email FROM users WHERE id = ANY($1::uuid[])`,
        [userIds]
      );
    } else {
      return res.status(400).json({ error: 'Mode de destinataires invalide.' });
    }

    if (recipients.rows.length === 0) {
      return res.status(400).json({ error: 'Aucun destinataire trouvé pour cette sélection.' });
    }

    let sent = 0;
    const failedEmails = [];

    for (const r of recipients.rows) {
      try {
        await sendCustomEmail(r.email, cleanSubject, cleanMessage);
        sent += 1;
      } catch (emailErr) {
        console.error('Échec envoi communication à', r.email, '-', emailErr.message);
        failedEmails.push(r.email);
      }
    }

    await logAccess({
      userId: adminId,
      action: `SEND_CUSTOM_EMAIL:${sent}/${recipients.rows.length}`,
      success: failedEmails.length === 0,
      req,
    });

    return res.json({ sent, failed: failedEmails.length, failedEmails, total: recipients.rows.length });
  } catch (err) {
    console.error(err);
    return res.status(500).json({ error: "Erreur serveur lors de l'envoi du message." });
  }
}


async function notifyMfaSetup(req, res) {
  const adminId = req.user.sub;

  try {
    const withoutMfa = await pool.query(`
      SELECT id, email, role
      FROM users
      WHERE is_active = true
        AND is_2fa_enabled = false
    `);

    if (withoutMfa.rows.length === 0) {
      return res.json({ sent: 0, failed: 0, failedEmails: [] });
    }

    let sent = 0;
    const failedEmails = [];

    for (const user of withoutMfa.rows) {
      try {
        await sendMfaGuideEmail(user.email, user.role);
        sent += 1;
      } catch (emailErr) {
        console.error('Échec envoi guide 2FA à', user.email, '-', emailErr.message);
        failedEmails.push(user.email);
      }
    }

    await logAccess({
      userId: adminId,
      action: `NOTIFY_MFA_SETUP:${sent}/${withoutMfa.rows.length}`,
      success: failedEmails.length === 0,
      req,
    });

    return res.json({ sent, failed: failedEmails.length, failedEmails });
  } catch (err) {
    console.error(err);
    return res.status(500).json({ error: "Erreur serveur lors de l'envoi du guide 2FA." });
  }
}


async function getOverview(req, res) {
  try {
    const [
      byRole, statusCounts, recentLockAlerts, recentActivity, lastEmailLog, pendingFirstLogin,
      mfaAdoption, activeTrend, actionHistory7d, emailHealth24h, emailHealthDaily,
      tempPasswordAges, dormantAccounts,
    ] = await Promise.all([
      pool.query(`SELECT role, COUNT(*)::int AS count FROM users GROUP BY role`),
      pool.query(`
        SELECT
          COUNT(*) FILTER (WHERE last_login_at IS NULL)::int AS never_logged_in,
          COUNT(*) FILTER (WHERE is_active AND last_login_at IS NULL)::int AS never_logged_in_active,
          COUNT(*) FILTER (WHERE locked_until IS NOT NULL AND locked_until > now())::int AS locked_now,
          COUNT(*) FILTER (WHERE NOT is_active)::int AS inactive_accounts,
          COUNT(*)::int AS total_users
        FROM users
      `),
      pool.query(`
        SELECT COUNT(*)::int AS count
        FROM access_logs
        WHERE action LIKE 'LOGIN_ATTEMPT_LOCKED%' AND created_at > now() - interval '1 hour'
      `),
      pool.query(`
        SELECT al.id, al.action, al.success, al.ip_address, al.created_at, u.email AS user_email
        FROM access_logs al
        LEFT JOIN users u ON u.id = al.user_id
        WHERE al.action ~ '^(CREATE_USER|RESEND_TEMP_PASSWORD|RESET_2FA|UNLOCK_ACCOUNT|DEACTIVATE_ACCOUNT|REACTIVATE_ACCOUNT)'
        ORDER BY al.created_at DESC
        LIMIT 10
      `),
      pool.query(`
        SELECT action, success, created_at
        FROM access_logs
        WHERE action ~ '^(CREATE_USER|RESEND_TEMP_PASSWORD)'
        ORDER BY created_at DESC
        LIMIT 1
      `),
      pool.query(`
        SELECT id, email, role, created_at, temp_password_created_at
        FROM users
        WHERE must_change_password = true
          AND COALESCE(temp_password_created_at, created_at) < now() - interval '24 hours'
        ORDER BY COALESCE(temp_password_created_at, created_at) ASC
      `),
      pool.query(`
        SELECT COUNT(*) FILTER (WHERE is_2fa_enabled)::int AS enabled, COUNT(*)::int AS total
        FROM users
      `),
      pool.query(`
        SELECT to_char(d, 'YYYY-MM-DD') AS day,
               COALESCE(COUNT(DISTINCT al.user_id) FILTER (WHERE al.action = 'LOGIN_PASSWORD_OK' AND al.success = true), 0)::int AS count
        FROM generate_series(
          (now() AT TIME ZONE 'Africa/Tunis')::date - interval '29 days',
          (now() AT TIME ZONE 'Africa/Tunis')::date,
          interval '1 day'
        ) d
        LEFT JOIN access_logs al ON (al.created_at AT TIME ZONE 'Africa/Tunis')::date = d::date
        GROUP BY d
        ORDER BY d
      `),
      
      pool.query(`
        SELECT to_char(d, 'YYYY-MM-DD') AS day,
               regexp_replace(al.action, ':.*$', '') AS action_type,
               COUNT(*)::int AS count
        FROM generate_series(
          (now() AT TIME ZONE 'Africa/Tunis')::date - interval '6 days',
          (now() AT TIME ZONE 'Africa/Tunis')::date,
          interval '1 day'
        ) d
        LEFT JOIN access_logs al
          ON (al.created_at AT TIME ZONE 'Africa/Tunis')::date = d::date
          AND al.action ~ '^(CREATE_USER|RESEND_TEMP_PASSWORD|RESET_2FA|UNLOCK_ACCOUNT|DEACTIVATE_ACCOUNT|REACTIVATE_ACCOUNT)'
        GROUP BY d, action_type
        ORDER BY d
      `),
      pool.query(`
        SELECT
          COUNT(*) FILTER (WHERE success)::int AS ok,
          COUNT(*)::int AS total
        FROM access_logs
        WHERE action ~ '^(CREATE_USER|RESEND_TEMP_PASSWORD)'
          AND created_at > now() - interval '24 hours'
      `),
      pool.query(`
        SELECT to_char(d, 'YYYY-MM-DD') AS day,
               COUNT(*) FILTER (WHERE al.success)::int AS ok,
               COUNT(al.id)::int AS total
        FROM generate_series(
          (now() AT TIME ZONE 'Africa/Tunis')::date - interval '6 days',
          (now() AT TIME ZONE 'Africa/Tunis')::date,
          interval '1 day'
        ) d
        LEFT JOIN access_logs al
          ON (al.created_at AT TIME ZONE 'Africa/Tunis')::date = d::date
          AND al.action ~ '^(CREATE_USER|RESEND_TEMP_PASSWORD)'
        GROUP BY d
        ORDER BY d
      `),
      pool.query(`
        SELECT id, temp_password_created_at, created_at
        FROM users
        WHERE must_change_password = true
      `),
      pool.query(`
        SELECT COUNT(*)::int AS count
        FROM users
        WHERE is_active = true
          AND last_login_at IS NOT NULL
          AND last_login_at < now() - interval '60 days'
      `),
    ]);

    const roleCounts = { admin: 0, clinicien: 0, chercheur: 0, statisticien: 0 };
    byRole.rows.forEach((r) => { roleCounts[r.role] = r.count; });

    const emailLog = lastEmailLog.rows[0] || null;

    const nowTs = Date.now();
    const tempBuckets = { h0_12: 0, h12_24: 0, h24_48: 0, expired: 0 };
    tempPasswordAges.rows.forEach((u) => {
      const createdAt = new Date(u.temp_password_created_at || u.created_at).getTime();
      const hoursElapsed = (nowTs - createdAt) / (1000 * 60 * 60);
      if (hoursElapsed > 48) tempBuckets.expired += 1;
      else if (hoursElapsed > 24) tempBuckets.h24_48 += 1;
      else if (hoursElapsed > 12) tempBuckets.h12_24 += 1;
      else tempBuckets.h0_12 += 1;
    });

    const days7 = [...new Set(actionHistory7d.rows.map((r) => r.day))];
    const actionTypes = ['CREATE_USER', 'RESEND_TEMP_PASSWORD', 'RESET_2FA', 'UNLOCK_ACCOUNT', 'DEACTIVATE_ACCOUNT', 'REACTIVATE_ACCOUNT'];
    const actionSeries = actionTypes.map((type) => ({
      type,
      values: days7.map((day) => {
        const row = actionHistory7d.rows.find(
          (r) => r.day === day && r.action_type === type
        );
        return row ? row.count : 0;
      }),
    })).filter((s) => s.values.some((v) => v > 0)); 

    return res.json({
      totalUsers: statusCounts.rows[0].total_users,
      roleCounts,
      neverLoggedIn: statusCounts.rows[0].never_logged_in,
      neverLoggedInActive: statusCounts.rows[0].never_logged_in_active,
      lockedNow: statusCounts.rows[0].locked_now,
      inactiveAccounts: statusCounts.rows[0].inactive_accounts,
      alerts: {
        lockoutsLastHour: recentLockAlerts.rows[0].count,
      },
      recentActivity: recentActivity.rows,
      emailStatus: emailLog
        ? { success: emailLog.success, action: emailLog.action, at: emailLog.created_at }
        : null,
      pendingFirstLoginOver24h: pendingFirstLogin.rows,

      mfaAdoption: { enabled: mfaAdoption.rows[0].enabled, total: mfaAdoption.rows[0].total },
      activeAccountsTrend: activeTrend.rows.map((r) => ({
        day: r.day,
        count: r.count,
      })),
      actionHistory7d: { days: days7, series: actionSeries },
      emailHealth: {
        rate24h: emailHealth24h.rows[0].total > 0
          ? Math.round((emailHealth24h.rows[0].ok / emailHealth24h.rows[0].total) * 100)
          : null,
        total24h: emailHealth24h.rows[0].total,
        dailyTrend: emailHealthDaily.rows.map((r) => ({
          day: r.day,
          rate: r.total > 0 ? Math.round((r.ok / r.total) * 100) : null,
        })),
      },
      tempPasswordAgeBuckets: tempBuckets,
      dormantAccounts: dormantAccounts.rows[0].count,
    });
  } catch (err) {
    console.error(err);
    return res.status(500).json({ error: 'Erreur serveur lors du chargement de la vue d\'ensemble.' });
  }
}

const KNOWN_ACTIONS = [
  'LOGIN_ATTEMPT', 'LOGIN_ATTEMPT_LOCKED', 'LOGIN_ATTEMPT_WHILE_LOCKED',
  'LOGIN_ATTEMPT_DISABLED_ACCOUNT', 'LOGIN_PASSWORD_OK', 'LOGIN_PASSWORD_OK_AWAITING_TOTP',
  'LOGOUT', 'TOTP_ATTEMPT', 'TOTP_OK', 'CREATE_USER', 'CREATE_USER_EMAIL_FAILED',
  'RESET_2FA', 'RESEND_TEMP_PASSWORD', 'RESEND_TEMP_PASSWORD_EMAIL_FAILED',
  'UNLOCK_ACCOUNT', 'DEACTIVATE_ACCOUNT', 'REACTIVATE_ACCOUNT', 'VIEW_USER_TIMELINE',
  'NOTIFY_DORMANT_USERS', 'SEND_CUSTOM_EMAIL', 'NOTIFY_MFA_SETUP',
];


async function getLogs(req, res) {
  const { action, userId, ip, dateFrom, dateTo, emailFailed } = req.query;
  const page = Math.max(parseInt(req.query.page, 10) || 1, 1);
  const pageSize = Math.min(Math.max(parseInt(req.query.pageSize, 10) || 50, 1), 200);
  const offset = (page - 1) * pageSize;

  const conditions = [];
  const params = [];


  if (emailFailed === 'true') {
    conditions.push(`(al.action LIKE 'CREATE_USER%' OR al.action LIKE 'RESEND_TEMP_PASSWORD%') AND al.success = false`);
  } else if (action) {
    params.push(`${action}%`);
    conditions.push(`al.action LIKE $${params.length}`);
  }
  if (userId) {
    params.push(userId);
    conditions.push(`al.user_id = $${params.length}`);
  }
  if (ip) {
    params.push(`%${ip}%`);
    conditions.push(`al.ip_address LIKE $${params.length}`);
  }
  if (dateFrom) {
    params.push(dateFrom);
    conditions.push(`al.created_at >= $${params.length}`);
  }
  if (dateTo) {
    params.push(dateTo);
    conditions.push(`al.created_at <= $${params.length}`);
  }

  const whereClause = conditions.length ? `WHERE ${conditions.join(' AND ')}` : '';

  try {
    const countResult = await pool.query(
      `SELECT COUNT(*)::int AS count FROM access_logs al ${whereClause}`,
      params
    );

    params.push(pageSize);
    params.push(offset);
    
    const result = await pool.query(
      `SELECT al.id, al.user_id, u.email AS user_email, al.action, al.success,
              al.ip_address, al.created_at, al.user_agent, al.session_id,
              target_u.email AS target_email
       FROM access_logs al
       LEFT JOIN users u ON u.id = al.user_id
       LEFT JOIN users target_u
         ON al.action ~ ':[0-9a-fA-F-]{36}$'
         AND target_u.id::text = split_part(al.action, ':', 2)
       ${whereClause}
       ORDER BY al.created_at DESC
       LIMIT $${params.length - 1} OFFSET $${params.length}`,
      params
    );

    return res.json({
      rows: result.rows,
      total: countResult.rows[0].count,
      page,
      pageSize,
      knownActions: KNOWN_ACTIONS,
    });
  } catch (err) {
    console.error(err);
    return res.status(500).json({ error: 'Erreur serveur lors du chargement des logs.' });
  }
}


async function getAnomalies(req, res) {
  try {
    const [bruteForce, credentialStuffing, totpBypass, freq2fa, massExport] = await Promise.all([
      pool.query(`
        SELECT u.email, COUNT(*)::int AS attempts, MAX(al.created_at) AS last_attempt
        FROM access_logs al
        JOIN users u ON u.id = al.user_id
        WHERE al.action IN ('LOGIN_ATTEMPT', 'LOGIN_ATTEMPT_LOCKED')
          AND al.created_at > now() - interval '24 hours'
        GROUP BY u.email
        HAVING COUNT(*) FILTER (WHERE al.action = 'LOGIN_ATTEMPT_LOCKED') > 0
        ORDER BY last_attempt DESC
      `),
      pool.query(`
        SELECT al.ip_address, COUNT(DISTINCT al.user_id)::int AS distinct_users, MAX(al.created_at) AS last_attempt
        FROM access_logs al
        WHERE al.action LIKE 'LOGIN_ATTEMPT%'
          AND al.ip_address IS NOT NULL
          AND al.created_at > now() - interval '24 hours'
        GROUP BY al.ip_address
        HAVING COUNT(DISTINCT al.user_id) >= 4
        ORDER BY distinct_users DESC
      `),
      pool.query(`
        SELECT u.email, COUNT(*)::int AS failed_attempts, MAX(al.created_at) AS last_attempt
        FROM access_logs al
        JOIN users u ON u.id = al.user_id
        WHERE al.action = 'TOTP_ATTEMPT' AND al.success = false
          AND al.created_at > now() - interval '24 hours'
        GROUP BY u.email
        HAVING COUNT(*) >= 3
        ORDER BY failed_attempts DESC
      `),
      pool.query(`
        SELECT u.email, COUNT(*)::int AS resets, MAX(al.created_at) AS last_reset
        FROM access_logs al
        JOIN users u ON u.id = al.user_id
        WHERE al.action LIKE 'RESET_2FA%'
          AND al.created_at > now() - interval '7 days'
        GROUP BY u.email
        HAVING COUNT(*) >= 2
        ORDER BY resets DESC
      `),
      pool.query(`
        SELECT u.email, COUNT(*)::int AS exports, MAX(al.created_at) AS last_export
        FROM access_logs al
        JOIN users u ON u.id = al.user_id
        WHERE al.action = 'EXPORT_LOGS_CSV'
          AND al.created_at > now() - interval '10 minutes'
        GROUP BY u.email
        HAVING COUNT(*) >= 5
        ORDER BY exports DESC
      `),
    ]);

    
    const bruteForceSuccess = await pool.query(`
      SELECT u.email, s.created_at AS success_at, s.ip_address, f.failed_count
      FROM access_logs s
      JOIN users u ON u.id = s.user_id
      JOIN LATERAL (
        SELECT COUNT(*)::int AS failed_count
        FROM access_logs f
        WHERE f.user_id = s.user_id
          AND f.action IN ('LOGIN_ATTEMPT', 'LOGIN_ATTEMPT_LOCKED')
          AND f.created_at BETWEEN s.created_at - interval '30 minutes' AND s.created_at
      ) f ON true
      WHERE s.action = 'LOGIN_PASSWORD_OK'
        AND s.created_at > now() - interval '7 days'
        AND f.failed_count >= 3
      ORDER BY s.created_at DESC
      LIMIT 20
    `);

    
    const reactivationImmediateUse = await pool.query(`
      SELECT
        target.email AS target_email,
        deact_admin.email AS deactivated_by,
        react_admin.email AS reactivated_by,
        react.created_at AS reactivated_at,
        login.created_at AS login_at
      FROM access_logs deact
      JOIN access_logs react
        ON react.action = 'REACTIVATE_ACCOUNT:' || split_part(deact.action, ':', 2)
       AND react.created_at > deact.created_at
       AND react.user_id IS DISTINCT FROM deact.user_id
      JOIN access_logs login
        ON login.user_id::text = split_part(deact.action, ':', 2)
       AND login.action = 'LOGIN_PASSWORD_OK'
       AND login.created_at BETWEEN react.created_at AND react.created_at + interval '1 hour'
      JOIN users target ON target.id::text = split_part(deact.action, ':', 2)
      JOIN users deact_admin ON deact_admin.id = deact.user_id
      JOIN users react_admin ON react_admin.id = react.user_id
      WHERE deact.action LIKE 'DEACTIVATE_ACCOUNT:%'
        AND deact.created_at > now() - interval '30 days'
      ORDER BY react.created_at DESC
      LIMIT 20
    `);

    
    const unusualHours = await pool.query(`
      SELECT u.email, al.created_at, al.ip_address
      FROM access_logs al
      JOIN users u ON u.id = al.user_id
      WHERE al.action = 'LOGIN_PASSWORD_OK'
        AND al.created_at > now() - interval '7 days'
        AND EXTRACT(HOUR FROM al.created_at) BETWEEN 0 AND 5
      ORDER BY al.created_at DESC
      LIMIT 20
    `);

    
    const WEIGHTS = {
      bruteForce: 25,        
      totpBypass: 15,        
      freq2fa: 12,          
      unusualHour: 5,        
      credentialStuffing: 20, 
      bruteForceSuccess: 35, 
      reactivationImmediateUse: 30, 
      massExport: 22,        
    };

    const scoresByEmail = new Map();
    const bump = (email, points, reason) => {
      const entry = scoresByEmail.get(email) || { subject: email, score: 0, reasons: [] };
      entry.score += points;
      entry.reasons.push(reason);
      scoresByEmail.set(email, entry);
    };
    bruteForce.rows.forEach((r) => bump(r.email, WEIGHTS.bruteForce, 'Verrouillage brute-force'));
    totpBypass.rows.forEach((r) => bump(r.email, WEIGHTS.totpBypass, 'Contournement 2FA suspecté'));
    freq2fa.rows.forEach((r) => bump(r.email, WEIGHTS.freq2fa, 'Resets 2FA fréquents'));
    unusualHours.rows.forEach((r) => bump(r.email, WEIGHTS.unusualHour, 'Connexion à horaire inhabituel'));
    bruteForceSuccess.rows.forEach((r) => bump(r.email, WEIGHTS.bruteForceSuccess, 'Brute-force abouti (succès après échecs)'));
    reactivationImmediateUse.rows.forEach((r) => bump(r.target_email, WEIGHTS.reactivationImmediateUse, 'Réactivation puis connexion immédiate'));
    massExport.rows.forEach((r) => bump(r.email, WEIGHTS.massExport, 'Exports CSV répétés (exfiltration ?)'));

    const scoresByIp = new Map();
    const bumpIp = (ip, points, reason) => {
      const entry = scoresByIp.get(ip) || { subject: ip, score: 0, reasons: [] };
      entry.score += points;
      entry.reasons.push(reason);
      scoresByIp.set(ip, entry);
    };
    credentialStuffing.rows.forEach((r) => bumpIp(r.ip_address, WEIGHTS.credentialStuffing, 'Énumération / credential stuffing'));

    const riskScores = [
      ...[...scoresByEmail.values()].map((e) => ({ ...e, type: 'user' })),
      ...[...scoresByIp.values()].map((e) => ({ ...e, type: 'ip' })),
    ]
      .map((e) => ({ ...e, score: Math.min(e.score, 100), reasons: [...new Set(e.reasons)] }))
      .sort((a, b) => b.score - a.score);

    return res.json({
      bruteForceLockouts: bruteForce.rows,
      credentialStuffingIps: credentialStuffing.rows,
      totpBypassAttempts: totpBypass.rows,
      frequent2faResets: freq2fa.rows,
      unusualHourLogins: unusualHours.rows,
      bruteForceSuccesses: bruteForceSuccess.rows,
      reactivationImmediateUse: reactivationImmediateUse.rows,
      massExports: massExport.rows,
      riskScores,
    });
  } catch (err) {
    console.error(err);
    return res.status(500).json({ error: 'Erreur serveur lors du calcul des anomalies.' });
  }
}


async function getUserTimeline(req, res) {
  const { id } = req.params;
  const adminId = req.user.sub;
  try {
    const userResult = await pool.query('SELECT id, email, role FROM users WHERE id = $1', [id]);
    if (userResult.rows.length === 0) {
      return res.status(404).json({ error: 'Utilisateur introuvable.' });
    }

    const logsResult = await pool.query(
      `SELECT al.id, al.action, al.success, al.ip_address, al.created_at, al.user_agent, al.session_id,
              target_u.email AS target_email
       FROM access_logs al
       LEFT JOIN users target_u
         ON al.action ~ ':[0-9a-fA-F-]{36}$'
         AND target_u.id::text = split_part(al.action, ':', 2)
       WHERE al.user_id = $1
       ORDER BY al.created_at DESC
       LIMIT 500`,
      [id]
    );

    
    logAccess({ userId: adminId, action: `VIEW_USER_TIMELINE:${id}`, success: true, req })
      .catch((e) => console.error('Log VIEW_USER_TIMELINE échoué :', e.message));

    return res.json({ user: userResult.rows[0], logs: logsResult.rows });
  } catch (err) {
    console.error(err);
    return res.status(500).json({ error: 'Erreur serveur lors du chargement de la timeline.' });
  }
}


async function exportLogsCsv(req, res) {
  const { action, userId, ip, dateFrom, dateTo } = req.query;
  const conditions = [];
  const params = [];

  if (action) {
    params.push(`${action}%`);
    conditions.push(`al.action LIKE $${params.length}`);
  }
  if (userId) {
    params.push(userId);
    conditions.push(`al.user_id = $${params.length}`);
  }
  if (ip) {
    params.push(`%${ip}%`);
    conditions.push(`al.ip_address LIKE $${params.length}`);
  }
  if (dateFrom) {
    params.push(dateFrom);
    conditions.push(`al.created_at >= $${params.length}`);
  }
  if (dateTo) {
    params.push(dateTo);
    conditions.push(`al.created_at <= $${params.length}`);
  }

  const whereClause = conditions.length ? `WHERE ${conditions.join(' AND ')}` : '';

  try {
    const result = await pool.query(
      `SELECT al.created_at, u.email AS user_email, al.action, al.success, al.ip_address
       FROM access_logs al
       LEFT JOIN users u ON u.id = al.user_id
       ${whereClause}
       ORDER BY al.created_at DESC
       LIMIT 10000`,
      params
    );

    const escapeCsv = (val) => {
      if (val === null || val === undefined) return '';
      const str = String(val);
      return /[",\n;]/.test(str) ? `"${str.replace(/"/g, '""')}"` : str;
    };

    const header = ['Date', 'Utilisateur', 'Action', 'Succès', 'Adresse IP'].join(';');
    const lines = result.rows.map((r) => [
      new Date(r.created_at).toISOString(),
      r.user_email || '',
      r.action,
      r.success ? 'OUI' : 'NON',
      r.ip_address || '',
    ].map(escapeCsv).join(';'));

    const adminId = req.user.sub;
    await logAccess({ userId: adminId, action: 'EXPORT_LOGS_CSV', success: true, req });

    const csv = '\uFEFF' + [header, ...lines].join('\n'); 
    res.setHeader('Content-Type', 'text/csv; charset=utf-8');
    res.setHeader('Content-Disposition', `attachment; filename="logs-export-${Date.now()}.csv"`);
    return res.send(csv);
  } catch (err) {
    console.error(err);
    return res.status(500).json({ error: 'Erreur serveur lors de l\'export.' });
  }
}

module.exports = {
  createUser,
  listUsers,
  resetTotp,
  listUsersDetailed, 
  resendTempPassword, 
  unlockUser, 
  toggleActive, 
  getOverview, 
  getLogs, 
  getAnomalies,
  getUserTimeline, 
  exportLogsCsv, 
  notifyDormantUsers, 
  retryFailedEmails, 
  sendCommunication, 
  notifyMfaSetup, 
};