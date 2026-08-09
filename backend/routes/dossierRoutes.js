const express = require('express');
const multer = require('multer');
const path = require('path');
const router = express.Router();
const { requireAuth, requireRole } = require('../middleware/auth');
const { listDossiers, getDossierDetail, updateChampDossier } = require('../controllers/dossierController');
const { uploaderFichierEntite, telechargerFichierEntite } = require('../controllers/entiteFichierController');

const UPLOAD_DIR = process.env.UPLOAD_DIR || path.join(__dirname, '..', 'uploads', 'entites');

const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, UPLOAD_DIR),
  filename: (req, file, cb) => {
    const suffix = `${Date.now()}-${Math.round(Math.random() * 1e9)}`;
    cb(null, `${suffix}${path.extname(file.originalname)}`);
  },
});

const ALLOWED_MIME_ENTITE = [
  'application/pdf', 'image/png', 'image/jpeg', 'image/tiff', 'image/webp', 'image/heic', 'image/heif',
  'video/mp4', 'video/quicktime', 'video/webm', 'video/x-msvideo', 'video/x-matroska',
];

const uploadEntite = multer({
  storage,
  limits: { fileSize: 200 * 1024 * 1024 }, // 200 Mo (vidéos)
  fileFilter: (req, file, cb) => {
    if (!ALLOWED_MIME_ENTITE.includes(file.mimetype)) {
      return cb(new Error('Format de fichier non autorisé (PDF, image ou vidéo attendu).'));
    }
    cb(null, true);
  },
});

router.use(requireAuth, requireRole('clinicien'));

router.get('/', listDossiers);
router.get('/:pseudonyme', getDossierDetail);
router.patch('/:pseudonyme/champ', updateChampDossier);


router.post('/:pseudonyme/entite-fichier', uploadEntite.single('fichier'), uploaderFichierEntite);
router.get('/entite-fichier/:table/:id/telecharger', telechargerFichierEntite);

module.exports = router;
