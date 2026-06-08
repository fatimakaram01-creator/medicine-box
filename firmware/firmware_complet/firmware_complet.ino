// =====================================================
// FIRMWARE COMPLET v5 — Logique réelle + Mode Remplissage
// =====================================================
// Fusionne :
//   - firmware_complet (WiFi AP, MQTT HiveMQ TLS, NTP, SPIFFS,
//     homing, couvercle, HX711, buzzer, heartbeat)
//   - logique_mod_remplissage_real (boutons Next/Prev, navigation
//     22 compartiments, session remplissage)
// =====================================================

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "HX711.h"
#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <SPIFFS.h>
#include <time.h>
#include <HTTPClient.h>

// ======================= MQTT HiveMQ Cloud =======================
// ======================= Backend API =======================
const char* BACKEND_URL = "https://medicine-box-zk7r.onrender.com";

// ======================= MQTT HiveMQ Cloud =======================
const char* MQTT_BROKER = "a46cf5176dea4d39974b641766e0a18c.s1.eu.hivemq.cloud";
const int   MQTT_PORT   = 8883;
const char* MQTT_USER   = "medicinebox";
const char* MQTT_PASS   = "MedBox2026!";

// TOPIC_ALERTE est dynamique (dépend du PATIENT_ID) — mis à jour dans connecterMQTT()
String TOPIC_ALERTE    = "medicinebox/alerte/0";
#define TOPIC_PRISE    "medicinebox/prise"
#define TOPIC_STATUT   "medicinebox/statut"
// TOPIC_CONFIG est dynamique (dépend du PATIENT_ID) — mis à jour dans connecterMQTT()
String TOPIC_CONFIG    = "medicinebox/config/0";  // mis à jour après chargement PATIENT_ID
#define TOPIC_COMMANDE "medicinebox/commande"

// ======================= WiFi AP =======================
const char* AP_SSID = "MedicineBox-Setup";
const char* AP_PASS = "medbox123";

// ======================= Objets globaux =======================
WebServer server(80);
Preferences prefs;
int PATIENT_ID = 1;  // Chargé depuis Preferences au démarrage
WiFiClientSecure espClient;
PubSubClient mqtt(espClient);

// ======================= OLED =======================
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 32
#define OLED_RESET -1
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
bool oledOK = false;

// ======================= PINS =======================
#define MOTOR_PIN1   14
#define MOTOR_PIN2   27
#define MOTOR_PIN3   26
#define MOTOR_PIN4   25
#define REED_PIN      4
#define COVER_PIN     2
#define HX711_DT     32
#define HX711_SCK    33
#define BUZZER_PIN   15
#define BTN_NEXT_PIN  5   // Mode remplissage — bouton suivant
#define BTN_PREV_PIN 18   // Mode remplissage — bouton précédent

// ======================= HX711 =======================
HX711 scale;

// ======================= CONFIG MOTEUR =======================
#define STEP_DELAY 3
#define STEPS_PER_REV 4096
#define NB_POSITIONS 22

const int stepsPC[22] = {
  186,186,186,186,186,186,186,186,186,186,187,
  186,186,186,186,186,186,186,186,186,186,187
};

const int stepSeq[8][4] = {
  {1,0,0,0},{1,1,0,0},{0,1,0,0},{0,1,1,0},
  {0,0,1,0},{0,0,1,1},{0,0,0,1},{1,0,0,1}
};

// ======================= CONFIG HX711 =======================
#define DROP_THRESHOLD_DETECT  25L
#define DROP_THRESHOLD_CLEAR   10L
#define NB_CONFIRMATIONS_PRISE  4

// ======================= BUZZER =======================
#define BUZZER_DUREE_MS 30000
bool buzzerActif = false;
unsigned long buzzerDebutMs = 0;

// ======================= HEARTBEAT =======================
#define HEARTBEAT_INTERVAL_MS 30000
unsigned long dernierHeartbeat = 0;

// ======================= SPIFFS BUFFER =======================
#define BUFFER_FILE "/prises_buffer.json"

// ======================= INTERVALLES DYNAMIQUES =======================
// Chargés depuis le backend après connexion WiFi
int MATIN_DEBUT_H  = 6;   int MATIN_FIN_H  = 12;
int MIDI_DEBUT_H   = 12;  int MIDI_FIN_H   = 18;
int SOIR_DEBUT_H   = 18;  int SOIR_FIN_H   = 22;

// ======================= VARIABLES GLOBALES =======================
bool wifiConfiguree   = false;
bool mqttConnecte     = false;
bool etaitConnecte    = false;
bool dernierEtatEnLigne = true;
bool systemActif      = false;  // false par défaut — activé par le backend via MQTT
bool modeSansWifi     = false;  // true → ESP32 stocke prises EEPROM si offline

int sIdx = 0;
int positionCourante = -1;
int positionCible    =  1;
bool homingFait      = false;

// Logique normale
bool compartimentPresente = false;
bool priseFaite           = false;
bool etatCouvercle        = false;
bool ancienEtatCouvercle  = false;
bool pretPourOuverture    = false;
long poidsReference = 0;
long poidsApres     = 0;
int  confirmationsPrise = 0;

// ── MODE REMPLISSAGE ──
bool modeRemplissage       = false;  // activé via MQTT
bool remplissageInitialise = false;  // plateau déjà au comp 1
bool lastNextRaw = HIGH;
bool lastPrevRaw = HIGH;

// ======================= NOMS COMPARTIMENTS =======================
const char* compartiments[22] = {
  "VIDE",
  "Lun matin","Lun midi","Lun soir",
  "Mar matin","Mar midi","Mar soir",
  "Mer matin","Mer midi","Mer soir",
  "Jeu matin","Jeu midi","Jeu soir",
  "Ven matin","Ven midi","Ven soir",
  "Sam matin","Sam midi","Sam soir",
  "Dim matin","Dim midi","Dim soir"
};

// =====================================================
// OLED
// =====================================================
void afficherOLED(const char* l1, const char* l2) {
  if (!oledOK) return;
  display.clearDisplay();
  display.setTextColor(WHITE);
  display.setTextSize(1);
  display.setCursor(0, 0);  display.println(l1);
  display.setCursor(0,16);  display.println(l2);
  display.display();
}

void afficherReposOLED() {
  bool enLigne = (WiFi.status() == WL_CONNECTED && mqtt.connected());
  if (enLigne != dernierEtatEnLigne) {
    dernierEtatEnLigne = enLigne;
    if (!enLigne) {
      afficherOLED("Hors ligne","Connexion...");
    } else if (couvercleEstOuvert()) {
      afficherOLED("Couvercle","ouvert");
    } else {
      afficherOLED("Attente","ouverture");
    }
  }
}

void forcerReposOLED() {
  bool enLigne = (WiFi.status() == WL_CONNECTED && mqtt.connected());
  dernierEtatEnLigne = enLigne;
  if (!enLigne) {
    afficherOLED("Hors ligne","Connexion...");
  } else if (couvercleEstOuvert()) {
    afficherOLED("Fermez le","couvercle");
  } else {
    afficherOLED("Attente","ouverture");
  }
}

// =====================================================
// REED & COUVERCLE
// =====================================================
int lireStableReedPos0() {
  int c = 0;
  for (int i=0;i<10;i++) { if(digitalRead(REED_PIN)==LOW) c++; delay(2); }
  return (c>7)?LOW:HIGH;
}
bool reedPos0Detecte() { return lireStableReedPos0()==LOW; }

int lireStableCouvercle() {
  int c = 0;
  for (int i=0;i<10;i++) { if(digitalRead(COVER_PIN)==HIGH) c++; delay(2); }
  return (c>7)?HIGH:LOW;
}
bool couvercleEstOuvert() { return lireStableCouvercle()==HIGH; }

// =====================================================
// MOTEUR
// =====================================================
void pas(int d) {
  sIdx = (sIdx + d + 8) % 8;
  digitalWrite(MOTOR_PIN1, stepSeq[sIdx][0]);
  digitalWrite(MOTOR_PIN2, stepSeq[sIdx][1]);
  digitalWrite(MOTOR_PIN3, stepSeq[sIdx][2]);
  digitalWrite(MOTOR_PIN4, stepSeq[sIdx][3]);
  delay(STEP_DELAY);
}

void stopM() {
  digitalWrite(MOTOR_PIN1,LOW); digitalWrite(MOTOR_PIN2,LOW);
  digitalWrite(MOTOR_PIN3,LOW); digitalWrite(MOTOR_PIN4,LOW);
}

int calculerPasEntrePositions(int from, int to) {
  int total = 0;
  if (to > from)      for (int p=from; p<to; p++) total += stepsPC[p];
  else if (to < from) for (int p=to;   p<from; p++) total += stepsPC[p];
  return total;
}

// =====================================================
// DÉPLACEMENTS
// =====================================================
void allerACompartiment(int cible) {
  int totalPas = calculerPasEntrePositions(0, cible);
  char ligne2[22];
  afficherOLED("Couvercle ouvert", compartiments[cible]);
  for (int i=0; i<totalPas; i++) {
    pas(1);
    if ((i%30)==0 || i==totalPas-1) {
      int resteDeg = ((totalPas-i-1)*360L)/STEPS_PER_REV;
      snprintf(ligne2,sizeof(ligne2),"Reste %d deg",resteDeg);
      afficherOLED(compartiments[cible], ligne2);
    }
  }
  stopM();
  positionCourante = cible;
  afficherOLED(compartiments[cible],"Attente prise");
}

void retourAZero() {
  if (positionCourante <= 0) return;
  int totalPas = calculerPasEntrePositions(0, positionCourante);
  char ligne2[22];
  afficherOLED("Retour","POS0");
  for (int i=0; i<totalPas; i++) {
    pas(-1);
    if ((i%30)==0 || i==totalPas-1) {
      int resteDeg = ((totalPas-i-1)*360L)/STEPS_PER_REV;
      snprintf(ligne2,sizeof(ligne2),"Reste %d deg",resteDeg);
      afficherOLED("Retour POS0", ligne2);
    }
  }
  stopM();
  positionCourante = 0;
}

// Mode remplissage : avancer d'une position
void remplissageNext() {
  int cible = positionCourante + 1;
  if (cible >= NB_POSITIONS) cible = 0;
  int nbPas = stepsPC[positionCourante];
  for (int i=0; i<nbPas; i++) pas(1);
  stopM();
  positionCourante = cible;
}

// Mode remplissage : reculer d'une position
void remplissagePrev() {
  int precedente = positionCourante - 1;
  if (precedente < 0) precedente = NB_POSITIONS - 1;
  int nbPas = stepsPC[precedente];
  for (int i=0; i<nbPas; i++) pas(-1);
  stopM();
  positionCourante = precedente;
}

// =====================================================
// HOMING
// =====================================================
void faireHoming() {
  afficherOLED("HOMING","Recherche POS0");
  bool ok = false;
  for (int i=0; i<4396; i++) {
    if (reedPos0Detecte()) { ok=true; break; }
    pas(1);
  }
  stopM();
  if (ok) {
    homingFait = true;
    positionCourante = 0;
    afficherOLED("POS0 detectee","OK");
  } else {
    afficherOLED("ERREUR","Reed absent");
  }
  delay(1000);
}

// =====================================================
// POSITION NTP (logique normale)
// =====================================================
int calculerPosition() {
  struct tm ti;
  if (!getLocalTime(&ti)) return -1;
  int jour = (ti.tm_wday==0)?6:ti.tm_wday-1;
  int moment;
  if      (ti.tm_hour>=MATIN_DEBUT_H && ti.tm_hour<MATIN_FIN_H) moment=0;
  else if (ti.tm_hour>=MIDI_DEBUT_H  && ti.tm_hour<MIDI_FIN_H)  moment=1;
  else if (ti.tm_hour>=SOIR_DEBUT_H  && ti.tm_hour<SOIR_FIN_H)  moment=2;
  else return -1;
  return 1 + (jour*3) + moment;
}

// =====================================================
// BUZZER
// =====================================================
void gererBuzzer() {
  if (!buzzerActif) return;
  if (millis()-buzzerDebutMs >= BUZZER_DUREE_MS) {
    digitalWrite(BUZZER_PIN, HIGH);
    buzzerActif = false;
    if (!compartimentPresente) forcerReposOLED();
  }
}

// =====================================================
// BOUTONS REMPLISSAGE
// =====================================================
bool boutonNextAppuye() {
  bool raw = digitalRead(BTN_NEXT_PIN);
  if (lastNextRaw==HIGH && raw==LOW) {
    delay(25);
    if (digitalRead(BTN_NEXT_PIN)==LOW) { lastNextRaw=raw; return true; }
  }
  lastNextRaw = raw;
  return false;
}

bool boutonPrevAppuye() {
  bool raw = digitalRead(BTN_PREV_PIN);
  if (lastPrevRaw==HIGH && raw==LOW) {
    delay(25);
    if (digitalRead(BTN_PREV_PIN)==LOW) { lastPrevRaw=raw; return true; }
  }
  lastPrevRaw = raw;
  return false;
}

// =====================================================
// SPIFFS
// =====================================================
void initSPIFFS() {
  if (!SPIFFS.begin(true)) Serial.println("SPIFFS : echec");
  else                     Serial.println("SPIFFS : OK");
}

// ── Charge les intervalles horaires depuis le backend ──
// ── Charge l'état system_on depuis le backend ──
// ── Vérifie si une prescription active existe pour ce patient ──
bool verifierPrescriptionActive() {
  if (WiFi.status() != WL_CONNECTED || PATIENT_ID == 0) return true; // fallback permissif si hors ligne
  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;
  http.begin(client, String(BACKEND_URL) + "/prescription/active?patient_id=" + String(PATIENT_ID));
  int code = http.GET();
  String body = http.getString();
  http.end();
  Serial.println("[Prescription] code=" + String(code) + " body=" + body.substring(0, 50));
  // Prescription active = 200 + contient "medicament" ou "id" sans erreur
  if (code == 200 && body.indexOf("\"error\"") < 0 &&
      (body.indexOf("\"medicament\"") >= 0 || body.indexOf("\"id\"") >= 0)) {
    return true;
  }
  return false;
}

void chargerSystemActif() {
  if (WiFi.status() != WL_CONNECTED || PATIENT_ID == 0) return;
  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;
  http.begin(client, String(BACKEND_URL) + "/config/status?patient_id=" + String(PATIENT_ID));
  int code = http.GET();
  if (code == 200) {
    String body = http.getString();
    systemActif = body.indexOf("\"system_on\":true") >= 0 ||
                  body.indexOf("\"system_on\": true") >= 0;
    // Charger aussi mode_sans_wifi
    modeSansWifi = body.indexOf("\"mode_sans_wifi\":true") >= 0 ||
                   body.indexOf("\"mode_sans_wifi\": true") >= 0;
    Serial.println("[System] system_on = " + String(systemActif ? "true" : "false"));
    Serial.println("[System] mode_sans_wifi = " + String(modeSansWifi ? "true" : "false"));
  }
  http.end();
}

// ─── Toggle mode sans WiFi ───
// Appui long BTN_PREV (>2s) hors mode remplissage
void toggleModeSansWifi() {
  modeSansWifi = !modeSansWifi;

  // ── Sauvegarder en NVS → survit au redémarrage et à l'offline ──
  prefs.begin("patient", false);
  prefs.putBool("sans_wifi", modeSansWifi);
  prefs.end();

  // ── Publier sur MQTT si connecté ──
  // Si pas connecté → sera publié au retour WiFi dans maintienMQTT()
  if (mqtt.connected()) {
    StaticJsonDocument<128> doc;
    doc["status"]     = "sans_wifi";
    doc["patient_id"] = PATIENT_ID;
    doc["actif"]      = modeSansWifi;
    String payload;
    serializeJson(doc, payload);
    mqtt.publish("medicinebox/mode", payload.c_str());
  }

  Serial.println(modeSansWifi ? "[Mode] Sans WiFi ACTIVE" : "[Mode] Sans WiFi DESACTIVE");
  display.clearDisplay();
  display.setTextSize(1); display.setTextColor(SSD1306_WHITE);
  display.setCursor(5, 15);
  display.println(modeSansWifi ? "Je pars sans WiFi" : "Mode normal");
  display.setCursor(5, 30);
  display.println(modeSansWifi ? "Prises conservees" : "Mode desactive");
  display.setCursor(5, 45);
  display.println(modeSansWifi ? "Bon voyage!" : "OK");
  display.display();
  delay(2500);
}

// ─── Charger modeSansWifi depuis NVS au démarrage ───
void chargerModeSansWifi() {
  prefs.begin("patient", true);
  modeSansWifi = prefs.getBool("sans_wifi", false);
  prefs.end();
  Serial.println("[NVS] mode_sans_wifi = " + String(modeSansWifi ? "true" : "false"));
}

// ─── Publier mode_sans_wifi au retour WiFi ───
void publierModeSansWifiSiActif() {
  if (!modeSansWifi) return;
  StaticJsonDocument<128> doc;
  doc["status"]     = "sans_wifi";
  doc["patient_id"] = PATIENT_ID;
  doc["actif"]      = true;
  String payload;
  serializeJson(doc, payload);
  mqtt.publish("medicinebox/mode", payload.c_str());
  Serial.println("[Mode] Publié sans_wifi actif au retour WiFi");
}

void chargerIntervalles() {
  if (WiFi.status() != WL_CONNECTED) return;
  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;
  http.begin(client, String(BACKEND_URL) + "/intervalles/profil-actif");
  int code = http.GET();
  if (code == 200) {
    String body = http.getString();
    // Parser matin_debut, matin_fin, midi_debut, midi_fin, soir_debut, soir_fin
    auto parseHeure = [](String json, String key) -> int {
      int idx = json.indexOf("\"" + key + "\":");
      if (idx < 0) return -1;
      idx += key.length() + 3;
      while (idx < json.length() && (json[idx] == ' ' || json[idx] == '"')) idx++;
      // Format HH:MM:SS
      return json.substring(idx, idx+2).toInt();
    };
    int md = parseHeure(body, "matin_debut");
    int mf = parseHeure(body, "matin_fin");
    int ld = parseHeure(body, "midi_debut");
    int lf = parseHeure(body, "midi_fin");
    int sd = parseHeure(body, "soir_debut");
    int sf = parseHeure(body, "soir_fin");
    if (md >= 0) MATIN_DEBUT_H = md;
    if (mf >= 0) MATIN_FIN_H  = mf;
    if (ld >= 0) MIDI_DEBUT_H  = ld;
    if (lf >= 0) MIDI_FIN_H   = lf;
    if (sd >= 0) SOIR_DEBUT_H  = sd;
    if (sf >= 0) SOIR_FIN_H   = sf;
    Serial.printf("[Intervalles] matin %dh-%dh | midi %dh-%dh | soir %dh-%dh\n",
      MATIN_DEBUT_H, MATIN_FIN_H, MIDI_DEBUT_H, MIDI_FIN_H, SOIR_DEBUT_H, SOIR_FIN_H);
  } else {
    Serial.println("[Intervalles] Échec chargement — valeurs par défaut conservées");
  }
  http.end();
}

void sauvegarderPriseLocale(const char* moment, long pAvant, long pApres) {
  struct tm ti;
  char dateStr[11] = "0000-00-00";
  if (getLocalTime(&ti))
    snprintf(dateStr,sizeof(dateStr),"%04d-%02d-%02d",ti.tm_year+1900,ti.tm_mon+1,ti.tm_mday);
  StaticJsonDocument<200> doc;
  doc["patient_id"]=PATIENT_ID; doc["moment"]=moment;
  doc["poids_avant"]=pAvant; doc["poids_apres"]=pApres; doc["date"]=dateStr;
  char ligne[200]; serializeJson(doc,ligne);
  File f = SPIFFS.open(BUFFER_FILE, FILE_APPEND);
  if (f) { f.println(ligne); f.close(); }
}

void viderBufferSPIFFS() {
  if (!SPIFFS.exists(BUFFER_FILE)) { publierStatutSimple("eeprom_vide"); return; }
  File f = SPIFFS.open(BUFFER_FILE, FILE_READ);
  if (!f) { publierStatutSimple("eeprom_vide"); return; }
  int count=0;
  while (f.available()) {
    String ligne = f.readStringUntil('\n'); ligne.trim();
    if (ligne.length()>0) { mqtt.publish(TOPIC_PRISE, ligne.c_str()); count++; delay(100); }
  }
  f.close(); SPIFFS.remove(BUFFER_FILE);
  if (count>0) publierStatutSimple("eeprom_fin");
  else         publierStatutSimple("eeprom_vide");
}

// =====================================================
// MQTT : PUBLIER
// =====================================================
void publierStatut(const char* status, const char* msg) {
  if (!mqtt.connected()) return;
  StaticJsonDocument<200> doc;
  doc["status"]=status;
  if (msg && strlen(msg)>0) doc["message"]=msg;
  char buf[200]; serializeJson(doc,buf);
  mqtt.publish(TOPIC_STATUT, buf);
}

void publierStatutSimple(const char* status) { publierStatut(status,""); }

void publierPrise(long pAvant, long pApres) {
  struct tm ti;
  const char* moment="inconnu";
  if (getLocalTime(&ti)) {
    if      (ti.tm_hour>=MATIN_DEBUT_H && ti.tm_hour<MATIN_FIN_H) moment="matin";
    else if (ti.tm_hour>=MIDI_DEBUT_H  && ti.tm_hour<MIDI_FIN_H)  moment="midi";
    else if (ti.tm_hour>=SOIR_DEBUT_H  && ti.tm_hour<SOIR_FIN_H)  moment="soir";
  }

  // Avertir si hors horaire
  if (strcmp(moment, "inconnu") == 0) {
    afficherOLED("Hors horaire", "Prise non comptee");
    delay(3000);
    return;  // ne pas publier la prise
  }

  if (mqtt.connected()) {
    StaticJsonDocument<200> doc;
    doc["patient_id"]=PATIENT_ID; doc["moment"]=moment;
    doc["poids_avant"]=pAvant; doc["poids_apres"]=pApres;
    char buf[200]; serializeJson(doc,buf);
    mqtt.publish(TOPIC_PRISE, buf);
  } else {
    sauvegarderPriseLocale(moment, pAvant, pApres);
  }
}

// =====================================================
// HEARTBEAT
// =====================================================
void envoyerHeartbeat() {
  if (millis()-dernierHeartbeat >= HEARTBEAT_INTERVAL_MS) {
    dernierHeartbeat = millis();
    if (mqtt.connected()) publierStatutSimple("heartbeat");
  }
}

// =====================================================
// MQTT : CALLBACK
// =====================================================
void mqttCallback(char* topic, byte* payload, unsigned int length) {
  char message[256];
  int len = min((int)length,255);
  memcpy(message,payload,len); message[len]='\0';
  Serial.printf("MQTT recu [%s] : %s\n", topic, message);

  // ── Alerte buzzer ──
  if (String(topic)==TOPIC_ALERTE) {
    StaticJsonDocument<200> doc;
    if (deserializeJson(doc,message)) return;
    const char* action = doc["action"];
    if (action && String(action)=="buzzer_on") {
      buzzerActif=true; buzzerDebutMs=millis();
      digitalWrite(BUZZER_PIN,LOW);
      const char* moment = doc["moment"]|"---";
      char l1[22]; snprintf(l1,sizeof(l1),"ALERTE %s",moment);
      afficherOLED(l1,"Prenez medicament");
    }
  }

  // ── Config (mode remplissage, system_on/off...) ──
  if (String(topic)==TOPIC_CONFIG) {
    StaticJsonDocument<200> doc;
    if (deserializeJson(doc,message)) return;
    const char* mode = doc["mode"]|"";
    bool enabled = doc["enabled"]|false;

    if (String(mode)=="remplissage") {
      if (enabled && !modeRemplissage) {
        // Activation du mode remplissage
        modeRemplissage = true;
        remplissageInitialise = false;
        afficherOLED("Mode remplissage","Ouvrir couvercle");
        Serial.println("Mode remplissage ACTIVE");
      } else if (!enabled && modeRemplissage) {
        // Désactivation → retour POS0
        modeRemplissage = false;
        remplissageInitialise = false;
        if (positionCourante != 0) {
          afficherOLED("Fin remplissage","Retour POS0");
          retourAZero();
          if (!reedPos0Detecte()) faireHoming();
        }
        forcerReposOLED();
        Serial.println("Mode remplissage DESACTIVE");
      }
    }

    if (String(mode)=="system_off") {
      systemActif = false;
      pretPourOuverture = false;
      // Vérifier si c'est dû à une prescription manquante ou juste système off
      if (!verifierPrescriptionActive()) {
        afficherOLED("Pas de","prescription");
      } else {
        afficherOLED("Systeme","Arrete");
      }
    }
    if (String(mode)=="system_on") {
      systemActif = true;
      // Vérifier prescription avant d'autoriser l'ouverture
      if (!verifierPrescriptionActive()) {
        afficherOLED("Pas de","prescription");
        pretPourOuverture = false;
      } else {
        pretPourOuverture = true;
        forcerReposOLED();
      }
    }
  }
  // ── Commande backend (update_intervalles, etc.) ──
  if (String(topic)==TOPIC_COMMANDE) {
    StaticJsonDocument<512> doc;
    if (deserializeJson(doc,message)) return;
    const char* action = doc["action"]|"";
    if (String(action)=="prescription_arretee") {
      int pid = doc["patient_id"] | 0;
      if (pid == 0 || pid == PATIENT_ID) {
        Serial.println("[MQTT] Prescription arrêtée");
        pretPourOuverture = false;
        compartimentPresente = false;
        afficherOLED("Pas de","prescription");
      }
    }

    if (String(action)=="prescription_activee") {
      int pid = doc["patient_id"] | 0;
      // Concerne ce patient ?
      if (pid == 0 || pid == PATIENT_ID) {
        Serial.println("[MQTT] Prescription activée — re-vérification...");
        afficherOLED("Prescription","recue !");
        delay(1500);
        // Re-vérifier prescription + system_on
        if (!verifierPrescriptionActive()) {
          afficherOLED("Pas de","prescription");
          pretPourOuverture = false;
        } else if (!systemActif) {
          afficherOLED("Activez le","systeme");
          pretPourOuverture = false;
        } else {
          pretPourOuverture = true;
          forcerReposOLED();
        }
      }
    }

    if (String(action)=="changer_patient") {
      int newPid = doc["patient_id"] | 0;
      if (newPid > 0 && newPid != PATIENT_ID) {
        // Sauvegarder le nouveau patient_id en flash
        sauverPatientId(newPid);
        PATIENT_ID = newPid;
        // Mettre à jour le topic alerte
        TOPIC_ALERTE = "medicinebox/alerte/" + String(PATIENT_ID);
        mqtt.subscribe(TOPIC_ALERTE.c_str());

        // Afficher le changement sur l'OLED
        char l2[22];
        snprintf(l2, sizeof(l2), "ID: %d", PATIENT_ID);
        afficherOLED("Patient change", l2);
        delay(2000);

        // Recharger system_on pour le nouveau patient
        chargerSystemActif();

        // Revenir au flux de démarrage
        pretPourOuverture = false;
        compartimentPresente = false;
        priseFaite = false;

        // Couvercle ouvert ? → demander de fermer
        if (couvercleEstOuvert()) {
          afficherOLED("Fermez le","couvercle");
          while (couvercleEstOuvert()) delay(100);
          delay(300);
        }

        // Retour position 0
        afficherOLED("Retour","position 0...");
        retourAZero();
        if (!reedPos0Detecte()) faireHoming();

        // Vérifier prescription et système
        if (!verifierPrescriptionActive()) {
          afficherOLED("Pas de","prescription");
        } else if (!systemActif) {
          afficherOLED("Activez le","systeme");
        } else {
          pretPourOuverture = true;
          forcerReposOLED();
        }

        Serial.println("[Patient] Changement → patient_" + String(PATIENT_ID));
      }
    }

    if (String(action)=="update_intervalles") {
      // Parser HH:MM:SS → extraire l'heure
      auto parseH = [](const char* s) -> int {
        if (!s || strlen(s) < 2) return -1;
        return (s[0]-'0')*10 + (s[1]-'0');
      };
      int md = parseH(doc["matin_debut"]|"06:00:00");
      int mf = parseH(doc["matin_fin"]|"12:00:00");
      int ld = parseH(doc["midi_debut"]|"12:00:00");
      int lf = parseH(doc["midi_fin"]|"18:00:00");
      int sd = parseH(doc["soir_debut"]|"18:00:00");
      int sf = parseH(doc["soir_fin"]|"22:00:00");
      if (md >= 0) MATIN_DEBUT_H = md;
      if (mf >= 0) MATIN_FIN_H  = mf;
      if (ld >= 0) MIDI_DEBUT_H  = ld;
      if (lf >= 0) MIDI_FIN_H   = lf;
      if (sd >= 0) SOIR_DEBUT_H  = sd;
      if (sf >= 0) SOIR_FIN_H   = sf;
      Serial.printf("[MQTT] Intervalles mis à jour : matin %dh-%dh | midi %dh-%dh | soir %dh-%dh\n",
        MATIN_DEBUT_H, MATIN_FIN_H, MIDI_DEBUT_H, MIDI_FIN_H, SOIR_DEBUT_H, SOIR_FIN_H);
      afficherOLED("Intervalles", "mis a jour");
      delay(1500);
      forcerReposOLED();
    }
  }
}

// =====================================================
// MQTT : CONNEXION
// =====================================================
bool connecterMQTT() {
  if (mqtt.connected()) return true;
  espClient.setInsecure();
  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
  // Construire les topics dynamiques pour ce patient
  TOPIC_ALERTE = "medicinebox/alerte/" + String(PATIENT_ID);
  TOPIC_CONFIG = "medicinebox/config/" + String(PATIENT_ID);
  // LWT inclut patient_id pour que le backend sache quel patient est offline
  String lwtPayload = "{\"status\":\"offline\",\"patient_id\":" + String(PATIENT_ID) + "}";
  if (mqtt.connect("medicinebox-esp32", MQTT_USER, MQTT_PASS,
                    TOPIC_STATUT, 1, true, lwtPayload.c_str())) {
    mqtt.subscribe(TOPIC_ALERTE.c_str());
    mqtt.subscribe(TOPIC_CONFIG.c_str());
    mqtt.subscribe(TOPIC_COMMANDE);
    mqttConnecte = true;
    return true;
  }
  mqttConnecte = false;
  return false;
}

void maintienMQTT() {
  if (WiFi.status()!=WL_CONNECTED) { etaitConnecte=false; return; }
  if (!mqtt.connected()) {
    etaitConnecte=false;
    if (connecterMQTT()) {
      publierStatut("online","Reconnexion");
      // Si mode_sans_wifi actif → informer backend avant sync EEPROM
      publierModeSansWifiSiActif();
      delay(500);
      viderBufferSPIFFS();
      // Désactiver mode_sans_wifi après sync
      if (modeSansWifi) {
        modeSansWifi = false;
        prefs.begin("patient", false);
        prefs.putBool("sans_wifi", false);
        prefs.end();
        StaticJsonDocument<128> doc;
        doc["status"] = "sans_wifi"; doc["patient_id"] = PATIENT_ID; doc["actif"] = false;
        String p; serializeJson(doc, p);
        mqtt.publish("medicinebox/mode", p.c_str());
        Serial.println("[Mode] Sans WiFi désactivé après sync");
      }
      etaitConnecte=true;
    }
  } else {
    if (!etaitConnecte) etaitConnecte=true;
  }
  mqtt.loop();
}

// =====================================================
// NTP
// =====================================================
void syncNTP() {
  configTime(3600,0,"pool.ntp.org");
  struct tm ti; int t=0;
  while (!getLocalTime(&ti) && t<20) { delay(500); t++; }
  if (getLocalTime(&ti))
    Serial.printf("NTP : %02d:%02d:%02d\n",ti.tm_hour,ti.tm_min,ti.tm_sec);
}

// =====================================================
// WIFI
// =====================================================
// =====================================================
// GESTION PATIENT ID
// =====================================================
int chargerPatientId() {
  prefs.begin("patient", true);
  int pid = prefs.getInt("id", 0);
  prefs.end();
  return pid;
}

void sauverPatientId(int pid) {
  prefs.begin("patient", false);
  prefs.putInt("id", pid);
  prefs.end();
  PATIENT_ID = pid;
  Serial.println("Patient ID sauvegardé : " + String(pid));
}

void effacerPatientId() {
  prefs.begin("patient", false);
  prefs.clear();
  prefs.end();
  PATIENT_ID = 0;
}

// Appelle le backend avec le code d'activation
// Retourne le patient_id ou 0 si erreur
int activerBoite(String code) {
  if (WiFi.status() != WL_CONNECTED) return 0;
  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;
  String url = String(BACKEND_URL) + "/patients/activer-boite";
  http.begin(client, url);
  http.addHeader("Content-Type", "application/json");
  String body = "{\"code_activation\":\"" + code + "\"}";
  Serial.println("[HTTP] POST " + url);
  Serial.println("[HTTP] Body: " + body);
  int httpCode = http.POST(body);
  Serial.println("[HTTP] Code: " + String(httpCode));
  if (httpCode == 200) {
    String response = http.getString();
    Serial.println("[HTTP] Response: " + response);
    // Parser "patient_id": X — cherche le chiffre après "patient_id":
    int idx = response.indexOf("\"patient_id\":");
    if (idx >= 0) {
      idx += 13; // saute "patient_id":
      // sauter les espaces éventuels
      while (idx < response.length() && response[idx] == ' ') idx++;
      // lire les chiffres
      String pidStr = "";
      while (idx < response.length() && isDigit(response[idx])) {
        pidStr += response[idx];
        idx++;
      }
      Serial.println("[HTTP] patient_id parsé: " + pidStr);
      http.end();
      return pidStr.toInt();
    }
  } else {
    Serial.println("[HTTP] Erreur: " + http.getString());
  }
  http.end();
  return 0;
}

bool chargerWiFi(String &ssid, String &pass) {
  prefs.begin("wifi",true);
  ssid=prefs.getString("ssid",""); pass=prefs.getString("pass","");
  prefs.end();
  return ssid.length()>0;
}
void sauverWiFi(String ssid, String pass) {
  prefs.begin("wifi",false);
  prefs.putString("ssid",ssid); prefs.putString("pass",pass);
  prefs.end();
}
void effacerWiFi() { prefs.begin("wifi",false); prefs.clear(); prefs.end(); }

bool connecterWiFi(String ssid, String pass) {
  WiFi.mode(WIFI_AP_STA);  // AP+STA pour garder le portail actif pendant la connexion
  WiFi.begin(ssid.c_str(), pass.c_str());
  Serial.print("Connexion WiFi...");
  int t = 0;
  while (WiFi.status() != WL_CONNECTED && t < 20) {
    delay(500);
    Serial.print(".");
    t++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n✅ WiFi connecté ! IP: " + WiFi.localIP().toString());
    return true;
  }
  Serial.println("\n❌ WiFi échoué");
  return false;
}

// =====================================================
// PAGE HTML WiFi
// =====================================================
const char PAGE_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Medicine Box - WiFi</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, sans-serif; background: #f7f6f2; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
    .card { background: #fff; border-radius: 16px; padding: 32px 24px; width: 90%; max-width: 360px; box-shadow: 0 2px 12px rgba(0,0,0,.08); }
    .logo { text-align: center; margin-bottom: 24px; }
    .logo-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: #1D9E75; margin-right: 8px; animation: pulse 1.8s ease-in-out infinite; }
    @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:.4; } }
    .logo-text { font-size: 18px; font-weight: 600; color: #1a1a18; }
    .subtitle { text-align: center; color: #6b6b66; font-size: 13px; margin-bottom: 20px; }
    label { font-size: 13px; color: #6b6b66; display: block; margin-bottom: 4px; margin-top: 14px; }
    input { width: 100%; padding: 10px 12px; border: 1px solid rgba(0,0,0,.12); border-radius: 8px; font-size: 14px; outline: none; }
    input:focus { border-color: #1D9E75; }
    input.code-input { font-family: monospace; letter-spacing: 2px; }
    button { width: 100%; padding: 12px; background: #1D9E75; color: #fff; border: none; border-radius: 8px; font-size: 14px; font-weight: 500; cursor: pointer; margin-top: 20px; }
    button:hover { opacity: .9; }
    .status { text-align: center; margin-top: 16px; font-size: 13px; color: #6b6b66; }
    #networks { margin-top: 8px; }
    .net-item { padding: 8px 12px; border: 1px solid rgba(0,0,0,.08); border-radius: 8px; margin-bottom: 6px; cursor: pointer; font-size: 13px; display: flex; justify-content: space-between; }
    .net-item:hover { background: #f0f0ec; }
    .signal { color: #6b6b66; font-size: 12px; }
  </style>
</head>
<body>
  <div class="card">
    <div class="logo"><span class="logo-dot"></span><span class="logo-text">Medicine Box</span></div>
    <div class="subtitle">Connectez votre boîte au WiFi</div>

    <label>Réseaux disponibles</label>
    <div id="networks"><div style="color:#a8a8a2;font-size:12px;padding:8px">Recherche...</div></div>

    <form action="/save" method="POST">
      <label>Nom du réseau (SSID)</label>
      <input type="text" name="ssid" id="ssid" placeholder="Sélectionnez ou tapez" required>

      <label>Mot de passe</label>
      <input type="password" name="pass" id="pass" placeholder="Mot de passe WiFi" required>

      <button type="submit">Connecter la boîte</button>
    </form>

    <div class="status" id="status"></div>
  </div>

  <script>
    fetch('/scan').then(r => r.json()).then(nets => {
      const div = document.getElementById('networks');
      if (nets.length === 0) { div.innerHTML = '<div style="color:#a8a8a2;font-size:12px;padding:8px">Aucun réseau trouvé</div>'; return; }
      div.innerHTML = nets.map(n =>
        `<div class="net-item" onclick="document.getElementById('ssid').value='${n.ssid}'">
          <span>${n.ssid}</span>
          <span class="signal">${n.rssi} dBm</span>
        </div>`
      ).join('');
    });
  </script>
</body>
</html>
)rawliteral";

const char PAGE_OK[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: -apple-system, sans-serif; background: #f7f6f2; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
    .card { background: #fff; border-radius: 16px; padding: 32px 24px; width: 90%; max-width: 360px; text-align: center; box-shadow: 0 2px 12px rgba(0,0,0,.08); }
    .icon { font-size: 48px; margin-bottom: 16px; }
    .title { font-size: 18px; font-weight: 600; margin-bottom: 8px; color: #1a1a18; }
    .sub { color: #6b6b66; font-size: 13px; line-height: 1.5; }
    .green { color: #1D9E75; font-weight: 500; }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✅</div>
    <div class="title">WiFi configuré !</div>
    <div class="sub">Votre Medicine Box est connectée au réseau.<br><br>
    <span class="green">La boîte redémarre...</span><br><br>
    Ouvrez l'application Medicine Box et entrez votre code d'activation pour accéder à votre dashboard.</div>
  </div>
</body>
</html>
)rawliteral";

// =====================================================
// MODE AP
// =====================================================
void lancerModeAP() {
  afficherOLED("WiFi Setup","MedicineBox-Setup");
  WiFi.mode(WIFI_AP);
  WiFi.softAP(AP_SSID, AP_PASS);

  server.on("/", HTTP_GET, [](){
    server.send_P(200,"text/html",PAGE_HTML);
  });
  server.on("/scan", HTTP_GET, [](){
    int n=WiFi.scanNetworks();
    String json="[";
    for(int i=0;i<n&&i<10;i++){
      if(i>0) json+=",";
      json+="{\"ssid\":\""+WiFi.SSID(i)+"\",\"rssi\":"+String(WiFi.RSSI(i))+"}";
    }
    json+="]";
    server.send(200,"application/json",json);
  });
  server.on("/save", HTTP_POST, [](){
    String ssid=server.arg("ssid");
    String pass=server.arg("pass");
    if(ssid.length()>0){
      sauverWiFi(ssid, pass);
      if(connecterWiFi(ssid, pass)){
        server.send_P(200,"text/html",PAGE_OK);
        server.client().flush();  // forcer l'envoi immédiat
        // Garder le serveur actif 5s pour que Safari reçoive la page
        unsigned long t = millis();
        while (millis() - t < 5000) server.handleClient();
        ESP.restart();
      } else {
        effacerWiFi();
        server.send(400,"text/html","<!DOCTYPE html><html><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1'><style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,sans-serif;background:#f7f6f2;min-height:100vh;display:flex;align-items:center;justify-content:center}.card{background:#fff;border-radius:16px;padding:32px 24px;width:90%;max-width:360px;text-align:center;box-shadow:0 2px 12px rgba(0,0,0,.08)}.icon{font-size:48px;margin-bottom:16px}.title{font-size:18px;font-weight:600;margin-bottom:8px;color:#E24B4A}.sub{color:#6b6b66;font-size:13px;line-height:1.6;margin-bottom:20px}.btn{display:inline-block;padding:12px 24px;background:#1D9E75;color:#fff;border-radius:8px;text-decoration:none;font-size:14px;font-weight:500}</style></head><body><div class='card'><div class='icon'>📶</div><div class='title'>WiFi incorrect</div><div class='sub'>Impossible de se connecter au réseau.<br>Vérifiez le nom et le mot de passe WiFi.</div><a class='btn' href='/'>Réessayer</a></div></body></html>");
      }
    } else {
      server.send(400,"text/html","<!DOCTYPE html><html><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1'><style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,sans-serif;background:#f7f6f2;min-height:100vh;display:flex;align-items:center;justify-content:center}.card{background:#fff;border-radius:16px;padding:32px 24px;width:90%;max-width:360px;text-align:center;box-shadow:0 2px 12px rgba(0,0,0,.08)}.icon{font-size:48px;margin-bottom:16px}.title{font-size:18px;font-weight:600;margin-bottom:8px;color:#E24B4A}.sub{color:#6b6b66;font-size:13px;line-height:1.6;margin-bottom:20px}.btn{display:inline-block;padding:12px 24px;background:#1D9E75;color:#fff;border-radius:8px;text-decoration:none;font-size:14px;font-weight:500}</style></head><body><div class='card'><div class='icon'>⚠️</div><div class='title'>Champ manquant</div><div class='sub'>Le nom du réseau WiFi est obligatoire.</div><a class='btn' href='/'>Réessayer</a></div></body></html>");
    }
  });
  server.on("/reset", HTTP_GET, [](){
    effacerWiFi();
    effacerPatientId();
    server.send(200,"text/plain","Efface. Redemarrage...");
    delay(1000); ESP.restart();
  });
  server.on("/reset-patient", HTTP_GET, [](){
    effacerPatientId();
    effacerWiFi();
    server.send(200,"text/plain","Patient et WiFi effacés. Redemarrage...");
    delay(1000); ESP.restart();
  });
  server.begin();
}

// =====================================================
// SETUP
// =====================================================
void setup() {
  delay(500);
  Serial.begin(115200);
  Serial.println("\n=== Medicine Box v5 ===");

  // Moteur
  pinMode(MOTOR_PIN1,OUTPUT); pinMode(MOTOR_PIN2,OUTPUT);
  pinMode(MOTOR_PIN3,OUTPUT); pinMode(MOTOR_PIN4,OUTPUT);
  stopM();

  // Capteurs
  pinMode(REED_PIN,  INPUT_PULLUP);
  pinMode(COVER_PIN, INPUT_PULLUP);

  // Boutons remplissage
  pinMode(BTN_NEXT_PIN, INPUT_PULLUP);
  pinMode(BTN_PREV_PIN, INPUT_PULLUP);

  // Buzzer OFF
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, HIGH);

  // OLED
  Wire.begin(16,17); delay(100);
  oledOK = display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  if (oledOK) afficherOLED("Medicine Box","Demarrage...");

  // HX711
  scale.begin(HX711_DT, HX711_SCK);

  // SPIFFS
  initSPIFFS();
  delay(800);

  // Charger patient_id
  int pid = chargerPatientId();
  if (pid > 0) {
    PATIENT_ID = pid;
    Serial.println("Patient ID chargé : " + String(PATIENT_ID));
  } else {
    Serial.println("⚠️ Pas de patient configuré — mode setup requis");
  }

  // WiFi d'abord
  String ssid, pass;
  if (chargerWiFi(ssid, pass)) {
    if (connecterWiFi(ssid, pass)) {
      wifiConfiguree = true;
      afficherOLED("WiFi OK", WiFi.localIP().toString().c_str());
      delay(500);
      syncNTP();
      chargerIntervalles();  // ← charger les plages horaires depuis le backend
      // Charger l'état system_on depuis le backend
      chargerSystemActif();
      chargerModeSansWifi();  // ← charger depuis NVS (survit à l'offline)
      if (connecterMQTT()) {
        publierStatut("online","Demarrage OK");
        // Si mode_sans_wifi était actif → informer le backend avant de vider l'EEPROM
        publierModeSansWifiSiActif();
        delay(500);  // laisser le backend traiter avant sync EEPROM
        viderBufferSPIFFS();
        // Désactiver mode_sans_wifi après sync
        if (modeSansWifi) {
          modeSansWifi = false;
          prefs.begin("patient", false);
          prefs.putBool("sans_wifi", false);
          prefs.end();
          StaticJsonDocument<128> doc;
          doc["status"] = "sans_wifi"; doc["patient_id"] = PATIENT_ID; doc["actif"] = false;
          String p; serializeJson(doc, p);
          mqtt.publish("medicinebox/mode", p.c_str());
          Serial.println("[Mode] Sans WiFi désactivé après sync");
        }
      }
    } else {
      effacerWiFi();
    }
  }

  if (!wifiConfiguree) lancerModeAP();

  // Vérifier couvercle fermé avant homing
  if (couvercleEstOuvert()) {
    afficherOLED("Fermez le","couvercle");
    while (couvercleEstOuvert()) {
      delay(100);
    }
    delay(300);
    afficherOLED("Couvercle","ferme OK");
    delay(500);
  }

  // Homing après WiFi (moteur nécessite alimentation stable)
  faireHoming();

  // Init couvercle + vérifications système
  if (homingFait) {
    etatCouvercle = false;
    ancienEtatCouvercle = false;
    pretPourOuverture = true;

    // 5. Vérifier prescription active
    if (wifiConfiguree && PATIENT_ID > 0 && !verifierPrescriptionActive()) {
      afficherOLED("Pas de","prescription");
      pretPourOuverture = false;
    }
    // 6. Vérifier système ON
    else if (!systemActif) {
      afficherOLED("Activez le","systeme");
      pretPourOuverture = false;
    }
    // 7. Tout OK → attente ouverture
    else {
      forcerReposOLED();
    }
  }
}

// =====================================================
// LOOP
// =====================================================
void loop() {

  // ── Mode AP : serveur web ──
  if (!wifiConfiguree) {
    server.handleClient();
    delay(10);
    return;
  }

  if (!homingFait) return;

  // ── WiFi perdu → reconnexion silencieuse ──
  if (WiFi.status()!=WL_CONNECTED && !compartimentPresente && !modeRemplissage) {
    String ssid, pass;
    if (chargerWiFi(ssid,pass)) {
      WiFi.begin(ssid.c_str(),pass.c_str());
      int t=0;
      while (WiFi.status()!=WL_CONNECTED && t<10) { delay(500); t++; }
      if (WiFi.status()==WL_CONNECTED) forcerReposOLED();
    }
  }

  // ── Appui long BTN_PREV (>2s) hors remplissage → toggle mode sans WiFi ──
  if (!modeRemplissage) {
    static unsigned long btnPrevPressedAt = 0;
    static bool btnPrevWasPressed = false;
    bool btnPrevNow = (digitalRead(BTN_PREV_PIN) == LOW);
    if (btnPrevNow && !btnPrevWasPressed) {
      btnPrevPressedAt = millis();
      btnPrevWasPressed = true;
    } else if (!btnPrevNow && btnPrevWasPressed) {
      if (millis() - btnPrevPressedAt >= 2000) {
        toggleModeSansWifi();
      }
      btnPrevWasPressed = false;
    }
  }

  maintienMQTT();
  envoyerHeartbeat();
  gererBuzzer();

  etatCouvercle = couvercleEstOuvert();

  // ═══════════════════════════════════════════════════
  // MODE REMPLISSAGE
  // ═══════════════════════════════════════════════════
  if (modeRemplissage) {

    // Couvercle vient d'être ouvert → aller au comp 1
    if (!remplissageInitialise && etatCouvercle) {
      afficherOLED("Remplissage","Aller comp 1...");
      delay(300);
      // Aller au compartiment 1 depuis POS0
      int nbPas = stepsPC[0];
      for (int i=0; i<nbPas; i++) pas(1);
      stopM();
      positionCourante = 1;
      remplissageInitialise = true;
      char l2[22];
      snprintf(l2,sizeof(l2),"%s",compartiments[positionCourante]);
      afficherOLED("Remplissage", l2);
    }

    // Couvercle ouvert + plateau initialisé → gérer boutons
    if (etatCouvercle && remplissageInitialise) {
      char l1[22], l2[22];
      snprintf(l1,sizeof(l1),"Comp %d/%d",positionCourante,NB_POSITIONS-1);
      snprintf(l2,sizeof(l2),"%s",compartiments[positionCourante]);
      afficherOLED(l1, l2);

      if (boutonNextAppuye()) {
        remplissageNext();
        snprintf(l1,sizeof(l1),"Comp %d/%d",positionCourante,NB_POSITIONS-1);
        snprintf(l2,sizeof(l2),"%s",compartiments[positionCourante]);
        afficherOLED(l1, l2);
        delay(200);
      }

      if (boutonPrevAppuye()) {
        remplissagePrev();
        snprintf(l1,sizeof(l1),"Comp %d/%d",positionCourante,NB_POSITIONS-1);
        snprintf(l2,sizeof(l2),"%s",compartiments[positionCourante]);
        afficherOLED(l1, l2);
        delay(200);
      }
    }

    // Couvercle fermé après remplissage → retour POS0
    if (!etatCouvercle && ancienEtatCouvercle && remplissageInitialise) {
      afficherOLED("Fin remplissage","Retour POS0...");
      delay(500);
      retourAZero();
      if (!reedPos0Detecte()) faireHoming();
      remplissageInitialise = false;
      // NE PAS désactiver modeRemplissage ici → attendre commande MQTT
      afficherOLED("Remplissage OK","Ouvrir pour suite");
      Serial.println("Session remplissage terminee — attente commande OFF");
    }

    if (!etatCouvercle && !remplissageInitialise) {
      afficherOLED("Mode remplissage","Ouvrir couvercle");
    }

    ancienEtatCouvercle = etatCouvercle;
    delay(50);
    return;  // ← Ne pas exécuter la logique normale
  }

  // ═══════════════════════════════════════════════════
  // LOGIQUE NORMALE (prise de médicament)
  // ═══════════════════════════════════════════════════

  if (!compartimentPresente && !etatCouvercle && !buzzerActif) {
    // Remettre pretPourOuverture à true seulement si conditions OK
    if (!pretPourOuverture) {
      bool prescOK = !wifiConfiguree || PATIENT_ID == 0 || verifierPrescriptionActive();
      bool sysOK   = systemActif;
      if (prescOK && sysOK) {
        pretPourOuverture = true;
      } else if (!prescOK) {
        afficherOLED("Pas de","prescription");
      } else {
        afficherOLED("Activez le","systeme");
      }
    } else {
      afficherReposOLED();
    }
  }

  // ── OUVERTURE ──
  if (pretPourOuverture &&
      etatCouvercle==true &&
      ancienEtatCouvercle==false &&
      !compartimentPresente) {

    // 1. Vérifier prescription active via backend
    if (!verifierPrescriptionActive()) {
      afficherOLED("Pas de","prescription");
      ancienEtatCouvercle = etatCouvercle;
      return;
    }

    // 2. Vérifier que le système est activé
    if (!systemActif) {
      afficherOLED("Activez le","systeme");
      ancienEtatCouvercle = etatCouvercle;
      return;
    }

    priseFaite=false; confirmationsPrise=0;
    positionCible = calculerPosition();

    if (positionCible<1) {
      afficherOLED("Hors horaire","Pas de prise");
      ancienEtatCouvercle = etatCouvercle;
      return;
    }

    allerACompartiment(positionCible);
    compartimentPresente = true;
    pretPourOuverture    = false;
    poidsReference = scale.is_ready() ? scale.read() : 0;
  }

  // ── SURVEILLANCE HX711 ──
  if (etatCouvercle && compartimentPresente) {
    long valeurActuelle = scale.is_ready() ? scale.read() : poidsReference;
    long baisse = poidsReference - valeurActuelle;

    if (priseFaite) {
      afficherOLED(compartiments[positionCible],"PRISE DETECTEE");
      delay(250);
      ancienEtatCouvercle = etatCouvercle;
      return;
    }

    if (baisse >= DROP_THRESHOLD_DETECT) {
      confirmationsPrise++;
      if (confirmationsPrise >= NB_CONFIRMATIONS_PRISE) {
        priseFaite = true;
        afficherOLED(compartiments[positionCible],"PRISE DETECTEE");
      } else {
        afficherOLED(compartiments[positionCible],"Verif prise...");
      }
    } else if (baisse >= DROP_THRESHOLD_CLEAR) {
      afficherOLED(compartiments[positionCible],"Stabilisation...");
    } else {
      confirmationsPrise = 0;
      afficherOLED(compartiments[positionCible],"Non prise");
    }
    delay(250);
  }

  // ── FERMETURE ──
  if (etatCouvercle==false &&
      ancienEtatCouvercle==true &&
      compartimentPresente) {

    if (priseFaite) {
      poidsApres = scale.is_ready() ? scale.read() : poidsReference;
      publierPrise(poidsReference, poidsApres);
      afficherOLED("Prise OK","Retour POS0");
    } else {
      afficherOLED("Aucune prise","Retour POS0");
    }

    delay(700);
    retourAZero();
    if (!reedPos0Detecte()) faireHoming();

    compartimentPresente = false;
    pretPourOuverture    = true;
    confirmationsPrise   = 0;
    forcerReposOLED();
  }

  ancienEtatCouvercle = etatCouvercle;
  delay(80);
}
