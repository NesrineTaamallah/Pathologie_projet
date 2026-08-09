import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

import scipy.stats as stats
import statsmodels.api as sm
import statsmodels.formula.api as smf
from statsmodels.stats.anova import anova_lm

from lifelines import KaplanMeierFitter, CoxPHFitter
from lifelines.statistics import logrank_test, multivariate_logrank_test
from lifelines.plotting import add_at_risk_counts

from sklearn.linear_model import LogisticRegression
from sklearn.neighbors import NearestNeighbors

import matplotlib.pyplot as plt

pd.set_option("display.width", 140)
pd.set_option("display.max_columns", 30)

import sys
import datetime

FICHIER_RESULTATS_TXT = "resultats_complets_analyse.txt"

class Logger:
    def __init__(self, fichier):
        self.terminal = sys.stdout
        self.fichier = open(fichier, "w", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.fichier.write(message)

    def flush(self):
        self.terminal.flush()
        self.fichier.flush()

sys.stdout = Logger(FICHIER_RESULTATS_TXT)

print("=" * 90)
print("RÉSULTATS COMPLETS — Analyse lignes thérapeutiques SEP")
print(f"Généré le : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 90)

DB_USER = "postgres_user"
DB_PASSWORD = "postgres_password"
DB_HOST = "localhost"
DB_PORT = 5432
DB_NAME = "registre_sep"

engine = create_engine(
    f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

GROUPES_AUTORISES = ["Faible_Moderee", "Haute_efficacite"]

def preparer_table_classement(engine):
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS reference_groupe_efficacite (
                molecule            VARCHAR PRIMARY KEY,
                groupe              VARCHAR NOT NULL,
                classe_par          VARCHAR,
                date_classement     TIMESTAMP DEFAULT now()
            );
        """))

def obtenir_molecules_non_classees(engine):
    requete = text("""
        SELECT DISTINCT tf.molecule
        FROM sep_traitement_fond tf
        WHERE tf.molecule IS NOT NULL
          AND tf.molecule NOT IN (SELECT molecule FROM reference_groupe_efficacite)
        ORDER BY tf.molecule;
    """)
    with engine.connect() as conn:
        return pd.read_sql(requete, conn)["molecule"].tolist()

def classer_molecules_avec_clinicien(engine, nom_clinicien=None):
    
    molecules_a_classer = obtenir_molecules_non_classees(engine)

    if not molecules_a_classer:
        print("Toutes les molécules présentes en base sont déjà classées.")
        return

    print("\n=== Classement des molécules par le clinicien ===")
    print(f"{len(molecules_a_classer)} molécule(s) à classer avant de lancer l'analyse.\n")
    print("Groupes disponibles :")
    for i, g in enumerate(GROUPES_AUTORISES, start=1):
        print(f"  {i}. {g}")

    if nom_clinicien is None:
        nom_clinicien = input("\nVotre nom (pour traçabilité du classement) : ").strip()

    classements = []
    for molecule in molecules_a_classer:
        while True:
            choix = input(f"\nMolécule : \"{molecule}\"  ->  groupe (1={GROUPES_AUTORISES[0]}, "
                           f"2={GROUPES_AUTORISES[1]}, s=passer) : ").strip().lower()
            if choix == "s":
                print(f"  -> \"{molecule}\" laissée non classée (sera ignorée dans l'analyse).")
                break
            if choix in ("1", "2"):
                groupe_choisi = GROUPES_AUTORISES[int(choix) - 1]
                classements.append({"molecule": molecule, "groupe": groupe_choisi,
                                     "classe_par": nom_clinicien})
                print(f"  -> \"{molecule}\" classée en \"{groupe_choisi}\".")
                break
            print("  Choix invalide, entrer 1, 2 ou s.")

    if classements:
        with engine.begin() as conn:
            for c in classements:
                conn.execute(text("""
                    INSERT INTO reference_groupe_efficacite (molecule, groupe, classe_par)
                    VALUES (:molecule, :groupe, :classe_par)
                    ON CONFLICT (molecule) DO UPDATE
                        SET groupe = EXCLUDED.groupe,
                            classe_par = EXCLUDED.classe_par,
                            date_classement = now();
                """), c)
        print(f"\n{len(classements)} molécule(s) enregistrée(s) dans reference_groupe_efficacite.")

def afficher_classement_actuel(engine):
    with engine.connect() as conn:
        df_ref = pd.read_sql(
            "SELECT molecule, groupe, classe_par, date_classement "
            "FROM reference_groupe_efficacite ORDER BY groupe, molecule;",
            conn
        )
    print("\n=== Classement actuel en base ===")
    print(df_ref.to_string(index=False))
    return df_ref

preparer_table_classement(engine)
classer_molecules_avec_clinicien(engine)
afficher_classement_actuel(engine)



REQUETE_TRAITEMENTS = text("""
WITH traitement AS (
    SELECT
        tf.id                     AS traitement_id,
        tf.pseudonyme,
        tf.molecule,
        tf.ligne_therapeutique,
        tf.date_debut,
        tf.date_fin,
        tf.motif_switch,
        tf.observance,
        p.age,
        p.date_inclusion,
        ic.age_diagnostic_mois,
        ic.age_premier_symptome_mois,
        ic.delai_diagnostic_mois,     -- colonne générée (dictionnaire), conservée à titre informatif
        ic.date_diagnostic,           -- colonne réelle présente en base (confirmée par l'encadrante)
        ic.sexe,
        -- date de fin effective de la fenêtre d'observation pour ce traitement :
        -- date_fin si renseignée, sinon date_dernier_suivi (traitement en cours)
        COALESCE(tf.date_fin, s.date_dernier_suivi, CURRENT_DATE) AS date_fin_effective
    FROM sep_traitement_fond tf
    JOIN patients p                          ON p.pseudonyme = tf.pseudonyme
    LEFT JOIN sep_suivi s                    ON s.pseudonyme = tf.pseudonyme
    LEFT JOIN sep_identification_clinique ic ON ic.pseudonyme = tf.pseudonyme
    WHERE p.registre = 'SEP'
      AND tf.date_debut IS NOT NULL
),
poussees_periode AS (
    -- nombre de poussées survenues DURANT chaque fenêtre de traitement
    SELECT
        t.traitement_id,
        COUNT(pou.id) AS nb_poussees
    FROM traitement t
    LEFT JOIN sep_poussees pou
        ON pou.pseudonyme = t.pseudonyme
       AND pou.date_poussee BETWEEN t.date_debut AND t.date_fin_effective
    GROUP BY t.traitement_id
),
irm_periode AS (
    -- nombre d'IRM avec nouvelles lésions DURANT chaque fenêtre de traitement
    -- + somme des lésions T2 rapportées comme référence de charge lésionnelle
    SELECT
        t.traitement_id,
        COUNT(i.id) FILTER (WHERE i.nouvelles_lesions_vs_irm_anterieure = TRUE) AS nb_irm_avec_nouvelles_lesions,
        COUNT(i.id)                                                              AS nb_irm_realisees,
        AVG(i.nb_lesions_t2)                                                     AS moy_lesions_t2
    FROM traitement t
    LEFT JOIN sep_irm i
        ON i.pseudonyme = t.pseudonyme
       AND i.date_examen BETWEEN t.date_debut AND t.date_fin_effective
    GROUP BY t.traitement_id
),
edss_baseline AS (
    -- dernier EDSS connu AVANT le début du traitement (covariable d'ajustement)
    SELECT DISTINCT ON (t.traitement_id)
        t.traitement_id,
        e.score_edss AS edss_baseline
    FROM traitement t
    LEFT JOIN sep_edss_visites e
        ON e.pseudonyme = t.pseudonyme
       AND e.date_visite <= t.date_debut
    ORDER BY t.traitement_id, e.date_visite DESC
),
lesions_t2_baseline AS (
    -- dernière IRM connue AVANT le début du traitement (charge lésionnelle initiale)
    SELECT DISTINCT ON (t.traitement_id)
        t.traitement_id,
        i.nb_lesions_t2 AS lesions_t2_baseline
    FROM traitement t
    LEFT JOIN sep_irm i
        ON i.pseudonyme = t.pseudonyme
       AND i.date_examen <= t.date_debut
    ORDER BY t.traitement_id, i.date_examen DESC
)
SELECT
    t.traitement_id,
    t.pseudonyme,
    t.molecule,
    t.ligne_therapeutique,
    rge.groupe AS groupe_efficacite,
    t.age,
    t.sexe,
    GREATEST(
        EXTRACT(EPOCH FROM (t.date_debut - t.date_diagnostic)) / (365.25 * 86400),
        0
    ) AS duree_maladie_avant_traitement_annees,
    t.date_debut,
    t.date_fin_effective,
    t.motif_switch,
    t.observance,
    GREATEST(
        EXTRACT(EPOCH FROM (t.date_fin_effective - t.date_debut)) / (365.25 * 86400),
        1.0/365.25   -- éviter durée nulle (division par zéro)
    ) AS duree_suivi_annees,
    COALESCE(pp.nb_poussees, 0)                       AS nb_poussees,
    COALESCE(ip.nb_irm_avec_nouvelles_lesions, 0)     AS nb_irm_nouvelles_lesions,
    COALESCE(ip.nb_irm_realisees, 0)                  AS nb_irm_realisees,
    ip.moy_lesions_t2,
    eb.edss_baseline,
    lb.lesions_t2_baseline
FROM traitement t
LEFT JOIN poussees_periode     pp  ON pp.traitement_id = t.traitement_id
LEFT JOIN irm_periode          ip  ON ip.traitement_id = t.traitement_id
LEFT JOIN edss_baseline        eb  ON eb.traitement_id = t.traitement_id
LEFT JOIN lesions_t2_baseline  lb  ON lb.traitement_id = t.traitement_id
LEFT JOIN reference_groupe_efficacite rge ON rge.molecule = t.molecule
ORDER BY t.pseudonyme, t.date_debut;
""")

with engine.connect() as conn:
    df = pd.read_sql(REQUETE_TRAITEMENTS, conn)

print(f"Nombre de séquences de traitement extraites : {len(df)}")
print(df.head())


df = df[df["ligne_therapeutique"].notna()]
df = df[df["ligne_therapeutique"].astype(str).str.upper() != "NA"]

print("\nRépartition molécule -> groupe d'efficacité (classé par le clinicien) :")
print(df.groupby(["molecule", "groupe_efficacite"], dropna=False).size())

molecules_non_classees_restantes = df[df["groupe_efficacite"].isna()]["molecule"].unique()
if len(molecules_non_classees_restantes) > 0:
    print(f"\nATTENTION : {len(molecules_non_classees_restantes)} molécule(s) encore non "
          f"classée(s), exclue(s) de l'analyse : {list(molecules_non_classees_restantes)}")

df_analyse = df[df["groupe_efficacite"].isin(["Faible_Moderee", "Haute_efficacite"])].copy()

df_analyse["TAP"] = df_analyse["nb_poussees"] / df_analyse["duree_suivi_annees"]

df_analyse["taux_nouvelles_lesions_irm_an"] = (
    df_analyse["nb_irm_nouvelles_lesions"] / df_analyse["duree_suivi_annees"]
)

df_analyse["evenement_echec"] = (df_analyse["motif_switch"] == "échec").astype(int)
df_analyse["temps_echec_annees"] = df_analyse["duree_suivi_annees"]

for col in ["edss_baseline", "lesions_t2_baseline", "age", "duree_maladie_avant_traitement_annees"]:
    df_analyse[f"{col}_manquant"] = df_analyse[col].isna().astype(int)
    df_analyse[col] = df_analyse[col].fillna(df_analyse[col].median())

df_analyse["sexe_F"] = (df_analyse["sexe"] == "F").astype(int)

print("\nEffectifs finaux par groupe d'efficacité :")
print(df_analyse["groupe_efficacite"].value_counts())

print("\n=== Comparabilité des groupes avant analyse (Mann-Whitney / Chi²) ===")
for var in ["age", "edss_baseline", "lesions_t2_baseline"]:
    g1 = df_analyse.loc[df_analyse.groupe_efficacite == "Faible_Moderee", var]
    g2 = df_analyse.loc[df_analyse.groupe_efficacite == "Haute_efficacite", var]
    stat_u, p_u = stats.mannwhitneyu(g1, g2, alternative="two-sided")
    print(f"{var:25s} | Mann-Whitney U p = {p_u:.4f} "
          f"| médiane Faible/Modérée = {g1.median():.2f} | Haute eff. = {g2.median():.2f}")

tableau_contingence = pd.crosstab(df_analyse["groupe_efficacite"], df_analyse["observance"])
chi2, p_chi2, dof, _ = stats.chi2_contingency(tableau_contingence)
print(f"\nObservance vs groupe : Chi² = {chi2:.2f}, p = {p_chi2:.4f}")

print("\n=== (a) ANOVA classique : TAP ~ molecule ===")

for mol, sous_df in df_analyse.groupby("molecule"):
    if len(sous_df) >= 3:
        _, p_shapiro = stats.shapiro(sous_df["TAP"])
        print(f"  Shapiro-Wilk {mol:15s} n={len(sous_df):3d} : p = {p_shapiro:.4f}")

groupes_tap = [g["TAP"].values for _, g in df_analyse.groupby("molecule")]
stat_levene, p_levene = stats.levene(*groupes_tap)
print(f"Test de Levene (homogénéité des variances) : p = {p_levene:.4f}")

modele_anova = smf.ols("TAP ~ C(molecule)", data=df_analyse).fit()
table_anova = anova_lm(modele_anova, typ=2)
print("\nTableau ANOVA (type II) :")
print(table_anova)

stat_kw, p_kw = stats.kruskal(*groupes_tap)
print(f"\nAlternative non paramétrique — Kruskal-Wallis : H = {stat_kw:.3f}, p = {p_kw:.4f}")


print("\n=== (b-i) GEE binomial négatif : nb_poussees ~ groupe + covariables, offset=log(durée) ===")

df_analyse["log_duree"] = np.log(df_analyse["duree_suivi_annees"])

modele_gee_tap = smf.gee(
    "nb_poussees ~ groupe_efficacite + age + sexe_F + edss_baseline + "
    "lesions_t2_baseline + duree_maladie_avant_traitement_annees",
    groups="pseudonyme",
    data=df_analyse,
    offset=df_analyse["log_duree"],
    family=sm.families.NegativeBinomial(alpha=1.0),
).fit()
print(modele_gee_tap.summary())

irr_tap = np.exp(modele_gee_tap.params)
ic_tap = np.exp(modele_gee_tap.conf_int())
ic_tap.columns = ["IC95%_bas", "IC95%_haut"]
print("\nIRR (Incidence Rate Ratio) — TAP :")
print(pd.concat([irr_tap.rename("IRR"), ic_tap], axis=1))

print("\n=== (b-ii) Modèle mixte linéaire : log(TAP+0.01) ~ groupe + covariables, (1|pseudonyme) ===")
df_analyse["log_TAP"] = np.log(df_analyse["TAP"] + 0.01)

modele_mixte_tap = smf.mixedlm(
    "log_TAP ~ groupe_efficacite + age + sexe_F + edss_baseline + "
    "lesions_t2_baseline + duree_maladie_avant_traitement_annees",
    data=df_analyse,
    groups=df_analyse["pseudonyme"],
).fit()
print(modele_mixte_tap.summary())

print(f"\nAIC modèle mixte (log-TAP) : {modele_mixte_tap.aic:.1f}" if hasattr(modele_mixte_tap, "aic") else "")


print("\n=== GEE binomial négatif : nb_irm_nouvelles_lesions ~ groupe + covariables ===")

modele_gee_irm = smf.gee(
    "nb_irm_nouvelles_lesions ~ groupe_efficacite + age + lesions_t2_baseline + "
    "duree_maladie_avant_traitement_annees",
    groups="pseudonyme",
    data=df_analyse,
    offset=df_analyse["log_duree"],
    family=sm.families.NegativeBinomial(alpha=1.0),
).fit()
print(modele_gee_irm.summary())

irr_irm = np.exp(modele_gee_irm.params)
ic_irm = np.exp(modele_gee_irm.conf_int())
ic_irm.columns = ["IC95%_bas", "IC95%_haut"]
print("\nIRR — Nouvelles lésions IRM :")
print(pd.concat([irr_irm.rename("IRR"), ic_irm], axis=1))

print("\n=== ANOVA classique : taux_nouvelles_lesions_irm_an ~ molecule ===")
modele_anova_irm = smf.ols("taux_nouvelles_lesions_irm_an ~ C(molecule)", data=df_analyse).fit()
print(anova_lm(modele_anova_irm, typ=2))

from statsmodels.stats.multicomp import pairwise_tukeyhsd
print("\nComparaisons post-hoc (Tukey HSD) — nouvelles lésions IRM :")
tukey_irm = pairwise_tukeyhsd(
    endog=df_analyse["taux_nouvelles_lesions_irm_an"],
    groups=df_analyse["molecule"],
    alpha=0.05,
)
print(tukey_irm)

groupes_irm = [g["taux_nouvelles_lesions_irm_an"].values for _, g in df_analyse.groupby("molecule")]
stat_kw_irm, p_kw_irm = stats.kruskal(*groupes_irm)
print(f"\nAlternative non paramétrique — Kruskal-Wallis (IRM) : H = {stat_kw_irm:.3f}, p = {p_kw_irm:.4f}")


print("\n=== Kaplan-Meier : délai avant échec thérapeutique ===")

kmf = KaplanMeierFitter()
fig, ax = plt.subplots(figsize=(9, 6))

resultats_km = {}
for groupe, sous_df in df_analyse.groupby("groupe_efficacite"):
    kmf.fit(
        durations=sous_df["temps_echec_annees"],
        event_observed=sous_df["evenement_echec"],
        label=groupe,
    )
    kmf.plot_survival_function(ax=ax, ci_show=True)
    resultats_km[groupe] = kmf
    print(f"\n--- {groupe} ---")
    print(f"Médiane de survie sans échec : {kmf.median_survival_time_:.2f} années")

ax.set_title("Kaplan-Meier — Délai avant échec thérapeutique\n"
              "(interférons/glatiramère vs thérapies de haute efficacité)")
ax.set_xlabel("Délai depuis l'initiation du traitement (années)")
ax.set_ylabel("Probabilité de rester sans échec thérapeutique")
plt.tight_layout()
plt.savefig("KM_groupe_efficacite.png", dpi=300)
plt.close(fig)
print("\nGraphique enregistré : KM_groupe_efficacite.png")

g1 = df_analyse[df_analyse.groupe_efficacite == "Faible_Moderee"]
g2 = df_analyse[df_analyse.groupe_efficacite == "Haute_efficacite"]
resultat_logrank = logrank_test(
    g1["temps_echec_annees"], g2["temps_echec_annees"],
    event_observed_A=g1["evenement_echec"], event_observed_B=g2["evenement_echec"],
)
print(f"\nTest du Log-Rank : statistique = {resultat_logrank.test_statistic:.3f}, "
      f"p = {resultat_logrank.p_value:.4f}")

resultat_logrank_molecule = multivariate_logrank_test(
    df_analyse["temps_echec_annees"], df_analyse["molecule"], df_analyse["evenement_echec"]
)
print(f"Log-Rank multivarié (par molécule) : p = {resultat_logrank_molecule.p_value:.4f}")

fig, ax = plt.subplots(figsize=(9, 6))
for molecule, sous_df in df_analyse.groupby("molecule"):
    kmf.fit(
        durations=sous_df["temps_echec_annees"],
        event_observed=sous_df["evenement_echec"],
        label=molecule,
    )
    kmf.plot_survival_function(ax=ax, ci_show=False)
    print(f"  Médiane de survie ({molecule}) : "
          f"{kmf.median_survival_time_:.2f} années, n={len(sous_df)}")
ax.set_title("Kaplan-Meier — Délai avant échec thérapeutique par molécule")
ax.set_xlabel("Délai depuis l'initiation du traitement (années)")
ax.set_ylabel("Probabilité de rester sans échec thérapeutique")
plt.tight_layout()
plt.savefig("KM_molecule.png", dpi=300)
plt.close(fig)
print("Graphique enregistré : KM_molecule.png")



print("\n=== Modèle de Cox univarié ===")
df_cox = df_analyse[[
    "temps_echec_annees", "evenement_echec", "groupe_efficacite",
    "age", "sexe_F", "edss_baseline", "lesions_t2_baseline",
    "duree_maladie_avant_traitement_annees", "pseudonyme"
]].copy()
df_cox = pd.get_dummies(df_cox, columns=["groupe_efficacite"], drop_first=True)

cph_univarie = CoxPHFitter()
cph_univarie.fit(
    df_cox[["temps_echec_annees", "evenement_echec", "groupe_efficacite_Haute_efficacite"]],
    duration_col="temps_echec_annees", event_col="evenement_echec",
)
cph_univarie.print_summary()

print("\n=== Modèle de Cox multivarié (ajusté) ===")
cph_multivarie = CoxPHFitter()
cph_multivarie.fit(
    df_cox.drop(columns=["pseudonyme"]),
    duration_col="temps_echec_annees", event_col="evenement_echec",
    robust=True,   
    cluster_col=None,
)
cph_multivarie.print_summary()

print("\nTest des résidus de Schoenfeld (proportionnalité des risques) :")
resultats_ph = cph_multivarie.check_assumptions(
    df_cox.drop(columns=["pseudonyme"]), p_value_threshold=0.05, show_plots=False
)

print("\n=== Modèle de Cox avec erreurs-types clusterisées par patient ===")
cph_cluster = CoxPHFitter()
cph_cluster.fit(
    df_cox, duration_col="temps_echec_annees", event_col="evenement_echec",
    cluster_col="pseudonyme",
)
cph_cluster.print_summary()

print(f"\nComparaison AIC :")
print(f"  Cox univarié   : {cph_univarie.AIC_partial_:.1f}")
print(f"  Cox multivarié : {cph_multivarie.AIC_partial_:.1f}")



print("\n=== Appariement par score de propension (1:1, nearest neighbor) ===")

covariables_ps = ["age", "edss_baseline", "lesions_t2_baseline", "duree_maladie_avant_traitement_annees"]
df_ps = df_analyse.dropna(subset=covariables_ps + ["groupe_efficacite"]).copy()
df_ps["traitement_binaire"] = (df_ps["groupe_efficacite"] == "Haute_efficacite").astype(int)

X_ps = df_ps[covariables_ps]
y_ps = df_ps["traitement_binaire"]
modele_logit_ps = LogisticRegression(max_iter=1000)
modele_logit_ps.fit(X_ps, y_ps)
df_ps["score_propension"] = modele_logit_ps.predict_proba(X_ps)[:, 1]

traites = df_ps[df_ps.traitement_binaire == 1]
controles = df_ps[df_ps.traitement_binaire == 0]
caliper = 0.2 * df_ps["score_propension"].std()

nn = NearestNeighbors(n_neighbors=1)
nn.fit(controles[["score_propension"]])
distances, indices = nn.kneighbors(traites[["score_propension"]])

paires_valides = distances.flatten() <= caliper
traites_apparies = traites[paires_valides].reset_index(drop=True)
controles_apparies = controles.iloc[indices.flatten()[paires_valides]].reset_index(drop=True)

df_apparie = pd.concat([traites_apparies, controles_apparies], axis=0).reset_index(drop=True)
print(f"Paires appariées : {len(traites_apparies)} / {len(traites)} patients haute efficacité")
print(f"Effectifs après appariement :\n{df_apparie['groupe_efficacite'].value_counts()}")

print("\nÉquilibre post-appariement (différences de moyennes) :")
for var in covariables_ps:
    m_traites = traites_apparies[var].mean()
    m_controles = controles_apparies[var].mean()
    print(f"  {var:20s} : Haute eff. = {m_traites:.2f} | Faible/Modérée = {m_controles:.2f}")

print("\n=== Kaplan-Meier post-appariement ===")
fig, ax = plt.subplots(figsize=(9, 6))
for groupe, sous_df in df_apparie.groupby("groupe_efficacite"):
    kmf.fit(sous_df["temps_echec_annees"], sous_df["evenement_echec"], label=groupe)
    kmf.plot_survival_function(ax=ax)
ax.set_title("Kaplan-Meier après appariement par score de propension")
plt.tight_layout()
plt.savefig("KM_apres_PS_matching.png", dpi=300)
plt.close(fig)

g1_a = df_apparie[df_apparie.groupe_efficacite == "Faible_Moderee"]
g2_a = df_apparie[df_apparie.groupe_efficacite == "Haute_efficacite"]
logrank_apparie = logrank_test(
    g1_a["temps_echec_annees"], g2_a["temps_echec_annees"],
    event_observed_A=g1_a["evenement_echec"], event_observed_B=g2_a["evenement_echec"],
)
print(f"Log-Rank post-appariement : p = {logrank_apparie.p_value:.4f}")

cph_apparie = CoxPHFitter()
df_cox_apparie = pd.get_dummies(
    df_apparie[["temps_echec_annees", "evenement_echec", "groupe_efficacite"]],
    columns=["groupe_efficacite"], drop_first=True
)
cph_apparie.fit(df_cox_apparie, duration_col="temps_echec_annees", event_col="evenement_echec")
print("\n=== Cox post-appariement ===")
cph_apparie.print_summary()


with pd.ExcelWriter("resultats_analyse_lignes_therapeutiques.xlsx") as writer:
    df_analyse.to_excel(writer, sheet_name="donnees_analysees", index=False)
    pd.concat([irr_tap.rename("IRR_TAP"), ic_tap], axis=1).to_excel(writer, sheet_name="TAP_GEE")
    pd.concat([irr_irm.rename("IRR_IRM"), ic_irm], axis=1).to_excel(writer, sheet_name="IRM_GEE")
    cph_multivarie.summary.to_excel(writer, sheet_name="Cox_multivarie")
    cph_apparie.summary.to_excel(writer, sheet_name="Cox_apparie")

print("\nRésultats exportés : resultats_analyse_lignes_therapeutiques.xlsx")


print("\n" + "=" * 90)
print("RÉCAPITULATIF DES FICHIERS GÉNÉRÉS")
print("=" * 90)
fichiers_generes = [
    "KM_groupe_efficacite.png       (Kaplan-Meier par groupe d'efficacité)",
    "KM_molecule.png                (Kaplan-Meier par molécule individuelle)",
    "KM_apres_PS_matching.png       (Kaplan-Meier post-appariement)",
    "resultats_analyse_lignes_therapeutiques.xlsx  (tableaux de résultats)",
    f"{FICHIER_RESULTATS_TXT}       (tous les chiffres/tests/résumés de modèles, ce fichier)",
]
for f in fichiers_generes:
    print(f"  - {f}")
print("=" * 90)

sys.stdout.fichier.close()
sys.stdout = sys.stdout.terminal