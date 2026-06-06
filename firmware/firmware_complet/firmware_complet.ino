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
#define TOPIC_CONFIG   "medicinebox/config"
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

// ======================= VARIABLES GLOBALES =======================
bool wifiConfiguree   = false;
bool mqttConnecte     = false;
bool etaitConnecte    = false;
bool dernierEtatEnLigne = true;

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
    if (enLigne) afficherOLED("Attente","ouverture");
    else         afficherOLED("Hors ligne","Attente ouverture");
  }
}

void forcerReposOLED() {
  bool enLigne = (WiFi.status() == WL_CONNECTED && mqtt.connected());
  dernierEtatEnLigne = enLigne;
  if (enLigne) afficherOLED("Attente","ouverture");
  else         afficherOLED("Hors ligne","Attente ouverture");
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
  if      (ti.tm_hour>=6  && ti.tm_hour<12) moment=0;
  else if (ti.tm_hour>=12 && ti.tm_hour<18) moment=1;
  else if (ti.tm_hour>=18 && ti.tm_hour<22) moment=2;
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
    if      (ti.tm_hour>=6  && ti.tm_hour<12) moment="matin";
    else if (ti.tm_hour>=12 && ti.tm_hour<18) moment="midi";
    else if (ti.tm_hour>=18 && ti.tm_hour<22) moment="soir";
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
      afficherOLED("Systeme","Arrete");
    }
    if (String(mode)=="system_on") {
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
  // Construire le topic alerte spécifique à ce patient
  TOPIC_ALERTE = "medicinebox/alerte/" + String(PATIENT_ID);
  if (mqtt.connect("medicinebox-esp32", MQTT_USER, MQTT_PASS,
                    TOPIC_STATUT, 1, true, "{\"status\":\"offline\"}")) {
    mqtt.subscribe(TOPIC_ALERTE.c_str());
    mqtt.subscribe(TOPIC_CONFIG);
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
      viderBufferSPIFFS();
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
  HTTPClient http;
  String url = String(BACKEND_URL) + "/patients/activer-boite";
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  String body = "{\"code_activation\":\"" + code + "\"}";
  int httpCode = http.POST(body);
  if (httpCode == 200) {
    String response = http.getString();
    // Parser le JSON : {"success":true,"patient_id":7,...}
    int idx = response.indexOf("\"patient_id\":");
    if (idx >= 0) {
      int start = idx + 14;
      int end = response.indexOf(",", start);
      if (end < 0) end = response.indexOf("}", start);
      String pidStr = response.substring(start, end);
      http.end();
      return pidStr.toInt();
    }
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
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid.c_str(),pass.c_str());
  int t=0;
  while (WiFi.status()!=WL_CONNECTED && t<20) { delay(500); t++; }
  return WiFi.status()==WL_CONNECTED;
}

// =====================================================
// PAGE HTML WiFi
// =====================================================
const char PAGE_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Medicine Box WiFi</title>
<style>*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,sans-serif;background:#f7f6f2;min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#fff;border-radius:16px;padding:32px 24px;width:90%;max-width:360px;box-shadow:0 2px 12px rgba(0,0,0,.08)}
.logo{text-align:center;margin-bottom:24px;font-size:18px;font-weight:600}
label{font-size:13px;color:#6b6b66;display:block;margin-bottom:4px;margin-top:14px}
input{width:100%;padding:10px 12px;border:1px solid rgba(0,0,0,.12);border-radius:8px;font-size:14px;outline:none}
button{width:100%;padding:12px;background:#1D9E75;color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:500;cursor:pointer;margin-top:20px}
.net{padding:8px 12px;border:1px solid rgba(0,0,0,.08);border-radius:8px;margin-bottom:6px;cursor:pointer;font-size:13px}
.net:hover{background:#f0f0ec}</style></head>
<body><div class="card">
<div class="logo">Medicine Box — WiFi Setup</div>
<label>Reseaux disponibles</label>
<div id="nets"><div style="color:#aaa;font-size:12px;padding:8px">Recherche...</div></div>
<form action="/save" method="POST">
<label>SSID</label><input type="text" name="ssid" id="ssid" required>
<label>Mot de passe</label><input type="password" name="pass" id="pass" required>
<label>Code d activation (ex: MB-2026-0047)</label><input type="text" name="code" id="code" placeholder="MB-YYYY-XXXX" style="font-family:monospace;letter-spacing:2px" required>
<button type="submit">Connecter et activer</button></form></div>
<script>fetch('/scan').then(r=>r.json()).then(nets=>{
const d=document.getElementById('nets');
d.innerHTML=nets.map(n=>'<div class="net" onclick="document.getElementById(\'ssid\').value=\''+n.ssid+'\'">'+n.ssid+' ('+n.rssi+' dBm)</div>').join('');
});</script></body></html>
)rawliteral";

const char PAGE_OK[] PROGMEM = R"rawliteral(
<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,sans-serif;text-align:center;padding:40px">
<h2>WiFi configure !</h2><p>La boite redemarre...</p></body></html>
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
    String code=server.arg("code");
    if(ssid.length()>0 && code.length()>0){
      // 1. Connexion WiFi temporaire pour vérifier le code
      sauverWiFi(ssid, pass);
      if(connecterWiFi(ssid, pass)){
        // 2. Activer la boîte avec le code
        int pid = activerBoite(code);
        if(pid > 0){
          sauverPatientId(pid);
          server.send_P(200,"text/html",PAGE_OK);
          delay(2000);
          ESP.restart();
        } else {
          // Code invalide
          effacerWiFi();
          server.send(400,"text/plain","Code invalide. Verifiez le code donne par le medecin.");
        }
      } else {
        effacerWiFi();
        server.send(400,"text/plain","WiFi incorrect. Verifiez SSID et mot de passe.");
      }
    } else {
      server.send(400,"text/plain","SSID et code obligatoires");
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

  // Homing
  faireHoming();

  // WiFi
  String ssid, pass;
  if (chargerWiFi(ssid, pass)) {
    if (connecterWiFi(ssid, pass)) {
      wifiConfiguree = true;
      afficherOLED("WiFi OK", WiFi.localIP().toString().c_str());
      delay(500);
      syncNTP();
      if (connecterMQTT()) {
        publierStatut("online","Demarrage OK");
        viderBufferSPIFFS();
      }
    } else {
      effacerWiFi();
    }
  }

  if (!wifiConfiguree) lancerModeAP();

  // Init couvercle
  if (homingFait) {
    etatCouvercle = couvercleEstOuvert();
    ancienEtatCouvercle = etatCouvercle;
    pretPourOuverture = !etatCouvercle;
    forcerReposOLED();
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
    afficherReposOLED();
  }

  // ── OUVERTURE ──
  if (pretPourOuverture &&
      etatCouvercle==true &&
      ancienEtatCouvercle==false &&
      !compartimentPresente) {

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
