const express = require('express');
const router = express.Router();
const {
  extraireEntites, listeNonExtraits, extraireEntitesDocument, enregistrerEntites,
  documentsNonExtraitsEntites,
} = require('../controllers/entitesExtractionController');
const { requireAuth, requireRole } = require('../middleware/auth');

router.use(requireAuth, requireRole('clinicien'));

router.post('/api/extraction/entites', extraireEntites);
router.get('/api/extraction/entites/non-extraits', listeNonExtraits);
router.get('/api/extraction/entites/:pseudonyme/documents-non-extraits', documentsNonExtraitsEntites);
router.post('/api/extraction/entites-document', extraireEntitesDocument);
router.post('/api/entites', enregistrerEntites);

module.exports = router;