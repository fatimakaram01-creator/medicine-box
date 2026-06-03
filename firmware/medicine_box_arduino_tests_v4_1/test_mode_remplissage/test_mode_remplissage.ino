/*
 * ============================================================
 *  MEDICINE BOX — Test logique remplissage + OLED suivi complet
 * ============================================================
 *
 *  Objectif :
 *  - Reprendre l'esprit du "code d'avant" orienté test logique.
 *  - Supposer que le patient a déjà cliqué sur "mode remplissage"
 *    dans l'application.
 *  - Démarrer directement en mode remplissage.
 *  - Les boutons poussoirs ne sont actifs que si le couvercle est ouvert.
 *  - L'OLED suit chaque étape :
 *      * état général
 *      * compartiment courant
 *      * degrés restants pendant le déplacement
 *      * poids total final mesuré à la fin de la session
 *  - À la fermeture du couvercle, on termine la session, on mesure
 *    le poids total, on le garde comme référence de la première prise.
 *
 *  Important :
 *  - Ce fichier est indépendant.
 *  - Aucun autre fichier .ino du projet n'est modifié.
 * ============================================================
 */

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ============================================================
// 1. PINS / CONSTANTES
// ============================================================

#define MOTOR_PIN1 8
#define MOTOR_PIN2 9
#define MOTOR_PIN3 10
#define MOTOR_PIN4 11

#define REED_PIN 2
#define COVER_SWITCH_PIN 3
#define LED_STATUS_PIN 4
#define BTN_NEXT_PIN 5
#define BTN_PREV_PIN 6
#define BUZZER_PIN 7
#define LED_POSITION_PIN 12
#define WEIGHT_PIN A0

#define STEP_DELAY_MS 3
#define STEPS_PER_REV 2048

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 32
#define OLED_RESET -1
#define OLED_ADDRESS 0x3C

#define NB_POSITIONS 22
#define POSITION_REPOS 0
#define COMPARTIMENT_MIN 1
#define COMPARTIMENT_MAX 21

#define DEBOUNCE_MS 50
#define COVER_DEBOUNCE_MS 80

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

const int stepsParPosition[NB_POSITIONS] = {
  93, 93, 93, 93, 93,
  93, 93, 93, 93, 93,
  94,
  93, 93, 93, 93, 93,
  93, 93, 93, 93, 93,
  94
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

struct BoutonState {
  bool etatStable;
  bool etatPrecedentRaw;
  unsigned long lastChangeMs;
  bool frontDetecte;
};

enum SystemState {
  STATE_OK,
  STATE_ERROR
};

int stepsCumulatifs[NB_POSITIONS];
int positionActuelle = POSITION_REPOS;
int currentStepIndex = 0;

bool modeRemplissage = true;
bool sessionRemplissageTerminee = false;
bool positionInitialeFaite = false;

bool couvercleOuvert = false;
bool couvercleStable = false;
bool couvercleStablePrecedent = false;
bool coverLastRawState = false;
unsigned long coverLastChangeMs = 0;

BoutonState btnNext = {HIGH, HIGH, 0, false};
BoutonState btnPrev = {HIGH, HIGH, 0, false};

SystemState etatSysteme = STATE_ERROR;

int poidsTotalFinRemplissage = 0;
bool poidsReferenceDisponible = false;

// ============================================================
// 2. OLED
// ============================================================

void afficherOLED(const char *ligne1, const char *ligne2 = "") {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.println(ligne1);
  display.setCursor(0, 16);
  display.println(ligne2);
  display.display();
}

const char *nomCompartiment(int compartiment) {
  switch (compartiment) {
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
    default: return "Pos 0 vide";
  }
}

void afficherCompartimentCourant() {
  if (positionActuelle == POSITION_REPOS) {
    afficherOLED("Mode remplissage", "Pos 0 vide");
  } else {
    afficherOLED("Compartiment", nomCompartiment(positionActuelle));
  }
}

// ============================================================
// 3. MOTEUR
// ============================================================

void tournerUnPas(int direction) {
  currentStepIndex += direction;
  if (currentStepIndex > 7) currentStepIndex = 0;
  if (currentStepIndex < 0) currentStepIndex = 7;

  digitalWrite(MOTOR_PIN1, stepSequence[currentStepIndex][0]);
  digitalWrite(MOTOR_PIN2, stepSequence[currentStepIndex][1]);
  digitalWrite(MOTOR_PIN3, stepSequence[currentStepIndex][2]);
  digitalWrite(MOTOR_PIN4, stepSequence[currentStepIndex][3]);
  delay(STEP_DELAY_MS);
}

void stopMoteur() {
  digitalWrite(MOTOR_PIN1, LOW);
  digitalWrite(MOTOR_PIN2, LOW);
  digitalWrite(MOTOR_PIN3, LOW);
  digitalWrite(MOTOR_PIN4, LOW);
}

bool lireReedSwitch() {
  return (digitalRead(REED_PIN) == LOW);
}

bool trouverPositionZero() {
  afficherOLED("Initialisation", "Homing POS0");

  if (lireReedSwitch()) {
    positionActuelle = POSITION_REPOS;
    return true;
  }

  for (int i = 0; i < STEPS_PER_REV + 300; i++) {
    if (lireReedSwitch()) {
      stopMoteur();
      positionActuelle = POSITION_REPOS;
      return true;
    }
    tournerUnPas(1);
  }

  stopMoteur();
  return false;
}

void allerAPositionAvecSuivi(int cible, const char *titre) {
  if (cible < 0 || cible >= NB_POSITIONS) return;
  if (cible == positionActuelle) return;

  int stepsActuel = stepsCumulatifs[positionActuelle];
  int stepsCible = stepsCumulatifs[cible];
  int distHoraire = (stepsCible - stepsActuel + STEPS_PER_REV) % STEPS_PER_REV;
  int distAntiHoraire = (stepsActuel - stepsCible + STEPS_PER_REV) % STEPS_PER_REV;

  int direction = 1;
  int steps = distHoraire;

  if (distAntiHoraire < distHoraire) {
    direction = -1;
    steps = distAntiHoraire;
  }

  int stepsRestants = steps;
  char ligne2[22];

  for (int i = 0; i < steps; i++) {
    tournerUnPas(direction);
    stepsRestants--;

    if ((i % 32) == 0 || stepsRestants == 0) {
      int degRestants = (stepsRestants * 360L) / STEPS_PER_REV;
      snprintf(ligne2, sizeof(ligne2), "Reste %d deg", degRestants);
      afficherOLED(titre, ligne2);
    }
  }

  stopMoteur();
  positionActuelle = cible;
}

void avancerCompartiment() {
  int cible = positionActuelle + 1;
  if (cible > COMPARTIMENT_MAX) cible = COMPARTIMENT_MIN;
  allerAPositionAvecSuivi(cible, "Avancer...");
}

void reculerCompartiment() {
  int cible = positionActuelle - 1;
  if (cible < COMPARTIMENT_MIN) cible = COMPARTIMENT_MAX;
  allerAPositionAvecSuivi(cible, "Reculer...");
}

// ============================================================
// 4. ENTREES
// ============================================================

void mettreAJourCouvercle() {
  bool raw = (digitalRead(COVER_SWITCH_PIN) == HIGH);

  if (raw != coverLastRawState) {
    coverLastChangeMs = millis();
    coverLastRawState = raw;
  }

  if ((millis() - coverLastChangeMs) > COVER_DEBOUNCE_MS) {
    couvercleStable = coverLastRawState;
  }

  couvercleOuvert = couvercleStable;
}

void mettreAJourBouton(BoutonState &btn, int pin) {
  bool raw = digitalRead(pin);

  if (raw != btn.etatPrecedentRaw) {
    btn.lastChangeMs = millis();
    btn.etatPrecedentRaw = raw;
  }

  if ((millis() - btn.lastChangeMs) > DEBOUNCE_MS) {
    bool nouveauStable = raw;
    if (btn.etatStable == HIGH && nouveauStable == LOW) {
      btn.frontDetecte = true;
    }
    btn.etatStable = nouveauStable;
  }
}

bool btnNextPresse() {
  mettreAJourBouton(btnNext, BTN_NEXT_PIN);
  if (btnNext.frontDetecte) {
    btnNext.frontDetecte = false;
    return true;
  }
  return false;
}

bool btnPrevPresse() {
  mettreAJourBouton(btnPrev, BTN_PREV_PIN);
  if (btnPrev.frontDetecte) {
    btnPrev.frontDetecte = false;
    return true;
  }
  return false;
}

int lireCapteurPoids() {
  long somme = 0;
  for (int i = 0; i < 8; i++) {
    somme += analogRead(WEIGHT_PIN);
    delay(2);
  }
  return (int)(somme / 8);
}

// ============================================================
// 5. LOGIQUE REMPLISSAGE
// ============================================================

void initialiserRemplissage() {
  afficherOLED("Init rempliss.", "Aller Lun matin");
  digitalWrite(LED_POSITION_PIN, HIGH);
  allerAPositionAvecSuivi(COMPARTIMENT_MIN, "Initialisation");
  positionInitialeFaite = true;
  afficherCompartimentCourant();
}

void terminerSessionRemplissage() {
  delay(200);
  poidsTotalFinRemplissage = lireCapteurPoids();
  poidsReferenceDisponible = true;
  sessionRemplissageTerminee = true;

  char ligne2[22];
  snprintf(ligne2, sizeof(ligne2), "Poids %d", poidsTotalFinRemplissage);

  afficherOLED("Fin rempliss.", ligne2);
  delay(1200);

  afficherOLED("Retour repos", "Position 0");
  digitalWrite(LED_POSITION_PIN, LOW);
  allerAPositionAvecSuivi(POSITION_REPOS, "Retour repos");

  modeRemplissage = false;
}

void gererModeRemplissage() {
  if (!positionInitialeFaite) {
    initialiserRemplissage();
  }

  if (!couvercleOuvert) {
    afficherOLED("Couvercle ferme", "Ouvrir = HIGH");
    return;
  }

  afficherCompartimentCourant();

  if (btnNextPresse()) {
    digitalWrite(LED_POSITION_PIN, LOW);
    avancerCompartiment();
    digitalWrite(LED_POSITION_PIN, HIGH);
    afficherCompartimentCourant();
  }

  if (btnPrevPresse()) {
    digitalWrite(LED_POSITION_PIN, LOW);
    reculerCompartiment();
    digitalWrite(LED_POSITION_PIN, HIGH);
    afficherCompartimentCourant();
  }
}

// ============================================================
// 6. SETUP / LOOP
// ============================================================

void setup() {
  Serial.begin(9600);

  display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDRESS);
  afficherOLED("Demarrage", "Test logique");

  pinMode(MOTOR_PIN1, OUTPUT);
  pinMode(MOTOR_PIN2, OUTPUT);
  pinMode(MOTOR_PIN3, OUTPUT);
  pinMode(MOTOR_PIN4, OUTPUT);
  stopMoteur();

  pinMode(REED_PIN, INPUT_PULLUP);
  pinMode(COVER_SWITCH_PIN, INPUT);
  pinMode(BTN_NEXT_PIN, INPUT_PULLUP);
  pinMode(BTN_PREV_PIN, INPUT_PULLUP);

  pinMode(LED_STATUS_PIN, OUTPUT);
  pinMode(LED_POSITION_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);

  digitalWrite(LED_STATUS_PIN, LOW);
  digitalWrite(LED_POSITION_PIN, LOW);
  digitalWrite(BUZZER_PIN, LOW);

  stepsCumulatifs[0] = 0;
  for (int i = 1; i < NB_POSITIONS; i++) {
    stepsCumulatifs[i] = stepsCumulatifs[i - 1] + stepsParPosition[i - 1];
  }

  coverLastRawState = (digitalRead(COVER_SWITCH_PIN) == HIGH);
  couvercleStable = coverLastRawState;
  couvercleOuvert = couvercleStable;
  couvercleStablePrecedent = couvercleStable;
  coverLastChangeMs = millis();

  if (trouverPositionZero()) {
    etatSysteme = STATE_OK;
    digitalWrite(LED_STATUS_PIN, HIGH);
    afficherOLED("Systeme pret", "Remplissage actif");
  } else {
    etatSysteme = STATE_ERROR;
    afficherOLED("ERREUR", "Reed introuvable");
  }

  Serial.println(F("================================="));
  Serial.println(F("MEDICINE BOX - test logique"));
  Serial.println(F("Mode remplissage direct actif"));
  Serial.println(F("Boutons actifs si couvercle HIGH"));
  Serial.println(F("OLED suit les etapes"));
  Serial.println(F("================================="));
}

void loop() {
  if (etatSysteme == STATE_ERROR) {
    afficherOLED("ERREUR", "Verifier systeme");
    delay(300);
    return;
  }

  mettreAJourCouvercle();

  if (modeRemplissage) {
    gererModeRemplissage();

    if (!couvercleOuvert && couvercleStablePrecedent && positionInitialeFaite) {
      terminerSessionRemplissage();
    }
  } else {
    char ligne2[22];
    if (poidsReferenceDisponible) {
      snprintf(ligne2, sizeof(ligne2), "Poids %d", poidsTotalFinRemplissage);
      afficherOLED("Session terminee", ligne2);
    } else if (sessionRemplissageTerminee) {
      afficherOLED("Session terminee", "Aucune ref");
    } else {
      afficherOLED("Attente", "Aucune action");
    }
  }

  couvercleStablePrecedent = couvercleOuvert;
}