#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// =====================================================
// OLED
// =====================================================
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 32
#define OLED_RESET -1
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// =====================================================
// PINS ESP32
// =====================================================
#define IN1 14
#define IN2 27
#define IN3 26
#define IN4 25

#define REED_PIN 4
#define COVER_PIN 2
#define BTN_NEXT_PIN 5
#define BTN_PREV_PIN 18

// =====================================================
// CONSTANTES
// =====================================================
#define NB_POSITIONS 22
#define POS0 0
#define PREMIER_COMPARTIMENT 1
#define DERNIER_COMPARTIMENT 21

#define STEP_DELAY_MS 3
#define STEPS_PER_REV 4096

const float ANGLE_PAR_POSITION = 360.0 / 22.0;

const int stepsParPosition[NB_POSITIONS] = {
  186, 186, 186, 186, 186,
  186, 186, 186, 186, 186,
  187,
  186, 186, 186, 186, 186,
  186, 186, 186, 186, 186,
  187
};

const int stepSequence[8][4] = {
  {1, 0, 0, 0},
  {1, 1, 0, 0},
  {0, 1, 0, 0},
  {0, 1, 1, 0},
  {0, 0, 1, 0},
  {0, 0, 1, 1},
  {0, 0, 0, 1},
  {1, 0, 0, 1}
};

// =====================================================
// VARIABLES
// =====================================================
int positionActuelle = 0;
int currentStepIndex = 0;

bool modeRemplissage = true;
bool positionInitialisee = false;
bool sessionTerminee = false;

bool lastCoverState = false;
bool currentCoverState = false;

bool lastNextRaw = HIGH;
bool lastPrevRaw = HIGH;

// =====================================================
// NOMS DES COMPARTIMENTS
// =====================================================
const char* nomCompartiment(int pos) {
  switch (pos) {
    case 0:  return "Pos0 vide";
    case 1:  return "Lun matin";
    case 2:  return "Lun midi";
    case 3:  return "Lun soir";
    case 4:  return "Mar matin";
    case 5:  return "Mar midi";
    case 6:  return "Mar soir";
    case 7:  return "Mer matin";
    case 8:  return "Mer midi";
    case 9:  return "Mer soir";
    case 10: return "Jeu matin";
    case 11: return "Jeu midi";
    case 12: return "Jeu soir";
    case 13: return "Ven matin";
    case 14: return "Ven midi";
    case 15: return "Ven soir";
    case 16: return "Sam matin";
    case 17: return "Sam midi";
    case 18: return "Sam soir";
    case 19: return "Dim matin";
    case 20: return "Dim midi";
    case 21: return "Dim soir";
    default: return "Inconnu";
  }
}

// =====================================================
// OLED
// =====================================================
void afficher2Lignes(String l1, String l2) {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(WHITE);

  display.setCursor(0, 0);
  display.println(l1);

  display.setCursor(0, 16);
  display.println(l2);

  display.display();
}

void afficherPosition() {
  float angle = positionActuelle * ANGLE_PAR_POSITION;

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(WHITE);

  display.setCursor(0, 0);
  display.print("Comp ");
  display.print(positionActuelle);
  display.print("  ");
  display.print(angle, 1);
  display.print((char)247);

  display.setCursor(0, 16);
  display.println(nomCompartiment(positionActuelle));

  display.display();
}

// =====================================================
// REED POS0 STABLE
// =====================================================
int lireStableReed() {
  int countLow = 0;

  for (int i = 0; i < 10; i++) {
    if (digitalRead(REED_PIN) == LOW) {
      countLow++;
    }
    delay(2);
  }

  if (countLow > 7) {
    return LOW;
  } else {
    return HIGH;
  }
}

bool reedDetecte() {
  return (lireStableReed() == LOW);
}

// =====================================================
// COUVERCLE STABLE
// =====================================================
int lireStableCouvercle() {
  int countHigh = 0;

  for (int i = 0; i < 10; i++) {
    if (digitalRead(COVER_PIN) == HIGH) {
      countHigh++;
    }
    delay(2);
  }

  if (countHigh > 7) {
    return HIGH;
  } else {
    return LOW;
  }
}

bool lireCouvercle() {
  return (lireStableCouvercle() == HIGH);
}

// =====================================================
// MOTEUR
// =====================================================
void appliquerEtape(int idx) {
  digitalWrite(IN1, stepSequence[idx][0]);
  digitalWrite(IN2, stepSequence[idx][1]);
  digitalWrite(IN3, stepSequence[idx][2]);
  digitalWrite(IN4, stepSequence[idx][3]);
}

void tournerUnPas(int direction) {
  currentStepIndex += direction;

  if (currentStepIndex > 7) currentStepIndex = 0;
  if (currentStepIndex < 0) currentStepIndex = 7;

  appliquerEtape(currentStepIndex);
  delay(STEP_DELAY_MS);
}

void stopMoteur() {
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, LOW);
}

void tournerPlusieursPas(int nbPas, int direction) {
  for (int i = 0; i < nbPas; i++) {
    tournerUnPas(direction);
  }
  stopMoteur();
}

// =====================================================
// HOMING
// =====================================================
bool trouverPositionZero() {
  afficher2Lignes("Homing...", "Recherche POS0");

  if (reedDetecte()) {
    positionActuelle = 0;
    afficher2Lignes("POS0 OK", "Deja detecte");
    delay(800);
    return true;
  }

  for (int i = 0; i < STEPS_PER_REV + 300; i++) {
    tournerUnPas(1);

    if (reedDetecte()) {
      stopMoteur();
      positionActuelle = 0;
      afficher2Lignes("POS0 OK", "Aimant detecte");
      delay(800);
      return true;
    }
  }

  stopMoteur();
  afficher2Lignes("ERREUR", "Reed introuvable");
  return false;
}

// =====================================================
// DEPLACEMENT ENTRE POSITIONS
// =====================================================
void allerPositionSuivante() {
  int cible = positionActuelle + 1;
  if (cible >= NB_POSITIONS) cible = 0;

  int nbPas = stepsParPosition[positionActuelle];
  tournerPlusieursPas(nbPas, 1);

  positionActuelle = cible;
}

void allerPositionPrecedente() {
  int precedente = positionActuelle - 1;
  if (precedente < 0) precedente = NB_POSITIONS - 1;

  int nbPas = stepsParPosition[precedente];
  tournerPlusieursPas(nbPas, -1);

  positionActuelle = precedente;
}

void allerACompartement1DepuisPos0() {
  tournerPlusieursPas(stepsParPosition[0], 1);
  positionActuelle = 1;
}

void retourPositionZeroDepuisActuelle() {
  while (positionActuelle != 0) {
    allerPositionPrecedente();
    afficherPosition();
    delay(150);
  }
}

// =====================================================
// BOUTONS
// =====================================================
bool boutonNextAppuye() {
  bool raw = digitalRead(BTN_NEXT_PIN);

  if (lastNextRaw == HIGH && raw == LOW) {
    delay(25);
    if (digitalRead(BTN_NEXT_PIN) == LOW) {
      lastNextRaw = raw;
      return true;
    }
  }

  lastNextRaw = raw;
  return false;
}

bool boutonPrevAppuye() {
  bool raw = digitalRead(BTN_PREV_PIN);

  if (lastPrevRaw == HIGH && raw == LOW) {
    delay(25);
    if (digitalRead(BTN_PREV_PIN) == LOW) {
      lastPrevRaw = raw;
      return true;
    }
  }

  lastPrevRaw = raw;
  return false;
}

// =====================================================
// SETUP
// =====================================================
void setup() {
  delay(500);
  Serial.begin(115200);

  // I2C + OLED EN PREMIER
  Wire.begin(16, 17);
  delay(100);

  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("OLED NON DETECTE");
    while (true);
  }

  afficher2Lignes("Demarrage", "Mode remplissage");
  delay(700);

  // PUIS les pins moteur et capteurs
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);

  pinMode(REED_PIN, INPUT_PULLUP);
  pinMode(COVER_PIN, INPUT_PULLUP);
  pinMode(BTN_NEXT_PIN, INPUT_PULLUP);
  pinMode(BTN_PREV_PIN, INPUT_PULLUP);

  // ← ICI
  pinMode(15, OUTPUT);
  digitalWrite(15, HIGH);
  stopMoteur();

  if (!trouverPositionZero()) {
    while (true) {
      afficher2Lignes("ERREUR", "Verifier reed");
      delay(500);
    }
  }

  currentCoverState = lireCouvercle();
  lastCoverState = currentCoverState;

  afficher2Lignes("Systeme pret", "Ouvre couvercle");
  delay(800);
}

// =====================================================
// LOOP
// =====================================================
void loop() {
  currentCoverState = lireCouvercle();

  if (modeRemplissage) {
    if (!positionInitialisee && currentCoverState) {
      afficher2Lignes("Ouverture detectee", "Aller comp 1");
      delay(400);

      allerACompartement1DepuisPos0();
      positionInitialisee = true;

      afficherPosition();
      delay(300);
    }

    if (currentCoverState && positionInitialisee) {
      afficherPosition();

      if (boutonNextAppuye()) {
        allerPositionSuivante();
        afficherPosition();
        delay(200);
      }

      if (boutonPrevAppuye()) {
        allerPositionPrecedente();
        afficherPosition();
        delay(200);
      }
    }

    if (!currentCoverState && lastCoverState && positionInitialisee) {
      afficher2Lignes("Fin remplissage", "Retour POS0");
      delay(500);

      retourPositionZeroDepuisActuelle();

      if (!reedDetecte()) {
        trouverPositionZero();
      }

      afficher2Lignes("Session terminee", "Retour POS0 OK");
      modeRemplissage = false;
      sessionTerminee = true;
      delay(800);
    }

    if (!positionInitialisee && !currentCoverState) {
      afficher2Lignes("Attente", "Ouvrir couvercle");
    }

  } else {
    if (sessionTerminee) {
      afficher2Lignes("Remplissage fini", "Systeme en pause");
    }
  }

  lastCoverState = currentCoverState;
  delay(50);
}
