const express = require('express');
const router = express.Router();
const { extraireEntites, listeNonExtraits } = require('../controllers/entitesExtractionController');
const { requireAuth, requireRole } = require('../middleware/auth');

router.use(requireAuth, requireRole('clinicien'));

router.post('/api/extraction/entites', extraireEntites);
router.get('/api/extraction/entites/non-extraits', listeNonExtraits);

module.exports = router;