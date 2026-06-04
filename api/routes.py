# api/routes.py
import json
from datetime import datetime, timedelta
from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional
from db.database import get_connection

router = APIRouter()

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


# ═══════════════════════════════════════════════════════
# PERSISTANCE system_on dans Supabase
# ═══════════════════════════════════════════════════════

def lire_system_on():
    """Lit system_on depuis la table config de Supabase au démarrage"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT valeur FROM config WHERE cle='system_on';")
        row = cursor.fetchone()
        cursor.close()
        valeur = row[0] == 'true' if row else False
        print(f"📖 system_on lu depuis Supabase : {valeur}")
        return valeur
    except Exception as e:
        print(f"❌ Erreur lecture system_on : {e}")
        return False
    finally:
        conn.close()

def sauvegarder_system_on(valeur: bool):
    """Sauvegarde system_on dans la table config de Supabase"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE config SET valeur=%s, updated_at=NOW() WHERE cle='system_on';",
            ('true' if valeur else 'false',)
        )
        conn.commit()
        cursor.close()
        print(f"💾 system_on sauvegardé dans Supabase : {valeur}")
    except Exception as e:
        print(f"❌ Erreur sauvegarde system_on : {e}")
    finally:
        conn.close()


config_state = {
    "system_on": lire_system_on(),
    "esp32_connected": False,
    "remplissage": False,
    "buzzer": True,
    "ramadan": False,
    "ramadan_ville": None,
    "hospitalisation": False,
}


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
def get_config_status():
    return config_state


@router.post("/config/remplissage")
def toggle_remplissage(body: ConfigToggle, request: Request):
    config_state["remplissage"] = body.enabled
    mqtt_client = getattr(request.app.state, 'mqtt_client', None)
    if mqtt_client and mqtt_client.is_connected():
        mqtt_client.publish("medicinebox/config", json.dumps({"mode": "remplissage", "enabled": body.enabled}))
    return {"status": "ok", "message": f"Mode remplissage {'activé' if body.enabled else 'désactivé'}"}


@router.post("/config/buzzer")
def toggle_buzzer(body: ConfigToggle, request: Request):
    config_state["buzzer"] = body.enabled
    mqtt_client = getattr(request.app.state, 'mqtt_client', None)
    if mqtt_client and mqtt_client.is_connected():
        mqtt_client.publish("medicinebox/config", json.dumps({"mode": "buzzer", "enabled": body.enabled}))
    return {"status": "ok", "message": f"Buzzer {'activé' if body.enabled else 'désactivé'}"}


@router.post("/config/ramadan")
def toggle_ramadan(body: ConfigRamadan, request: Request):
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
    mqtt_client = getattr(request.app.state, 'mqtt_client', None)
    if body.enabled and body.ville:
        horaires = horaires_ramadan.get(body.ville, {"fajr": "04:30", "iftar": "19:45"})
        if mqtt_client and mqtt_client.is_connected():
            mqtt_client.publish("medicinebox/config", json.dumps({"mode": "ramadan", "enabled": True, "ville": body.ville, "fajr": horaires["fajr"], "iftar": horaires["iftar"]}))
        creer_alerte_systeme("contexte", f"Mode Ramadan activé — {body.ville}")
        return {"status": "ok", "message": f"Mode Ramadan activé — {body.ville}", "horaires": {"shour": f"Avant {horaires['fajr']}", "midi": "Suspendu (jeûne)", "iftar": f"Après {horaires['iftar']}"}}
    else:
        if mqtt_client and mqtt_client.is_connected():
            mqtt_client.publish("medicinebox/config", json.dumps({"mode": "ramadan", "enabled": False}))
        creer_alerte_systeme("contexte", "Mode Ramadan désactivé")
        return {"status": "ok", "message": "Mode Ramadan désactivé"}


@router.post("/config/hospitalisation")
def toggle_hospitalisation(body: ConfigHospitalisation, request: Request):
    config_state["hospitalisation"] = body.enabled
    config_state["hospitalisation_date_retour"] = body.date_retour if body.enabled else None
    mqtt_client = getattr(request.app.state, 'mqtt_client', None)
    if mqtt_client and mqtt_client.is_connected():
        mqtt_client.publish("medicinebox/config", json.dumps({"mode": "hospitalisation", "enabled": body.enabled, "date_retour": body.date_retour}))
    if body.enabled:
        creer_alerte_systeme("contexte", "Patient hospitalisé")
        return {"status": "ok", "message": "Système suspendu — hospitalisation"}
    else:
        creer_alerte_systeme("contexte", "Fin d'hospitalisation — système réactivé")
        return {"status": "ok", "message": "Système réactivé — fin d'hospitalisation"}


@router.post("/config/system")
def toggle_system(body: ConfigToggle, request: Request):
    mqtt_client = getattr(request.app.state, 'mqtt_client', None)

    if body.enabled:
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM patients LIMIT 1;")
            row = cursor.fetchone()
            if not row:
                cursor.close()
                return {"status": "error", "message": "Aucun patient trouvé"}
            patient_id = row[0]
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

        # Mettre à jour en mémoire ET dans Supabase
        config_state["system_on"] = True
        sauvegarder_system_on(True)

        # Générer les prises du jour immédiatement
        aujourd_hui = datetime.now().date()
        moments_config = {'matin': '08:30:00', 'midi': '13:30:00', 'soir': '20:30:00'}
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
                print(f"📋 {prises_creees} prise(s) créée(s) pour {aujourd_hui}")
            cursor2.close()
        except Exception as e:
            print(f"❌ Erreur génération prises : {e}")
        finally:
            conn2.close()

        if mqtt_client and mqtt_client.is_connected():
            mqtt_client.publish("medicinebox/config", json.dumps({"mode": "system_on", "enabled": True}))

        creer_alerte_systeme("systeme", "Système activé par le patient")
        return {"status": "ok", "message": "Système activé — les alertes et le suivi reprennent"}

    else:
        # Mettre à jour en mémoire ET dans Supabase
        config_state["system_on"] = False
        sauvegarder_system_on(False)

        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM patients LIMIT 1;")
            row = cursor.fetchone()
            if row:
                cursor.execute("""
                    DELETE FROM prises
                    WHERE patient_id = %s AND heure_prevue::date = CURRENT_DATE AND statut = 'en_attente';
                """, (row[0],))
                conn.commit()
            cursor.close()
        except Exception as e:
            print(f"❌ Erreur suppression prises : {e}")
        finally:
            conn.close()

        if mqtt_client and mqtt_client.is_connected():
            mqtt_client.publish("medicinebox/config", json.dumps({"mode": "system_off", "enabled": False}))

        creer_alerte_systeme("systeme", "Système arrêté par le patient")
        return {"status": "ok", "message": "Système arrêté"}


@router.post("/config/esp32-status")
def update_esp32_status(body: ConfigToggle):
    config_state["esp32_connected"] = body.enabled
    return {"status": "ok"}


@router.post("/config/changement-rx")
def changement_rx(body: ChangementRx):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM patients LIMIT 1;")
        row = cursor.fetchone()
        if not row:
            cursor.close()
            return {"error": "Aucun patient trouvé"}
        patient_id = row[0]
        cursor.execute("SELECT id FROM medicaments WHERE nom = %s;", (body.medicament,))
        med_row = cursor.fetchone()
        if med_row:
            medicament_id = med_row[0]
        else:
            cursor.execute("INSERT INTO medicaments (nom, dosage) VALUES (%s, %s) RETURNING id;", (body.medicament, body.dosage))
            medicament_id = cursor.fetchone()[0]
        date_debut = datetime.now().date()
        date_fin = date_debut + timedelta(days=body.duree_jours)
        cursor.execute("""
            INSERT INTO prescriptions (patient_id, medecin, date_debut, date_fin, active)
            VALUES (%s, %s, %s, %s, true) RETURNING id;
        """, (patient_id, body.prescrit_par, date_debut, date_fin))
        prescription_id = cursor.fetchone()[0]
        moments = ['matin', 'midi', 'soir']
        heures = ['08:30', '13:30', '20:30']
        for i in range(body.frequence):
            cursor.execute("""
                INSERT INTO prescription_doses (prescription_id, medicament_id, moment, heure_prevue, quantite)
                VALUES (%s, %s, %s, %s, 1);
            """, (prescription_id, medicament_id, moments[i], heures[i]))
        conn.commit()
        cursor.close()
        return {"status": "ok", "message": f"Prescription créée : {body.medicament} × {body.frequence}/jour", "prescription_id": prescription_id}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def creer_alerte_systeme(type_alerte, message):
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
