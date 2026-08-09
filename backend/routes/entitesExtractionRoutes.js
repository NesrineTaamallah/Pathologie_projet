const express = require('express');
const router = express.Router();
const {
  extraireEntites, listeNonExtraits, extraireEntitesDocument, enregistrerEntites,
} = require('../controllers/entitesExtractionController');
const { requireAuth, requireRole } = require('../middleware/auth');

router.use(requireAuth, requireRole('clinicien'));

router.post('/api/extraction/entites', extraireEntites);
router.get('/api/extraction/entites/non-extraits', listeNonExtraits);
router.post('/api/extraction/entites-document', extraireEntitesDocument);
router.post('/api/entites', enregistrerEntites);

module.exports = router;