const express = require('express');
const multer = require('multer');
const path = require('path');
const router = express.Router();
const { requireAuth, requireRole } = require('../middleware/auth');
const {
  verifierDossier,
  creerDossier,
  corrigerTexteTranscrit,
  getDocumentsByPseudonyme,
  getDocumentsNonExtraits,
  telechargerFichier,
} = require('../controllers/dossierUploadController');

const UPLOAD_DIR = process.env.UPLOAD_DIR || path.join(__dirname, '..', 'uploads');

const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, UPLOAD_DIR),
  filename: (req, file, cb) => {
    const suffix = `${Date.now()}-${Math.round(Math.random() * 1e9)}`;
    cb(null, `${suffix}${path.extname(file.originalname)}`);
  },
});

const ALLOWED_MIME = {
  audio: ['audio/wav', 'audio/x-wav', 'audio/mpeg', 'audio/mp4', 'audio/x-m4a', 'audio/flac'],
  scan: ['application/pdf', 'image/png', 'image/jpeg', 'image/tiff', 'image/heic', 'image/heif', 'image/webp'],
  // Vidéo brute stockée telle quelle (ex. enregistrement EEG vidéo), sans transcription.
  video: ['video/mp4', 'video/quicktime', 'video/webm', 'video/x-msvideo', 'video/x-matroska'],
};

const upload = multer({
  storage,
  limits: { fileSize: 200 * 1024 * 1024 }, // 200 Mo (dictées audio longues)
  fileFilter: (req, file, cb) => {
    const typeEntree = req.body.type_entree;
    const allowed = ALLOWED_MIME[typeEntree];
    if (!allowed) return cb(new Error("Type d'entrée manquant ou invalide."));
    if (!allowed.includes(file.mimetype)) {
      return cb(new Error(`Format de fichier non autorisé pour l'entrée "${typeEntree}".`));
    }
    cb(null, true);
  },
});


router.get('/verifier', requireAuth, requireRole('clinicien'), verifierDossier);

router.post('/creer', requireAuth, requireRole('clinicien'), upload.single('fichier'), creerDossier);


router.patch('/documents/:id/texte', requireAuth, requireRole('clinicien'), corrigerTexteTranscrit);


router.get('/:pseudonyme/documents', requireAuth, requireRole('clinicien'), getDocumentsByPseudonyme);

router.get('/:pseudonyme/documents-non-extraits', requireAuth, requireRole('clinicien'), getDocumentsNonExtraits);
router.get('/documents/:id/fichier', requireAuth, requireRole('clinicien'), telechargerFichier);

module.exports = router;

