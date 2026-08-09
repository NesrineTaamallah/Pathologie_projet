const express = require('express');
const router = express.Router();
const {
  rechercherCim11,
  detailCim11,
  associerCim11,
  listeCim11Patient,
} = require('../controllers/cim11Controller');
const { requireAuth, requireRole } = require('../middleware/auth');

router.use(requireAuth, requireRole('clinicien'));

router.get('/api/cim11/recherche', rechercherCim11);
router.get('/api/cim11/code/:code', detailCim11);
router.post('/api/cim11/associer', associerCim11);
router.get('/api/cim11/patient/:pseudonyme', listeCim11Patient);

module.exports = router;
