# api/main.py
# ─────────────────────────────────────────────────────────────
# FastAPI app — importe les routes, lance le subscriber MQTT,
# et lance les tâches planifiées
# ─────────────────────────────────────────────────────────────
#
# ARCHITECTURE DU FLUX D'ALERTE :
#
#   seed.py / ML  →  table alertes_optimisation  →  tâche planifiée  →  MQTT  →  ESP32  →  buzzer
#   (calcule         (stocke l'heure               (vérifie chaque      (publie     (reçoit     (sonne
#    l'heure          d'alerte pour                  minute si une       buzzer_on)   le signal)  30 sec)
#    optimale)        chaque moment)                 alerte est due)
#
# Le firmware ne décide JAMAIS quand sonner.
# C'est toujours le backend qui envoie l'ordre, basé sur
# l'heure calculée par le seed (phase découverte) ou le ML (phase adaptée).
# ─────────────────────────────────────────────────────────────

import asyncio
import json
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI
from mqtt.subscriber import start_mqtt
from db.database import get_connection
from api.routes import config_state, creer_alerte_systeme, _get_moments_config


# ─────────────────────────────────────────────────────────────
# NETTOYAGE AU DÉMARRAGE : PRISES OBSOLÈTES
# ─────────────────────────────────────────────────────────────
#
# Problème résolu :
#   Si la boîte s'éteint (coupure courant, batterie vide) pendant
#   plusieurs jours, les prises en_attente des jours passés restent
#   dans la base sans jamais être résolues.
#
#   Sans ce nettoyage :
#     → La tâche doses_manquees les marquerait "manquée" au redémarrage
#     → Le ML apprendrait que le patient est non-observant → BIAIS TOTAL
#
#   Avec ce nettoyage :
#     → Au redémarrage, toutes les prises en_attente dont l'heure
#       est déjà passée sont supprimées silencieusement
#     → Aucune fausse "manquée" dans la base → ML propre ✅
#
# Cette fonction est appelée UNE SEULE FOIS au démarrage du backend,
# avant le lancement des tâches planifiées.
# ─────────────────────────────────────────────────────────────

async def nettoyer_prises_obsoletes():
    """
    Supprime toutes les prises en_attente dont l'heure prévue
    est déjà passée. Appelée au démarrage du backend.

    Logique :
    - DELETE prises WHERE statut = 'en_attente' AND heure_prevue < NOW()
    - Couvre : extinction de la boîte, redémarrage Render, coupure réseau
    - Garantit l'intégrité des données ML
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM prises
            WHERE statut = 'en_attente'
              AND heure_prevue < NOW();
        """)
        deleted = cursor.rowcount
        conn.commit()
        cursor.close()
        if deleted > 0:
            print(f"🧹 {deleted} prise(s) obsolète(s) supprimée(s) au démarrage")
        else:
            print("🧹 Aucune prise obsolète — base propre")
    except Exception as e:
        print(f"❌ Erreur nettoyage prises : {e}")
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# TÂCHE PLANIFIÉE : ENVOI DES ALERTES BUZZER
# ─────────────────────────────────────────────────────────────
#
# Cette fonction tourne en boucle infinie en arrière-plan.
# Toutes les 60 secondes, elle vérifie :
#   "Est-ce qu'il y a une alerte prévue pour cette minute ?"
#
# Elle compare l'heure actuelle (HH:MM) avec les heures d'alerte
# stockées dans la table alertes_optimisation par le seed/ML.
#
# Si une alerte correspond → elle publie sur medicinebox/alerte
# → l'ESP32 reçoit → le buzzer sonne 30 secondes
#
# IMPORTANT : une alerte n'est envoyée qu'UNE SEULE FOIS.
# On ne re-sonne pas si le patient n'a pas pris.
# C'est le ML qui décide s'il faut un deuxième rappel
# (dans ce cas il crée une deuxième entrée dans alertes_optimisation).
# ─────────────────────────────────────────────────────────────

async def tache_alertes(mqtt_client):
    """
    Boucle infinie qui vérifie chaque minute si une alerte doit être envoyée.

    Logique :
    1. Lire l'heure actuelle (HH:MM)
    2. Chercher dans alertes_optimisation :
       - patient_id du premier patient
       - moment (matin/midi/soir)
       - heure_alerte qui correspond à HH:MM actuel
       - seulement pour aujourd'hui (on utilise le jour de la semaine)
    3. Vérifier que la prise correspondante est encore 'en_attente'
       (pas besoin d'alerter si la prise est déjà faite)
    4. Si tout correspond → publier buzzer_on sur MQTT
    5. Attendre 60 secondes et recommencer
    """

    # Attendre 5 secondes au démarrage pour que tout s'initialise
    # (broker MQTT, base de données, subscriber)
    await asyncio.sleep(5)
    print("⏰ Tâche alertes démarrée — vérification toutes les 60s")

    # Set pour stocker les alertes déjà envoyées aujourd'hui
    # Format : "2026-04-29_matin" → évite d'envoyer 2 fois la même alerte
    # Réinitialisé à minuit (quand la date change)
    alertes_envoyees = set()
    date_courante = datetime.now().date()

    while True:
        try:
            # ── Vérifier que le système est ON ──
            # Si le patient a arrêté le système → ne rien faire
            if not config_state.get("system_on", False):
                await asyncio.sleep(60)
                continue

            # ── Vérifier que le mode hospitalisation n'est pas actif ──
            if config_state.get("hospitalisation", False):
                await asyncio.sleep(60)
                continue

            # ── Réinitialiser le set à minuit ──
            # Quand on passe à un nouveau jour, les alertes d'hier
            # ne sont plus pertinentes → on vide le set
            maintenant = datetime.now()
            if maintenant.date() != date_courante:
                alertes_envoyees.clear()
                date_courante = maintenant.date()
                print("🔄 Nouveau jour — alertes réinitialisées")

            # ── Vérifier s'il y a une alerte à envoyer ──
            heure_actuelle = maintenant.strftime("%H:%M")
            jour_semaine = maintenant.weekday()  # 0=lundi ... 6=dimanche

            conn = get_connection()
            try:
                cursor = conn.cursor()

                # Récupérer le patient_id (prototype = 1 seul patient)
                cursor.execute("SELECT id FROM patients LIMIT 1;")
                row = cursor.fetchone()
                if not row:
                    cursor.close()
                    conn.close()
                    await asyncio.sleep(60)
                    continue
                patient_id = row[0]

                # ── Chercher les alertes prévues pour cette minute ──
                #
                # On compare heure_alerte (format 'HH:MM:SS' dans la BDD)
                # avec l'heure actuelle (format 'HH:MM')
                #
                # Filtre jour_semaine : le seed génère une alerte par jour×moment,
                # donc on filtre sur le jour actuel pour ne pas envoyer les alertes
                # d'un autre jour
                cursor.execute("""
                    SELECT DISTINCT moment, heure_alerte
                    FROM alertes_optimisation
                    WHERE patient_id = %s
                      AND jour_semaine = %s
                      AND TO_CHAR(heure_alerte, 'HH24:MI') = %s
                    ORDER BY moment;
                """, (patient_id, jour_semaine, heure_actuelle))

                alertes = cursor.fetchall()

                for alerte in alertes:
                    moment = alerte[0]  # 'matin', 'midi', ou 'soir'

                    # ── Clé unique pour cette alerte ──
                    # Empêche d'envoyer la même alerte plusieurs fois
                    cle = f"{date_courante}_{moment}"
                    if cle in alertes_envoyees:
                        continue  # déjà envoyée aujourd'hui

                    # ── Vérifier que la prise est encore en attente ──
                    # Pas besoin de buzzer si le patient a déjà pris
                    cursor.execute("""
                        SELECT statut FROM prises
                        WHERE patient_id = %s
                          AND moment = %s
                          AND heure_prevue::date = CURRENT_DATE
                          AND statut = 'en_attente';
                    """, (patient_id, moment))

                    prise_en_attente = cursor.fetchone()

                    if not prise_en_attente:
                        # Prise déjà faite ou pas de prise aujourd'hui → pas de buzzer
                        continue

                    # ── ENVOYER L'ALERTE BUZZER ──
                    # Publier sur medicinebox/alerte → l'ESP32 reçoit → buzzer 30s
                    if mqtt_client and mqtt_client.is_connected():
                        message = json.dumps({
                            "action": "buzzer_on",
                            "moment": moment
                        })
                        mqtt_client.publish("medicinebox/alerte", message)
                        alertes_envoyees.add(cle)
                        print(f"🔔 Alerte envoyée : buzzer_on pour {moment} à {heure_actuelle}")
                    else:
                        print(f"⚠️ MQTT non connecté — alerte {moment} non envoyée")

                cursor.close()
            except Exception as e:
                print(f"❌ Erreur tâche alertes (BDD) : {e}")
            finally:
                conn.close()

        except Exception as e:
            print(f"❌ Erreur tâche alertes : {e}")

        # ── Attendre 60 secondes avant la prochaine vérification ──
        await asyncio.sleep(60)


# ─────────────────────────────────────────────────────────────
# TÂCHE PLANIFIÉE : DÉTECTION DES DOSES MANQUÉES
# ─────────────────────────────────────────────────────────────
#
# Vérifie toutes les 5 minutes si une dose est manquée.
#
# Une dose est manquée quand la FIN de la plage horaire du profil
# actif est dépassée et que la prise est toujours 'en_attente'.
#
# IMPORTANT : cette tâche lit le profil actif depuis intervalles_profils.
# Si la plage est désactivée (00:00→00:00) → elle est ignorée.
# Si le profil change → les fins d'intervalles changent automatiquement.
#
# IMPORTANT : cette tâche ne tourne QUE si l'ESP32 est connecté.
# Si la boîte est éteinte → les prises restent en_attente (gelées).
# C'est le nettoyage au démarrage qui les supprime à la reconnexion.
# ─────────────────────────────────────────────────────────────

def _get_fin_intervalles():
    """
    Lit les fins de plages du profil actif depuis Supabase.
    Retourne un dict {moment: heure_fin_en_heures}.
    Ignore les plages désactivées (00:00).
    Fallback sur les valeurs par défaut si aucun profil.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT matin_fin, midi_fin, soir_fin
            FROM intervalles_profils WHERE actif = TRUE;
        """)
        row = cursor.fetchone()
        cursor.close()
        if not row:
            # Fallback : intervalles par défaut
            return {'matin': 11, 'midi': 16, 'soir': 22}
        result = {}
        noms = ['matin', 'midi', 'soir']
        for i, nom in enumerate(noms):
            fin = row[i]
            if fin and str(fin) != '00:00:00':
                result[nom] = fin.hour  # ex: time(11,0,0) → 11
        return result if result else {'matin': 11, 'midi': 16, 'soir': 22}
    except Exception as e:
        print(f"❌ Erreur lecture fin intervalles : {e}")
        return {'matin': 11, 'midi': 16, 'soir': 22}
    finally:
        conn.close()


async def tache_doses_manquees(mqtt_client):
    """
    Boucle infinie qui vérifie toutes les 5 minutes
    si des doses en_attente ont dépassé leur intervalle.
    """

    # Attendre 10 secondes au démarrage
    await asyncio.sleep(10)
    print("⏰ Tâche doses manquées démarrée — vérification toutes les 5 min")

    while True:
        try:
            # ── Vérifier que le système est ON ──
            if not config_state.get("system_on", False):
                await asyncio.sleep(300)
                continue

            if config_state.get("hospitalisation", False):
                await asyncio.sleep(300)
                continue

            # ── Si ESP32 déconnecté → geler les doses (pas de faux manque) ──
            # Les prises restent en_attente jusqu'à la reconnexion
            if not config_state.get("esp32_connected", False):
                await asyncio.sleep(300)
                continue

            maintenant = datetime.now()
            heure_actuelle = maintenant.hour

            # ── Lire les fins de plages du profil actif ──
            # (lu à chaque cycle pour capter les changements de profil)
            fin_intervalle = _get_fin_intervalles()

            conn = get_connection()
            try:
                cursor = conn.cursor()

                cursor.execute("SELECT id FROM patients LIMIT 1;")
                row = cursor.fetchone()
                if not row:
                    cursor.close()
                    conn.close()
                    await asyncio.sleep(300)
                    continue
                patient_id = row[0]

                # ── Vérifier chaque moment actif du profil ──
                for moment, heure_fin in fin_intervalle.items():

                    # Si l'heure actuelle n'a pas encore dépassé la fin de l'intervalle
                    # → trop tôt pour déclarer une dose manquée
                    if heure_actuelle < heure_fin:
                        continue

                    # ── Chercher une prise en_attente pour ce moment aujourd'hui ──
                    cursor.execute("""
                        SELECT id FROM prises
                        WHERE patient_id = %s
                          AND moment = %s
                          AND heure_prevue::date = CURRENT_DATE
                          AND statut = 'en_attente';
                    """, (patient_id, moment))

                    prise = cursor.fetchone()

                    if prise:
                        prise_id = prise[0]

                        # ── Marquer la prise comme manquée ──
                        cursor.execute("""
                            UPDATE prises SET statut = 'manque'
                            WHERE id = %s;
                        """, (prise_id,))

                        # ── Créer une alerte pour le dashboard du médecin ──
                        cursor.execute("""
                            INSERT INTO alertes (patient_id, type, message, created_at, lu)
                            VALUES (%s, 'dose_manquee', %s, NOW(), FALSE);
                        """, (
                            patient_id,
                            f"Dose {moment} manquee - {maintenant.strftime('%d/%m/%Y')}"
                        ))

                        conn.commit()
                        print(f"⚠️ Dose manquée détectée : {moment} ({maintenant.strftime('%H:%M')})")

                cursor.close()
            except Exception as e:
                print(f"❌ Erreur tâche doses manquées (BDD) : {e}")
            finally:
                conn.close()

        except Exception as e:
            print(f"❌ Erreur tâche doses manquées : {e}")

        # ── Attendre 5 minutes ──
        await asyncio.sleep(300)


# ─────────────────────────────────────────────────────────────
# TÂCHE PLANIFIÉE : GÉNÉRATION AUTOMATIQUE DES PRISES DU JOUR
# ─────────────────────────────────────────────────────────────
#
# Problème résolu :
#   Le seed.py génère 90 jours passés + 30 jours futurs d'un coup.
#   Mais quand ces 30 jours sont passés, il n'y a plus de prises
#   en_attente pour les nouveaux jours → le système est aveugle.
#
# Solution :
#   Cette tâche vérifie toutes les 30 minutes si les prises
#   du jour existent dans la table prises.
#   Si elles n'existent pas → elle les crée avec statut='en_attente'.
#
# IMPORTANT : les heures prévues sont lues depuis le profil horaire
# actif (table intervalles_profils) via _get_moments_config().
# Si le médecin change le profil → les nouvelles prises utilisent
# automatiquement les nouveaux milieux de plages.
#
# Si une plage est désactivée (00:00) → _get_moments_config()
# l'ignore → pas de prise créée pour ce moment.
#
# Pourquoi toutes les 30 min et pas juste à minuit ?
#   - Si le backend redémarre en milieu de journée, les prises
#     du jour doivent quand même être créées
#   - La vérification est idempotente (si les prises existent déjà,
#     on ne fait rien → pas de doublons)
# ─────────────────────────────────────────────────────────────

async def tache_generation_prises():
    """
    Boucle infinie qui vérifie toutes les 30 minutes
    si les prises du jour existent. Si non, les crée
    selon le profil horaire actif.
    """

    # Attendre 3 secondes au démarrage pour que la BDD soit prête
    await asyncio.sleep(3)
    print("⏰ Tâche génération prises démarrée — vérification toutes les 30 min")

    while True:
        try:
            # ── Vérifier que le système est ON ──
            if not config_state.get("system_on", False):
                await asyncio.sleep(1800)
                continue

            if config_state.get("hospitalisation", False):
                await asyncio.sleep(1800)
                continue

            maintenant = datetime.now()
            aujourd_hui = maintenant.date()

            # ── Lire le profil horaire actif depuis Supabase ──
            # _get_moments_config() lit intervalles_profils WHERE actif=TRUE
            # et calcule le milieu de chaque plage active.
            # Les plages désactivées (00:00) sont ignorées.
            # Fallback : 08:30 / 13:30 / 20:30 si aucun profil trouvé.
            moments_config = _get_moments_config()

            conn = get_connection()
            try:
                cursor = conn.cursor()

                # ── Récupérer le patient et sa prescription active ──
                cursor.execute("SELECT id FROM patients LIMIT 1;")
                row = cursor.fetchone()
                if not row:
                    cursor.close()
                    conn.close()
                    await asyncio.sleep(1800)
                    continue
                patient_id = row[0]

                # Récupérer la prescription active (la plus récente)
                cursor.execute("""
                    SELECT id FROM prescriptions
                    WHERE patient_id = %s
                      AND date_debut <= %s
                      AND date_fin >= %s
                    ORDER BY id DESC
                    LIMIT 1;
                """, (patient_id, aujourd_hui, aujourd_hui))

                row_presc = cursor.fetchone()
                if not row_presc:
                    cursor.close()
                    conn.close()
                    await asyncio.sleep(1800)
                    continue
                prescription_id = row_presc[0]

                # ── Vérifier et créer les prises manquantes ──
                prises_creees = 0

                for moment, heure_str in moments_config.items():
                    # Vérifier si la prise existe déjà pour ce moment aujourd'hui
                    cursor.execute("""
                        SELECT id FROM prises
                        WHERE patient_id = %s
                          AND moment = %s
                          AND heure_prevue::date = %s;
                    """, (patient_id, moment, aujourd_hui))

                    if cursor.fetchone():
                        continue  # déjà existante → pas de doublon

                    # ── Créer la prise en_attente ──
                    heure_prevue = datetime.combine(
                        aujourd_hui,
                        datetime.strptime(heure_str, "%H:%M:%S").time()
                    )

                    cursor.execute("""
                        INSERT INTO prises (
                            patient_id, prescription_id, moment,
                            heure_prevue, heure_reelle, statut,
                            poids_avant, poids_apres
                        ) VALUES (%s, %s, %s, %s, NULL, 'en_attente', NULL, NULL);
                    """, (patient_id, prescription_id, moment, heure_prevue))

                    prises_creees += 1

                if prises_creees > 0:
                    conn.commit()
                    print(f"📋 {prises_creees} prise(s) créée(s) pour {aujourd_hui} (profil actif)")

                cursor.close()
            except Exception as e:
                print(f"❌ Erreur tâche génération prises (BDD) : {e}")
            finally:
                conn.close()

        except Exception as e:
            print(f"❌ Erreur tâche génération prises : {e}")

        # ── Attendre 30 minutes ──
        await asyncio.sleep(1800)


# ─────────────────────────────────────────────────────────────
# TÂCHE PLANIFIÉE : SURVEILLANCE HEARTBEAT ESP32
# ─────────────────────────────────────────────────────────────
#
# L'ESP32 envoie un heartbeat sur medicinebox/statut toutes les
# 30 secondes ({"status":"heartbeat"}). Cette tâche vérifie toutes
# les 10 secondes si un heartbeat a été reçu récemment.
#
# LOGIQUE DE DÉCONNEXION (après 40s sans heartbeat) :
#   - esp32_connected = false
#   - Les tâches se gèlent : les prises restent en_attente
#     (PAS marquées manquées → pas de biais ML)
#   - Le bandeau rouge "Boîte non connectée" s'affiche
#   - Le médecin est notifié
#
# LOGIQUE DE RECONNEXION (géré par subscriber.py) :
#   - L'ESP32 envoie "online" → esp32_connected = true
#   - L'ESP32 vide son SPIFFS → publie les prises stockées offline
# ─────────────────────────────────────────────────────────────

# Variable globale : timestamp du dernier heartbeat reçu
# Mis à jour par subscriber.py quand il reçoit "online" ou "heartbeat"
dernier_heartbeat = datetime.now()

def mettre_a_jour_heartbeat():
    """Appelée par subscriber.py à chaque heartbeat/online reçu"""
    global dernier_heartbeat
    dernier_heartbeat = datetime.now()
    config_state["esp32_connected"] = True


async def tache_heartbeat():
    """
    Vérifie toutes les 10 secondes si l'ESP32 a envoyé un heartbeat.
    Si pas de heartbeat depuis 40s → déconnexion détectée.
    """
    await asyncio.sleep(15)
    print("⏰ Tâche heartbeat démarrée — vérification toutes les 10 min")

    deja_deconnecte = False  # éviter de notifier le médecin en boucle

    while True:
        try:
            global dernier_heartbeat
            maintenant = datetime.now()
            delta = maintenant - dernier_heartbeat

            if delta > timedelta(seconds=40):
                # ── ESP32 DÉCONNECTÉ ──
                if not deja_deconnecte:
                    config_state["esp32_connected"] = False
                    deja_deconnecte = True
                    creer_alerte_systeme(
                        "systeme",
                        "Boîte déconnectée depuis plus de 24h"
                    )
                    print("🔴 ESP32 déconnecté depuis 24h — doses gelées")

            elif delta < timedelta(hours=24) and deja_deconnecte:
                # ── ESP32 RECONNECTÉ ──
                deja_deconnecte = False
                print("🟢 ESP32 reconnecté — en attente du vidage EEPROM")

        except Exception as e:
            print(f"❌ Erreur tâche heartbeat : {e}")

        # Vérifier toutes les 10 secondes
        await asyncio.sleep(10)


# ─────────────────────────────────────────────────────────────
# LIFESPAN — DÉMARRAGE ET ARRÊT DE L'APPLICATION
# ─────────────────────────────────────────────────────────────
#
# Au démarrage :
#   1. Nettoie les prises obsolètes (boîte éteinte longtemps)
#   2. Lance le subscriber MQTT (écoute ESP32)
#   3. Lance la tâche d'envoi des alertes buzzer (toutes les 60s)
#   4. Lance la tâche de détection des doses manquées (toutes les 5 min)
#   5. Lance la tâche de génération des prises du jour (toutes les 30 min)
#   6. Lance la tâche heartbeat (toutes les 10s)
#
# À l'arrêt :
#   1. Annule les tâches en arrière-plan
#   2. Déconnecte MQTT proprement
# ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Nettoyage des prises obsolètes au démarrage ──
    # Supprime les prises en_attente dont l'heure est déjà passée
    # Évite les faux manquées après extinction de la boîte
    await nettoyer_prises_obsoletes()

    # ── Démarrage : lance le subscriber MQTT ──
    mqtt_client = start_mqtt()
    app.state.mqtt_client = mqtt_client

    # ── Démarrage : lance les tâches en arrière-plan ──
    # asyncio.create_task() = exécute la fonction en parallèle
    # sans bloquer le démarrage de FastAPI
    tache1 = asyncio.create_task(tache_alertes(mqtt_client))
    tache2 = asyncio.create_task(tache_doses_manquees(mqtt_client))
    tache3 = asyncio.create_task(tache_generation_prises())
    tache4 = asyncio.create_task(tache_heartbeat())

    print("🟢 Medicine Box API prête")
    print("   ⏰ Tâche alertes buzzer      : active (toutes les 60s)")
    print("   ⏰ Tâche doses manquées      : active (toutes les 5 min)")
    print("   ⏰ Tâche génération prises   : active (toutes les 30 min)")
    print("   ⏰ Tâche heartbeat ESP32     : active (toutes les 10 min)")

    yield

    # ── Arrêt : annuler les tâches proprement ──
    tache1.cancel()
    tache2.cancel()
    tache3.cancel()
    tache4.cancel()

    # ── Arrêt : déconnecte MQTT proprement ──
    if mqtt_client:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        print("🔴 MQTT déconnecté")


# ─────────────────────────────────────────────────────────────
# APPLICATION FASTAPI
# ─────────────────────────────────────────────────────────────

app = FastAPI(title="Medicine Box API", lifespan=lifespan)

# ── CORS — permet à l'app HTML d'appeler l'API depuis un navigateur ──
# Sans CORS, le navigateur bloque les requêtes fetch() vers localhost:8000
# quand le HTML est ouvert depuis un fichier local ou un autre port
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # en production → restreindre à l'URL de l'app
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Importe toutes les routes depuis api/routes.py ──
from api.routes import router
from fastapi.staticfiles import StaticFiles
app.mount("/app", StaticFiles(directory="api/static", html=True), name="static")
app.include_router(router)