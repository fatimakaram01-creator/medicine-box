# api/routes.py
import json
from datetime import datetime, timedelta
from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional
from db.database import get_connection

router = APIRouter()

def get_patient_id_from_db(patient_id: int = None) -> int:
    """Retourne le patient_id fourni ou le premier patient de la base"""
    if patient_id:
        return patient_id
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM patients ORDER BY id LIMIT 1;")
        row = cursor.fetchone()
        cursor.close()
        return row[0] if row else None
    finally:
        conn.close()



class ConfigToggle(BaseModel):
    enabled: bool

class ConfigRamadan(BaseModel):
    enabled: bool
    ville: Optional[str] = None

class ConfigHospitalisation(BaseModel):
    enabled: bool
    date_retour: Optional[str] = None

class ChangementRx(BaseModel):
    medicament: str
    dosage: str
    frequence: int
    prescrit_par: str
    duree_jours: int
    mode: str = "remplacer"  # "remplacer" ou "nouveau" (polythérapie)


# ═══════════════════════════════════════════════════════
# PERSISTANCE system_on dans Supabase — par patient
# ═══════════════════════════════════════════════════════

def lire_system_on(patient_id=None):
    """Lit system_on depuis la table config — par patient si patient_id fourni"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        if patient_id:
            cle = f'system_on_{patient_id}'
        else:
            cle = 'system_on'
        cursor.execute("SELECT valeur FROM config WHERE cle=%s;", (cle,))
        row = cursor.fetchone()
        cursor.close()
        if row:
            return row[0] == 'true'
        # Si pas de config spécifique → system OFF par défaut pour nouveau patient
        return False
    except Exception as e:
        print(f"❌ Erreur lecture system_on : {e}")
        return False
    finally:
        conn.close()

def sauvegarder_system_on(valeur: bool, patient_id=None):
    """Sauvegarde system_on dans la table config — par patient si patient_id fourni"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        if patient_id:
            cle = f'system_on_{patient_id}'
        else:
            cle = 'system_on'
        cursor.execute("""
            INSERT INTO config (cle, valeur, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (cle) DO UPDATE SET valeur=%s, updated_at=NOW();
        """, (cle, 'true' if valeur else 'false', 'true' if valeur else 'false'))
        conn.commit()
        cursor.close()
        print(f"💾 system_on[{patient_id}] sauvegardé : {valeur}")
    except Exception as e:
        print(f"❌ Erreur sauvegarde system_on : {e}")
    finally:
        conn.close()


config_state = {
    "system_on": False,  # global — chaque patient a son propre system_on en base
    "esp32_connected": False,
    "remplissage": False,
    "buzzer": True,
    "ramadan": False,
    "ramadan_ville": None,
    "hospitalisation": False,
}



# ══════════════════════════════════════════════════
# PATIENTS — CRUD
# ══════════════════════════════════════════════════
@router.get("/patients/liste")
def liste_patients():
    """Retourne tous les patients avec leur observance"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.id, p.nom, p.prenom, p.medecin,
                   COUNT(pr.id) FILTER (WHERE pr.statut = 'pris'
                     AND pr.heure_prevue >= NOW() - INTERVAL '7 days') as pris_7j,
                   COUNT(pr.id) FILTER (WHERE pr.statut IN ('pris','manque')
                     AND pr.heure_prevue >= NOW() - INTERVAL '7 days') as total_7j
            FROM patients p
            LEFT JOIN prises pr ON pr.patient_id = p.id
            GROUP BY p.id, p.nom, p.prenom, p.medecin
            ORDER BY p.id;
        """)
        rows = cursor.fetchall()
        cursor.close()
        return [{
            "id": r[0], "nom": r[1], "prenom": r[2], "medecin": r[3],
            "observance_7j": round(r[4]/r[5]*100) if r[5] > 0 else 100
        } for r in rows]
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


@router.post("/patients/creer")
def creer_patient(body: dict):
    """
    Crée un nouveau patient OU ajoute un médecin à un patient existant.
    Si code_activation fourni → cherche le patient existant et ajoute le médecin.
    Sinon → crée un nouveau patient avec un code généré.
    """
    import random
    from datetime import datetime

    conn = get_connection()
    try:
        cursor = conn.cursor()

        code_existant = body.get("code_activation_existant", "").strip().upper()
        medecin = body.get("medecin", "")

        # ── Cas 1 : Patient existant — associer à ce médecin ──
        if code_existant:
            cursor.execute("""
                SELECT id, prenom, nom FROM patients
                WHERE code_activation = %s;
            """, (code_existant,))
            row = cursor.fetchone()
            if not row:
                cursor.close()
                return {"error": "Code d'activation introuvable — vérifiez le code du patient"}
            cursor.close()
            return {
                "success": True,
                "patient_id": row[0],
                "code_activation": code_existant,
                "prenom": row[1],
                "nom": row[2],
                "message": f"Patient {row[1]} {row[2]} associé à votre compte"
            }

        # ── Cas 2 : Nouveau patient ──
        annee = datetime.now().year
        while True:
            numero = random.randint(1, 9999)
            code = f"MB-{annee}-{numero:04d}"
            cursor.execute("SELECT id FROM patients WHERE code_activation = %s;", (code,))
            if not cursor.fetchone():
                break

        cursor.execute("""
            INSERT INTO patients (nom, prenom, medecin, code_activation)
            VALUES (%s, %s, %s, %s) RETURNING id;
        """, (body.get("nom",""), body.get("prenom",""), medecin, code))
        new_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        return {
            "success": True,
            "patient_id": new_id,
            "code_activation": code,
            "message": f"Patient créé — Code d'activation : {code}"
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


@router.post("/patients/activer-boite")
def activer_boite(body: dict, request: Request):
    """
    Reçu depuis la boîte ESP32 lors de la première configuration,
    ou depuis l'app web quand un patient change de compte.
    Retourne le patient_id correspondant et publie via MQTT si boîte connectée.
    """
    code = body.get("code_activation", "").strip().upper()
    if not code:
        return {"success": False, "error": "Code manquant"}

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, prenom, nom FROM patients
            WHERE code_activation = %s;
        """, (code,))
        row = cursor.fetchone()
        cursor.close()
        if not row:
            return {"success": False, "error": "Code invalide"}

        patient_id = row[0]

        # Publier via MQTT pour mettre à jour l'ESP32 en temps réel
        try:
            mqtt_client = getattr(request.app.state, 'mqtt_client', None)
            if mqtt_client and mqtt_client.is_connected():
                import json as json_mod
                payload = json_mod.dumps({
                    "action": "changer_patient",
                    "patient_id": patient_id,
                    "prenom": row[1],
                    "nom": row[2]
                })
                mqtt_client.publish("medicinebox/commande", payload)
                print(f"[MQTT] changer_patient → patient_{patient_id}")
        except Exception as e:
            print(f"[MQTT] Erreur publication changer_patient : {e}")

        return {
            "success": True,
            "patient_id": patient_id,
            "prenom": row[1],
            "nom": row[2],
            "message": f"Boîte liée à {row[1]} {row[2]}"
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


@router.get("/patients/code/{patient_id}")
def get_code_patient(patient_id: int):
    """Retourne le code d'activation d'un patient (pour le médecin)"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT code_activation, prenom, nom FROM patients WHERE id = %s;
        """, (patient_id,))
        row = cursor.fetchone()
        cursor.close()
        if not row:
            return {"error": "Patient introuvable"}
        return {
            "patient_id": patient_id,
            "code_activation": row[0],
            "prenom": row[1],
            "nom": row[2]
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


@router.get("/")
def home():
    return {"message": "Medicine Box API fonctionne !"}


@router.get("/patients")
def get_patients():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, nom, prenom, medecin FROM patients;")
        rows = cursor.fetchall()
        cursor.close()
        return [{"id": r[0], "nom": r[1], "prenom": r[2], "medecin": r[3]} for r in rows]
    except Exception as e:
        return {"error": str(e), "patients": []}
    finally:
        conn.close()


@router.get("/prises/today")
def get_prises_today(patient_id: int = None):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        patient_id = get_patient_id_from_db(patient_id)
        if not patient_id:
            cursor.close()
            return {"error": "Aucun patient trouvé", "count": 0, "total": 0, "prises": []}
        cursor.execute("""
            SELECT moment, heure_prevue, heure_reelle, statut, poids_avant, poids_apres
            FROM prises
            WHERE patient_id = %s AND heure_prevue::date = CURRENT_DATE
            ORDER BY heure_prevue;
        """, (patient_id,))
        rows = cursor.fetchall()
        cursor.close()
        return {
            "count": len([r for r in rows if r[3] == 'pris']),
            "total": len(rows),
            "prises": [{"moment": r[0], "heure_prevue": str(r[1]), "heure_reelle": str(r[2]) if r[2] else None, "statut": r[3], "poids_avant": r[4], "poids_apres": r[5]} for r in rows]
        }
    except Exception as e:
        return {"error": str(e), "count": 0, "total": 0, "prises": []}
    finally:
        conn.close()


@router.get("/alertes")
def get_alertes(patient_id: int = None):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        patient_id = get_patient_id_from_db(patient_id)
        if not patient_id:
            cursor.close()
            return {"error": "Aucun patient trouvé", "alertes": []}
        cursor.execute("""
            SELECT type, message, created_at, lu FROM alertes
            WHERE patient_id = %s ORDER BY created_at DESC LIMIT 10;
        """, (patient_id,))
        rows = cursor.fetchall()
        cursor.close()
        return [{"type": r[0], "message": r[1], "created_at": str(r[2]), "lu": r[3]} for r in rows]
    except Exception as e:
        return {"error": str(e), "alertes": []}
    finally:
        conn.close()


@router.get("/observance")
def get_observance(patient_id: int = None):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        patient_id = get_patient_id_from_db(patient_id)
        if not patient_id:
            cursor.close()
            return {"error": "Aucun patient trouvé"}
        cursor.execute("""
            SELECT heure_prevue::date as jour, statut FROM prises
            WHERE patient_id = %s
              AND heure_prevue::date >= CURRENT_DATE - INTERVAL '7 days'
              AND heure_prevue::date <= CURRENT_DATE
            ORDER BY heure_prevue;
        """, (patient_id,))
        rows = cursor.fetchall()
        cursor.close()
        jours = {}
        for r in rows:
            jour = str(r[0])
            if jour not in jours:
                jours[jour] = {"total": 0, "pris": 0}
            jours[jour]["total"] += 1
            if r[1] == 'pris':
                jours[jour]["pris"] += 1
        total_global = sum(j["total"] for j in jours.values())
        pris_global = sum(j["pris"] for j in jours.values())
        pourcentage = round((pris_global / total_global) * 100) if total_global > 0 else 0
        return {"pourcentage": pourcentage, "total": total_global, "pris": pris_global, "par_jour": jours}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


@router.get("/prises/historique")
def get_prises_historique(patient_id: int = None):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        patient_id = get_patient_id_from_db(patient_id)
        if not patient_id:
            cursor.close()
            return []
        cursor.execute("""
            SELECT moment, heure_prevue, heure_reelle, statut, poids_avant, poids_apres
            FROM prises
            WHERE patient_id = %s AND heure_prevue::date >= CURRENT_DATE - INTERVAL '30 days'
            ORDER BY heure_prevue DESC LIMIT 50;
        """, (patient_id,))
        rows = cursor.fetchall()
        cursor.close()
        return [{"moment": r[0], "heure_prevue": str(r[1]), "heure_reelle": str(r[2]) if r[2] else None, "statut": r[3], "poids_avant": r[4], "poids_apres": r[5]} for r in rows]
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


@router.get("/config/status")
def get_config_status(patient_id: int = None):
    state = dict(config_state)
    # system_on est par patient — le lire depuis la base
    if patient_id:
        state["system_on"] = lire_system_on(patient_id)
    else:
        pid = get_patient_id_from_db(None)
        state["system_on"] = lire_system_on(pid) if pid else False
    return state


@router.post("/config/remplissage")
def toggle_remplissage(body: ConfigToggle, request: Request, patient_id: int = None):
    config_state["remplissage"] = body.enabled
    pid = get_patient_id_from_db(patient_id)
    mqtt_client = getattr(request.app.state, 'mqtt_client', None)
    if mqtt_client and mqtt_client.is_connected():
        mqtt_client.publish(f"medicinebox/config/{pid}", json.dumps({"mode": "remplissage", "enabled": body.enabled}))
    return {"status": "ok", "message": f"Mode remplissage {'activé' if body.enabled else 'désactivé'}"}


@router.post("/config/buzzer")
def toggle_buzzer(body: ConfigToggle, request: Request, patient_id: int = None):
    config_state["buzzer"] = body.enabled
    pid = get_patient_id_from_db(patient_id)
    mqtt_client = getattr(request.app.state, 'mqtt_client', None)
    if mqtt_client and mqtt_client.is_connected():
        mqtt_client.publish(f"medicinebox/config/{pid}", json.dumps({"mode": "buzzer", "enabled": body.enabled}))
    return {"status": "ok", "message": f"Buzzer {'activé' if body.enabled else 'désactivé'}"}


@router.post("/config/ramadan")
def toggle_ramadan(body: ConfigRamadan, request: Request, patient_id: int = None):
    config_state["ramadan"] = body.enabled
    config_state["ramadan_ville"] = body.ville if body.enabled else None
    horaires_ramadan = {
        "Agadir": {"fajr": "04:30", "iftar": "19:45"},
        "Casablanca": {"fajr": "04:20", "iftar": "19:40"},
        "Marrakech": {"fajr": "04:25", "iftar": "19:42"},
        "Rabat": {"fajr": "04:18", "iftar": "19:38"},
        "Fes": {"fajr": "04:15", "iftar": "19:35"},
        "Tanger": {"fajr": "04:10", "iftar": "19:30"},
    }
    pid = get_patient_id_from_db(patient_id)
    mqtt_client = getattr(request.app.state, 'mqtt_client', None)
    if body.enabled and body.ville:
        horaires = horaires_ramadan.get(body.ville, {"fajr": "04:30", "iftar": "19:45"})
        if mqtt_client and mqtt_client.is_connected():
            mqtt_client.publish(f"medicinebox/config/{pid}", json.dumps({"mode": "ramadan", "enabled": True, "ville": body.ville, "fajr": horaires["fajr"], "iftar": horaires["iftar"]}))
        creer_alerte_systeme("contexte", f"Mode Ramadan activé — {body.ville}")
        return {"status": "ok", "message": f"Mode Ramadan activé — {body.ville}", "horaires": {"shour": f"Avant {horaires['fajr']}", "midi": "Suspendu (jeûne)", "iftar": f"Après {horaires['iftar']}"}}
    else:
        if mqtt_client and mqtt_client.is_connected():
            mqtt_client.publish(f"medicinebox/config/{pid}", json.dumps({"mode": "ramadan", "enabled": False}))
        creer_alerte_systeme("contexte", "Mode Ramadan désactivé")
        return {"status": "ok", "message": "Mode Ramadan désactivé"}


@router.post("/config/hospitalisation")
def toggle_hospitalisation(body: ConfigHospitalisation, request: Request, patient_id: int = None):
    config_state["hospitalisation"] = body.enabled
    config_state["hospitalisation_date_retour"] = body.date_retour if body.enabled else None
    pid = get_patient_id_from_db(patient_id)
    mqtt_client = getattr(request.app.state, 'mqtt_client', None)
    if mqtt_client and mqtt_client.is_connected():
        mqtt_client.publish(f"medicinebox/config/{pid}", json.dumps({"mode": "hospitalisation", "enabled": body.enabled, "date_retour": body.date_retour}))
    if body.enabled:
        creer_alerte_systeme("contexte", "Patient hospitalisé")
        return {"status": "ok", "message": "Système suspendu — hospitalisation"}
    else:
        creer_alerte_systeme("contexte", "Fin d'hospitalisation — système réactivé")
        return {"status": "ok", "message": "Système réactivé — fin d'hospitalisation"}


@router.post("/config/system")
def toggle_system(body: ConfigToggle, request: Request, patient_id: int = None):
    mqtt_client = getattr(request.app.state, 'mqtt_client', None)
    patient_id = get_patient_id_from_db(patient_id)
    if not patient_id:
        return {"status": "error", "message": "Aucun patient trouvé"}

    if body.enabled:
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id FROM prescriptions
                WHERE patient_id = %s AND date_debut <= CURRENT_DATE AND date_fin >= CURRENT_DATE
                ORDER BY id DESC LIMIT 1;
            """, (patient_id,))
            presc = cursor.fetchone()
            cursor.close()
        except Exception as e:
            return {"status": "error", "message": str(e)}
        finally:
            conn.close()

        if not presc:
            return {"status": "error", "message": "Prescription expirée — contactez votre médecin"}

        # Sauvegarder system_on par patient
        sauvegarder_system_on(True, patient_id)

        # Générer les prises du jour selon le profil horaire actif
        aujourd_hui = datetime.now().date()
        moments_config = _get_moments_config()
        conn2 = get_connection()
        try:
            cursor2 = conn2.cursor()
            prises_creees = 0
            for moment, heure_str in moments_config.items():
                cursor2.execute("""
                    SELECT id FROM prises WHERE patient_id = %s AND moment = %s AND heure_prevue::date = %s;
                """, (patient_id, moment, aujourd_hui))
                if not cursor2.fetchone():
                    heure_prevue = datetime.combine(aujourd_hui, datetime.strptime(heure_str, "%H:%M:%S").time())
                    cursor2.execute("""
                        INSERT INTO prises (patient_id, prescription_id, moment, heure_prevue, statut)
                        VALUES (%s, %s, %s, %s, 'en_attente');
                    """, (patient_id, presc[0], moment, heure_prevue))
                    prises_creees += 1
            if prises_creees > 0:
                conn2.commit()
                print(f"📋 {prises_creees} prise(s) créée(s) pour patient_{patient_id}")
            cursor2.close()
        except Exception as e:
            print(f"❌ Erreur génération prises : {e}")
        finally:
            conn2.close()

        if mqtt_client and mqtt_client.is_connected():
            mqtt_client.publish(f"medicinebox/config/{patient_id}", json.dumps({"mode": "system_on", "enabled": True}))

        creer_alerte_systeme("systeme", f"Système activé — patient_{patient_id}")
        return {"status": "ok", "message": "Système activé — les alertes et le suivi reprennent"}

    else:
        # Désactiver system_on pour ce patient
        sauvegarder_system_on(False, patient_id)

        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM prises
                WHERE patient_id = %s AND heure_prevue::date = CURRENT_DATE AND statut = 'en_attente';
            """, (patient_id,))
            conn.commit()
            cursor.close()
        except Exception as e:
            print(f"❌ Erreur suppression prises : {e}")
        finally:
            conn.close()

        if mqtt_client and mqtt_client.is_connected():
            mqtt_client.publish(f"medicinebox/config/{patient_id}", json.dumps({"mode": "system_off", "enabled": False}))

        creer_alerte_systeme("systeme", f"Système arrêté — patient_{patient_id}")
        return {"status": "ok", "message": "Système arrêté"}


@router.post("/config/esp32-status")
def update_esp32_status(body: ConfigToggle):
    config_state["esp32_connected"] = body.enabled
    return {"status": "ok"}


@router.post("/config/changement-rx")
def changement_rx(body: ChangementRx, patient_id: int = None):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        patient_id = get_patient_id_from_db(patient_id)
        if not patient_id:
            cursor.close()
            return {"error": "Aucun patient trouvé"}

        # mode : "nouveau" (polythérapie) ou "remplacer" (changement traitement)
        mode = getattr(body, 'mode', 'remplacer')

        if mode == 'remplacer':
            # ── Changement de traitement : désactiver l'ancienne prescription ──
            cursor.execute("""
                UPDATE prescriptions
                SET active = FALSE, date_fin = CURRENT_DATE - INTERVAL '1 day'
                WHERE patient_id = %s AND active = TRUE;
            """, (patient_id,))
            # Supprimer les prises en_attente du jour
            cursor.execute("""
                DELETE FROM prises
                WHERE patient_id = %s
                  AND statut = 'en_attente'
                  AND heure_prevue::date = CURRENT_DATE;
            """, (patient_id,))
        # mode 'nouveau' (polythérapie) → garder les prescriptions actives existantes

        # ── Médicament : retrouver ou créer ──
        cursor.execute("SELECT id FROM medicaments WHERE nom = %s;", (body.medicament,))
        med_row = cursor.fetchone()
        if med_row:
            medicament_id = med_row[0]
        else:
            cursor.execute("INSERT INTO medicaments (nom, dosage) VALUES (%s, %s) RETURNING id;", (body.medicament, body.dosage))
            medicament_id = cursor.fetchone()[0]

        # ── Créer la nouvelle prescription ──
        date_debut = datetime.now().date()
        date_fin = date_debut + timedelta(days=body.duree_jours)
        cursor.execute("""
            INSERT INTO prescriptions (patient_id, medecin, date_debut, date_fin, active)
            VALUES (%s, %s, %s, %s, true) RETURNING id;
        """, (patient_id, body.prescrit_par, date_debut, date_fin))
        prescription_id = cursor.fetchone()[0]

        # ── Créer les doses selon le profil actif ──
        # Les heures sont lues depuis _get_moments_config()
        # pour être cohérentes avec les plages du profil actif
        moments_config = _get_moments_config()
        moments = ['matin', 'midi', 'soir']
        for i in range(body.frequence):
            moment = moments[i]
            heure = moments_config.get(moment, '08:30:00')
            cursor.execute("""
                INSERT INTO prescription_doses (prescription_id, medicament_id, moment, heure_prevue, quantite)
                VALUES (%s, %s, %s, %s, 1);
            """, (prescription_id, medicament_id, moment, heure))

        conn.commit()
        cursor.close()
        creer_alerte_systeme("contexte", f"{'Nouveau médicament ajouté' if mode == 'nouveau' else 'Prescription modifiée'} : {body.medicament} × {body.frequence}/jour")

        # Notifier l'ESP32 via MQTT — prescription créée pour ce patient
        try:
            from api.main import app as main_app
            mqtt_client = getattr(main_app.state, 'mqtt_client', None)
            if mqtt_client and mqtt_client.is_connected():
                import json as json_mod
                payload = json_mod.dumps({
                    "action": "prescription_activee",
                    "patient_id": patient_id
                })
                mqtt_client.publish("medicinebox/commande", payload)
                print(f"[MQTT] prescription_activee → patient_{patient_id}")
        except Exception as e:
            print(f"[MQTT] Erreur publication prescription : {e}")

        return {"status": "ok", "message": f"Prescription créée : {body.medicament} × {body.frequence}/jour", "prescription_id": prescription_id}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


@router.post("/config/arreter-traitement")
def arreter_traitement(patient_id: int = None):
    """
    Arrête la prescription active du patient.
    - Met date_fin = aujourd'hui sur la prescription active
    - Supprime les prises en_attente du jour
    - Met system_on = FALSE
    - Crée une alerte pour le dashboard
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        patient_id = get_patient_id_from_db(patient_id)
        if not patient_id:
            cursor.close()
            return {"error": "Aucun patient trouvé"}

        # ── Désactiver la prescription active ──
        # date_fin = hier pour éviter conflit si nouvelle prescription créée le même jour
        cursor.execute("""
            UPDATE prescriptions
            SET active = FALSE, date_fin = CURRENT_DATE - INTERVAL '1 day'
            WHERE patient_id = %s AND active = TRUE;
        """, (patient_id,))

        # ── Supprimer les prises en_attente ──
        cursor.execute("""
            DELETE FROM prises
            WHERE patient_id = %s
              AND statut = 'en_attente'
              AND heure_prevue::date = CURRENT_DATE;
        """, (patient_id,))

        conn.commit()
        cursor.close()

        # ── Mettre system_on = FALSE par patient ──
        sauvegarder_system_on(False, patient_id)

        # Notifier l'ESP32 via MQTT
        try:
            from api.main import app as main_app
            mqtt_client = getattr(main_app.state, 'mqtt_client', None)
            if mqtt_client and mqtt_client.is_connected():
                import json as json_mod
                # system_off
                mqtt_client.publish(f"medicinebox/config/{patient_id}", json_mod.dumps({"mode": "system_off", "enabled": False}))
                # prescription_arretee
                mqtt_client.publish("medicinebox/commande", json_mod.dumps({
                    "action": "prescription_arretee",
                    "patient_id": patient_id
                }))
                print(f"[MQTT] prescription_arretee + system_off → patient_{patient_id}")
        except Exception as e:
            print(f"[MQTT] Erreur : {e}")

        creer_alerte_systeme("contexte", "Traitement arrêté par le médecin")
        return {"status": "ok", "message": "Traitement arrêté — le système est mis en pause"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def creer_alerte_systeme(type_alerte, message):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        _pid3 = get_patient_id_from_db(None)
        if _pid3:
            cursor.execute("""
                INSERT INTO alertes (patient_id, type, message, created_at, lu)
                VALUES (%s, %s, %s, NOW(), FALSE);
            """, (_pid3, type_alerte, message))
            conn.commit()
        cursor.close()
    except Exception as e:
        print(f"❌ Erreur création alerte système : {e}")
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════
# HELPER : lire les plages horaires du profil actif
# ═══════════════════════════════════════════════════════

def _get_moments_config():
    """
    Retourne les heures de référence (milieu de plage) du profil actif.
    Fallback sur les valeurs par défaut si aucun profil trouvé.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT matin_debut, matin_fin, midi_debut, midi_fin, soir_debut, soir_fin
            FROM intervalles_profils WHERE actif = TRUE;
        """)
        row = cursor.fetchone()
        cursor.close()
        if not row:
            return {'matin': '08:30:00', 'midi': '13:30:00', 'soir': '20:30:00'}
        # Calcule le milieu de chaque plage
        def milieu(debut, fin):
            if not debut or str(debut) == '00:00:00':
                return None  # plage désactivée
            d = datetime.combine(datetime.today(), debut)
            f = datetime.combine(datetime.today(), fin)
            if f < d:
                f += timedelta(days=1)
            mid = d + (f - d) / 2
            return mid.strftime("%H:%M:%S")
        result = {}
        m = milieu(row[0], row[1])
        if m: result['matin'] = m
        m = milieu(row[2], row[3])
        if m: result['midi'] = m
        m = milieu(row[4], row[5])
        if m: result['soir'] = m
        return result if result else {'matin': '08:30:00', 'midi': '13:30:00', 'soir': '20:30:00'}
    except Exception as e:
        print(f"❌ Erreur lecture profil actif : {e}")
        return {'matin': '08:30:00', 'midi': '13:30:00', 'soir': '20:30:00'}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════
# INTERVALLES PROFILS
# ═══════════════════════════════════════════════════════

@router.get("/prescription/active")
def get_prescription_active(patient_id: int = None):
    """Retourne la prescription active du patient avec les détails"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        patient_id = get_patient_id_from_db(patient_id)
        if not patient_id:
            cursor.close()
            return {"error": "Aucun patient"}
        cursor.execute("""
            SELECT p.id, p.medecin, p.date_debut::text, p.date_fin::text,
                   m.nom, m.dosage,
                   COUNT(pd.id) as nb_prises
            FROM prescriptions p
            LEFT JOIN prescription_doses pd ON pd.prescription_id = p.id
            LEFT JOIN medicaments m ON m.id = pd.medicament_id
            WHERE p.patient_id = %s AND p.active = TRUE
              AND p.date_fin >= CURRENT_DATE
            GROUP BY p.id, p.medecin, p.date_debut, p.date_fin, m.nom, m.dosage
            ORDER BY p.id DESC LIMIT 1;
        """, (patient_id,))
        row = cursor.fetchone()
        cursor.close()
        if not row:
            return {"active": False}
        return {
            "active": True,
            "id": row[0],
            "medecin": row[1],
            "date_debut": row[2],
            "date_fin": row[3],
            "medicament": row[4] or "—",
            "dosage": row[5] or "—",
            "nb_prises_jour": row[6]
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()



def get_profil_actif():
    """Retourne le profil actif uniquement"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, label,
                   matin_debut::text, matin_fin::text,
                   midi_debut::text,  midi_fin::text,
                   soir_debut::text,  soir_fin::text
            FROM intervalles_profils WHERE actif = TRUE;
        """)
        row = cursor.fetchone()
        cursor.close()
        if not row:
            return {"error": "Aucun profil actif"}
        return {"id": row[0], "label": row[1],
                "matin_debut": row[2], "matin_fin": row[3],
                "midi_debut":  row[4], "midi_fin":  row[5],
                "soir_debut":  row[6], "soir_fin":  row[7]}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()



@router.get("/prescriptions/historique")
def get_prescriptions_historique(patient_id: int = None):
    """Retourne toutes les prescriptions du patient avec leurs médicaments groupés"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        patient_id = get_patient_id_from_db(patient_id)
        if not patient_id:
            cursor.close()
            return []
        # Récupérer toutes les prescriptions avec leurs médicaments groupés
        cursor.execute("""
            SELECT p.id, p.medecin, p.date_debut::text, p.date_fin::text,
                   p.active,
                   STRING_AGG(DISTINCT m.nom || ' ' || COALESCE(m.dosage,''), ', ') as medicaments,
                   COUNT(DISTINCT pd.id) as nb_doses,
                   (p.date_fin - p.date_debut) as duree_jours
            FROM prescriptions p
            LEFT JOIN prescription_doses pd ON pd.prescription_id = p.id
            LEFT JOIN medicaments m ON m.id = pd.medicament_id
            WHERE p.patient_id = %s
            GROUP BY p.id, p.medecin, p.date_debut, p.date_fin, p.active
            ORDER BY p.date_debut DESC;
        """, (patient_id,))
        rows = cursor.fetchall()
        cursor.close()
        return [{
            "id": r[0],
            "medecin": r[1],
            "date_debut": r[2],
            "date_fin": r[3],
            "active": r[4],
            "medicament": r[5] or "—",
            "dosage": "",
            "nb_prises_jour": r[6],
            "duree_jours": r[7].days if r[7] else 0
        } for r in rows]
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


@router.get("/intervalles/profil-actif")
def get_profil_actif_firmware():
    """Retourne le profil actif avec les heures brutes — utilisé par le firmware ESP32"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, label,
                   matin_debut::text, matin_fin::text,
                   midi_debut::text,  midi_fin::text,
                   soir_debut::text,  soir_fin::text
            FROM intervalles_profils WHERE actif = TRUE;
        """)
        row = cursor.fetchone()
        cursor.close()
        if not row:
            return {
                "matin_debut": "06:00:00", "matin_fin": "12:00:00",
                "midi_debut":  "12:00:00", "midi_fin":  "18:00:00",
                "soir_debut":  "18:00:00", "soir_fin":  "22:00:00"
            }
        return {
            "id": row[0], "label": row[1],
            "matin_debut": row[2], "matin_fin": row[3],
            "midi_debut":  row[4], "midi_fin":  row[5],
            "soir_debut":  row[6], "soir_fin":  row[7]
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def _regenerer_prises_du_jour(conn_externe=None):
    """
    Supprime et régénère les prises EN_ATTENTE du jour pour tous les patients
    selon le nouveau profil actif. Appelé après changement d'intervalle.
    """
    conn = conn_externe or get_connection()
    try:
        cursor = conn.cursor()
        moments_config = _get_moments_config()
        cursor.execute("SELECT id FROM patients WHERE active = TRUE;")
        patients = [r[0] for r in cursor.fetchall()]
        for patient_id in patients:
            # Supprimer uniquement les prises en_attente d'aujourd'hui
            cursor.execute("""
                DELETE FROM prises
                WHERE patient_id = %s
                  AND statut = 'en_attente'
                  AND heure_prevue::date = CURRENT_DATE;
            """, (patient_id,))
            # Récupérer prescription active
            cursor.execute("""
                SELECT id FROM prescriptions
                WHERE patient_id = %s AND active = TRUE
                LIMIT 1;
            """, (patient_id,))
            presc = cursor.fetchone()
            if not presc:
                continue
            aujourd_hui = datetime.today().date()
            for moment, heure_str in moments_config.items():
                heure_prevue = datetime.combine(aujourd_hui, datetime.strptime(heure_str, "%H:%M:%S").time())
                cursor.execute("""
                    INSERT INTO prises (patient_id, prescription_id, moment, heure_prevue, statut)
                    VALUES (%s, %s, %s, %s, 'en_attente');
                """, (patient_id, presc[0], moment, heure_prevue))
        conn.commit()
        cursor.close()
        print(f"✅ Prises du jour régénérées pour {len(patients)} patient(s)")
    except Exception as e:
        print(f"❌ Erreur régénération prises : {e}")
    finally:
        if not conn_externe:
            conn.close()


# ── Supprimer un patient ──
@router.delete("/patients/{patient_id}")
def supprimer_patient(patient_id: int):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT prenom, nom FROM patients WHERE id = %s;", (patient_id,))
        row = cursor.fetchone()
        if not row:
            return {"error": "Patient introuvable"}
        cursor.execute("""
            DELETE FROM alertes_optimisation WHERE patient_id = %s;
            DELETE FROM alertes WHERE patient_id = %s;
            DELETE FROM prises WHERE patient_id = %s;
        """, (patient_id, patient_id, patient_id))
        cursor.execute("""
            DELETE FROM prescription_doses WHERE prescription_id IN (
                SELECT id FROM prescriptions WHERE patient_id = %s
            );
        """, (patient_id,))
        cursor.execute("DELETE FROM prescriptions WHERE patient_id = %s;", (patient_id,))
        cursor.execute("DELETE FROM patients WHERE id = %s;", (patient_id,))
        conn.commit()
        cursor.close()
        return {"success": True, "message": f"Patient {row[0]} {row[1]} supprimé"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


# ── Modifier un patient ──
@router.put("/patients/{patient_id}")
def modifier_patient(patient_id: int, body: dict):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        prenom = body.get("prenom")
        nom = body.get("nom")
        medecin = body.get("medecin")
        cursor.execute("""
            UPDATE patients SET prenom = COALESCE(%s, prenom),
                                nom = COALESCE(%s, nom),
                                medecin = COALESCE(%s, medecin)
            WHERE id = %s RETURNING prenom, nom;
        """, (prenom, nom, medecin, patient_id))
        row = cursor.fetchone()
        if not row:
            return {"error": "Patient introuvable"}
        conn.commit()
        cursor.close()
        return {"success": True, "prenom": row[0], "nom": row[1]}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


# ── Supprimer un profil intervalle ──
@router.delete("/intervalles/profils/{profil_id}")
def supprimer_profil(profil_id: int):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT actif, label FROM intervalles_profils WHERE id = %s;", (profil_id,))
        row = cursor.fetchone()
        if not row:
            return {"error": "Profil introuvable"}
        if row[0]:
            return {"error": "Impossible de supprimer le profil actif — activez-en un autre d'abord"}
        cursor.execute("DELETE FROM intervalles_profils WHERE id = %s;", (profil_id,))
        conn.commit()
        cursor.close()
        return {"success": True, "message": f"Profil '{row[1]}' supprimé"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


@router.get("/intervalles/profils")
def get_intervalles_profils():
    """Retourne tous les profils d'intervalles (historique complet)"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, label,
                   matin_debut::text, matin_fin::text,
                   midi_debut::text,  midi_fin::text,
                   soir_debut::text,  soir_fin::text,
                   date_debut::text,  date_fin::text,
                   actif, nb_prises
            FROM intervalles_profils
            ORDER BY actif DESC, date_debut DESC;
        """)
        rows = cursor.fetchall()
        cursor.close()
        return [{"id": r[0], "label": r[1],
                 "matin_debut": r[2], "matin_fin": r[3],
                 "midi_debut":  r[4], "midi_fin":  r[5],
                 "soir_debut":  r[6], "soir_fin":  r[7],
                 "date_debut":  r[8], "date_fin":  r[9],
                 "actif": r[10], "nb_prises": r[11]} for r in rows]
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


class NouveauProfil(BaseModel):
    label: str
    matin_debut: str
    matin_fin: str
    midi_debut: str
    midi_fin: str
    soir_debut: str
    soir_fin: str


@router.post("/intervalles/profils")
def creer_profil(data: NouveauProfil):
    """Crée un nouveau profil et le définit comme actif"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        # Désactiver l'ancien profil actif
        cursor.execute("""
            UPDATE intervalles_profils
            SET actif = FALSE, date_fin = CURRENT_DATE
            WHERE actif = TRUE;
        """)
        # Créer le nouveau profil
        cursor.execute("""
            INSERT INTO intervalles_profils
                (label, matin_debut, matin_fin, midi_debut, midi_fin,
                 soir_debut, soir_fin, actif, date_debut)
            VALUES (%s,%s,%s,%s,%s,%s,%s, TRUE, CURRENT_DATE)
            RETURNING id;
        """, (data.label, data.matin_debut, data.matin_fin,
              data.midi_debut, data.midi_fin,
              data.soir_debut, data.soir_fin))
        new_id = cursor.fetchone()[0]
        # Mettre à jour config
        cursor.execute("""
            UPDATE config SET valeur = %s
            WHERE cle = 'intervalles_profil_actif_id';
        """, (str(new_id),))
        cursor.execute("""
            UPDATE config SET valeur = CURRENT_DATE::TEXT
            WHERE cle = 'intervalles_modifies_le';
        """)
        conn.commit()
        cursor.close()
        creer_alerte_systeme("contexte", f"Nouveau profil horaire créé : {data.label}")
        _regenerer_prises_du_jour(conn_externe=None)
        # Publier les nouveaux intervalles via MQTT → ESP32 les reçoit en temps réel
        try:
            from api.main import app as main_app
            mqtt_client = getattr(main_app.state, 'mqtt_client', None)
            if mqtt_client and mqtt_client.is_connected():
                import json as json_mod
                payload = json_mod.dumps({
                    "action": "update_intervalles",
                    "matin_debut": data.matin_debut, "matin_fin": data.matin_fin,
                    "midi_debut":  data.midi_debut,  "midi_fin":  data.midi_fin,
                    "soir_debut":  data.soir_debut,  "soir_fin":  data.soir_fin
                })
                mqtt_client.publish("medicinebox/commande", payload)
                print(f"[MQTT] Intervalles publiés → ESP32")
        except Exception as e:
            print(f"[MQTT] Erreur publication intervalles : {e}")
        return {"success": True, "id": new_id}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


@router.post("/intervalles/profils/{profil_id}/activer")
def activer_profil(profil_id: int):
    """Réactive un ancien profil (médecin)"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        # Vérifier que le profil existe
        cursor.execute("SELECT label FROM intervalles_profils WHERE id = %s;", (profil_id,))
        row = cursor.fetchone()
        if not row:
            cursor.close()
            return {"error": "Profil introuvable"}
        label = row[0]
        # Désactiver l'actuel
        cursor.execute("""
            UPDATE intervalles_profils
            SET actif = FALSE, date_fin = CURRENT_DATE
            WHERE actif = TRUE;
        """)
        # Réactiver l'ancien — nouvelle période
        cursor.execute("""
            UPDATE intervalles_profils
            SET actif = TRUE, date_fin = NULL, date_debut = CURRENT_DATE
            WHERE id = %s;
        """, (profil_id,))
        # Mettre à jour config
        cursor.execute("""
            UPDATE config SET valeur = %s
            WHERE cle = 'intervalles_profil_actif_id';
        """, (str(profil_id),))
        cursor.execute("""
            UPDATE config SET valeur = CURRENT_DATE::TEXT
            WHERE cle = 'intervalles_modifies_le';
        """)
        conn.commit()
        cursor.close()
        creer_alerte_systeme("contexte", f"Profil horaire réactivé : {label}")
        _regenerer_prises_du_jour(conn_externe=None)
        # Publier les nouveaux intervalles via MQTT → ESP32 les reçoit en temps réel
        try:
            from api.main import app as main_app
            mqtt_client = getattr(main_app.state, 'mqtt_client', None)
            if mqtt_client and mqtt_client.is_connected():
                import json as json_mod
                # Relire les intervalles du profil activé
                conn2 = get_connection()
                cur2 = conn2.cursor()
                cur2.execute("""
                    SELECT matin_debut::text, matin_fin::text,
                           midi_debut::text, midi_fin::text,
                           soir_debut::text, soir_fin::text
                    FROM intervalles_profils WHERE id = %s;
                """, (profil_id,))
                r = cur2.fetchone()
                cur2.close(); conn2.close()
                if r:
                    payload = json_mod.dumps({
                        "action": "update_intervalles",
                        "matin_debut": r[0], "matin_fin": r[1],
                        "midi_debut":  r[2], "midi_fin":  r[3],
                        "soir_debut":  r[4], "soir_fin":  r[5]
                    })
                    mqtt_client.publish("medicinebox/commande", payload)
                    print(f"[MQTT] Intervalles publiés → ESP32")
        except Exception as e:
            print(f"[MQTT] Erreur publication intervalles : {e}")
        return {"success": True, "label": label}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()

# ══════════════════════════════════════════════════
# ML — PREDICT
# ══════════════════════════════════════════════════
@router.get("/ml/predict")
def ml_predict(patient_id: int = None):
    """Retourne le risque d'oubli et l'heure optimale d'alerte via ML"""
    try:
        import sys, os
        sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from ml.predict import predict, models_exist
        if not models_exist(patient_id):
            return {
                "risque_oubli": None,
                "niveau_risque": "—",
                "heure_optimale": None,
                "anomalie": False,
                "error": "Modèles non entraînés"
            }
        result = predict(patient_id)
        return result
    except Exception as e:
        return {
            "risque_oubli": None,
            "niveau_risque": "—",
            "heure_optimale": None,
            "anomalie": False,
            "error": str(e)
        }
