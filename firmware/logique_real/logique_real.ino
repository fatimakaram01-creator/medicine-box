#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "HX711.h"
#include <WiFi.h>
#include <time.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <SPIFFS.h>           // ← NOUVEAU : stockage local quand WiFi indisponible

// ======================= CONFIGURATION WIFI + NTP =======================
const char* WIFI_SSID = "TON_WIFI";
const char* WIFI_PASS = "TON_MOT_DE_PASSE";

// ======================= CONFIGURATION MQTT =======================
const char* MQTT_SERVER = "192.168.1.100";  // ← REMPLACER par l'IP de ton PC
const int   MQTT_PORT   = 1883;

#define TOPIC_ALERTE "medicinebox/alerte"
#define TOPIC_PRISE  "medicinebox/prise"
#define TOPIC_STATUT "medicinebox/statut"

WiFiClient espClient;
PubSubClient mqtt(espClient);

// ======================= BUZZER =======================
#define BUZZER_PIN 15 // ← REMPLACER par le bon GPIO de ton buzzer
#define BUZZER_DUREE_MS 30000

bool buzzerActif = false;
unsigned long buzzerDebutMs = 0;

// ======================= HEARTBEAT =======================
// Envoie un ping au backend toutes les 5 minutes
// Le backend utilise ce heartbeat pour savoir si la boîte est connectée
// Si pas de heartbeat depuis 24h → le backend gèle les doses (pas de faux manque)
#define HEARTBEAT_INTERVAL_MS 300000  // 5 minutes
unsigned long dernierHeartbeat = 0;

// ======================= SPIFFS BUFFER =======================
// Quand le WiFi est indisponible (montagne, travail, panne internet),
// les prises confirmées par le HX711 sont sauvegardées dans un fichier
// SPIFFS au lieu d'être publiées sur MQTT.
//
// Format : une ligne JSON par prise dans /prises_buffer.json
//   {"patient_id":1,"moment":"matin","poids_avant":12500,"poids_apres":10200,"date":"2026-04-28"}
//
// Quand le WiFi revient → on vide le buffer en publiant chaque ligne
// sur medicinebox/prise → le backend rattrape les prises manquées.
// Puis le fichier est supprimé.
//
// Capacité : SPIFFS ≈ 1.5 MB, une prise ≈ 100 octets → ~15 000 prises
// À 3 prises/jour → ~13 ans de stockage. Aucun risque de remplir.
#define BUFFER_FILE "/prises_buffer.json"

// Variable pour détecter la transition déconnecté → connecté
bool etaitConnecte = false;

void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  int t = 0;
  while (WiFi.status() != WL_CONNECTED && t < 20) { delay(500); t++; }
}

void syncNTP() {
  configTime(3600, 0, "pool.ntp.org");
  struct tm ti;
  int t = 0;
  while (!getLocalTime(&ti) && t < 10) { delay(500); t++; }
}

// ======================= MQTT : CALLBACK =======================
void mqttCallback(char* topic, byte* payload, unsigned int length) {
  char message[256];
  int len = min((int)length, 255);
  memcpy(message, payload, len);
  message[len] = '\0';

  Serial.print("MQTT recu [");
  Serial.print(topic);
  Serial.print("] : ");
  Serial.println(message);

  if (String(topic) == TOPIC_ALERTE) {
    StaticJsonDocument<200> doc;
    DeserializationError err = deserializeJson(doc, message);
    if (err) {
      Serial.print("Erreur JSON : ");
      Serial.println(err.c_str());
      return;
    }

    const char* action = doc["action"];
    if (action && String(action) == "buzzer_on") {
      buzzerActif = true;
      buzzerDebutMs = millis();
      digitalWrite(BUZZER_PIN, HIGH);

      const char* moment = doc["moment"] | "---";
      char ligne1[22];
      snprintf(ligne1, sizeof(ligne1), "ALERTE %s", moment);
      afficherOLED(ligne1, "Prenez vos medicaments");

      Serial.print("BUZZER ON - moment : ");
      Serial.println(moment);
    }
  }
}

// ======================= MQTT : CONNEXION =======================
void connectMQTT() {
  mqtt.setServer(MQTT_SERVER, MQTT_PORT);
  mqtt.setCallback(mqttCallback);

  int tentatives = 0;
  while (!mqtt.connected() && tentatives < 3) {
    Serial.print("Connexion MQTT...");
    if (mqtt.connect("MedicineBox_ESP32")) {
      Serial.println("connecte !");
      mqtt.subscribe(TOPIC_ALERTE);
      mqtt.subscribe("medicinebox/config");  // ← pour recevoir system_on/off, ramadan, etc.
      Serial.println("Abonne a medicinebox/alerte + medicinebox/config");
    } else {
      Serial.print("echec, code=");
      Serial.print(mqtt.state());
      Serial.println(" nouvelle tentative dans 2s...");
      delay(2000);
      tentatives++;
    }
  }
}

// ======================= MQTT : PUBLIER STATUT =======================
// Types de statut envoyés au backend :
//   "online"      → ESP32 vient de démarrer ou de se reconnecter
//   "heartbeat"   → ping périodique toutes les 5 min
//   "error"       → erreur hardware (homing échoué)
//   "eeprom_vide" → buffer SPIFFS vide (rien à envoyer après reconnexion)
//   "eeprom_fin"  → fin du vidage SPIFFS (toutes les prises envoyées)
void publierStatut(const char* status, const char* msg) {
  if (!mqtt.connected()) return;

  StaticJsonDocument<200> doc;
  doc["status"] = status;
  if (msg != nullptr && strlen(msg) > 0) {
    doc["message"] = msg;
  }

  char buffer[200];
  serializeJson(doc, buffer);
  mqtt.publish(TOPIC_STATUT, buffer);

  Serial.print("MQTT publie [statut] : ");
  Serial.println(buffer);
}

// ======================= SPIFFS : INIT =======================
// Initialiser SPIFFS — appelé dans setup()
// true = formater automatiquement si c'est le premier usage
void initSPIFFS() {
  if (!SPIFFS.begin(true)) {
    Serial.println("SPIFFS : echec init");
  } else {
    Serial.println("SPIFFS : OK");
  }
}

// ======================= SPIFFS : SAUVEGARDER PRISE LOCALE =======================
// Appelée quand WiFi/MQTT est down et qu'une prise est confirmée par le HX711
// Sauvegarde la prise dans /prises_buffer.json avec la date du jour
// pour que le backend sache quel jour cette prise concerne (rattrapage)
void sauvegarderPriseLocale(const char* moment, long poidsAvant, long poidsApres) {
  // Récupérer la date actuelle (NTP ou horloge interne)
  struct tm ti;
  char dateStr[11] = "0000-00-00";
  if (getLocalTime(&ti)) {
    snprintf(dateStr, sizeof(dateStr), "%04d-%02d-%02d",
             ti.tm_year + 1900, ti.tm_mon + 1, ti.tm_mday);
  }

  // Construire la ligne JSON
  StaticJsonDocument<200> doc;
  doc["patient_id"] = 1;
  doc["moment"] = moment;
  doc["poids_avant"] = poidsAvant;
  doc["poids_apres"] = poidsApres;
  doc["date"] = dateStr;  // ← date de la prise pour le rattrapage

  char ligne[200];
  serializeJson(doc, ligne);

  // Ajouter au fichier (mode append = ajout à la fin)
  File f = SPIFFS.open(BUFFER_FILE, FILE_APPEND);
  if (f) {
    f.println(ligne);  // une ligne par prise
    f.close();
    Serial.print("SPIFFS sauvegarde : ");
    Serial.println(ligne);
  } else {
    Serial.println("SPIFFS : erreur ecriture");
  }
}

// ======================= SPIFFS : VIDER LE BUFFER =======================
// Appelée quand le WiFi revient et que MQTT est connecté
// Publie chaque prise stockée sur medicinebox/prise
// Le subscriber.py reçoit ces prises retardées (avec champ "date")
// et fait UPDATE prises SET statut='pris' pour le bon jour
//
// Après vidage :
//   - Données envoyées → supprime le fichier → envoie "eeprom_fin"
//   - Fichier vide ou absent → envoie "eeprom_vide"
//     (le backend sait alors que la boîte était éteinte → efface les en_attente)
void viderBufferSPIFFS() {
  if (!SPIFFS.exists(BUFFER_FILE)) {
    Serial.println("SPIFFS : buffer vide — aucune prise stockee");
    publierStatut("eeprom_vide", "");
    return;
  }

  File f = SPIFFS.open(BUFFER_FILE, FILE_READ);
  if (!f) {
    Serial.println("SPIFFS : erreur lecture");
    publierStatut("eeprom_vide", "");
    return;
  }

  int count = 0;
  while (f.available()) {
    String ligne = f.readStringUntil('\n');
    ligne.trim();
    if (ligne.length() > 0) {
      // Publier chaque prise stockée sur MQTT
      mqtt.publish(TOPIC_PRISE, ligne.c_str());
      count++;
      Serial.print("SPIFFS -> MQTT : ");
      Serial.println(ligne);
      delay(100);  // petit délai pour ne pas surcharger le broker
    }
  }
  f.close();

  // Supprimer le fichier buffer (vidé avec succès)
  SPIFFS.remove(BUFFER_FILE);

  if (count > 0) {
    Serial.print("SPIFFS : ");
    Serial.print(count);
    Serial.println(" prise(s) envoyee(s) au backend");
    publierStatut("eeprom_fin", "");
  } else {
    publierStatut("eeprom_vide", "");
  }
}

// ======================= MQTT : PUBLIER PRISE =======================
// Envoie la confirmation de prise sur medicinebox/prise
// SI le WiFi/MQTT est disponible → publie directement
// SI le WiFi/MQTT est indisponible → sauvegarde dans SPIFFS
//
// C'est la fonction clé qui garantit qu'aucune prise n'est perdue :
//   - WiFi OK → donnée envoyée immédiatement au backend
//   - WiFi DOWN → donnée stockée localement → envoyée à la reconnexion
void publierPrise(long poidsAvant, long poidsApres) {
  // Déterminer le moment actuel (matin/midi/soir)
  struct tm ti;
  const char* moment = "inconnu";
  if (getLocalTime(&ti)) {
    if (ti.tm_hour >= 6 && ti.tm_hour < 12) moment = "matin";
    else if (ti.tm_hour >= 12 && ti.tm_hour < 18) moment = "midi";
    else if (ti.tm_hour >= 18 && ti.tm_hour < 22) moment = "soir";
  }

  if (mqtt.connected()) {
    // ── WiFi OK + MQTT OK → publier directement ──
    StaticJsonDocument<200> doc;
    doc["patient_id"] = 1;
    doc["moment"] = moment;
    doc["poids_avant"] = poidsAvant;
    doc["poids_apres"] = poidsApres;

    char buffer[200];
    serializeJson(doc, buffer);
    mqtt.publish(TOPIC_PRISE, buffer);

    Serial.print("MQTT publie [prise] : ");
    Serial.println(buffer);
  } else {
    // ── WiFi/MQTT indisponible → sauvegarder dans SPIFFS ──
    // La prise sera envoyée automatiquement quand le WiFi reviendra
    Serial.println("MQTT indisponible — sauvegarde locale SPIFFS");
    sauvegarderPriseLocale(moment, poidsAvant, poidsApres);
  }
}

// ======================= HEARTBEAT =======================
// Envoie un ping au backend toutes les 5 minutes
// Le backend vérifie : pas de heartbeat depuis 24h → boîte déconnectée
// → les doses sont gelées (restent en_attente, pas de faux manque)
void envoyerHeartbeat() {
  unsigned long maintenant = millis();
  if (maintenant - dernierHeartbeat >= HEARTBEAT_INTERVAL_MS) {
    dernierHeartbeat = maintenant;
    if (mqtt.connected()) {
      publierStatut("heartbeat", "");
      Serial.println("Heartbeat envoye");
    }
  }
}

// ======================= POSITION =======================
int calculerPosition() {
  struct tm ti;
  if (!getLocalTime(&ti)) return -1;

  int jour = (ti.tm_wday == 0) ? 6 : ti.tm_wday - 1;

  int moment;
  if (ti.tm_hour >= 6 && ti.tm_hour < 12) moment = 0;       // matin
  else if (ti.tm_hour >= 12 && ti.tm_hour < 18) moment = 1;  // midi
  else if (ti.tm_hour >= 18 && ti.tm_hour < 22) moment = 2;  // soir
  else return -1;  // nuit (22h → 6h) → pas de prise

  return 1 + (jour * 3) + moment;
}

// ======================= OLED =======================
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 32
#define OLED_RESET -1
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
bool oledOK = false;

// ======================= PINS ESP32 =======================
#define MOTOR_PIN1 14
#define MOTOR_PIN2 27
#define MOTOR_PIN3 26
#define MOTOR_PIN4 25
#define REED_PIN   4
#define COVER_PIN 2
 // probleme ici normalement pas de led
#define HX711_DT  32
#define HX711_SCK 33

// ======================= HX711 =======================
HX711 scale;

// ======================= CONFIG =======================
#define STEP_DELAY 3
#define DROP_THRESHOLD_DETECT 25L
#define DROP_THRESHOLD_CLEAR 10L
#define NB_CONFIRMATIONS_PRISE 4
#define STEPS_PER_REV 4096

const int stepsPC[22] = {
  186,186,186,186,186,186,186,186,186,186,187,
  186,186,186,186,186,186,186,186,186,186,187
};

const int stepSeq[8][4] = {
  {1,0,0,0},{1,1,0,0},{0,1,0,0},{0,1,1,0},
  {0,0,1,0},{0,0,1,1},{0,0,0,1},{1,0,0,1}
};

// ======================= VARIABLES =======================
int sIdx = 0;
int positionCourante = -1;
int positionCible = 1;
bool homingFait = false;
bool compartimentPresente = false;
bool priseFaite = false;
bool etatCouvercle = false;
bool ancienEtatCouvercle = false;
bool pretPourOuverture = false;
long poidsReference = 0;
long poidsApres = 0;
int confirmationsPrise = 0;

const char* compartiments[22] = {
  "VIDE",
  "Lun matin", "Lun midi", "Lun soir",
  "Mar matin", "Mar midi", "Mar soir",
  "Mer matin", "Mer midi", "Mer soir",
  "Jeu matin", "Jeu midi", "Jeu soir",
  "Ven matin", "Ven midi", "Ven soir",
  "Sam matin", "Sam midi", "Sam soir",
  "Dim matin", "Dim midi", "Dim soir"
};

// ======================= OLED =======================
void afficherOLED(const char* l1, const char* l2) {
  if (!oledOK) return;
  display.clearDisplay();
  display.setTextColor(WHITE);
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println(l1);
  display.setCursor(0, 16);
  display.println(l2);
  display.display();
}

// ======================= REED POS0 STABLE =======================
int lireStableReedPos0() {
  int countLow = 0;
  for (int i = 0; i < 10; i++) {
    if (digitalRead(REED_PIN) == LOW) countLow++;
    delay(2);
  }
  return (countLow > 7) ? LOW : HIGH;
}

bool reedPos0Detecte() {
  return (lireStableReedPos0() == LOW);
}

// ======================= COUVERCLE STABLE =======================
int lireStableCouvercle() {
  int countHigh = 0;
  for (int i = 0; i < 10; i++) {
    if (digitalRead(COVER_PIN) == HIGH) countHigh++;
    delay(2);
  }
  return (countHigh > 7) ? HIGH : LOW;
}

bool couvercleEstOuvert() {
  return (lireStableCouvercle() == HIGH);
}

// ======================= MOTEUR =======================
void pas(int d) {
  sIdx = (sIdx + d + 8) % 8;
  digitalWrite(MOTOR_PIN1, stepSeq[sIdx][0]);
  digitalWrite(MOTOR_PIN2, stepSeq[sIdx][1]);
  digitalWrite(MOTOR_PIN3, stepSeq[sIdx][2]);
  digitalWrite(MOTOR_PIN4, stepSeq[sIdx][3]);
  delay(STEP_DELAY);
}

void stopM() {
  digitalWrite(MOTOR_PIN1, LOW);
  digitalWrite(MOTOR_PIN2, LOW);
  digitalWrite(MOTOR_PIN3, LOW);
  digitalWrite(MOTOR_PIN4, LOW);
}

// ======================= HX711 =======================
long lirePoidsMoyen(int n) {
  long somme = 0;
  int lecturesValides = 0;
  for (int i = 0; i < n; i++) {
    if (scale.is_ready()) {
      somme += scale.read();
      lecturesValides++;
    }
    delay(10);
  }
  if (lecturesValides == 0) return 0;
  return somme / lecturesValides;
}

// ======================= CALCUL PAS =======================
int calculerPasEntrePositions(int fromPos, int toPos) {
  int total = 0;
  if (toPos > fromPos) {
    for (int p = fromPos; p < toPos; p++) total += stepsPC[p];
  } else if (toPos < fromPos) {
    for (int p = toPos; p < fromPos; p++) total += stepsPC[p];
  }
  return total;
}

// ======================= DEPLACEMENT =======================
void allerACompartiment(int cible) {
  int totalPas = calculerPasEntrePositions(0, cible);
  char ligne2[22];

  afficherOLED("Couvercle ouvert", compartiments[cible]);
  Serial.print("Aller vers : ");
  Serial.println(compartiments[cible]);

  for (int i = 0; i < totalPas; i++) {
    pas(1);
    if ((i % 30) == 0 || i == totalPas - 1) {
      int restePas = totalPas - i - 1;
      int resteDeg = (restePas * 360L) / STEPS_PER_REV;
      snprintf(ligne2, sizeof(ligne2), "Reste %d deg", resteDeg);
      afficherOLED(compartiments[cible], ligne2);
    }
  }

  stopM();
  positionCourante = cible;
  afficherOLED(compartiments[cible], "Attente prise");
  Serial.println("Compartiment arrive");
}

void retourAZero() {
  if (positionCourante <= 0) return;

  int totalPas = calculerPasEntrePositions(0, positionCourante);
  char ligne2[22];

  afficherOLED("Retour", "POS0");
  Serial.println("Retour position 0");

  for (int i = 0; i < totalPas; i++) {
    pas(-1);
    if ((i % 30) == 0 || i == totalPas - 1) {
      int restePas = totalPas - i - 1;
      int resteDeg = (restePas * 360L) / STEPS_PER_REV;
      snprintf(ligne2, sizeof(ligne2), "Reste %d deg", resteDeg);
      afficherOLED("Retour POS0", ligne2);
    }
  }

  stopM();
  positionCourante = 0;
  afficherOLED("Repos", "POS0");
  Serial.println("Retour POS0 termine");
}

// ======================= HOMING =======================
void faireHoming() {
  afficherOLED("HOMING", "Recherche POS0");
  Serial.println("Homing...");

  bool ok = false;
  for (int i = 0; i < 4396; i++) {
    if (reedPos0Detecte()) {
      ok = true;
      break;
    }
    pas(1);
  }

  stopM();

  if (ok) {
    homingFait = true;
    positionCourante = 0;

    afficherOLED("POS0 detectee", "OK");
    Serial.println("POS0 detectee");
  } else {
    afficherOLED("ERREUR", "Reed absent");
    Serial.println("ERREUR HOMING");
  }

  delay(1000);
}

// ======================= BUZZER : GESTION =======================
void gererBuzzer() {
  if (!buzzerActif) return;

  if (millis() - buzzerDebutMs >= BUZZER_DUREE_MS) {
    digitalWrite(BUZZER_PIN, LOW);
    buzzerActif = false;
    Serial.println("BUZZER OFF (30s ecoulees)");

    if (!compartimentPresente) {
      afficherOLED("Attente", "ouverture");
    }
  }
}

// ======================= MQTT : RECONNEXION =======================
// Vérifie la connexion MQTT à chaque tour de loop()
// Si reconnecté après une déconnexion :
//   1. Envoie "online" au backend
//   2. Vide le buffer SPIFFS (prises stockées pendant la déconnexion)
//   Le subscriber.py attend ces données pendant 30s puis décide :
//     - Données reçues → rattrapage (UPDATE prises)
//     - Rien reçu → effacement (DELETE prises en_attente)
void maintienMQTT() {
  // Vérifier si le WiFi est toujours connecté
  if (WiFi.status() != WL_CONNECTED) {
    etaitConnecte = false;
    return;  // pas de WiFi → pas la peine de tenter MQTT
  }

  if (!mqtt.connected()) {
    etaitConnecte = false;
    Serial.println("MQTT deconnecte, tentative reconnexion...");
    if (mqtt.connect("MedicineBox_ESP32")) {
      mqtt.subscribe(TOPIC_ALERTE);
      mqtt.subscribe("medicinebox/config");
      Serial.println("MQTT reconnecte + re-abonne");

      // ── RECONNEXION DÉTECTÉE ──
      // Dire au backend qu'on est de retour
      publierStatut("online", "Reconnexion");

      // Vider le buffer SPIFFS
      // Les prises stockées localement sont envoyées au backend
      // Si le buffer est vide (boîte était éteinte) → envoie "eeprom_vide"
      viderBufferSPIFFS();

      etaitConnecte = true;
    } else {
      Serial.println("MQTT reconnexion echouee");
    }
  } else {
    if (!etaitConnecte) {
      etaitConnecte = true;
    }
  }

  // Traiter les messages en attente
  mqtt.loop();
}

// ======================= SETUP =======================
void setup() {
  Serial.begin(115200);

  pinMode(MOTOR_PIN1, OUTPUT);
  pinMode(MOTOR_PIN2, OUTPUT);
  pinMode(MOTOR_PIN3, OUTPUT);
  pinMode(MOTOR_PIN4, OUTPUT);
  pinMode(REED_PIN, INPUT_PULLUP);
  pinMode(COVER_PIN, INPUT_PULLUP);


  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, HIGH);

  stopM();

  Wire.begin(16, 17);
  oledOK = display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  if (oledOK) {
    afficherOLED("Medicine Box", "Init ESP32...");
  }

  scale.begin(HX711_DT, HX711_SCK);

  // ── NOUVEAU : Initialiser SPIFFS (stockage local) ──
  initSPIFFS();

  delay(800);

  faireHoming();

  connectWiFi();
  syncNTP();

  connectMQTT();

  if (homingFait) {
    publierStatut("online", "Demarrage OK");
    // Vider le buffer SPIFFS au démarrage (si des prises étaient stockées)
    viderBufferSPIFFS();
  } else {
    publierStatut("error", "Reed switch non detecte");
    return;
  }

  etatCouvercle = couvercleEstOuvert();
  ancienEtatCouvercle = etatCouvercle;

  if (etatCouvercle == false) {
    pretPourOuverture = true;
  } else {
    pretPourOuverture = false;
  }

  afficherOLED("Attente", "ouverture");
}

// ======================= LOOP =======================
void loop() {
  if (!homingFait) return;

  // ── Maintenir la connexion MQTT ──
  // Si reconnecté après déconnexion → vide le buffer SPIFFS automatiquement
  maintienMQTT();

  // ── Heartbeat toutes les 5 minutes ──
  // Le backend sait que la boîte est connectée
  envoyerHeartbeat();

  // ── Gérer le buzzer (arrêt automatique après 30s) ──
  gererBuzzer();

  etatCouvercle = couvercleEstOuvert();

  if (!compartimentPresente && etatCouvercle == false && !buzzerActif) {
    afficherOLED("Attente", "ouverture");
  }

  // ──────── ÉVÉNEMENT D'OUVERTURE ────────
  if (pretPourOuverture &&
      etatCouvercle == true &&
      ancienEtatCouvercle == false &&
      !compartimentPresente) {

    Serial.println("Evenement ouverture valide");

    priseFaite = false;
    confirmationsPrise = 0;

    positionCible = calculerPosition();

    if (positionCible < 1) {
      afficherOLED("Hors horaire", "Pas de prise");
      ancienEtatCouvercle = etatCouvercle;
      return;
    }

    allerACompartiment(positionCible);
    compartimentPresente = true;
    pretPourOuverture = false;

    poidsReference = lirePoidsMoyen(12);
    Serial.print("Poids ref HX711 = ");
    Serial.println(poidsReference);
  }

  // ──────── SURVEILLANCE PENDANT OUVERTURE ────────
  if (etatCouvercle && compartimentPresente) {
    long valeurActuelle = lirePoidsMoyen(12);
    long baisse = poidsReference - valeurActuelle;

    Serial.print("Poids ref = ");
    Serial.print(poidsReference);
    Serial.print(" | actuel = ");
    Serial.print(valeurActuelle);
    Serial.print(" | baisse = ");
    Serial.print(baisse);
    Serial.print(" | conf = ");
    Serial.print(confirmationsPrise);
    Serial.print(" | prise = ");
    Serial.println(priseFaite);

    if (priseFaite) {
      afficherOLED(compartiments[positionCible], "PRISE DETECTEE");
      delay(250);
      ancienEtatCouvercle = etatCouvercle;
      return;
    }

    if (baisse >= DROP_THRESHOLD_DETECT) {
      confirmationsPrise++;
      if (confirmationsPrise >= NB_CONFIRMATIONS_PRISE) {
        priseFaite = true;
        afficherOLED(compartiments[positionCible], "PRISE DETECTEE");
      } else {
        afficherOLED(compartiments[positionCible], "Verif prise...");
      }
    }
    else if (baisse >= DROP_THRESHOLD_CLEAR) {
      afficherOLED(compartiments[positionCible], "Stabilisation...");
    }
    else {
      confirmationsPrise = 0;
      afficherOLED(compartiments[positionCible], "Non prise");
    }

    delay(250);
  }

  // ──────── ÉVÉNEMENT DE FERMETURE ────────
  if (etatCouvercle == false &&
      ancienEtatCouvercle == true &&
      compartimentPresente) {

    Serial.println("Evenement fermeture valide");

    if (priseFaite) {
      poidsApres = lirePoidsMoyen(12);

      // ── Publier la prise (MQTT si connecté, SPIFFS si déconnecté) ──
      publierPrise(poidsReference, poidsApres);

      afficherOLED("Prise OK", "Retour POS0");
    } else {
      afficherOLED("Aucune prise", "Retour POS0");
    }

    delay(700);

    retourAZero();

    if (!reedPos0Detecte()) {
      faireHoming();
    }

    compartimentPresente = false;
    pretPourOuverture = true;
    confirmationsPrise = 0;
    afficherOLED("Attente", "ouverture");
  }

  ancienEtatCouvercle = etatCouvercle;
  delay(80);
}