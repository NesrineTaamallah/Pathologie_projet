const nodemailer = require('nodemailer');
require('dotenv').config();

const transporter = nodemailer.createTransport({
  host: process.env.SMTP_HOST,
  port: Number(process.env.SMTP_PORT),
  secure: process.env.SMTP_SECURE === 'true',
  auth: {
    user: process.env.SMTP_USER,
    pass: process.env.SMTP_PASSWORD,
  },
  family: 4, 
  connectionTimeout: 10000,
  greetingTimeout: 10000,
  socketTimeout: 15000,
  logger: true, 
  debug: true,
});

transporter.verify((err) => {
  if (err) {
    console.error('SMTP indisponible au démarrage :', err.message);
  } else {
    console.log('SMTP prêt.');
  }
});

async function sendTempPasswordEmail(toEmail, tempPassword, role) {
  const appUrl = process.env.APP_URL || 'http://localhost:3000';

  await transporter.sendMail({
    from: `"Registre CDR NeuroExo-Predict" <${process.env.SMTP_USER}>`,
    to: toEmail,
    subject: 'Votre accès au registre clinique — mot de passe temporaire',
    html: `
      <p>Bonjour,</p>
      <p>Un compte vous a été créé sur le registre clinique NeuroExo-Predict
      avec le rôle : <strong>${role}</strong>.</p>
      <p>Votre mot de passe temporaire est : <strong>${tempPassword}</strong></p>
      <p>Ce mot de passe est <strong>valable 48h</strong> et devra être changé
      dès votre première connexion.</p>
      <p><a href="${appUrl}/login">Se connecter</a></p>
      <p>Si vous n'êtes pas à l'origine de cette demande, contactez
      l'administrateur du système.</p>
    `,
  });
}

async function sendDormantReminderEmail(toEmail, role) {
  const appUrl = process.env.APP_URL || 'http://localhost:3000';

  await transporter.sendMail({
    from: `"Registre CDR NeuroExo-Predict" <${process.env.SMTP_USER}>`,
    to: toEmail,
    subject: 'Votre compte est inactif depuis plus de 60 jours',
    html: `
      <p>Bonjour,</p>
      <p>Nous avons remarqué que vous ne vous êtes pas connecté(e) au registre clinique
      NeuroExo-Predict (rôle : <strong>${role}</strong>) depuis plus de 60 jours.</p>
      <p>Si vous utilisez toujours cette plateforme, merci de vous reconnecter prochainement
      afin de garder votre accès actif. À défaut, votre compte pourra être désactivé.</p>
      <p><a href="${appUrl}/login">Se connecter</a></p>
      <p>Si vous n'utilisez plus la plateforme, aucune action n'est requise de votre part.</p>
    `,
  });
}

async function sendCustomEmail(toEmail, subject, message) {
  
  const escapeHtml = (str) => String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  const htmlBody = escapeHtml(message).replace(/\n/g, '<br>');

  await transporter.sendMail({
    from: `"Registre CDR NeuroExo-Predict" <${process.env.SMTP_USER}>`,
    to: toEmail,
    subject,
    html: `
      <p>${htmlBody}</p>
      <p style="margin-top:24px;color:#64748b;font-size:12px;">
        Ce message vous a été envoyé depuis le registre clinique NeuroExo-Predict par un administrateur.
      </p>
    `,
  });
}

async function sendMfaGuideEmail(toEmail, role) {
  await transporter.sendMail({
    from: `"Registre CDR NeuroExo-Predict" <${process.env.SMTP_USER}>`,
    to: toEmail,
    subject: 'Guide : activer la double authentification (2FA) sur votre compte',
    html: `
      <p>Bonjour,</p>
      <p>Pour renforcer la sécurité de votre compte (rôle : <strong>${role}</strong>) sur le
      registre clinique NeuroExo-Predict, nous vous invitons à activer la double
      authentification (2FA) si ce n'est pas déjà fait.</p>

      <p><strong>1. Installez une application d'authentification</strong> sur votre téléphone,
      par exemple :</p>
      <ul>
        <li>Google Authenticator</li>
        <li>Microsoft Authenticator</li>
        <li>Authy</li>
      </ul>

      <p><strong>2. Connectez l'application à votre compte :</strong></p>
      <ol>
        <li>Connectez-vous à la plateforme avec votre email et votre mot de passe.</li>
        <li>Un QR code s'affiche à l'écran lors de la configuration de la 2FA.</li>
        <li>Ouvrez l'application d'authentification sur votre téléphone et choisissez
          « Ajouter un compte » / « Scanner un QR code ».</li>
        <li>Scannez le QR code affiché sur votre écran.</li>
        <li>L'application génère un code à 6 chiffres, renouvelé toutes les 30 secondes ;
          saisissez ce code sur la plateforme pour valider l'activation.</li>
      </ol>

      <p><strong>En cas de problème de connexion</strong> (téléphone perdu, application
      désinstallée, code refusé...), contactez l'administrateur de la plateforme :
      votre 2FA pourra être réinitialisée pour vous permettre de la reconfigurer.</p>

      <p>Merci de votre vigilance sur la sécurité des données du registre.</p>
    `,
  });
}

async function sendAccountLockedAlertEmail(adminEmail, lockedUserEmail, lockedUserRole) {
  await transporter.sendMail({
    from: `"Registre CDR NeuroExo-Predict — Alerte sécurité" <${process.env.SMTP_USER}>`,
    to: adminEmail,
    subject: `⚠️ Compte verrouillé : ${lockedUserEmail}`,
    html: `
      <p>Bonjour,</p>
      <p>Le compte <strong>${lockedUserEmail}</strong> (rôle : <strong>${lockedUserRole}</strong>) vient
      d'être <strong>verrouillé automatiquement</strong> après plusieurs tentatives de connexion échouées.</p>
      <p>Le verrouillage est temporaire (15 minutes), mais si vous ne reconnaissez pas cette activité,
      il peut s'agir d'une tentative d'accès non autorisée à surveiller.</p>
      <p>Vous pouvez consulter et, si besoin, déverrouiller ce compte manuellement depuis
      l'onglet Utilisateurs du registre.</p>
    `,
  });
}

module.exports = { sendTempPasswordEmail, sendDormantReminderEmail, sendCustomEmail, sendMfaGuideEmail, sendAccountLockedAlertEmail };