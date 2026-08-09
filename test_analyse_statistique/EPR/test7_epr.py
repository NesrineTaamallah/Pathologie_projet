
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
import sys
from sqlalchemy import create_engine, text
from scipy import stats
import statsmodels.api as sm
import statsmodels.formula.api as smf
from statsmodels.stats.outliers_influence import variance_inflation_factor
from statsmodels.stats.diagnostic import het_breuschpagan
from statsmodels.graphics.gofplots import qqplot

OUT_DIR = "/mnt/user-data/outputs"


class Tee:
    
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)

    def flush(self):
        for s in self.streams:
            s.flush()


_log_file = open(f"{OUT_DIR}/resultats_textuels.txt", "w")
sys.stdout = Tee(sys.__stdout__, _log_file)


DB_URI = "postgresql+psycopg2://<user>:<password>@<host>:<port>/<dbname>"
engine = create_engine(DB_URI)



QUERY = text("""
WITH qi_valide AS (
    SELECT
        bn.id,
        bn.pseudonyme,
        bn.date_bilan,
        bn.qi
    FROM epr_bilan_neuropsy bn
    WHERE bn.qi IS NOT NULL
),
freq_appariee AS (
    -- pour chaque bilan QI, la mesure de fréquence la plus récente
    -- rapportée avant (ou le jour de) la date du bilan
    SELECT DISTINCT ON (q.id)
        q.id AS bilan_id,
        q.pseudonyme,
        q.date_bilan,
        q.qi,
        fc.frequence_normalisee_mois,
        fc.duree_moyenne_min,
        fc.date_rapport
    FROM qi_valide q
    LEFT JOIN epr_frequence_crises fc
        ON fc.pseudonyme = q.pseudonyme
        AND fc.date_rapport <= q.date_bilan
        AND fc.frequence_normalisee_mois IS NOT NULL
    ORDER BY q.id, fc.date_rapport DESC
),
etiologie_principale AS (
    SELECT pseudonyme, categorie_etiologique
    FROM epr_etiologie
    WHERE etiologie_principale = TRUE
),
onset AS (
    SELECT pseudonyme, age_debut_crises_mois
    FROM epr_identification_clinique
    WHERE age_debut_crises_mois IS NOT NULL
),
nb_ae AS (
    SELECT pseudonyme, COUNT(*) AS nb_ae_essayes
    FROM epr_liste_ae
    GROUP BY pseudonyme
)
SELECT
    f.pseudonyme,
    f.date_bilan,
    f.qi,
    f.frequence_normalisee_mois,
    f.duree_moyenne_min,
    o.age_debut_crises_mois,
    p.age                           AS age_patient_ans,
    p.date_inclusion,
    et.categorie_etiologique,
    COALESCE(a.nb_ae_essayes, 0)    AS nb_ae_essayes
FROM freq_appariee f
JOIN patients p               ON p.pseudonyme = f.pseudonyme
LEFT JOIN etiologie_principale et ON et.pseudonyme = f.pseudonyme
LEFT JOIN onset o             ON o.pseudonyme = f.pseudonyme
LEFT JOIN nb_ae a             ON a.pseudonyme = f.pseudonyme
WHERE p.registre = 'EPRLEPSIE'
  AND f.frequence_normalisee_mois IS NOT NULL
  AND (et.categorie_etiologique IS NOT NULL AND et.categorie_etiologique != 'NA');
""")

df = pd.read_sql(QUERY, engine)


df["date_bilan"] = pd.to_datetime(df["date_bilan"])
df["date_inclusion"] = pd.to_datetime(df["date_inclusion"])

df["decalage_inclusion_bilan_mois"] = (
    (df["date_bilan"] - df["date_inclusion"]).dt.days / 30.44
)
df["age_au_bilan_mois"] = df["age_patient_ans"] * 12 + df["decalage_inclusion_bilan_mois"]
df["duree_epilepsie_mois"] = df["age_au_bilan_mois"] - df["age_debut_crises_mois"]


n_avant = (df["duree_epilepsie_mois"] < 0).sum()
if n_avant:
    print(f"[Attention] {n_avant} lignes avec durée d'épilepsie négative — exclues.")
df = df[df["duree_epilepsie_mois"] >= 0].copy()

n_multi = (df.groupby("pseudonyme").size() > 1).sum()
print(f"Échantillon final : {df.shape[0]} bilans QI sur {df['pseudonyme'].nunique()} patients "
      f"({n_multi} patients avec plusieurs bilans QI)")
if n_multi:
    print("-> Mesures répétées détectées : les erreurs-types de la régression seront "
          "clusterisées par pseudonyme (cov_type='cluster') pour ne pas sous-estimer "
          "la variance (indépendance violée sinon).")



def test_normalite(nom, serie):
    stat, p = stats.shapiro(serie.dropna())
    print(f"Shapiro-Wilk {nom} : W={stat:.3f}, p={p:.4f} "
          f"{'(non normal -> Spearman justifié)' if p < 0.05 else '(normal)'}")

print("\n--- Tests de normalité ---")
test_normalite("QI", df["qi"])
test_normalite("Fréquence crises (mois)", df["frequence_normalisee_mois"])
test_normalite("Durée épilepsie (mois)", df["duree_epilepsie_mois"])


df["log_frequence_mois"] = np.log1p(df["frequence_normalisee_mois"])

print("\n--- Corrélations de Spearman (bivariées) ---")
for var in ["frequence_normalisee_mois", "duree_moyenne_min", "duree_epilepsie_mois"]:
    sous_df = df.dropna(subset=["qi", var])
    if len(sous_df) < 3:
        print(f"QI vs {var} : échantillon insuffisant (n={len(sous_df)})")
        continue
    rho, p = stats.spearmanr(sous_df["qi"], sous_df[var])
    print(f"QI vs {var} : rho={rho:.3f}, p={p:.4f}, n={len(sous_df)}")



df_reg = df.dropna(subset=[
    "qi", "frequence_normalisee_mois", "log_frequence_mois", "duree_epilepsie_mois",
    "duree_moyenne_min", "categorie_etiologique", "age_debut_crises_mois"
]).copy()

df_reg["categorie_etiologique"] = pd.Categorical(
    df_reg["categorie_etiologique"],
    categories=["Inconnue", "Structurelle", "Génétique", "Métabolique",
                "Infectieuse", "Immune"]
)


CLUSTER_KW = dict(cov_type="cluster", cov_kwds={"groups": df_reg["pseudonyme"]})


FORMULE_BASE = (
    "qi ~ {freq} + duree_epilepsie_mois + duree_moyenne_min "
    "+ age_debut_crises_mois + C(categorie_etiologique)"
)


print("\n=== Modèle A : fréquence brute (sans nb_ae_essayes) ===")
modele_a = smf.ols(FORMULE_BASE.format(freq="frequence_normalisee_mois"),
                    data=df_reg).fit(**CLUSTER_KW)
print(modele_a.summary())

print("\n=== Modèle B : log(fréquence) (sans nb_ae_essayes) ===")
modele_b = smf.ols(FORMULE_BASE.format(freq="log_frequence_mois"),
                    data=df_reg).fit(**CLUSTER_KW)
print(modele_b.summary())
print(f"\nComparaison ajustement : R² ajusté brut={modele_a.rsquared_adj:.3f} "
      f"vs log={modele_b.rsquared_adj:.3f}")

print("\n=== Modèle C : log(fréquence) + nb_ae_essayes (analyse de sensibilité) ===")
modele_c = smf.ols(
    FORMULE_BASE.format(freq="log_frequence_mois") + " + nb_ae_essayes",
    data=df_reg
).fit(**CLUSTER_KW)
print(modele_c.summary())


print("\n--- Comparaison des coefficients, modèle B (sans nb_ae_essayes) "
      "vs modèle C (avec) ---")
variables_communes = [v for v in modele_b.params.index if v in modele_c.params.index]
print(f"{'Variable':40s} {'Coef. B (sans)':>16s} {'Coef. C (avec)':>16s} {'Variation %':>12s}")
resultats_sensibilite = {}
for v in variables_communes:
    cb, cc = modele_b.params[v], modele_c.params[v]
    var_pct = 100 * abs(cc - cb) / abs(cb) if abs(cb) > 1e-9 else np.nan
    resultats_sensibilite[v] = var_pct
    alerte = " <-- masquage probable" if (not np.isnan(var_pct) and var_pct > 20) else ""
    print(f"{v:40s} {cb:16.3f} {cc:16.3f} {var_pct:11.1f}%{alerte}")

print(f"\nCoefficient propre de nb_ae_essayes dans le modèle C : "
      f"{modele_c.params.get('nb_ae_essayes', np.nan):.3f} "
      f"(p={modele_c.pvalues.get('nb_ae_essayes', np.nan):.4f})")
print("Interprétation : nb_ae_essayes n'est pas une cause du QI mais un marqueur "
      "indirect de sévérité/pharmacorésistance (plus d'échecs d'AE reflète un cas "
      "plus sévère). Un coefficient élevé et très significatif sur cette variable, "
      "combiné à une variation importante des coefficients fréquence/durée entre "
      "B et C, indique que nb_ae_essayes capture une partie de l'information que "
      "la fréquence/durée est censée mesurer -> risque de sur-ajustement "
      "(over-adjustment bias). Le modèle B (sans nb_ae_essayes) est donc le "
      "modèle principal recommandé pour répondre à la question de l'encadrante ; "
      "le modèle C reste une analyse de sensibilité, pas le modèle de référence.")


modele = modele_b

X_vif = df_reg[["log_frequence_mois", "duree_epilepsie_mois", "duree_moyenne_min",
                 "age_debut_crises_mois"]].dropna()
X_vif = sm.add_constant(X_vif)
print("\n--- VIF (multicolinéarité, modèle B) ---")
for i, col in enumerate(X_vif.columns):
    if col == "const":
        continue
    vif = variance_inflation_factor(X_vif.values, i)
    print(f"{col} : VIF={vif:.2f} {'(>5, à surveiller)' if vif > 5 else ''}")


print("\n--- Test de Breusch-Pagan (homoscédasticité) ---")
modele_ols_classique = smf.ols(FORMULE_BASE.format(freq="log_frequence_mois"),
                                data=df_reg).fit()
bp_stat, bp_p, bp_f, bp_fp = het_breuschpagan(
    modele_ols_classique.resid, modele_ols_classique.model.exog
)
print(f"LM stat={bp_stat:.3f}, p={bp_p:.4f} "
      f"{'(hétéroscédasticité détectée -> envisager des erreurs-types robustes HC3)' if bp_p < 0.05 else '(homoscédasticité non rejetée)'}")

print("\n--- Normalité des résidus (Shapiro-Wilk) ---")
resid = modele_ols_classique.resid
w_stat, w_p = stats.shapiro(resid)
print(f"W={w_stat:.3f}, p={w_p:.4f} "
      f"{'(résidus non normaux -> IC/p-values à interpréter avec prudence)' if w_p < 0.05 else '(normalité non rejetée)'}")

fig, axes = plt.subplots(1, 2, figsize=(10, 4))
qqplot(resid, line="s", ax=axes[0])
axes[0].set_title("Q-Q plot des résidus")
axes[1].scatter(modele_ols_classique.fittedvalues, resid, alpha=0.6)
axes[1].axhline(0, color="red", linestyle="--")
axes[1].set_xlabel("Valeurs prédites (QI)")
axes[1].set_ylabel("Résidus")
axes[1].set_title("Résidus vs valeurs prédites")
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/diagnostics_residus.png", dpi=150)
plt.close()
print("Graphique de diagnostics sauvegardé : diagnostics_residus.png")



print("\n--- Spearman QI vs fréquence, stratifié par étiologie principale ---")
for etio, sous_groupe in df.dropna(subset=["categorie_etiologique"]).groupby("categorie_etiologique"):
    sous_groupe = sous_groupe.dropna(subset=["qi", "frequence_normalisee_mois"])
    if len(sous_groupe) < 5:
        print(f"{etio} : n={len(sous_groupe)} (insuffisant, non testé)")
        continue
    rho, p = stats.spearmanr(sous_groupe["qi"], sous_groupe["frequence_normalisee_mois"])
    print(f"{etio} (n={len(sous_groupe)}) : rho={rho:.3f}, p={p:.4f}")



print("\n" + "=" * 70)
print("DISCUSSION / LIMITES (générée à partir des résultats ci-dessus)")
print("=" * 70)

print("""
1. nb_ae_essayes n'est pas une variable causale : c'est un marqueur indirect
   de sévérité/pharmacorésistance, pas un déterminant biologique du QI. Elle
   est probablement colinéaire sur le plan conceptuel avec la fréquence des
   crises (un patient ayant échoué à plus d'AE est structurellement plus
   sévère). Le modèle B (sans cette variable) est retenu comme modèle
   principal ; le modèle C est rapporté uniquement en analyse de
   sensibilité, avec la comparaison complète des coefficients ci-dessus.""")

rho_duree_crise, p_duree_crise = (np.nan, np.nan)
sous_dm = df.dropna(subset=["qi", "duree_moyenne_min"])
if len(sous_dm) >= 3:
    rho_duree_crise, p_duree_crise = stats.spearmanr(sous_dm["qi"], sous_dm["duree_moyenne_min"])
coef_dm = modele_b.params.get("duree_moyenne_min", np.nan)
p_coef_dm = modele_b.pvalues.get("duree_moyenne_min", np.nan)
print(f"""
2. Durée d'une crise (duree_moyenne_min) : incluse dans le modèle B/C
   conformément à la demande "fréquence/durée des crises". Corrélation
   bivariée QI vs durée moyenne : rho={rho_duree_crise:.3f}, p={p_duree_crise:.3f}
   ; coefficient ajusté dans le modèle B : {coef_dm:.3f} (p={p_coef_dm:.4f}).
   Si ce résultat est non significatif, ce n'est pas un oubli méthodologique :
   la durée d'un épisode critique (minutes) et la durée de la maladie
   (chronicité, en mois) sont deux dimensions distinctes de "durée" dans la
   littérature, et rien n'impose qu'elles aient le même pouvoir prédictif.
   Ce résultat (absence d'effet de la durée par crise) est en lui-même une
   information à discuter avec l'encadrante, pas un résultat à occulter.""")

coef_duree_epi = modele_b.params.get("duree_epilepsie_mois", np.nan)
p_duree_epi = modele_b.pvalues.get("duree_epilepsie_mois", np.nan)
print(f"""
3. Durée de l'épilepsie (duree_epilepsie_mois) : coefficient ajusté
   {coef_duree_epi:.3f} (p={p_duree_epi:.4f}). Si le p est proche du seuil
   0.05 (tendanciel), cela est cohérent avec une littérature partagée :
   Bourgeois et al. (Epilepsy & Behavior) trouvent la durée et l'âge de
   début comme meilleurs prédicteurs du devenir développemental ; l'étude
   PMC10006677 (n=80) trouve l'étiologie ET la durée totale comme
   meilleurs prédicteurs en régression multiple ; à l'inverse, dans le
   Dravet (Auvin et al. 2022), aucun paramètre épileptique classique
   (dont la durée) n'était corrélé au devenir cognitif, l'effet génétique
   dominant. Un résultat tendanciel ici n'invalide donc pas l'hypothèse
   de l'encéphalopathie épileptique — il peut simplement refléter une
   puissance statistique limitée (taille d'échantillon) ou un effet réel
   mais modeste, à confirmer sur un échantillon plus large ou avec une
   transformation (log/quadratique) de la durée.""")
print("=" * 70)


sns.set_style("whitegrid")
VARS_CONTINUES = {
    "qi": "QI",
    "frequence_normalisee_mois": "Fréquence des crises (crises/mois)",
    "log_frequence_mois": "log(1 + fréquence des crises)",
    "duree_moyenne_min": "Durée moyenne d'une crise (min)",
    "duree_epilepsie_mois": "Durée de l'épilepsie (mois)",
}

fig, axes = plt.subplots(2, 3, figsize=(15, 8))
for ax, (col, label) in zip(axes.flat, VARS_CONTINUES.items()):
    sns.histplot(df[col].dropna(), kde=True, ax=ax, color="steelblue")
    ax.set_title(label)
    ax.set_xlabel("")
axes.flat[-1].axis("off")
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/01_distributions.png", dpi=150)
plt.close()


paires = [
    ("frequence_normalisee_mois", "Fréquence des crises (crises/mois)"),
    ("log_frequence_mois", "log(1 + fréquence des crises)"),
    ("duree_moyenne_min", "Durée moyenne d'une crise (min)"),
    ("duree_epilepsie_mois", "Durée de l'épilepsie (mois)"),
]
fig, axes = plt.subplots(2, 2, figsize=(12, 10))
for ax, (col, label) in zip(axes.flat, paires):
    sous_df = df.dropna(subset=["qi", col])
    sns.regplot(data=sous_df, x=col, y="qi", ax=ax,
                scatter_kws={"alpha": 0.6}, line_kws={"color": "firebrick"})
    if len(sous_df) >= 3:
        rho, p = stats.spearmanr(sous_df["qi"], sous_df[col])
        ax.set_title(f"{label}\nrho={rho:.2f}, p={p:.3f}, n={len(sous_df)}")
    else:
        ax.set_title(f"{label} (n insuffisant)")
    ax.set_xlabel(label)
    ax.set_ylabel("QI")
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/02_qi_vs_frequence_duree.png", dpi=150)
plt.close()

fig, ax = plt.subplots(figsize=(9, 5))
ordre = (df.dropna(subset=["categorie_etiologique", "qi"])
           .groupby("categorie_etiologique")["qi"].median()
           .sort_values().index)
sns.boxplot(data=df, x="categorie_etiologique", y="qi", order=ordre,
            ax=ax, color="lightsteelblue", showfliers=False)
sns.stripplot(data=df, x="categorie_etiologique", y="qi", order=ordre,
              ax=ax, color="black", alpha=0.5, size=4, jitter=True)
ax.set_xlabel("Étiologie principale")
ax.set_ylabel("QI")
ax.set_title("QI par étiologie principale")
plt.xticks(rotation=30, ha="right")
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/03_qi_par_etiologie.png", dpi=150)
plt.close()


fig, ax = plt.subplots(figsize=(9, 6))
sns.scatterplot(data=df.dropna(subset=["categorie_etiologique"]),
                 x="frequence_normalisee_mois", y="qi",
                 hue="categorie_etiologique", ax=ax, alpha=0.75, s=60)
ax.set_xlabel("Fréquence des crises (crises/mois)")
ax.set_ylabel("QI")
ax.set_title("QI vs fréquence des crises, par étiologie")
ax.legend(title="Étiologie", bbox_to_anchor=(1.02, 1), loc="upper left")
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/04_qi_vs_frequence_par_etiologie.png", dpi=150)
plt.close()
mat_corr = df[list(VARS_CONTINUES.keys()) + ["age_debut_crises_mois", "nb_ae_essayes"]].corr(method="spearman")
fig, ax = plt.subplots(figsize=(8, 6))
sns.heatmap(mat_corr, annot=True, fmt=".2f", cmap="coolwarm", center=0,
            square=True, ax=ax, cbar_kws={"label": "rho de Spearman"})
ax.set_title("Matrice de corrélation (Spearman)")
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/05_heatmap_correlations.png", dpi=150)
plt.close()

print("\nGraphiques sauvegardés : 01_distributions.png, "
      "02_qi_vs_frequence_duree.png, 03_qi_par_etiologie.png, "
      "04_qi_vs_frequence_par_etiologie.png, 05_heatmap_correlations.png, "
      "diagnostics_residus.png")



df.to_csv(f"{OUT_DIR}/dataset_frequence_qi_epr.csv", index=False)
with open(f"{OUT_DIR}/resultats_regression.txt", "w") as f:
    f.write("=== Modèle A : fréquence brute (sans nb_ae_essayes) ===\n")
    f.write(modele_a.summary().as_text())
    f.write("\n\n=== Modèle B : log(fréquence) (sans nb_ae_essayes) — modèle retenu ===\n")
    f.write(modele_b.summary().as_text())
    f.write("\n\n=== Modèle C : log(fréquence) + nb_ae_essayes (sensibilité) ===\n")
    f.write(modele_c.summary().as_text())

print("\nExports finaux :")
print("  - dataset_frequence_qi_epr.csv   (données appariées)")
print("  - resultats_regression.txt        (summary() complet des 3 modèles)")
print("  - resultats_textuels.txt          (log complet : normalité, Spearman, VIF, BP, sensibilité)")
print("  - 5 graphiques PNG + diagnostics_residus.png")

sys.stdout = sys.__stdout__
_log_file.close()
