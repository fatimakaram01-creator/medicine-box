"""
Medicine Box — ML Training v2 (Multi-patients)
===============================================
Entraîne UN set de modèles PAR patient.
Chaque patient a son propre dossier : ml/models/patient_{id}/

3 modèles par patient :
1. Isolation Forest    → détection d'anomalies
2. RF Classifier       → prédiction risque d'oubli
3. RF Regressor        → heure optimale d'alerte
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
from sklearn.metrics import mean_absolute_error

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
    """
    Charge les données d'un patient spécifique.
    - Filtre cause_manque != 'offline' → exclut les faux manques dus à une panne
    - Si profil_id fourni → données de ce profil uniquement
    - Sinon → toutes les données du patient (modèle global)
    """
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
    # profil_id → le ML distingue automatiquement Standard / Ramadan / autres
    features = ['heure_alerte_min', 'jour_semaine', 'moment_enc', 'phase_enc', 'is_weekend', 'heure_norm', 'profil_id']
    return df, features, le_moment, le_phase


def train_patient(patient_id, prenom, nom):
    """
    Entraîne les modèles pour un patient :
    1. Modèle global (toutes données confondues)
    2. Un modèle par profil si assez de données (≥20 lignes par profil)
    """
    print(f"\n{'='*55}")
    print(f"  Patient {patient_id} — {prenom} {nom}")
    print(f"{'='*55}")

    df = load_data_patient(patient_id)
    print(f"✅ {len(df)} lignes chargées (données valides — offline exclues)")

    if len(df) < 20:
        print(f"⚠️  Pas assez de données ({len(df)} < 20) — patient ignoré")
        return False

    # ── Entraînement modèle global ──
    print("\n── Modèle global (tous profils) ──")
    models_dir = get_models_dir(patient_id)
    df, features, le_moment, le_phase = build_features(df)

    # ── Isolation Forest ──
    print("\n── Isolation Forest ──")
    X = df[features].dropna()
    iso = IsolationForest(n_estimators=100, contamination=0.1, random_state=42)
    iso.fit(X)
    n_anomalies = (iso.predict(X) == -1).sum()
    print(f"   Anomalies : {n_anomalies}/{len(X)} ({100*n_anomalies/len(X):.1f}%)")
    joblib.dump(iso, os.path.join(models_dir, "isolation_forest.joblib"))
    print(f"   ✅ Sauvegardé")

    # ── RF Classifier ──
    print("\n── RF Classifier (risque d'oubli) ──")
    df_clf = df[features + ['alerte_efficace']].dropna()
    X_clf = df_clf[features]
    y_clf = df_clf['alerte_efficace'].astype(int)
    print(f"   Distribution : {y_clf.value_counts().to_dict()}")
    X_tr, X_te, y_tr, y_te = train_test_split(X_clf, y_clf, test_size=0.2, random_state=42)
    clf = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42, class_weight='balanced')
    clf.fit(X_tr, y_tr)
    acc = (clf.predict(X_te) == y_te).mean()
    print(f"   Accuracy : {acc:.2%}")
    joblib.dump(clf, os.path.join(models_dir, "rf_classifier.joblib"))
    print(f"   ✅ Sauvegardé")

    # ── RF Regressor ──
    print("\n── RF Regressor (heure optimale) ──")
    df_reg = df[features + ['delai_minutes']].dropna()
    df_reg = df_reg[df_reg['delai_minutes'] >= 0]
    X_reg = df_reg[features]
    y_reg = df_reg['delai_minutes']
    X_tr, X_te, y_tr, y_te = train_test_split(X_reg, y_reg, test_size=0.2, random_state=42)
    reg = RandomForestRegressor(n_estimators=100, max_depth=6, random_state=42)
    reg.fit(X_tr, y_tr)
    mae = mean_absolute_error(y_te, reg.predict(X_te))
    print(f"   MAE : {mae:.1f} minutes")
    joblib.dump(reg, os.path.join(models_dir, "rf_regressor.joblib"))
    print(f"   ✅ Sauvegardé")

    # ── Encodeurs + métadonnées ──
    joblib.dump(le_moment, os.path.join(models_dir, "le_moment.joblib"))
    joblib.dump(le_phase, os.path.join(models_dir, "le_phase.joblib"))
    meta = {
        "patient_id": patient_id,
        "prenom": prenom,
        "nom": nom,
        "trained_at": datetime.now().isoformat(),
        "n_samples": len(df),
        "features": features,
        "moment_classes": list(le_moment.classes_),
        "phase_classes": list(le_phase.classes_),
        "stats": {
            "heure_alerte_mean": float(df['heure_alerte_min'].astype(float).mean()),
            "heure_alerte_std": float(df['heure_alerte_min'].astype(float).std()),
            "delai_mean": float(df['delai_minutes'].dropna().astype(float).mean()),
            "alerte_efficace_rate": float(df['alerte_efficace'].mean()),
        }
    }
    with open(os.path.join(models_dir, "metadata.json"), 'w') as f:
        json.dump(meta, f, indent=2)
    print(f"\n✅ Modèles global patient_{patient_id} sauvegardés dans {models_dir}")

    # ── Entraînement par profil ──
    profils = get_profils_patient(patient_id)
    for profil_id, label in profils:
        df_profil = load_data_patient(patient_id, profil_id=profil_id)
        if len(df_profil) < 20:
            print(f"⚠️  Profil '{label}' ({len(df_profil)} lignes < 20) — ignoré")
            continue
        print(f"\n── Profil '{label}' (profil_id={profil_id}) — {len(df_profil)} lignes ──")
        models_dir_p = get_models_dir(patient_id, profil_id=profil_id)
        df_profil, features_p, le_m_p, le_ph_p = build_features(df_profil)
        _train_and_save(df_profil, features_p, le_m_p, le_ph_p, models_dir_p, patient_id, prenom, nom, label)
        print(f"✅ Modèles profil '{label}' sauvegardés dans {models_dir_p}")

    return True


def _train_and_save(df, features, le_moment, le_phase, models_dir, patient_id, prenom, nom, label="global"):
    """Entraîne et sauvegarde les 3 modèles dans un dossier donné"""
    from sklearn.ensemble import IsolationForest, RandomForestClassifier, RandomForestRegressor
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import mean_absolute_error

    X = df[features].dropna()
    iso = IsolationForest(n_estimators=100, contamination=0.1, random_state=42)
    iso.fit(X)
    joblib.dump(iso, os.path.join(models_dir, "isolation_forest.joblib"))

    df_clf = df[features + ['alerte_efficace']].dropna()
    X_clf, y_clf = df_clf[features], df_clf['alerte_efficace'].astype(int)
    X_tr, X_te, y_tr, y_te = train_test_split(X_clf, y_clf, test_size=0.2, random_state=42)
    clf = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42, class_weight='balanced')
    clf.fit(X_tr, y_tr)
    joblib.dump(clf, os.path.join(models_dir, "rf_classifier.joblib"))

    df_reg = df[features + ['delai_minutes']].dropna()
    df_reg = df_reg[df_reg['delai_minutes'] >= 0]
    X_reg, y_reg = df_reg[features], df_reg['delai_minutes']
    X_tr, X_te, y_tr, y_te = train_test_split(X_reg, y_reg, test_size=0.2, random_state=42)
    reg = RandomForestRegressor(n_estimators=100, max_depth=6, random_state=42)
    reg.fit(X_tr, y_tr)
    joblib.dump(reg, os.path.join(models_dir, "rf_regressor.joblib"))

    joblib.dump(le_moment, os.path.join(models_dir, "le_moment.joblib"))
    joblib.dump(le_phase, os.path.join(models_dir, "le_phase.joblib"))
    meta = {
        "patient_id": patient_id, "prenom": prenom, "nom": nom,
        "label": label,
        "trained_at": datetime.now().isoformat(),
        "n_samples": len(df), "features": features,
        "moment_classes": list(le_moment.classes_),
        "phase_classes": list(le_phase.classes_),
    }
    with open(os.path.join(models_dir, "metadata.json"), 'w') as f:
        json.dump(meta, f, indent=2)


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
    print(f"  Modèles dans : ml/models/patient_*/")
    print("="*55 + "\n")


if __name__ == "__main__":
    train()
