#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ************************************************************
// TEST 5 : BOUTONS + OLED + MOTEUR STEPPER
// NEXT  -> avance d'un compartiment
// PREV  -> recule d'un compartiment
// Action autorisee seulement si couvercle OUVERT
// ************************************************************

// ---------------- OLED ----------------
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

// ---------------- PINS ----------------
#define BTN_NEXT   5
#define BTN_PREV   6
#define COVER_PIN  3

#define IN1 8
#define IN2 9
#define IN3 10
#define IN4 11

// Temps anti-rebond boutons
#define DB_MS 50

// Petit delai moteur
#define STEP_DELAY 3

// Nombre total de positions
#define NB_POS 22

// ---------------- STEPPER ----------------
// Sequence demi-pas (plus douce)
const int stepSeq[8][4] = {
  {1,0,0,0},
  {1,1,0,0},
  {0,1,0,0},
  {0,1,1,0},
  {0,0,1,0},
  {0,0,1,1},
  {0,0,0,1},
  {1,0,0,1}
};

// Nombre de pas entre compartiments
// Compensation pour total = 2048 pas / tour
const int stepsPC[22] = {
  93,93,93,93,93, 93,93,93,93,93, 94,
  93,93,93,93,93, 93,93,93,93,93, 94
};

// Index sequence moteur
int sIdx = 0;

// Position actuelle
int pos = 0;

// Message OLED
String msg = "ATTENTE";

// ---------------- STRUCTURE BOUTON ----------------
struct Btn {
  bool stab;              // etat stable
  bool prevR;             // lecture brute precedente
  unsigned long lc;       // dernier changement
  bool front;             // appui detecte
};

Btn bN = {HIGH, HIGH, 0, false};
Btn bP = {HIGH, HIGH, 0, false};

// ************************************************************
// Mise a jour bouton avec anti-rebond
// ************************************************************
void updBtn(Btn &b, int pin) {
  bool r = digitalRead(pin);

  // Si lecture brute change -> restart timer
  if (r != b.prevR) {
    b.lc = millis();
    b.prevR = r;
  }

  // Si stable assez longtemps
  if ((millis() - b.lc) > DB_MS) {
    bool nv = r;

    // Front d'appui : HIGH -> LOW
    if (b.stab == HIGH && nv == LOW) {
      b.front = true;
    }

    b.stab = nv;
  }
}

// ************************************************************
// Retourne true une seule fois par appui
// ************************************************************
bool pressed(Btn &b) {
  if (b.front) {
    b.front = false;
    return true;
  }
  return false;
}

// ************************************************************
// Coupe les 4 sorties moteur
// ************************************************************
void stopMotor() {
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, LOW);
}

// ************************************************************
// Fait avancer le moteur d'un certain nombre de demi-pas
// dir = +1 -> sens avant
// dir = -1 -> sens arriere
// ************************************************************
void moveSteps(int nbSteps, int dir) {

  int d = 5; // vitesse initiale (lent)

  for (int i = 0; i < nbSteps; i++) {

    // accélération progressive
    if (i < nbSteps / 2 && d > 1) {
      d--;   // accélère
    } 
    else if (i > nbSteps / 2) {
      d++;   // ralentit (freinage)
    }

    if (dir > 0)
      sIdx = (sIdx + 1) % 8;
    else
      sIdx = (sIdx - 1 + 8) % 8;

    digitalWrite(IN1, stepSeq[sIdx][0]);
    digitalWrite(IN2, stepSeq[sIdx][1]);
    digitalWrite(IN3, stepSeq[sIdx][2]);
    digitalWrite(IN4, stepSeq[sIdx][3]);

    delay(d);
  }

  stopMotor();
}

// ************************************************************
// Avance d'un compartiment
// ************************************************************
void moveNextPosition() {
  int steps = stepsPC[pos];       // nb de pas pour quitter position actuelle
  moveSteps(steps, +1);           // avancer
  pos = (pos + 1) % NB_POS;       // maj position
}

// ************************************************************
// Recule d'un compartiment
// Pour revenir de pos actuelle vers precedente,
// on prend le nombre de pas du segment precedent
// ************************************************************
void movePrevPosition() {
  int prevIndex = (pos - 1 + NB_POS) % NB_POS;
  int steps = stepsPC[prevIndex];
  moveSteps(steps, -1);           // reculer
  pos = prevIndex;                // maj position
}

// ************************************************************
// Affichage OLED
// ************************************************************
void showOLED(bool couvOuvert) {
  display.clearDisplay();

  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println("BOUTONS + MOTEUR");

  display.setCursor(0, 12);
  display.print("Couv: ");
  display.println(couvOuvert ? "OUVERT" : "FERME");

  display.setCursor(0, 24);
  display.print("Pos: ");
  display.println(pos);

  display.setTextSize(2);
  display.setCursor(0, 42);
  display.println(msg);

  display.display();
}

// ************************************************************
// SETUP
// ************************************************************
void setup() {
  Serial.begin(9600);

  pinMode(BTN_NEXT, INPUT_PULLUP);
  pinMode(BTN_PREV, INPUT_PULLUP);
  pinMode(COVER_PIN, INPUT_PULLUP);

  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);

  stopMotor();

  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("Erreur OLED");
    for (;;);
  }

  display.clearDisplay();
  display.setTextColor(WHITE);

  Serial.println("TEST 5 : BOUTONS + OLED + STEPPER");
}

// ************************************************************
// LOOP
// ************************************************************
void loop() {
  // HIGH = couvercle ouvert
  // LOW  = couvercle ferme
  bool couvOuvert = (digitalRead(COVER_PIN) == HIGH);

  // Mise a jour boutons
  updBtn(bN, BTN_NEXT);
  updBtn(bP, BTN_PREV);

  // Si couvercle ferme -> boutons bloques
  if (!couvOuvert) {
    bN.front = false;
    bP.front = false;
    msg = "OUVRIR";
    showOLED(couvOuvert);
    delay(20);
    return;
  }

  // Si bouton NEXT appuye
  if (pressed(bN)) {
    msg = "NEXT";
    showOLED(couvOuvert);   // afficher avant mouvement
    moveNextPosition();

    Serial.print(">> NEXT -> pos ");
    Serial.println(pos);
  }

  // Si bouton PREV appuye
  if (pressed(bP)) {
    msg = "PREV";
    showOLED(couvOuvert);   // afficher avant mouvement
    movePrevPosition();

    Serial.print(">> PREV -> pos ");
    Serial.println(pos);
  }

  // Affichage permanent
  showOLED(couvOuvert);
  delay(20);
}