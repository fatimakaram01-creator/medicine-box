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

        # ── Désactiver l'ancienne prescription active ──
        # Sans ça, l'ancienne et la nouvelle coexistent
        # → le système génèrerait des prises en double
        cursor.execute("""
            UPDATE prescriptions
            SET active = FALSE, date_fin = CURRENT_DATE
            WHERE patient_id = %s AND active = TRUE;
        """, (patient_id,))

        # ── Supprimer les prises en_attente du jour ──
        # Les prises générées par l'ancienne prescription
        # ne sont plus valides → on les supprime
        cursor.execute("""
            DELETE FROM prises
            WHERE patient_id = %s
              AND statut = 'en_attente'
              AND heure_prevue::date = CURRENT_DATE;
        """, (patient_id,))

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
        creer_alerte_systeme("contexte", f"Nouvelle prescription : {body.medicament} × {body.frequence}/jour")
        return {"status": "ok", "message": f"Prescription créée : {body.medicament} × {body.frequence}/jour", "prescription_id": prescription_id}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


@router.post("/config/arreter-traitement")
def arreter_traitement():
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
        cursor.execute("SELECT id FROM patients LIMIT 1;")
        row = cursor.fetchone()
        if not row:
            cursor.close()
            return {"error": "Aucun patient trouvé"}
        patient_id = row[0]

        # ── Désactiver la prescription active ──
        cursor.execute("""
            UPDATE prescriptions
            SET active = FALSE, date_fin = CURRENT_DATE
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

        # ── Mettre system_on = FALSE ──
        config_state["system_on"] = False
        sauvegarder_system_on(False)

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
def get_prescription_active():
    """Retourne la prescription active du patient avec les détails"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM patients LIMIT 1;")
        row = cursor.fetchone()
        if not row:
            cursor.close()
            return {"error": "Aucun patient"}
        patient_id = row[0]
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
        return {"success": True, "label": label}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()