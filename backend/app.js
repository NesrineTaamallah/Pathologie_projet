const express = require('express');
require('dotenv').config({ override: true });
const helmet = require('helmet');
const cors = require('cors');
const cookieParser = require('cookie-parser');

const authRoutes = require('./routes/authRoutes');
const adminRoutes = require('./routes/adminRoutes');
const totpRoutes = require('./routes/totpRoutes');

const analysisRoutes = require('./routes/analysisRoutes');
const coordonneePatientRoutes = require('./routes/coordonneePatientRoutes');
const clinicienOverviewRoutes = require('./routes/clinicienOverviewRoutes');
const dossierRoutes = require('./routes/dossierRoutes');
const dossierUploadRoutes = require('./routes/dossierUploadRoutes'); 
const extractionRoutes = require('./routes/extractionRoutes'); 
const entitesExtractionRoutes = require('./routes/entitesExtractionRoutes');
const cim11Routes = require('./routes/cim11Routes');
const app = express();


app.use(helmet()); 
app.use(cors({
  origin: process.env.CORS_ORIGIN || 'http://localhost:5173',
  credentials: true, 
}));
app.use(express.json());
app.use(cookieParser());

app.use('/', authRoutes);
app.use('/admin', adminRoutes);
app.use('/2fa', totpRoutes);
app.use('/api/analyses', analysisRoutes);
app.use('/api/coordonnees', coordonneePatientRoutes);
app.use('/api/clinicien', clinicienOverviewRoutes);

app.use('/api/dossiers', dossierUploadRoutes);
app.use('/api/dossiers', dossierRoutes);
app.use('/api/extraction', extractionRoutes);
app.use(entitesExtractionRoutes);
app.use(cim11Routes);

const PORT = process.env.PORT || 4000;
app.listen(PORT, () => console.log(`Serveur démarré sur le port ${PORT}`));