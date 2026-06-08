"""
Medicine Box — ML Predict v2 (Multi-patients)
=============================================
Charge les modèles du patient spécifique depuis ml/models/patient_{id}/
"""

import os
import sys
import json
import joblib
import numpy as np
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection

MODELS_BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")


def get_models_dir(patient_id, profil_id=None):
    if profil_id:
        return os.path.join(MODELS_BASE_DIR, f"patient_{patient_id}", f"profil_{profil_id}")
    return os.path.join(MODELS_BASE_DIR, f"patient_{patient_id}", "global")


def models_exist(patient_id=None, profil_id=None):
    """Vérifie que les modèles existent pour ce patient/profil"""
    if patient_id is None:
        if not os.path.exists(MODELS_BASE_DIR):
            return False
        for d in os.listdir(MODELS_BASE_DIR):
            if d.startswith("patient_"):
                return True
        return False

    models_dir = get_models_dir(patient_id, profil_id)
    required = ["isolation_forest.joblib", "rf_classifier.joblib", "rf_regressor.joblib",
                "le_moment.joblib", "le_phase.joblib", "metadata.json"]
    return all(os.path.exists(os.path.join(models_dir, f)) for f in required)


def load_models(patient_id, profil_id=None):
    """
    Charge les modèles du patient.
    Priorité : profil actif → global patient → erreur
    """
    # Essayer le modèle du profil actif d'abord
    if profil_id and models_exist(patient_id, profil_id):
        models_dir = get_models_dir(patient_id, profil_id)
        source = f"profil_{profil_id}"
    # Fallback → modèle global du patient
    elif models_exist(patient_id):
        models_dir = get_models_dir(patient_id)
        source = "global"
    else:
        raise FileNotFoundError(f"Aucun modèle trouvé pour patient_{patient_id}")

    iso = joblib.load(os.path.join(models_dir, "isolation_forest.joblib"))
    clf = joblib.load(os.path.join(models_dir, "rf_classifier.joblib"))
    reg = joblib.load(os.path.join(models_dir, "rf_regressor.joblib"))
    le_moment = joblib.load(os.path.join(models_dir, "le_moment.joblib"))
    le_phase = joblib.load(os.path.join(models_dir, "le_phase.joblib"))
    with open(os.path.join(models_dir, "metadata.json")) as f:
        meta = json.load(f)
    meta["source_modele"] = source
    return iso, clf, reg, le_moment, le_phase, meta


def get_patient_context(patient_id):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT moment, heure_prevue FROM prises
            WHERE patient_id = %s AND statut = 'en_attente'
              AND DATE(heure_prevue) = CURRENT_DATE
            ORDER BY heure_prevue ASC LIMIT 1;
        """, (patient_id,))
        next_prise = cursor.fetchone()

        cursor.execute("""
            SELECT COUNT(*) FROM alertes_optimisation WHERE patient_id = %s;
        """, (patient_id,))
        n_data = cursor.fetchone()[0]
        phase = "decouverte" if n_data < 21 else "adapte"

        # Observance 7j — exclure cause_manque = 'offline'
        cursor.execute("""
            SELECT
                COUNT(*) FILTER (WHERE statut = 'pris') AS pris,
                COUNT(*) FILTER (WHERE statut IN ('pris','manque')
                    AND (cause_manque IS NULL OR cause_manque != 'offline')) AS total
            FROM prises
            WHERE patient_id = %s AND heure_prevue >= NOW() - INTERVAL '7 days';
        """, (patient_id,))
        obs = cursor.fetchone()
        observance_7j = (obs[0] / obs[1] * 100) if obs and obs[1] > 0 else 100

        # Profil actif → pour charger le bon modèle
        cursor.execute("""
            SELECT id FROM intervalles_profils WHERE actif = TRUE ORDER BY id DESC LIMIT 1;
        """)
        row_profil = cursor.fetchone()
        profil_actif_id = row_profil[0] if row_profil else None

        cursor.close()
        return {
            "next_prise": next_prise,
            "jour_semaine": datetime.now().weekday(),
            "phase": phase,
            "observance_7j": observance_7j,
            "n_data": n_data,
            "profil_actif_id": profil_actif_id
        }
    finally:
        conn.close()


def predict(patient_id=None):
    """Prédit le risque d'oubli pour un patient spécifique"""
    # Récupérer patient_id si non fourni
    if patient_id is None:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM patients ORDER BY id LIMIT 1;")
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if not row:
            return {"error": "Aucun patient", "risque_oubli": None, "niveau_risque": "—"}
        patient_id = row[0]

    if not models_exist(patient_id) and not models_exist():
        return {
            "error": "Modèles non entraînés — lancez python3 ml/train.py",
            "risque_oubli": None,
            "niveau_risque": "—",
            "heure_optimale": None,
            "anomalie": False
        }

    try:
        ctx = get_patient_context(patient_id)
        profil_actif_id = ctx.get("profil_actif_id")
        iso, clf, reg, le_moment, le_phase, meta = load_models(patient_id, profil_id=profil_actif_id)
        now = datetime.now()
        heure_alerte_min = now.hour * 60 + now.minute

        moment = "matin" if heure_alerte_min < 660 else "midi" if heure_alerte_min < 960 else "soir"

        try:
            moment_enc = le_moment.transform([moment])[0]
        except:
            moment_enc = 0
        try:
            phase_enc = le_phase.transform([ctx["phase"]])[0]
        except:
            phase_enc = 1

        is_weekend = 1 if ctx["jour_semaine"] >= 5 else 0
        heure_norm = heure_alerte_min / (24 * 60)
        profil_id_val = profil_actif_id or 0

        X = np.array([[heure_alerte_min, ctx["jour_semaine"], moment_enc, phase_enc, is_weekend, heure_norm, profil_id_val]])

        anomalie_score = iso.decision_function(X)[0]
        anomalie = bool(iso.predict(X)[0] == -1)

        proba_prise = clf.predict_proba(X)[0]
        risque_oubli = round((1 - proba_prise[1]) * 100, 1)
        obs_factor = (100 - ctx["observance_7j"]) / 100
        risque_final = round(min(100, risque_oubli * 0.7 + obs_factor * 30), 1)

        delai_optimal = reg.predict(X)[0]
        heure_optimale = (now + timedelta(minutes=max(0, delai_optimal))).strftime("%H:%M")

        niveau = "Faible" if risque_final < 25 else "Modéré" if risque_final < 60 else "Élevé"

        return {
            "patient_id": patient_id,
            "risque_oubli": risque_final,
            "niveau_risque": niveau,
            "heure_optimale": heure_optimale,
            "anomalie": anomalie,
            "anomalie_score": round(float(anomalie_score), 3),
            "observance_7j": ctx["observance_7j"],
            "moment": moment,
            "phase": ctx["phase"],
            "profil_actif_id": profil_actif_id,
            "trained_at": meta.get("trained_at", "—"),
            "modele": meta.get("source_modele", "global")
        }

    except Exception as e:
        return {
            "error": str(e),
            "risque_oubli": None,
            "niveau_risque": "—",
            "heure_optimale": None,
            "anomalie": False
        }


if __name__ == "__main__":
    result = predict()
    print("\n── Résultat ML ──")
    for k, v in result.items():
        print(f"  {k}: {v}")
