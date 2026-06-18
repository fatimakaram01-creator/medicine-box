"""
Medicine Box — ML Training v3 (avec génération de rapports MD)
================================================================
Entraîne UN set de modèles PAR patient ET par profil horaire.
Génère automatiquement un rapport report.md pour le PFE.

3 modèles par patient/profil :
1. Isolation Forest    → détection d'anomalies
2. RF Classifier       → optimisation heure d'alerte
3. RF Regressor        → risque d'oubli (délai prédit)
"""

import os
import sys
import json
import joblib
import numpy as np
import pandas as pd
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection

from sklearn.ensemble import IsolationForest, RandomForestClassifier, RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import mean_absolute_error, accuracy_score, f1_score, confusion_matrix, r2_score

MODELS_BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
os.makedirs(MODELS_BASE_DIR, exist_ok=True)


def get_models_dir(patient_id, profil_id=None):
    """Retourne le dossier des modèles pour un patient et un profil donné"""
    if profil_id:
        path = os.path.join(MODELS_BASE_DIR, f"patient_{patient_id}", f"profil_{profil_id}")
    else:
        path = os.path.join(MODELS_BASE_DIR, f"patient_{patient_id}", "global")
    os.makedirs(path, exist_ok=True)
    return path


def get_all_patients():
    """Retourne la liste des patients ayant des données"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT ao.patient_id, p.prenom, p.nom
            FROM alertes_optimisation ao
            JOIN patients p ON p.id = ao.patient_id
            ORDER BY ao.patient_id;
        """)
        rows = cursor.fetchall()
        cursor.close()
        return rows
    finally:
        conn.close()


def get_profils_patient(patient_id):
    """Retourne les profils ayant des données pour un patient"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT ao.profil_id, ip.label
            FROM alertes_optimisation ao
            JOIN intervalles_profils ip ON ip.id = ao.profil_id
            WHERE ao.patient_id = %s AND ao.profil_id IS NOT NULL
            ORDER BY ao.profil_id;
        """, (patient_id,))
        rows = cursor.fetchall()
        cursor.close()
        return rows
    finally:
        conn.close()


def load_data_patient(patient_id, profil_id=None):
    """Charge les données filtrées (exclut cause_manque='offline')"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        filtre_profil = "AND ao.profil_id = %s" if profil_id else ""
        params = (patient_id, profil_id) if profil_id else (patient_id,)
        cursor.execute(f"""
            SELECT
                ao.patient_id,
                ao.moment,
                COALESCE(ao.profil_id, 0) AS profil_id,
                EXTRACT(HOUR FROM ao.heure_alerte) * 60 + EXTRACT(MINUTE FROM ao.heure_alerte) AS heure_alerte_min,
                ao.jour_semaine,
                CASE
                    WHEN ao.heure_prise_apres_alerte IS NOT NULL
                    THEN EXTRACT(HOUR FROM ao.heure_prise_apres_alerte) * 60 + EXTRACT(MINUTE FROM ao.heure_prise_apres_alerte)
                    ELSE NULL
                END AS heure_prise_min,
                ao.delai_minutes,
                ao.alerte_efficace,
                ao.phase
            FROM alertes_optimisation ao
            LEFT JOIN prises pr ON pr.patient_id = ao.patient_id
                AND pr.moment = ao.moment
                AND pr.heure_prevue::date = ao.created_at::date
            WHERE ao.patient_id = %s
              AND (pr.cause_manque IS NULL OR pr.cause_manque != 'offline')
              {filtre_profil}
            ORDER BY ao.id ASC;
        """, params)
        rows = cursor.fetchall()
        cursor.close()
        df = pd.DataFrame(rows, columns=[
            'patient_id', 'moment', 'profil_id', 'heure_alerte_min', 'jour_semaine',
            'heure_prise_min', 'delai_minutes', 'alerte_efficace', 'phase'
        ])
        for col in ['heure_alerte_min', 'heure_prise_min', 'delai_minutes', 'profil_id']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        return df
    finally:
        conn.close()


def build_features(df):
    le_moment = LabelEncoder()
    df['moment_enc'] = le_moment.fit_transform(df['moment'])
    le_phase = LabelEncoder()
    df['phase_enc'] = le_phase.fit_transform(df['phase'])
    df['is_weekend'] = df['jour_semaine'].apply(lambda x: 1 if x >= 5 else 0)
    df['heure_norm'] = df['heure_alerte_min'] / (24 * 60)
    features = ['heure_alerte_min', 'jour_semaine', 'moment_enc', 'phase_enc', 'is_weekend', 'heure_norm', 'profil_id']
    return df, features, le_moment, le_phase


def generer_rapport_md(models_dir, patient_id, prenom, nom, label, n_samples,
                       features, metrics, feature_importances):
    """
    Génère un rapport report.md avec toutes les métriques d'entraînement.
    Utilisé pour l'Annexe E du rapport PFE.
    """
    md = []
    md.append(f"# Rapport d'entraînement ML — Patient {patient_id} ({label})\n")
    md.append(f"**Patient :** {prenom} {nom}  ")
    md.append(f"**Profil :** {label}  ")
    md.append(f"**Date d'entraînement :** {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}  ")
    md.append(f"**Échantillons utilisés :** {n_samples} lignes  ")
    md.append(f"**Données exclues :** prises avec `cause_manque = 'offline'`\n")

    md.append("---\n")

    # ── Tableau des performances ──
    md.append("## 📊 Performances des modèles\n")
    md.append("| Modèle | Métrique | Valeur |")
    md.append("|---|---|---|")
    md.append(f"| **Isolation Forest** | Anomalies détectées | {metrics['iso_anomalies']}/{n_samples} ({metrics['iso_pct']:.1f}%) |")
    md.append(f"| **RF Classifier** (heure optimale alerte) | Accuracy | {metrics['clf_accuracy']:.2%} |")
    md.append(f"| **RF Classifier** | F1-score | {metrics['clf_f1']:.3f} |")
    md.append(f"| **RF Regressor** (délai prise) | MAE | {metrics['reg_mae']:.2f} minutes |")
    md.append(f"| **RF Regressor** | R² | {metrics['reg_r2']:.3f} |")
    md.append("")

    # ── Matrice de confusion (RF Classifier) ──
    if 'clf_confusion' in metrics:
        cm = metrics['clf_confusion']
        md.append("## 🎯 Matrice de confusion — RF Classifier\n")
        md.append("|  | Prédit : Inefficace | Prédit : Efficace |")
        md.append("|---|---|---|")
        md.append(f"| **Réel : Inefficace** | {cm[0][0]} (TN) | {cm[0][1]} (FP) |")
        md.append(f"| **Réel : Efficace** | {cm[1][0]} (FN) | {cm[1][1]} (TP) |")
        md.append("")
        md.append("- **TN** (True Negative)  : alerte correctement classée inefficace")
        md.append("- **TP** (True Positive)  : alerte correctement classée efficace")
        md.append("- **FP** (False Positive) : alerte classée efficace mais réellement inefficace")
        md.append("- **FN** (False Negative) : alerte classée inefficace mais réellement efficace\n")

    # ── Importance des features ──
    md.append("## 🌟 Importance des features (RF Classifier)\n")
    md.append("| Feature | Importance | Interprétation |")
    md.append("|---|---|---|")
    interpretations = {
        'heure_alerte_min': "Heure d'envoi de l'alerte",
        'jour_semaine': "Jour de la semaine (0=lundi)",
        'moment_enc': "Moment de la journée (matin/midi/soir)",
        'phase_enc': "Phase d'apprentissage (decouverte/adapte)",
        'is_weekend': "Indicateur week-end",
        'heure_norm': "Heure normalisée [0-1]",
        'profil_id': "Profil horaire actif (Standard/Ramadan/...)"
    }
    sorted_features = sorted(zip(features, feature_importances), key=lambda x: x[1], reverse=True)
    for feat, imp in sorted_features:
        interp = interpretations.get(feat, "—")
        md.append(f"| `{feat}` | {imp:.3f} | {interp} |")
    md.append("")

    # ── Configuration ──
    md.append("## ⚙️ Configuration d'entraînement\n")
    md.append(f"- **Algorithmes :** Isolation Forest + Random Forest (Classifier + Regressor)")
    md.append(f"- **Split train/test :** 80% / 20%")
    md.append(f"- **n_estimators :** 100 arbres")
    md.append(f"- **max_depth :** 5 (Classifier) / 6 (Regressor)")
    md.append(f"- **class_weight :** balanced (Classifier)")
    md.append(f"- **contamination :** 0.1 (Isolation Forest)")
    md.append(f"- **random_state :** 42")
    md.append(f"- **Features ({len(features)}) :** {', '.join(features)}\n")

    # ── Fichiers générés ──
    md.append("## 📁 Fichiers générés\n")
    md.append(f"```")
    md.append(f"{models_dir}/")
    md.append(f"├── isolation_forest.joblib   ({metrics.get('iso_size_kb', '~')} KB)")
    md.append(f"├── rf_classifier.joblib      ({metrics.get('clf_size_kb', '~')} KB)")
    md.append(f"├── rf_regressor.joblib       ({metrics.get('reg_size_kb', '~')} KB)")
    md.append(f"├── le_moment.joblib")
    md.append(f"├── le_phase.joblib")
    md.append(f"├── metadata.json")
    md.append(f"└── report.md                 ← ce fichier")
    md.append(f"```\n")

    md.append("---\n")
    md.append(f"*Rapport généré automatiquement par `ml/train.py` le {datetime.now().strftime('%d/%m/%Y à %H:%M:%S')}*\n")

    # Sauvegarde
    rapport_path = os.path.join(models_dir, "report.md")
    with open(rapport_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(md))
    print(f"📄 Rapport généré : {rapport_path}")


def _train_and_save(df, features, le_moment, le_phase, models_dir, patient_id, prenom, nom, label="global"):
    """
    Entraîne les 3 modèles, calcule les métriques, sauvegarde et génère le rapport.
    """
    n_samples = len(df)
    metrics = {}

    # ── Isolation Forest ──
    X = df[features].dropna()
    iso = IsolationForest(n_estimators=100, contamination=0.1, random_state=42)
    iso.fit(X)
    iso_pred = iso.predict(X)
    n_anomalies = int((iso_pred == -1).sum())
    iso_path = os.path.join(models_dir, "isolation_forest.joblib")
    joblib.dump(iso, iso_path)
    metrics['iso_anomalies'] = n_anomalies
    metrics['iso_pct'] = 100 * n_anomalies / len(X) if len(X) > 0 else 0
    metrics['iso_size_kb'] = round(os.path.getsize(iso_path) / 1024, 1)

    # ── RF Classifier ──
    df_clf = df[features + ['alerte_efficace']].dropna()
    X_clf = df_clf[features]
    y_clf = df_clf['alerte_efficace'].astype(int)
    stratify = y_clf if y_clf.nunique() > 1 else None
    X_tr, X_te, y_tr, y_te = train_test_split(X_clf, y_clf, test_size=0.2, random_state=42, stratify=stratify)
    clf = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42, class_weight='balanced')
    clf.fit(X_tr, y_tr)
    y_pred = clf.predict(X_te)
    clf_path = os.path.join(models_dir, "rf_classifier.joblib")
    joblib.dump(clf, clf_path)
    metrics['clf_accuracy'] = accuracy_score(y_te, y_pred)
    metrics['clf_f1'] = f1_score(y_te, y_pred, average='weighted', zero_division=0)
    metrics['clf_confusion'] = confusion_matrix(y_te, y_pred, labels=[0, 1]).tolist()
    metrics['clf_size_kb'] = round(os.path.getsize(clf_path) / 1024, 1)
    feature_importances = clf.feature_importances_.tolist()

    # ── RF Regressor ──
    df_reg = df[features + ['delai_minutes']].dropna()
    df_reg = df_reg[df_reg['delai_minutes'] >= 0]
    X_reg, y_reg = df_reg[features], df_reg['delai_minutes']
    X_tr, X_te, y_tr, y_te = train_test_split(X_reg, y_reg, test_size=0.2, random_state=42)
    reg = RandomForestRegressor(n_estimators=100, max_depth=6, random_state=42)
    reg.fit(X_tr, y_tr)
    y_pred = reg.predict(X_te)
    reg_path = os.path.join(models_dir, "rf_regressor.joblib")
    joblib.dump(reg, reg_path)
    metrics['reg_mae'] = mean_absolute_error(y_te, y_pred)
    metrics['reg_r2'] = r2_score(y_te, y_pred)
    metrics['reg_size_kb'] = round(os.path.getsize(reg_path) / 1024, 1)

    # ── Encodeurs ──
    joblib.dump(le_moment, os.path.join(models_dir, "le_moment.joblib"))
    joblib.dump(le_phase, os.path.join(models_dir, "le_phase.joblib"))

    # ── Métadonnées JSON ──
    meta = {
        "patient_id": patient_id, "prenom": prenom, "nom": nom,
        "label": label,
        "trained_at": datetime.now().isoformat(),
        "n_samples": n_samples,
        "features": features,
        "moment_classes": list(le_moment.classes_),
        "phase_classes": list(le_phase.classes_),
        "metrics": {
            "isolation_forest": {
                "anomalies": metrics['iso_anomalies'],
                "anomalies_pct": round(metrics['iso_pct'], 2),
            },
            "rf_classifier": {
                "accuracy": round(metrics['clf_accuracy'], 4),
                "f1_score": round(metrics['clf_f1'], 4),
                "confusion_matrix": metrics['clf_confusion'],
            },
            "rf_regressor": {
                "mae_minutes": round(metrics['reg_mae'], 2),
                "r2_score": round(metrics['reg_r2'], 4),
            },
            "feature_importance": dict(zip(features, [round(x, 4) for x in feature_importances]))
        }
    }
    with open(os.path.join(models_dir, "metadata.json"), 'w') as f:
        json.dump(meta, f, indent=2)

    # ── Rapport MD pour le PFE ──
    generer_rapport_md(models_dir, patient_id, prenom, nom, label,
                       n_samples, features, metrics, feature_importances)

    return metrics


def train_patient(patient_id, prenom, nom):
    """Entraîne le modèle global + un modèle par profil horaire"""
    print(f"\n{'='*55}")
    print(f"  Patient {patient_id} — {prenom} {nom}")
    print(f"{'='*55}")

    df = load_data_patient(patient_id)
    print(f"✅ {len(df)} lignes chargées (données valides — offline exclues)")

    if len(df) < 20:
        print(f"⚠️  Pas assez de données ({len(df)} < 20) — patient ignoré")
        return False

    # ── Modèle global ──
    print(f"\n── Modèle global (tous profils confondus) ──")
    models_dir = get_models_dir(patient_id)
    df_g, features, le_moment, le_phase = build_features(df.copy())
    metrics = _train_and_save(df_g, features, le_moment, le_phase, models_dir,
                              patient_id, prenom, nom, "global")
    print(f"   Accuracy RF Classifier : {metrics['clf_accuracy']:.2%}")
    print(f"   MAE RF Regressor       : {metrics['reg_mae']:.1f} min")
    print(f"   Anomalies              : {metrics['iso_anomalies']}/{len(df_g)} ({metrics['iso_pct']:.1f}%)")
    print(f"   ✅ Sauvegardé dans {models_dir}")

    # ── Modèles par profil ──
    profils = get_profils_patient(patient_id)
    for profil_id, label in profils:
        df_profil = load_data_patient(patient_id, profil_id=profil_id)
        if len(df_profil) < 20:
            print(f"⚠️  Profil '{label}' ({len(df_profil)} lignes < 20) — ignoré")
            continue
        print(f"\n── Profil '{label}' (profil_id={profil_id}) — {len(df_profil)} lignes ──")
        models_dir_p = get_models_dir(patient_id, profil_id=profil_id)
        df_p, features_p, le_m_p, le_ph_p = build_features(df_profil.copy())
        metrics_p = _train_and_save(df_p, features_p, le_m_p, le_ph_p, models_dir_p,
                                    patient_id, prenom, nom, label)
        print(f"   Accuracy : {metrics_p['clf_accuracy']:.2%} | MAE : {metrics_p['reg_mae']:.1f} min")
        print(f"   ✅ Sauvegardé dans {models_dir_p}")

    return True


def train():
    """Entraîne les modèles pour TOUS les patients"""
    print("\n" + "="*55)
    print("  MEDICINE BOX — ENTRAÎNEMENT ML MULTI-PATIENTS")
    print("="*55)

    patients = get_all_patients()
    if not patients:
        print("❌ Aucun patient avec des données dans alertes_optimisation")
        return

    print(f"\n{len(patients)} patient(s) trouvé(s) : {[f'{p[1]} {p[2]}' for p in patients]}")

    success = 0
    for patient_id, prenom, nom in patients:
        if train_patient(patient_id, prenom, nom):
            success += 1

    print(f"\n{'='*55}")
    print(f"  ✅ ENTRAÎNEMENT TERMINÉ — {success}/{len(patients)} patients")
    print(f"  📄 Rapports MD disponibles dans : ml/models/patient_*/")
    print(f"     → À inclure dans l'Annexe E du rapport PFE")
    print("="*55 + "\n")


if __name__ == "__main__":
    train()
