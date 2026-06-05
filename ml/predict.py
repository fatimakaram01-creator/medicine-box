"""
Medicine Box — ML Predict
=========================
Charge les modèles entraînés et fait des prédictions pour un patient.

Retourne :
- risque_oubli     : float 0-100 (probabilité d'oubli prochaine prise)
- heure_optimale   : str "HH:MM" (heure optimale pour l'alerte)
- anomalie         : bool (la dernière prise est-elle anormale ?)
- niveau_risque    : str "Faible" / "Modéré" / "Élevé"
"""

import os
import sys
import json
import joblib
import numpy as np
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection

MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")


def models_exist():
    """Vérifie que les modèles sont entraînés"""
    required = ["isolation_forest.joblib", "rf_classifier.joblib", "rf_regressor.joblib", "metadata.json"]
    return all(os.path.exists(os.path.join(MODELS_DIR, f)) for f in required)


def load_models():
    """Charge les modèles depuis le disque"""
    iso = joblib.load(os.path.join(MODELS_DIR, "isolation_forest.joblib"))
    clf = joblib.load(os.path.join(MODELS_DIR, "rf_classifier.joblib"))
    reg = joblib.load(os.path.join(MODELS_DIR, "rf_regressor.joblib"))
    le_moment = joblib.load(os.path.join(MODELS_DIR, "le_moment.joblib"))
    le_phase = joblib.load(os.path.join(MODELS_DIR, "le_phase.joblib"))
    with open(os.path.join(MODELS_DIR, "metadata.json")) as f:
        meta = json.load(f)
    return iso, clf, reg, le_moment, le_phase, meta


def get_patient_context(patient_id):
    """
    Récupère le contexte actuel du patient :
    - moment prochain (matin/midi/soir)
    - heure prévue de la prochaine prise
    - historique récent (7 derniers jours)
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Prochaine prise en_attente aujourd'hui
        cursor.execute("""
            SELECT moment, heure_prevue
            FROM prises
            WHERE patient_id = %s AND statut = 'en_attente'
              AND DATE(heure_prevue) = CURRENT_DATE
            ORDER BY heure_prevue ASC
            LIMIT 1;
        """, (patient_id,))
        next_prise = cursor.fetchone()

        # Jour de la semaine (0=lundi ... 6=dimanche)
        jour_semaine = datetime.now().weekday()

        # Phase actuelle (découverte si < 8 jours de données)
        cursor.execute("""
            SELECT COUNT(*) FROM alertes_optimisation WHERE patient_id = %s;
        """, (patient_id,))
        n_data = cursor.fetchone()[0]
        phase = "decouverte" if n_data < 21 else "adapte"  # 7j × 3 moments

        # Taux d'observance 7 derniers jours
        cursor.execute("""
            SELECT
                COUNT(*) FILTER (WHERE statut = 'pris') AS pris,
                COUNT(*) FILTER (WHERE statut IN ('pris','manque')) AS total
            FROM prises
            WHERE patient_id = %s
              AND heure_prevue >= NOW() - INTERVAL '7 days';
        """, (patient_id,))
        obs = cursor.fetchone()
        observance_7j = (obs[0] / obs[1] * 100) if obs and obs[1] > 0 else 100

        cursor.close()
        return {
            "next_prise": next_prise,
            "jour_semaine": jour_semaine,
            "phase": phase,
            "observance_7j": observance_7j,
            "n_data": n_data
        }
    finally:
        conn.close()


def predict(patient_id=None):
    """
    Prédit le risque d'oubli et l'heure optimale d'alerte.
    Retourne un dict avec les résultats.
    """
    if not models_exist():
        return {
            "error": "Modèles non entraînés",
            "risque_oubli": None,
            "niveau_risque": "—",
            "heure_optimale": None,
            "anomalie": False
        }

    try:
        # Charger les modèles
        iso, clf, reg, le_moment, le_phase, meta = load_models()

        # Récupérer le patient
        if patient_id is None:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM patients LIMIT 1;")
            row = cursor.fetchone()
            cursor.close()
            conn.close()
            if not row:
                return {"error": "Aucun patient"}
            patient_id = row[0]

        # Contexte patient
        ctx = get_patient_context(patient_id)

        # Heure actuelle
        now = datetime.now()
        heure_alerte_min = now.hour * 60 + now.minute

        # Moment (matin/midi/soir) selon heure actuelle
        if heure_alerte_min < 11 * 60:
            moment = "matin"
        elif heure_alerte_min < 16 * 60:
            moment = "midi"
        else:
            moment = "soir"

        # Encoder les features
        try:
            moment_enc = le_moment.transform([moment])[0]
        except ValueError:
            moment_enc = 0

        try:
            phase_enc = le_phase.transform([ctx["phase"]])[0]
        except ValueError:
            phase_enc = 1

        is_weekend = 1 if ctx["jour_semaine"] >= 5 else 0
        heure_norm = heure_alerte_min / (24 * 60)

        X = np.array([[
            heure_alerte_min,
            ctx["jour_semaine"],
            moment_enc,
            phase_enc,
            is_weekend,
            heure_norm
        ]])

        # ── Prédiction 1 : anomalie ──
        anomalie_score = iso.decision_function(X)[0]
        anomalie = bool(iso.predict(X)[0] == -1)

        # ── Prédiction 2 : risque d'oubli ──
        proba_prise = clf.predict_proba(X)[0]
        # proba_prise[1] = probabilité de prise
        # risque_oubli = 1 - proba_prise
        risque_oubli = round((1 - proba_prise[1]) * 100, 1)

        # Ajuster avec l'observance réelle
        obs_factor = (100 - ctx["observance_7j"]) / 100
        risque_final = round(min(100, risque_oubli * 0.7 + obs_factor * 30), 1)

        # ── Prédiction 3 : délai optimal ──
        delai_optimal = reg.predict(X)[0]
        heure_alerte_optimale = now + timedelta(minutes=max(0, delai_optimal))
        heure_optimale_str = heure_alerte_optimale.strftime("%H:%M")

        # ── Niveau de risque ──
        if risque_final < 25:
            niveau = "Faible"
        elif risque_final < 60:
            niveau = "Modéré"
        else:
            niveau = "Élevé"

        return {
            "patient_id": patient_id,
            "risque_oubli": risque_final,
            "niveau_risque": niveau,
            "heure_optimale": heure_optimale_str,
            "anomalie": anomalie,
            "anomalie_score": round(float(anomalie_score), 3),
            "observance_7j": ctx["observance_7j"],
            "moment": moment,
            "phase": ctx["phase"],
            "trained_at": meta.get("trained_at", "—")
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
