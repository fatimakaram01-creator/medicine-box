# mqtt/subscriber.py
# ─────────────────────────────────────────────────────────────
# Écoute les messages MQTT publiés par l'ESP32
# et les insère dans PostgreSQL via db.database
#
# Flux : ESP32 → MQTT broker → on_message() → handler → PostgreSQL
#
# 3 topics écoutés :
#   - medicinebox/prise  → UPDATE table prises (prise confirmée)
#   - medicinebox/statut → INSERT table alertes (si erreur ESP32)
#                          + gestion heartbeat + logique reconnexion
#   - medicinebox/config → (publié par le backend, pas écouté ici)
#
# LOGIQUE DE RECONNEXION :
#   Quand l'ESP32 envoie "online" après une déconnexion :
#   1. On attend 30 secondes que l'ESP32 vide son EEPROM
#   2. Si des prises arrivent → rattrapage (UPDATE prises)
#   3. Si rien n'arrive → effacement (DELETE prises en_attente
#      de la période de déconnexion)
# ─────────────────────────────────────────────────────────────

import json
import threading
import paho.mqtt.client as mqtt
from datetime import datetime, timedelta
from db.database import get_connection

# ─── Configuration broker ───
# ─── Configuration broker ───
import os
import ssl
from dotenv import load_dotenv

load_dotenv()

MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT = int(os.getenv("MQTT_PORT", 8883))
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
MQTT_TOPICS = [
    "medicinebox/prise",
    "medicinebox/statut",
    "medicinebox/mode",
]

# ─── Variables de suivi reconnexion ───
# Quand l'ESP32 envoie "online", on lance un timer de 30s
# pour attendre les données EEPROM avant de décider rattrapage/effacement
reconnexion_en_cours = False
prises_recues_apres_reconnexion = False
derniere_deconnexion = None  # timestamp de la dernière déconnexion détectée


# ─── Callback : connexion au broker ───
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("✅ MQTT connecté au broker")
        for topic in MQTT_TOPICS:
            client.subscribe(topic)
            print(f"   📡 Abonné à : {topic}")
    else:
        print(f"❌ MQTT connexion échouée, code : {rc}")


# ─── Callback : message reçu ───
def on_message(client, userdata, msg):
    topic = msg.topic
    try:
        data = json.loads(msg.payload.decode())
        print(f"📩 Reçu sur {topic} : {data}")

        if topic == "medicinebox/prise":
            handle_prise(data)
        elif topic == "medicinebox/statut":
            handle_statut(data)
        elif topic == "medicinebox/mode":
            handle_mode(data)

    except json.JSONDecodeError:
        print(f"⚠️ Message non-JSON sur {topic}: {msg.payload}")
    except Exception as e:
        print(f"❌ Erreur traitement : {e}")


# ─── Handler : prise de médicament détectée ───
# Message attendu de l'ESP32 :
# {"patient_id": 1, "moment": "matin", "poids_avant": 45.2, "poids_apres": 38.7}
# OU depuis le buffer EEPROM (prises retardées) :
# {"patient_id": 1, "moment": "matin", "poids_avant": 45.2, "poids_apres": 38.7, "date": "2026-04-28"}
#
# Si "date" est présent → c'est une prise retardée du buffer EEPROM
# → UPDATE la prise de ce jour-là (pas d'aujourd'hui)
def handle_prise(data):
    global prises_recues_apres_reconnexion

    # ── Validation ──
    if "moment" not in data or "poids_avant" not in data or "poids_apres" not in data:
        print("⚠️ Message prise incomplet — ignoré")
        return

    # Marquer qu'on a reçu des données après reconnexion
    prises_recues_apres_reconnexion = True

    conn = get_connection()
    try:
        cursor = conn.cursor()

        # Patient ID depuis le message MQTT ou premier patient par défaut
        if "patient_id" in data:
            patient_id = int(data["patient_id"])
        else:
            cursor.execute("SELECT id FROM patients ORDER BY id LIMIT 1;")
            row = cursor.fetchone()
            patient_id = row[0] if row else 1

        # Déterminer la date de la prise
        # Si "date" est présent → prise retardée depuis EEPROM
        # Sinon → prise en temps réel (aujourd'hui)
        if "date" in data:
            # Prise retardée — UPDATE la prise de ce jour-là
            date_prise = data["date"]
            filtre_date = f"heure_prevue::date = '{date_prise}'"
            print(f"📦 Prise retardée du {date_prise} — rattrapage")
        else:
            # Prise en temps réel
            filtre_date = "heure_prevue::date = CURRENT_DATE"

        cursor.execute(f"""
            UPDATE prises
            SET heure_reelle = NOW(),
                statut = 'pris',
                poids_avant = %s,
                poids_apres = %s
            WHERE patient_id = %s
              AND moment = %s
              AND {filtre_date}
              AND statut = 'en_attente';
        """, (
            data["poids_avant"],
            data["poids_apres"],
            patient_id,
            data["moment"]
        ))
        conn.commit()

        if cursor.rowcount > 0:
            print(f"✅ Prise enregistrée : {data['moment']} {'(retardée)' if 'date' in data else ''}")
        else:
            print(f"⚠️ Prise {data['moment']} déjà enregistrée ou pas de prise en_attente")
        cursor.close()
    except Exception as e:
        print(f"❌ Erreur handle_prise : {e}")
    finally:
        conn.close()


# ─── Handler : statut ESP32 ───
# Messages possibles :
#   {"status": "online", "message": "Demarrage OK"}     → ESP32 vient de démarrer
#   {"status": "heartbeat"}                               → ping périodique (toutes les 5 min)
#   {"status": "error", "message": "Reed switch..."}     → erreur hardware
#   {"status": "eeprom_vide"}                             → EEPROM vidé, rien à envoyer
#   {"status": "eeprom_fin"}                              → fin du vidage EEPROM
def handle_statut(data):
    global reconnexion_en_cours, prises_recues_apres_reconnexion, derniere_deconnexion

    status = data.get("status", "")

    # ── Heartbeat — mise à jour du timestamp ──
    if status in ("heartbeat", "online"):
        # Importer et appeler la fonction de mise à jour
        try:
            from api.main import mettre_a_jour_heartbeat
            mettre_a_jour_heartbeat()
        except ImportError:
            # Fallback si import circulaire
            from api.routes import config_state
            config_state["esp32_connected"] = True

    # ── Online — ESP32 vient de démarrer ou de se reconnecter ──
    if status == "online":
        from api.routes import config_state
        was_disconnected = not config_state.get("esp32_connected", False)
        config_state["esp32_connected"] = True

        if was_disconnected:
            print("🟢 ESP32 reconnecté — attente vidage EEPROM (30s)...")
            reconnexion_en_cours = True
            prises_recues_apres_reconnexion = False

            # Désactiver mode_sans_wifi au retour WiFi
            pid_online = data.get("patient_id")
            if pid_online:
                conn_tmp = get_connection()
                try:
                    cur_tmp = conn_tmp.cursor()
                    cur_tmp.execute("""
                        UPDATE config SET valeur = 'false', updated_at = NOW()
                        WHERE cle = %s;
                    """, (f'mode_sans_wifi_{pid_online}',))
                    conn_tmp.commit()
                    cur_tmp.close()
                    print(f"📶 mode_sans_wifi patient_{pid_online} → false (WiFi retrouvé)")
                except Exception:
                    pass
                finally:
                    conn_tmp.close()

            # Lancer un timer de 30 secondes
            # Après 30s, on vérifie si des données EEPROM sont arrivées
            timer = threading.Timer(30.0, verifier_donnees_reconnexion)
            timer.start()
        else:
            print(f"📟 ESP32 en ligne : {data.get('message', '')}")

    # ── EEPROM vidé — l'ESP32 confirme que son buffer est vide ──
    elif status == "eeprom_vide":
        print("📭 ESP32 : EEPROM vide — aucune prise stockée localement")
        # Pas besoin d'attendre 30s, on sait déjà qu'il n'y a rien
        if reconnexion_en_cours:
            reconnexion_en_cours = False
            if not prises_recues_apres_reconnexion:
                effacer_prises_deconnexion()

    # ── Fin vidage EEPROM — toutes les prises stockées ont été envoyées ──
    elif status == "eeprom_fin":
        print("📬 ESP32 : fin du vidage EEPROM")
        reconnexion_en_cours = False
        # Les prises ont déjà été traitées par handle_prise()

    # ── Erreur hardware ──
    # ── Offline — ESP32 déconnecté (LWT) ──
    elif status == "offline":
        from api.routes import config_state, sauvegarder_system_on
        config_state["esp32_connected"] = False
        print("🔴 ESP32 déconnecté (LWT)")

        # Récupérer le patient_id depuis le message si présent
        patient_id = data.get("patient_id", None)

        conn = get_connection()
        try:
            cursor = conn.cursor()

            # Si pas de patient_id dans le message → chercher tous les patients actifs
            if patient_id:
                patients = [patient_id]
            else:
                cursor.execute("SELECT id FROM patients WHERE active = TRUE ORDER BY id;")
                patients = [r[0] for r in cursor.fetchall()]

            for pid in patients:
                # Mettre system_on = False pour ce patient
                sauvegarder_system_on(False, pid)
                print(f"💾 system_on[{pid}] → False (boîte éteinte)")

                # Marquer les prises en_attente des jours passés comme 'manque'
                # cause_manque = 'offline' → exclues du ML (panne involontaire)
                cursor.execute("""
                    UPDATE prises SET statut = 'manque', cause_manque = 'offline'
                    WHERE patient_id = %s
                      AND statut = 'en_attente'
                      AND heure_prevue::date < CURRENT_DATE;
                """, (pid,))
                nb_manque = cursor.rowcount

                # Supprimer les prises futures SEULEMENT si pas en mode_sans_wifi
                # mode_sans_wifi = patient parti avec la boîte → prises conservées pour sync EEPROM
                cursor.execute("""
                    SELECT valeur FROM config WHERE cle = %s;
                """, (f'mode_sans_wifi_{pid}',))
                row_wifi = cursor.fetchone()
                mode_sans_wifi = row_wifi and row_wifi[0] == 'true'

                if mode_sans_wifi:
                    nb_sup = 0
                    print(f"📶 patient_{pid} : mode_sans_wifi actif — prises futures conservées")
                else:
                    cursor.execute("""
                        DELETE FROM prises
                        WHERE patient_id = %s
                          AND statut = 'en_attente'
                          AND heure_prevue > NOW();
                    """, (pid,))
                    nb_sup = cursor.rowcount

                conn.commit()
                if nb_manque > 0 or nb_sup > 0:
                    print(f"📋 patient_{pid} : {nb_manque} prise(s) → manque, {nb_sup} future(s) supprimée(s)")

            cursor.close()
        except Exception as e:
            print(f"❌ Erreur offline handler : {e}")
        finally:
            conn.close()

        try:
            from api.routes import creer_alerte_systeme
            creer_alerte_systeme("systeme", "Boîte déconnectée — système mis en pause automatiquement")
        except Exception:
            pass
    elif status == "error":
        conn = get_connection()
        try:
            cursor = conn.cursor()
            # Utiliser patient_id du message si présent, sinon premier patient
            if "patient_id" in data:
                patient_id = int(data["patient_id"])
            else:
                cursor.execute("SELECT id FROM patients ORDER BY id LIMIT 1;")
                row = cursor.fetchone()
                patient_id = row[0] if row else None
            if patient_id:
                cursor.execute("""
                    INSERT INTO alertes (patient_id, type, message, created_at, lu)
                    VALUES (%s, 'erreur_dispositif', %s, NOW(), FALSE);
                """, (patient_id, data.get("message", "Erreur ESP32")))
                conn.commit()
            cursor.close()
            print(f"🚨 Alerte : {data.get('message')}")
        except Exception as e:
            print(f"❌ Erreur handle_statut : {e}")
        finally:
            conn.close()


# ─── Handler : mode sans WiFi ───
# {"status": "sans_wifi", "patient_id": 1, "actif": true/false}
# actif=true  → patient part sans WiFi → conserver prises futures
# actif=false → retour WiFi après absence → recréer prises manquantes + désactiver
def handle_mode(data):
    patient_id = data.get("patient_id")
    actif = data.get("actif", False)
    status = data.get("status", "")

    if status != "sans_wifi" or not patient_id:
        return

    conn = get_connection()
    try:
        cursor = conn.cursor()
        valeur = 'true' if actif else 'false'

        # Mettre à jour le flag en base
        cursor.execute("""
            INSERT INTO config (cle, valeur, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (cle) DO UPDATE SET valeur = %s, updated_at = NOW();
        """, (f'mode_sans_wifi_{patient_id}', valeur, valeur))
        conn.commit()
        print(f"📶 mode_sans_wifi patient_{patient_id} → {valeur}")

        # ── Si désactivation (retour WiFi) → recréer les prises manquantes ──
        # Le patient était absent avec la boîte — les prises ont été supprimées
        # On les recrée depuis la prescription active pour la période manquante
        if not actif:
            recreer_prises_absence(cursor, conn, patient_id)

        cursor.close()
    except Exception as e:
        print(f"❌ Erreur handle_mode : {e}")
    finally:
        conn.close()


def recreer_prises_absence(cursor, conn, patient_id):
    """
    Recrée les prises manquantes depuis la prescription active.
    Appelée au retour WiFi après mode sans_wifi.
    Ne recrée que les prises qui n'existent pas encore.
    """
    from datetime import date, timedelta

    # Récupérer prescription active
    cursor.execute("""
        SELECT id FROM prescriptions
        WHERE patient_id = %s AND active = TRUE
          AND date_debut <= CURRENT_DATE AND date_fin >= CURRENT_DATE
        ORDER BY id DESC LIMIT 1;
    """, (patient_id,))
    presc = cursor.fetchone()
    if not presc:
        print(f"⚠️ Aucune prescription active pour patient_{patient_id} — pas de recréation")
        return

    prescription_id = presc[0]

    # Récupérer les moments et heures du profil actif
    cursor.execute("""
        SELECT
            CASE WHEN matin_debut != matin_fin
                THEN to_char((matin_debut + (matin_fin - matin_debut)/2), 'HH24:MI') END AS matin,
            CASE WHEN midi_debut != midi_fin
                THEN to_char((midi_debut + (midi_fin - midi_debut)/2), 'HH24:MI') END AS midi,
            CASE WHEN soir_debut != soir_fin
                THEN to_char((soir_debut + (soir_fin - soir_debut)/2), 'HH24:MI') END AS soir
        FROM intervalles_profils WHERE actif = TRUE LIMIT 1;
    """)
    profil = cursor.fetchone()
    if not profil:
        return

    moments = {}
    for i, m in enumerate(['matin', 'midi', 'soir']):
        if profil[i]:
            moments[m] = profil[i]

    # Recréer les prises manquantes des 7 derniers jours
    aujourd_hui = date.today()
    nb_recrees = 0
    for j in range(7, -1, -1):
        jour = aujourd_hui - timedelta(days=j)
        for moment, heure_str in moments.items():
            # Vérifier si la prise existe déjà
            cursor.execute("""
                SELECT id FROM prises
                WHERE patient_id = %s AND moment = %s AND heure_prevue::date = %s;
            """, (patient_id, moment, jour))
            if not cursor.fetchone():
                from datetime import datetime
                heure_prevue = datetime.combine(jour, datetime.strptime(heure_str, "%H:%M").time())
                # Prise passée → statut manque (le patient avait ses médicaments manuellement)
                # Prise aujourd'hui → en_attente
                statut = 'en_attente' if jour == aujourd_hui else 'manque'
                cursor.execute("""
                    INSERT INTO prises (patient_id, prescription_id, moment, heure_prevue, statut, cause_manque)
                    VALUES (%s, %s, %s, %s, %s, %s);
                """, (patient_id, prescription_id, moment, heure_prevue, statut,
                        None if statut == 'en_attente' else 'sans_wifi'))
                nb_recrees += 1

    if nb_recrees > 0:
        conn.commit()
        print(f"✅ patient_{patient_id} : {nb_recrees} prise(s) recrée(s) après retour WiFi")


def verifier_donnees_reconnexion():
    """
    Appelée 30 secondes après la reconnexion de l'ESP32.
    Vérifie si des prises EEPROM ont été reçues.

    Cas 1 : prises reçues → rien à faire (déjà traitées par handle_prise)
    Cas 2 : rien reçu → la boîte était éteinte → effacer les prises en_attente
    """
    global reconnexion_en_cours, prises_recues_apres_reconnexion

    if not reconnexion_en_cours:
        return  # déjà traité (par eeprom_vide ou eeprom_fin)

    reconnexion_en_cours = False

    if prises_recues_apres_reconnexion:
        print("✅ Rattrapage terminé — prises EEPROM intégrées")
    else:
        print("🗑️ Aucune donnée EEPROM — effacement des prises en_attente")
        effacer_prises_deconnexion()


def effacer_prises_deconnexion():
    """
    Supprime les prises en_attente de la période de déconnexion.
    C'est comme si ces jours n'avaient pas existé → pas de biais ML.

    On supprime toutes les prises en_attente (pas celles qui sont 'pris' ou 'manque')
    car les prises en_attente non résolues sont celles créées pendant la déconnexion
    par la tâche de génération automatique.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM patients ORDER BY id;")
        patients = cursor.fetchall()
        for row in patients:
         if row:
            # Supprimer les prises en_attente qui traînent
            # (celles des jours passés qui n'ont jamais été résolues)
            cursor.execute("""
                DELETE FROM prises
                WHERE patient_id = %s
                  AND statut = 'en_attente'
                  AND heure_prevue::date < CURRENT_DATE;
            """, (row[0],))
            deleted = cursor.rowcount

            # Aussi supprimer celles d'aujourd'hui (la boîte vient de se rallumer)
            cursor.execute("""
                DELETE FROM prises
                WHERE patient_id = %s
                  AND statut = 'en_attente'
                  AND heure_prevue::date = CURRENT_DATE;
            """, (row[0],))
            deleted += cursor.rowcount

            conn.commit()
            if deleted > 0:
                print(f"🗑️ {deleted} prise(s) en_attente supprimée(s) (période de déconnexion)")

                # Notifier le médecin
                try:
                    from api.routes import creer_alerte_systeme
                    creer_alerte_systeme(
                        "systeme",
                        f"Boîte reconnectée — {deleted} prise(s) supprimée(s) (boîte était éteinte)"
                    )
                except Exception:
                    pass
        cursor.close()
    except Exception as e:
        print(f"❌ Erreur effacement prises déconnexion : {e}")
    finally:
        conn.close()


# ─── Démarrage du client MQTT ───
def start_mqtt():
    client = mqtt.Client(
        client_id="medicinebox-backend",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION1
    )
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    client.tls_set(tls_version=ssl.PROTOCOL_TLS)
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        client.loop_start()
        print("🚀 MQTT subscriber démarré (HiveMQ Cloud)")
        return client
    except Exception as e:
        print(f"❌ Connexion broker impossible : {e}")
        return None