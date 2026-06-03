# api/routes.py
# ─────────────────────────────────────────────────────────────
# Toutes les routes de l'API Medicine Box
#
# ROUTES LECTURE (GET) :
#   /                  → health check
#   /patients          → liste patients
#   /prises/today      → prises du jour
#   /stock             → stock médicaments
#   /alertes           → 10 dernières alertes
#   /observance        → observance 7 derniers jours
#   /prises/historique → historique complet des prises (30j)
#   /config/status     → état actuel des modes (remplissage, buzzer, ramadan, hospitalisation)
#
# ROUTES ACTION (POST) :
#   /config/remplissage   → toggle mode remplissage ON/OFF → MQTT
#   /config/buzzer        → toggle buzzer ON/OFF → MQTT
#   /config/ramadan       → activer/désactiver mode Ramadan
#   /config/hospitalisation → activer/désactiver mode hospitalisation
#   /config/changement-rx → nouvelle prescription
# ─────────────────────────────────────────────────────────────

import json
from datetime import datetime, timedelta
from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional
from db.database import get_connection

router = APIRouter()


# ═══════════════════════════════════════════════════════
# MODÈLES PYDANTIC — validation des données entrantes
# ═══════════════════════════════════════════════════════

class ConfigToggle(BaseModel):
    """Pour les toggles ON/OFF simples (remplissage, buzzer)"""
    enabled: bool

class ConfigRamadan(BaseModel):
    """Mode Ramadan — ville pour calculer fajr/iftar via NTP"""
    enabled: bool
    ville: Optional[str] = None  # ex: "Agadir", "Casablanca", "Marrakech"

class ConfigHospitalisation(BaseModel):
    """Mode hospitalisation — date de retour prévue"""
    enabled: bool
    date_retour: Optional[str] = None  # format "YYYY-MM-DD"

class ChangementRx(BaseModel):
    """Changement de prescription par le médecin"""
    medicament: str        # ex: "Doliprane"
    dosage: str            # ex: "500mg"
    frequence: int         # nombre de prises par jour (1, 2 ou 3)
    prescrit_par: str      # ex: "Dr. Saidi"
    duree_jours: int       # durée de la nouvelle prescription


# ═══════════════════════════════════════════════════════
# VARIABLE GLOBALE — état des modes
# En production ce serait en BDD, mais pour le prototype
# on garde en mémoire (reset au redémarrage du serveur)
# ═══════════════════════════════════════════════════════

config_state = {
    "system_on": False,          # système OFF par défaut — le patient doit l'activer
    "esp32_connected": False,    # true quand l'ESP32 envoie un statut "online"
    "remplissage": False,
    "buzzer": True,              # buzzer activé par défaut
    "ramadan": False,
    "ramadan_ville": None,
    "hospitalisation": False,
}


# ═══════════════════════════════════════════════════════
# ROUTE 1 : Health Check
# ═══════════════════════════════════════════════════════
@router.get("/")
def home():
    return {"message": "Medicine Box API fonctionne !"}


# ═══════════════════════════════════════════════════════
# ROUTE 2 : Liste des patients
# ═══════════════════════════════════════════════════════
@router.get("/patients")
def get_patients():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, nom, prenom, medecin FROM patients;")
        rows = cursor.fetchall()
        cursor.close()
        return [
            {"id": r[0], "nom": r[1], "prenom": r[2], "medecin": r[3]}
            for r in rows
        ]
    except Exception as e:
        return {"error": str(e), "patients": []}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════
# ROUTE 3 : Prises du jour
# ═══════════════════════════════════════════════════════
@router.get("/prises/today")
def get_prises_today():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM patients LIMIT 1;")
        row = cursor.fetchone()
        if not row:
            cursor.close()
            return {"error": "Aucun patient trouvé", "count": 0, "total": 0, "prises": []}
        patient_id = row[0]

        cursor.execute("""
            SELECT moment, heure_prevue, heure_reelle, statut,
                   poids_avant, poids_apres
            FROM prises
            WHERE patient_id = %s
              AND heure_prevue::date = CURRENT_DATE
            ORDER BY heure_prevue;
        """, (patient_id,))
        rows = cursor.fetchall()
        cursor.close()

        return {
            "count": len([r for r in rows if r[3] == 'pris']),
            "total": len(rows),
            "prises": [
                {
                    "moment": r[0],
                    "heure_prevue": str(r[1]),
                    "heure_reelle": str(r[2]) if r[2] else None,
                    "statut": r[3],
                    "poids_avant": r[4],
                    "poids_apres": r[5]
                }
                for r in rows
            ]
        }
    except Exception as e:
        return {"error": str(e), "count": 0, "total": 0, "prises": []}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════
# ROUTE 4 : Stock de médicaments
# ═══════════════════════════════════════════════════════
@router.get("/stock")
def get_stock():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM patients LIMIT 1;")
        row = cursor.fetchone()
        if not row:
            cursor.close()
            return {"error": "Aucun patient trouvé"}
        patient_id = row[0]

        cursor.execute("""
            SELECT s.quantite, s.seuil_alerte, m.nom, m.dosage
            FROM stock s
            JOIN medicaments m ON s.medicament_id = m.id
            WHERE s.patient_id = %s;
        """, (patient_id,))
        row = cursor.fetchone()
        if not row:
            cursor.close()
            return {"error": "Aucun stock trouvé"}
        cursor.close()

        return {
            "quantite": row[0],
            "seuil_alerte": row[1],
            "medicament": row[2],
            "dosage": row[3],
            "alerte": row[0] <= row[1]
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════
# ROUTE 5 : Dernières alertes
# ═══════════════════════════════════════════════════════
@router.get("/alertes")
def get_alertes():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM patients LIMIT 1;")
        row = cursor.fetchone()
        if not row:
            cursor.close()
            return {"error": "Aucun patient trouvé", "alertes": []}
        patient_id = row[0]

        cursor.execute("""
            SELECT type, message, created_at, lu
            FROM alertes
            WHERE patient_id = %s
            ORDER BY created_at DESC
            LIMIT 10;
        """, (patient_id,))
        rows = cursor.fetchall()
        cursor.close()

        return [
            {
                "type": r[0],
                "message": r[1],
                "created_at": str(r[2]),
                "lu": r[3]
            }
            for r in rows
        ]
    except Exception as e:
        return {"error": str(e), "alertes": []}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════
# ROUTE 6 : Observance 7 jours (NOUVELLE)
# But : calculer le % d'observance sur les 7 derniers jours
# + détail par jour (3/3, 2/3, etc.)
# Utilisé par le dashboard médecin
# ═══════════════════════════════════════════════════════
@router.get("/observance")
def get_observance():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM patients LIMIT 1;")
        row = cursor.fetchone()
        if not row:
            cursor.close()
            return {"error": "Aucun patient trouvé"}
        patient_id = row[0]

        # Prises des 7 derniers jours
        cursor.execute("""
            SELECT heure_prevue::date as jour, statut
            FROM prises
            WHERE patient_id = %s
              AND heure_prevue::date >= CURRENT_DATE - INTERVAL '7 days'
              AND heure_prevue::date <= CURRENT_DATE
            ORDER BY heure_prevue;
        """, (patient_id,))
        rows = cursor.fetchall()
        cursor.close()

        # Calculer par jour
        jours = {}
        for r in rows:
            jour = str(r[0])
            if jour not in jours:
                jours[jour] = {"total": 0, "pris": 0}
            jours[jour]["total"] += 1
            if r[1] == 'pris':
                jours[jour]["pris"] += 1

        # Calcul global
        total_global = sum(j["total"] for j in jours.values())
        pris_global = sum(j["pris"] for j in jours.values())
        pourcentage = round((pris_global / total_global) * 100) if total_global > 0 else 0

        return {
            "pourcentage": pourcentage,
            "total": total_global,
            "pris": pris_global,
            "par_jour": jours
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════
# ROUTE 7 : Historique des prises (NOUVELLE)
# But : 30 derniers jours de prises pour l'onglet historique
# ═══════════════════════════════════════════════════════
@router.get("/prises/historique")
def get_prises_historique():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM patients LIMIT 1;")
        row = cursor.fetchone()
        if not row:
            cursor.close()
            return []
        patient_id = row[0]

        cursor.execute("""
            SELECT moment, heure_prevue, heure_reelle, statut,
                   poids_avant, poids_apres
            FROM prises
            WHERE patient_id = %s
              AND heure_prevue::date >= CURRENT_DATE - INTERVAL '30 days'
            ORDER BY heure_prevue DESC
            LIMIT 50;
        """, (patient_id,))
        rows = cursor.fetchall()
        cursor.close()

        return [
            {
                "moment": r[0],
                "heure_prevue": str(r[1]),
                "heure_reelle": str(r[2]) if r[2] else None,
                "statut": r[3],
                "poids_avant": r[4],
                "poids_apres": r[5]
            }
            for r in rows
        ]
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════
# ROUTE 8 : État des modes (NOUVELLE)
# But : l'app lit l'état actuel des toggles
# ═══════════════════════════════════════════════════════
@router.get("/config/status")
def get_config_status():
    return config_state


# ═══════════════════════════════════════════════════════
# ROUTE 9 : Toggle mode remplissage (NOUVELLE)
# But : activer/désactiver le mode remplissage
# → Publie sur MQTT medicinebox/config
# → L'ESP32 reçoit et désactive la détection HX711
# ═══════════════════════════════════════════════════════
@router.post("/config/remplissage")
def toggle_remplissage(body: ConfigToggle, request: Request):
    config_state["remplissage"] = body.enabled

    # Publier sur MQTT pour que l'ESP32 reçoive
    mqtt_client = getattr(request.app.state, 'mqtt_client', None)
    if mqtt_client and mqtt_client.is_connected():
        message = json.dumps({
            "mode": "remplissage",
            "enabled": body.enabled
        })
        mqtt_client.publish("medicinebox/config", message)

    action = "activé" if body.enabled else "désactivé"
    return {"status": "ok", "message": f"Mode remplissage {action}"}


# ═══════════════════════════════════════════════════════
# ROUTE 10 : Toggle buzzer (NOUVELLE)
# But : activer/désactiver le buzzer depuis l'app
# → Publie sur MQTT medicinebox/config
# → L'ESP32 ignore les buzzer_on si buzzer désactivé
# ═══════════════════════════════════════════════════════
@router.post("/config/buzzer")
def toggle_buzzer(body: ConfigToggle, request: Request):
    config_state["buzzer"] = body.enabled

    mqtt_client = getattr(request.app.state, 'mqtt_client', None)
    if mqtt_client and mqtt_client.is_connected():
        message = json.dumps({
            "mode": "buzzer",
            "enabled": body.enabled
        })
        mqtt_client.publish("medicinebox/config", message)

    action = "activé" if body.enabled else "désactivé"
    return {"status": "ok", "message": f"Buzzer {action}"}


# ═══════════════════════════════════════════════════════
# ROUTE 11 : Mode Ramadan (NOUVELLE)
# But : décaler les horaires de prise pour le Ramadan
#   - Matin → avant le fajr (shour)
#   - Midi → supprimé (jeûne)
#   - Soir → après l'iftar
# La ville est utilisée pour calculer fajr/iftar
# Pour le prototype : horaires approximatifs par ville
# En production : API Aladhan pour les horaires exacts
# ═══════════════════════════════════════════════════════
@router.post("/config/ramadan")
def toggle_ramadan(body: ConfigRamadan, request: Request):
    config_state["ramadan"] = body.enabled
    config_state["ramadan_ville"] = body.ville if body.enabled else None

    # Horaires approximatifs fajr/iftar par ville marocaine
    # En production → appeler l'API Aladhan avec la ville
    horaires_ramadan = {
        "Agadir":      {"fajr": "04:30", "iftar": "19:45"},
        "Casablanca":  {"fajr": "04:20", "iftar": "19:40"},
        "Marrakech":   {"fajr": "04:25", "iftar": "19:42"},
        "Rabat":       {"fajr": "04:18", "iftar": "19:38"},
        "Fes":         {"fajr": "04:15", "iftar": "19:35"},
        "Tanger":      {"fajr": "04:10", "iftar": "19:30"},
    }

    mqtt_client = getattr(request.app.state, 'mqtt_client', None)

    if body.enabled and body.ville:
        horaires = horaires_ramadan.get(body.ville, {"fajr": "04:30", "iftar": "19:45"})

        # Publier les nouveaux horaires sur MQTT
        if mqtt_client and mqtt_client.is_connected():
            message = json.dumps({
                "mode": "ramadan",
                "enabled": True,
                "ville": body.ville,
                "fajr": horaires["fajr"],
                "iftar": horaires["iftar"]
            })
            mqtt_client.publish("medicinebox/config", message)

        creer_alerte_systeme("contexte", f"Mode Ramadan activé — {body.ville}")
        return {
            "status": "ok",
            "message": f"Mode Ramadan activé — {body.ville}",
            "horaires": {
                "shour": f"Avant {horaires['fajr']}",
                "midi": "Suspendu (jeûne)",
                "iftar": f"Après {horaires['iftar']}"
            }
        }
    else:
        # Désactiver → retour aux horaires normaux
        if mqtt_client and mqtt_client.is_connected():
            message = json.dumps({
                "mode": "ramadan",
                "enabled": False
            })
            mqtt_client.publish("medicinebox/config", message)

        creer_alerte_systeme("contexte", "Mode Ramadan désactivé — retour aux horaires normaux")
        return {"status": "ok", "message": "Mode Ramadan désactivé — horaires normaux"}


# ═══════════════════════════════════════════════════════
# ROUTE 12 : Mode hospitalisation (NOUVELLE)
# But : suspendre complètement le système
# → Aucune alerte, aucun buzzer, aucune détection
# → Le médecin voit "patient hospitalisé du X au Y"
# → Quand le patient revient, il désactive le mode
# ═══════════════════════════════════════════════════════
@router.post("/config/hospitalisation")
def toggle_hospitalisation(body: ConfigHospitalisation, request: Request):
    config_state["hospitalisation"] = body.enabled
    config_state["hospitalisation_date_retour"] = body.date_retour if body.enabled else None

    mqtt_client = getattr(request.app.state, 'mqtt_client', None)
    if mqtt_client and mqtt_client.is_connected():
        message = json.dumps({
            "mode": "hospitalisation",
            "enabled": body.enabled,
            "date_retour": body.date_retour
        })
        mqtt_client.publish("medicinebox/config", message)

    if body.enabled:
        creer_alerte_systeme("contexte", "Patient hospitalisé")
        return {"status": "ok", "message": "Système suspendu — hospitalisation"}
    else:
        creer_alerte_systeme("contexte", "Fin d'hospitalisation — système réactivé")
        return {"status": "ok", "message": "Système réactivé — fin d'hospitalisation"}


# ═══════════════════════════════════════════════════════
# ROUTE 13 : Changement de prescription (NOUVELLE)
# But : le médecin crée une nouvelle prescription
# → Nouvelle entrée dans prescriptions + prescription_doses
# → Le système utilise automatiquement la prescription active
# ═══════════════════════════════════════════════════════
@router.post("/config/changement-rx")
def changement_rx(body: ChangementRx):
    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Récupérer le patient
        cursor.execute("SELECT id FROM patients LIMIT 1;")
        row = cursor.fetchone()
        if not row:
            cursor.close()
            return {"error": "Aucun patient trouvé"}
        patient_id = row[0]

        # Vérifier si le médicament existe, sinon le créer
        cursor.execute("SELECT id FROM medicaments WHERE nom = %s;", (body.medicament,))
        med_row = cursor.fetchone()
        if med_row:
            medicament_id = med_row[0]
        else:
            cursor.execute("""
                INSERT INTO medicaments (nom, dosage)
                VALUES (%s, %s) RETURNING id;
            """, (body.medicament, body.dosage))
            medicament_id = cursor.fetchone()[0]

        # Créer la nouvelle prescription
        date_debut = datetime.now().date()
        date_fin = date_debut + timedelta(days=body.duree_jours)

        cursor.execute("""
            INSERT INTO prescriptions (
                patient_id, medicament_id, prescrit_par,
                date_debut, date_fin
            ) VALUES (%s, %s, %s, %s, %s) RETURNING id;
        """, (patient_id, medicament_id, body.prescrit_par, date_debut, date_fin))
        prescription_id = cursor.fetchone()[0]

        # Créer les doses selon la fréquence
        if body.frequence >= 1:
            cursor.execute("""
                INSERT INTO prescription_doses (prescription_id, moment, heure_debut, heure_fin, nb_comprimes, heure_optimisee)
                VALUES (%s, 'matin', '06:00', '11:00', 1, NULL);
            """, (prescription_id,))
        if body.frequence >= 2:
            cursor.execute("""
                INSERT INTO prescription_doses (prescription_id, moment, heure_debut, heure_fin, nb_comprimes, heure_optimisee)
                VALUES (%s, 'midi', '11:00', '16:00', 1, NULL);
            """, (prescription_id,))
        if body.frequence >= 3:
            cursor.execute("""
                INSERT INTO prescription_doses (prescription_id, moment, heure_debut, heure_fin, nb_comprimes, heure_optimisee)
                VALUES (%s, 'soir', '19:00', '22:00', 1, NULL);
            """, (prescription_id,))

        # Créer le stock initial
        stock_initial = body.frequence * body.duree_jours
        seuil = body.frequence * 5
        cursor.execute("""
            INSERT INTO stock (patient_id, medicament_id, quantite, seuil_alerte)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (patient_id, medicament_id)
            DO UPDATE SET quantite = %s, seuil_alerte = %s;
        """, (patient_id, medicament_id, stock_initial, seuil, stock_initial, seuil))

        conn.commit()
        cursor.close()

        return {
            "status": "ok",
            "message": f"Nouvelle prescription créée : {body.medicament} {body.dosage} × {body.frequence}/jour pour {body.duree_jours} jours",
            "prescription_id": prescription_id
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════
# HELPER : Créer une alerte système pour le médecin
# Appelée à chaque changement de mode (ON/OFF, Ramadan, etc.)
# Le médecin voit ces alertes dans son interface
# ═══════════════════════════════════════════════════════
def creer_alerte_systeme(type_alerte, message):
    """Insère une alerte dans la table alertes pour que le médecin soit notifié"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM patients LIMIT 1;")
        row = cursor.fetchone()
        if row:
            cursor.execute("""
                INSERT INTO alertes (patient_id, type, message, created_at, lu)
                VALUES (%s, %s, %s, NOW(), FALSE);
            """, (row[0], type_alerte, message))
            conn.commit()
        cursor.close()
    except Exception as e:
        print(f"❌ Erreur création alerte système : {e}")
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════
# ROUTE 14 : Toggle système ON/OFF (NOUVELLE)
# But : le patient peut arrêter/démarrer le système
#
# Quand OFF :
#   - Les 3 tâches planifiées vérifient config_state["system_on"]
#     et ne font rien si False
#   - Les prises en_attente du jour sont supprimées
#     (pas marquées manquée → pas de biais ML)
#   - L'ESP32 reçoit {"mode": "system_off"} → ignore tout
#   - Le médecin est notifié via une alerte
#
# Quand ON :
#   - Vérifie que la prescription est encore active
#   - Si expirée → erreur "Prescription expirée"
#   - Si valide → les tâches reprennent, l'ESP32 reçoit system_on
#   - Le médecin est notifié
# ═══════════════════════════════════════════════════════
@router.post("/config/system")
def toggle_system(body: ConfigToggle, request: Request):
    mqtt_client = getattr(request.app.state, 'mqtt_client', None)

    if body.enabled:
        # ── DÉMARRER LE SYSTÈME ──
        # Vérifier que la prescription est encore active
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM patients LIMIT 1;")
            row = cursor.fetchone()
            if not row:
                cursor.close()
                conn.close()
                return {"status": "error", "message": "Aucun patient trouvé"}
            patient_id = row[0]

            cursor.execute("""
                SELECT id FROM prescriptions
                WHERE patient_id = %s
                  AND date_debut <= CURRENT_DATE
                  AND date_fin >= CURRENT_DATE
                ORDER BY id DESC LIMIT 1;
            """, (patient_id,))
            presc = cursor.fetchone()
            cursor.close()
            conn.close()

            if not presc:
                return {
                    "status": "error",
                    "message": "Prescription expirée — contactez votre médecin pour un renouvellement"
                }
        except Exception as e:
            conn.close()
            return {"status": "error", "message": str(e)}

        config_state["system_on"] = True

        # Notifier l'ESP32
        if mqtt_client and mqtt_client.is_connected():
            mqtt_client.publish("medicinebox/config", json.dumps({
                "mode": "system_on", "enabled": True
            }))

        # Notifier le médecin
        creer_alerte_systeme("systeme", "Système activé par le patient")

        return {"status": "ok", "message": "Système activé — les alertes et le suivi reprennent"}
        # ── Générer les prises du jour immédiatement ──
        from datetime import datetime
        aujourd_hui = datetime.now().date()
        moments_config = {
            'matin': '08:30:00',
            'midi':  '13:30:00',
            'soir':  '20:30:00',
        }
        conn2 = get_connection()
        try:
            cursor2 = conn2.cursor()
            prises_creees = 0
            for moment, heure_str in moments_config.items():
                cursor2.execute("""
                    SELECT id FROM prises
                    WHERE patient_id = %s AND moment = %s AND heure_prevue::date = %s;
                """, (patient_id, moment, aujourd_hui))
                if not cursor2.fetchone():
                    heure_prevue = datetime.combine(
                        aujourd_hui,
                        datetime.strptime(heure_str, "%H:%M:%S").time()
                    )
                    cursor2.execute("""
                        INSERT INTO prises (patient_id, prescription_id, moment, heure_prevue, statut)
                        VALUES (%s, %s, %s, %s, 'en_attente');
                    """, (patient_id, presc[0], moment, heure_prevue))
                    prises_creees += 1
            if prises_creees > 0:
                conn2.commit()
                print(f"📋 {prises_creees} prise(s) créée(s) pour {aujourd_hui}")
            cursor2.close()
        except Exception as e:
            print(f"❌ Erreur génération prises : {e}")
        finally:
            conn2.close()

    else:
        # ── ARRÊTER LE SYSTÈME ──
        config_state["system_on"] = False

        # Supprimer les prises en_attente du jour (pas de faux manque)
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM patients LIMIT 1;")
            row = cursor.fetchone()
            if row:
                cursor.execute("""
                    DELETE FROM prises
                    WHERE patient_id = %s
                      AND heure_prevue::date = CURRENT_DATE
                      AND statut = 'en_attente';
                """, (row[0],))
                deleted = cursor.rowcount
                conn.commit()
                if deleted > 0:
                    print(f"🗑️ {deleted} prise(s) en_attente supprimée(s)")
            cursor.close()
        except Exception as e:
            print(f"❌ Erreur suppression prises : {e}")
        finally:
            conn.close()

        # Notifier l'ESP32
        if mqtt_client and mqtt_client.is_connected():
            mqtt_client.publish("medicinebox/config", json.dumps({
                "mode": "system_off", "enabled": False
            }))

        # Notifier le médecin
        creer_alerte_systeme("systeme", "Système arrêté par le patient")

        return {"status": "ok", "message": "Système arrêté — aucune donnée ne sera générée"}


# ═══════════════════════════════════════════════════════
# ROUTE 15 : Statut ESP32 (NOUVELLE)
# But : le subscriber.py appelle cette route quand il reçoit
# un statut de l'ESP32 pour mettre à jour config_state
# Utilisé aussi par le frontend pour vérifier la connexion
# ═══════════════════════════════════════════════════════
@router.post("/config/esp32-status")
def update_esp32_status(body: ConfigToggle):
    config_state["esp32_connected"] = body.enabled
    return {"status": "ok"}
