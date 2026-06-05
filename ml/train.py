"""
Medicine Box — ML Training
==========================
3 modèles entraînés sur les données de alertes_optimisation :

1. Isolation Forest    → détection d'anomalies (prises hors habitudes)
2. RF Classifier       → prédiction risque d'oubli (0/1)
3. RF Regressor        → estimation heure optimale d'alerte (délai en min)

Les modèles sont sauvegardés dans ml/models/ via joblib.
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
from sklearn.metrics import classification_report, mean_absolute_error

# ── Dossier modèles ──
MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
os.makedirs(MODELS_DIR, exist_ok=True)


# ══════════════════════════════════════════════════════════════
# 1. CHARGEMENT DES DONNÉES
# ══════════════════════════════════════════════════════════════

def load_data():
    """Charge alertes_optimisation depuis Supabase"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                ao.patient_id,
                ao.moment,
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
            ORDER BY ao.id ASC;
        """)
        rows = cursor.fetchall()
        cursor.close()

        df = pd.DataFrame(rows, columns=[
            'patient_id', 'moment', 'heure_alerte_min', 'jour_semaine',
            'heure_prise_min', 'delai_minutes', 'alerte_efficace', 'phase'
        ])
        print(f"✅ {len(df)} lignes chargées depuis alertes_optimisation")
        return df
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
# 2. FEATURE ENGINEERING
# ══════════════════════════════════════════════════════════════

def build_features(df):
    """Construit les features pour les 3 modèles"""
    # Encoder le moment (matin=0, midi=1, soir=2)
    le_moment = LabelEncoder()
    df['moment_enc'] = le_moment.fit_transform(df['moment'])

    # Encoder la phase (decouverte=0, adapte=1)
    le_phase = LabelEncoder()
    df['phase_enc'] = le_phase.fit_transform(df['phase'])

    # Weekend (0=semaine, 1=weekend)
    df['is_weekend'] = df['jour_semaine'].apply(lambda x: 1 if x >= 5 else 0)

    # Heure alerte normalisée (0-1)
    df['heure_norm'] = df['heure_alerte_min'] / (24 * 60)

    # Features communes
    features = ['heure_alerte_min', 'jour_semaine', 'moment_enc', 'phase_enc', 'is_weekend', 'heure_norm']

    return df, features, le_moment, le_phase


# ══════════════════════════════════════════════════════════════
# 3. MODÈLE 1 — ISOLATION FOREST (anomalies)
# ══════════════════════════════════════════════════════════════

def train_isolation_forest(df, features):
    """
    Détecte les prises anormales (hors habitudes du patient).
    Anomalie = prise très en dehors de l'heure habituelle.
    """
    print("\n── Isolation Forest ──")
    X = df[features].dropna()

    model = IsolationForest(
        n_estimators=100,
        contamination=0.1,   # 10% d'anomalies attendues
        random_state=42
    )
    model.fit(X)

    scores = model.decision_function(X)
    predictions = model.predict(X)  # -1=anomalie, 1=normal

    n_anomalies = (predictions == -1).sum()
    print(f"   Anomalies détectées : {n_anomalies}/{len(X)} ({100*n_anomalies/len(X):.1f}%)")

    path = os.path.join(MODELS_DIR, "isolation_forest.joblib")
    joblib.dump(model, path)
    print(f"   ✅ Sauvegardé : {path}")
    return model


# ══════════════════════════════════════════════════════════════
# 4. MODÈLE 2 — RF CLASSIFIER (risque d'oubli)
# ══════════════════════════════════════════════════════════════

def train_rf_classifier(df, features):
    """
    Prédit si une alerte sera efficace (prise effectuée = 1) ou non (oubli = 0).
    Target : alerte_efficace (bool)
    """
    print("\n── Random Forest Classifier (risque d'oubli) ──")

    df_clean = df[features + ['alerte_efficace']].dropna()
    X = df_clean[features]
    y = df_clean['alerte_efficace'].astype(int)

    print(f"   Distribution : {y.value_counts().to_dict()}")

    if len(X) < 10:
        print("   ⚠️  Pas assez de données pour entraîner le classifier")
        return None

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    model = RandomForestClassifier(
        n_estimators=100,
        max_depth=5,
        random_state=42,
        class_weight='balanced'
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    accuracy = (y_pred == y_test).mean()
    print(f"   Accuracy : {accuracy:.2%}")
    print(f"   Feature importances :")
    for feat, imp in sorted(zip(features, model.feature_importances_), key=lambda x: -x[1]):
        print(f"     {feat}: {imp:.3f}")

    path = os.path.join(MODELS_DIR, "rf_classifier.joblib")
    joblib.dump(model, path)
    print(f"   ✅ Sauvegardé : {path}")
    return model


# ══════════════════════════════════════════════════════════════
# 5. MODÈLE 3 — RF REGRESSOR (heure optimale d'alerte)
# ══════════════════════════════════════════════════════════════

def train_rf_regressor(df, features):
    """
    Prédit le délai optimal entre l'alerte et la prise (en minutes).
    Target : delai_minutes (int)
    Utilisé pour optimiser l'heure d'envoi des alertes.
    """
    print("\n── Random Forest Regressor (heure optimale alerte) ──")

    df_clean = df[features + ['delai_minutes']].dropna()
    df_clean = df_clean[df_clean['delai_minutes'] >= 0]  # garder délais valides

    X = df_clean[features]
    y = df_clean['delai_minutes']

    if len(X) < 10:
        print("   ⚠️  Pas assez de données pour entraîner le regresseur")
        return None

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    model = RandomForestRegressor(
        n_estimators=100,
        max_depth=6,
        random_state=42
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    mae = mean_absolute_error(y_test, y_pred)
    print(f"   MAE : {mae:.1f} minutes")
    print(f"   Délai moyen prédit : {y_pred.mean():.1f} min")

    path = os.path.join(MODELS_DIR, "rf_regressor.joblib")
    joblib.dump(model, path)
    print(f"   ✅ Sauvegardé : {path}")
    return model


# ══════════════════════════════════════════════════════════════
# 6. SAUVEGARDE DES MÉTADONNÉES
# ══════════════════════════════════════════════════════════════

def save_metadata(df, le_moment, le_phase, features):
    """Sauvegarde les encodeurs et stats pour predict.py"""
    meta = {
        "trained_at": datetime.now().isoformat(),
        "n_samples": len(df),
        "features": features,
        "moment_classes": list(le_moment.classes_),
        "phase_classes": list(le_phase.classes_),
        "stats": {
            "heure_alerte_mean": float(df['heure_alerte_min'].mean()),
            "heure_alerte_std": float(df['heure_alerte_min'].std()),
            "delai_mean": float(df['delai_minutes'].dropna().mean()),
            "alerte_efficace_rate": float(df['alerte_efficace'].mean()),
        }
    }

    path = os.path.join(MODELS_DIR, "metadata.json")
    with open(path, 'w') as f:
        json.dump(meta, f, indent=2)
    print(f"\n   ✅ Métadonnées sauvegardées : {path}")

    # Sauvegarder les encodeurs
    joblib.dump(le_moment, os.path.join(MODELS_DIR, "le_moment.joblib"))
    joblib.dump(le_phase, os.path.join(MODELS_DIR, "le_phase.joblib"))


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def train():
    print("\n" + "="*55)
    print("  MEDICINE BOX — ENTRAÎNEMENT ML")
    print("="*55)

    df = load_data()

    if len(df) < 20:
        print("❌ Pas assez de données (minimum 20 lignes)")
        return

    df, features, le_moment, le_phase = build_features(df)

    # Convertir les colonnes Decimal en float
    for col in ['heure_alerte_min', 'heure_prise_min', 'delai_minutes']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    train_isolation_forest(df, features)
    train_rf_classifier(df, features)
    train_rf_regressor(df, features)
    save_metadata(df, le_moment, le_phase, features)

    print("\n" + "="*55)
    print("  ✅ ENTRAÎNEMENT TERMINÉ")
    print(f"  Modèles dans : ml/models/")
    print("="*55 + "\n")


if __name__ == "__main__":
    train()
