# mqtt/simulator.py
# ─────────────────────────────────────────────────────────────
# Simule l'ESP32 en publiant des messages MQTT
# Utile pour tester le backend sans le hardware
#
# Usage : python -m mqtt.simulator
# ─────────────────────────────────────────────────────────────

import json
import time
import random
import paho.mqtt.client as mqtt

MQTT_BROKER = "localhost"
MQTT_PORT = 1883

client = mqtt.Client(client_id="medicinebox-simulator")


def publish(topic, data):
    """Publie un message JSON sur un topic MQTT."""
    payload = json.dumps(data)
    client.publish(topic, payload)
    print(f"📤 Publié sur {topic} : {data}")


def simuler_prise(moment="matin"):
    """
    Simule une prise confirmée par le firmware v4.1.
    Le firmware a déjà filtré le bruit du HX711,
    appliqué le DROP_THRESHOLD et confirmé la prise.
    """
    poids_avant = round(random.uniform(40, 50), 1)
    poids_apres = round(poids_avant - random.uniform(3, 8), 1)
    publish("medicinebox/prise", {
        "patient_id": 1,
        "moment": moment,
        "poids_avant": poids_avant,
        "poids_apres": poids_apres,
    })


def simuler_statut(status="online"):
    """Simule un message de statut ESP32."""
    messages = {
        "online": "Démarrage OK — calibration réussie",
        "error": "Calibration échouée — reed switch non détecté",
        "calibrating": "Calibration en cours..."
    }
    publish("medicinebox/statut", {
        "status": status,
        "message": messages.get(status, ""),
    })


def run_simulation():
    """
    Scénario d'une journée :
    1. ESP32 démarre → online
    2. Prise matin
    3. Prise midi
    4. Prise soir
    5. Erreur ESP32 (test alerte)
    """
    print("=" * 50)
    print("🔬 Simulation Medicine Box")
    print("=" * 50)

    print("\n── 1. ESP32 démarre ──")
    simuler_statut("online")
    time.sleep(2)

    print("\n── 2. Prise matin ──")
    simuler_prise("matin")
    time.sleep(2)

    print("\n── 3. Prise midi ──")
    simuler_prise("midi")
    time.sleep(2)

    print("\n── 4. Prise soir ──")
    simuler_prise("soir")
    time.sleep(2)

    print("\n── 5. Erreur ESP32 (test) ──")
    simuler_statut("error")
    time.sleep(1)

    print("\n" + "=" * 50)
    print("✅ Simulation terminée")
    print("=" * 50)


if __name__ == "__main__":
    try:
        client.connect(MQTT_BROKER, MQTT_PORT)
        client.loop_start()
        print(f"✅ Connecté au broker ({MQTT_BROKER}:{MQTT_PORT})\n")
        run_simulation()
    except ConnectionRefusedError:
        print("❌ Broker MQTT non disponible !")
        print("   Lance : brew services start mosquitto")
    except KeyboardInterrupt:
        print("\n⏹ Arrêté")
    finally:
        client.loop_stop()
        client.disconnect()
