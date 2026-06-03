/*
 * ============================================================
 *  MEDICINE BOX — Code embarqué Arduino UNO
 *  Prototype Proteus — Version 4.1 (finale)
 * ============================================================
 *
 *  AJUSTEMENTS v4.1 vs v4.0 :
 *    1. Clarification : position 0 = compartiment VIDE (sécurité)
 *       Compartiments médicaments = 1 à 21 (21 au total)
 *    2. Mode remplissage : premier appui = activation seulement
 *       (documenté explicitement). Mouvement dès le 2e appui.
 *    3. buzzerDoubleBip() rendu cohérent (utilise uniquement
 *       digitalWrite, pas de double vérification)
 *    4. Commentaires de la table cumulative clarifiés
 *    5. Vérification complète de cohérence effectuée
 *
 *  ARCHITECTURE DE LA BOÎTE :
 *    22 positions au total sur le plateau rotatif :
 *      - Position 0 : compartiment VIDE (fenêtre d'accès / sécurité)
 *                      C'est la position de repos et de référence.
 *      - Positions 1 à 21 : compartiments MÉDICAMENTS
 *                      (7 jours × 3 prises = 21 compartiments utiles)
 *    Le plateau est toujours sur position 0 quand la boîte est fermée.
 *
 *  LOGIQUE RÉELLE :
 *    Mode normal     : patient ouvre → moteur amène compartiment 1-21 →
 *                      patient prend → patient ferme → retour pos 0
 *    Mode remplissage : boutons actifs SEULEMENT si couvercle ouvert
 *    Reed switch     : recalage initial au démarrage UNIQUEMENT
 *    Buzzer          : muet pendant les 7 premiers jours
 *
 *  Auteur : Claude (agent software) pour Fatima
 *  Date : Avril 2026
 * ============================================================
 */

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ============================================================
// 1. CONSTANTES ET PINS
// ============================================================

// --- Moteur stepper (via ULN2003A) ---
#define MOTOR_PIN1  8
#define MOTOR_PIN2  9
#define MOTOR_PIN3  10
#define MOTOR_PIN4  11

// --- Interrupteurs (2 distincts — NE PAS CONFONDRE) ---
#define REED_PIN         2  // Reed switch : position 0 (recalage démarrage UNIQUEMENT)
#define COVER_SWITCH_PIN 3  // Interrupteur couvercle : ouverture/fermeture boîte

// --- Boutons (MODE REMPLISSAGE + couvercle ouvert OBLIGATOIRE) ---
#define BTN_NEXT_PIN  5  // Avancer d'un compartiment
#define BTN_PREV_PIN  6  // Reculer d'un compartiment

// --- LEDs ---
#define LED_STATUS_PIN    4   // État système (prêt / erreur)
#define LED_POSITION_PIN  12  // Compartiment aligné avec l'ouverture

// --- Buzzer ---
#define BUZZER_PIN  7  // Rappel (MUET pendant apprentissage)

// --- Capteur poids simulé ---
#define WEIGHT_PIN  A0  // Potentiomètre

// --- Paramètres moteur ---
#define STEP_DELAY_MS   3
#define STEPS_PER_REV   2048  // 28BYJ-48

// --- OLED SSD1306 I2C ---
#define SCREEN_WIDTH   128
#define SCREEN_HEIGHT   64
#define OLED_RESET      -1
#define OLED_ADDRESS   0x3C

// --- Compartiments ---
// 22 positions : pos 0 = VIDE (sécurité), pos 1-21 = médicaments
#define NB_POSITIONS         22  // Nombre total de positions sur le plateau
#define COMPARTIMENT_MIN      1  // Premier compartiment médicament
#define COMPARTIMENT_MAX     21  // Dernier compartiment médicament
#define POSITION_REPOS        0  // Position de repos = compartiment vide

// --- Anti-rebond ---
#define DEBOUNCE_MS        50
#define COVER_DEBOUNCE_MS  80

// --- Seuils poids ---
#define WEIGHT_EMPTY    200
#define WEIGHT_PRESENT  600
#define WEIGHT_DIFF_MIN  50  // Différence minimale pour détecter une prise

// --- Apprentissage buzzer ---
#define LEARNING_DAYS  7

// ============================================================
// 2. TABLE DE COMPENSATION DÉRIVE MOTEUR
// ============================================================

/*
 * PROBLÈME : 2048 pas / 22 positions = 93.0909... pas par position.
 * Avec 93 pas fixes : 93 × 22 = 2046 → 2 pas manquants par tour.
 *
 * SOLUTION : donner 94 pas à 2 positions (les positions 11 et 22,
 * réparties au milieu et en fin de tour).
 * Total : 20×93 + 2×94 = 1860 + 188 = 2048 ✓
 *
 * stepsParPosition[i] = nombre de pas pour aller du CENTRE de la
 * position i au CENTRE de la position i+1.
 *
 * Exemple :
 *   stepsParPosition[0] = 93 → du centre de pos 0 au centre de pos 1
 *   stepsParPosition[10] = 94 → du centre de pos 10 au centre de pos 11
 */
const int stepsParPosition[NB_POSITIONS] = {
  93, 93, 93, 93, 93,   // pos 0→1, 1→2, 2→3, 3→4, 4→5
  93, 93, 93, 93, 93,   // pos 5→6, 6→7, 7→8, 8→9, 9→10
  94,                     // pos 10→11 (+1 compensation)
  93, 93, 93, 93, 93,   // pos 11→12, 12→13, 13→14, 14→15, 15→16
  93, 93, 93, 93, 93,   // pos 16→17, 17→18, 18→19, 19→20, 20→21
  94                      // pos 21→0 (+1 compensation, ferme le tour)
};

/*
 * Table cumulative : nombre total de pas du centre de pos 0
 * vers le centre de la position i.
 *
 * stepsCumulatifs[0] = 0      → pos 0 est le point de départ
 * stepsCumulatifs[1] = 93     → du centre pos 0 au centre pos 1
 * stepsCumulatifs[2] = 186    → du centre pos 0 au centre pos 2
 * ...
 * stepsCumulatifs[21] = 2048 - stepsParPosition[21] = 1954
 *
 * NOTE : l'index correspond directement au numéro de position.
 * Pas de décalage ±1.
 */
int stepsCumulatifs[NB_POSITIONS];

// ============================================================
// 3. VARIABLES GLOBALES
// ============================================================

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// --- État système ---
enum SystemState {
  STATE_OK,     // Système recalé et opérationnel
  STATE_ERROR   // Recalage échoué — système bloqué
};
SystemState etatSysteme = STATE_ERROR;  // En erreur jusqu'au recalage

// --- Position ---
int positionActuelle = 0;  // Position courante (0 = repos/vide, 1-21 = médicaments)

// --- Mode ---
bool modeRemplissage = false;

// --- Couvercle (avec debounce) ---
bool couvercleOuvert = false;
bool couvercleStable = false;
bool couvercleStablePrecedent = false;
unsigned long coverLastChangeMs = 0;
bool coverLastRawState = false;

// --- Cycle normal ---
enum CycleNormal {
  CYCLE_ATTENTE,        // Boîte fermée, plateau sur pos 0, attend ouverture
  CYCLE_DEPLACEMENT,    // Moteur amène le compartiment médicament
  CYCLE_PRISE_EN_COURS, // Compartiment aligné, attend fermeture
  CYCLE_RETOUR          // Retour à position 0 (repos)
};
CycleNormal cycleNormal = CYCLE_ATTENTE;
bool ouvertureCycleArmee = false;  // Vrai seulement après avoir vu le couvercle fermé

// Prochain compartiment médicament à présenter (1-21)
// En production : reçu du backend via MQTT
int compartimentProgramme = COMPARTIMENT_MIN;
int poidsAvantPrise = 0;
int poidsReferencePostRemplissage = 0;
bool poidsReferencePostRemplissageValide = false;

// --- Buzzer / apprentissage ---
int nombreJoursObserves = 0;
bool phaseApprentissageTerminee = false;
unsigned long dernierJourMs = 0;

// --- Anti-rebond boutons ---
struct BoutonState {
  bool etatStable;
  bool etatPrecedentRaw;
  unsigned long lastChangeMs;
  bool frontDetecte;  // Front descendant confirmé, consommé une seule fois
};

BoutonState btnNext = {HIGH, HIGH, 0, false};
BoutonState btnPrev = {HIGH, HIGH, 0, false};

// --- Moteur ---
int currentStepIndex = 0;

// ============================================================
// 4.bis OLED
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

void afficherAttenteOuverture() {
  afficherOLED("Attente", "ouverture");
}

void afficherRemplissageCourant() {
  if (positionActuelle == POSITION_REPOS) {
    afficherOLED("Mode remplissage", "Pos 0 vide");
  } else {
    afficherOLED("Mode remplissage", nomCompartiment(positionActuelle));
  }
}

// ============================================================
// 4. SÉQUENCE DE PAS (half-step)
// ============================================================

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

// ============================================================
// 5. BLOC MOTEUR
// ============================================================

/*
 * Après le recalage initial, TOUTES les positions sont suivies
 * par COMPTAGE DE PAS. Le reed switch n'intervient plus.
 */

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

void tournerSteps(int nbSteps, int direction) {
  for (int i = 0; i < nbSteps; i++) {
    tournerUnPas(direction);
  }
}

void stopMoteur() {
  digitalWrite(MOTOR_PIN1, LOW);
  digitalWrite(MOTOR_PIN2, LOW);
  digitalWrite(MOTOR_PIN3, LOW);
  digitalWrite(MOTOR_PIN4, LOW);
}

// Aller à une position (0 à 21) par le chemin le plus court.
// Utilise la table cumulative pour un positionnement au CENTRE exact.
void allerAPosition(int cible) {
  if (cible < 0 || cible >= NB_POSITIONS) return;
  if (cible == positionActuelle) return;

  // Pas absolus depuis pos 0
  int stepsActuel = stepsCumulatifs[positionActuelle];
  int stepsCible  = stepsCumulatifs[cible];

  // Distances dans les deux sens
  int distHoraire     = (stepsCible - stepsActuel + STEPS_PER_REV) % STEPS_PER_REV;
  int distAntiHoraire = (stepsActuel - stepsCible + STEPS_PER_REV) % STEPS_PER_REV;

  int steps;
  int direction;

  if (distHoraire <= distAntiHoraire) {
    steps = distHoraire;
    direction = 1;
  } else {
    steps = distAntiHoraire;
    direction = -1;
  }

  Serial.print("[MOTOR] pos ");
  Serial.print(positionActuelle);
  Serial.print(" -> pos ");
  Serial.print(cible);
  Serial.print(" (");
  Serial.print(steps);
  Serial.print(" pas ");
  Serial.print(direction == 1 ? "horaire" : "anti-horaire");
  Serial.println(")");

  int stepsRestants = steps;
  char ligne2[22];
  bool afficherProgression = (cycleNormal == CYCLE_DEPLACEMENT || cycleNormal == CYCLE_RETOUR);

  for (int i = 0; i < steps; i++) {
    tournerUnPas(direction);
    stepsRestants--;

    if (afficherProgression && ((i % 32) == 0 || stepsRestants == 0)) {
      int degRestants = (stepsRestants * 360L) / STEPS_PER_REV;
      snprintf(ligne2, sizeof(ligne2), "Reste %d deg", degRestants);
      if (cycleNormal == CYCLE_DEPLACEMENT) {
        afficherOLED(nomCompartiment(cible), ligne2);
      } else {
        afficherOLED("Retour pos0", ligne2);
      }
    }
  }

  stopMoteur();
  positionActuelle = cible;
}

// Avancer d'une position (mode remplissage)
void avancerUnePosition() {
  int steps = stepsParPosition[positionActuelle];
  tournerSteps(steps, 1);
  stopMoteur();
  positionActuelle = (positionActuelle + 1) % NB_POSITIONS;
}

// Reculer d'une position (mode remplissage)
void reculerUnePosition() {
  // Pour reculer vers la position précédente, on utilise le nombre
  // de pas qui mène DE cette position précédente VERS la position actuelle
  int posPrecedente = (positionActuelle - 1 + NB_POSITIONS) % NB_POSITIONS;
  int steps = stepsParPosition[posPrecedente];
  tournerSteps(steps, -1);
  stopMoteur();
  positionActuelle = posPrecedente;
}

// ============================================================
// 6. BLOC RECALAGE (Reed Switch — DÉMARRAGE UNIQUEMENT)
// ============================================================

/*
 * Le reed switch (D2) sert UNIQUEMENT à retrouver la position 0
 * (compartiment vide / position de repos) au démarrage.
 * Après le recalage, il n'est PLUS lu pendant le fonctionnement.
 *
 * Si le recalage échoue : etatSysteme = STATE_ERROR.
 * Le système refuse de fonctionner. Les LEDs clignotent en erreur.
 *
 * HYPOTHÈSE : INPUT_PULLUP, reed fermé (aimant présent) = LOW.
 */

bool lireReedSwitch() {
  return (digitalRead(REED_PIN) == LOW);
}

bool trouverPositionZero() {
  Serial.println("[RECALAGE] Recherche position 0 (compartiment vide)...");
  afficherOLED("HOMING", "Recherche POS0");

  // Vérifier si on est déjà dessus
  if (lireReedSwitch()) {
    positionActuelle = POSITION_REPOS;
    Serial.println("[RECALAGE] Déjà sur position 0 !");
    afficherOLED("POS0 detectee", "");
    return true;
  }

  // Tourner au maximum un tour complet + marge
  int maxSteps = STEPS_PER_REV + 300;

  for (int i = 0; i < maxSteps; i++) {
    if (lireReedSwitch()) {
      stopMoteur();
      positionActuelle = POSITION_REPOS;
      Serial.println("[RECALAGE] Position 0 trouvée !");
      afficherOLED("POS0 detectee", "");
      return true;
    }
    tournerUnPas(1);
  }

  stopMoteur();
  Serial.println("[RECALAGE] ERREUR CRITIQUE : position 0 non trouvée !");
  afficherOLED("ERREUR", "POS0 absente");
  return false;
}

// ============================================================
// 7. BLOC LECTURE CAPTEURS ET INTERRUPTEURS
// ============================================================

/*
 * RAPPEL : 2 interrupteurs distincts, ne PAS confondre.
 *   D2 = Reed switch       → position 0 (démarrage uniquement)
 *   D3 = Inter. couvercle  → ouverture/fermeture boîte (continu)
 */

void mettreAJourCouvercle() {
  bool raw = (digitalRead(COVER_SWITCH_PIN) == LOW);

  if (raw != coverLastRawState) {
    coverLastChangeMs = millis();
    coverLastRawState = raw;
  }

  if ((millis() - coverLastChangeMs) > COVER_DEBOUNCE_MS) {
    couvercleStable = coverLastRawState;
  }

  couvercleOuvert = couvercleStable;
}

int lireCapteurPoids() {
  long somme = 0;
  for (int i = 0; i < 5; i++) {
    somme += analogRead(WEIGHT_PIN);
    delay(2);
  }
  return (int)(somme / 5);
}

int interpreterPoids(int valeur) {
  if (valeur < WEIGHT_EMPTY) return 0;   // Vide
  if (valeur > WEIGHT_PRESENT) return 2; // Présent
  return 1;                               // Intermédiaire
}

// ============================================================
// 8. BLOC BOUTONS (anti-rebond fiable)
// ============================================================

/*
 * Machine d'états par bouton :
 *   1. Lecture brute du pin
 *   2. Si changement → reset timer
 *   3. Si stable > DEBOUNCE_MS → confirmer nouvel état
 *   4. Front descendant (HIGH→LOW stable) → frontDetecte = true
 *   5. Consommé une seule fois par appel à btnXxxPresse()
 *
 * Les boutons servent au mode remplissage UNIQUEMENT.
 * Condition : mode remplissage activé + couvercle ouvert.
 */

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

// ============================================================
// 9. BLOC LED
// ============================================================

/*
 * LED_STATUS (D4) :
 *   ON fixe       = système recalé et prêt
 *   Clignotant    = erreur (recalage échoué)
 *   OFF           = non initialisé
 *
 * LED_POSITION (D12) :
 *   ON  = compartiment correctement aligné avec l'ouverture
 *   OFF = en mouvement ou pas de cible
 */

void ledStatusOn()  { digitalWrite(LED_STATUS_PIN, HIGH); }
void ledStatusOff() { digitalWrite(LED_STATUS_PIN, LOW); }

void ledStatusBlink(int fois, int delaiMs) {
  for (int i = 0; i < fois; i++) {
    ledStatusOn();  delay(delaiMs);
    ledStatusOff(); delay(delaiMs);
  }
}

void ledPositionOn()  { digitalWrite(LED_POSITION_PIN, HIGH); }
void ledPositionOff() { digitalWrite(LED_POSITION_PIN, LOW); }

// ============================================================
// 10. BLOC BUZZER
// ============================================================

/*
 * MUET pendant les 7 premiers jours (phase d'apprentissage).
 * PAS de bip au démarrage du système.
 *
 * Simulation Proteus : 1 "jour" = 10 secondes.
 * Production : remplacer par 86400000UL (24h).
 */

// Fonction interne : écriture directe sur le buzzer.
// Vérifie l'apprentissage UNE SEULE FOIS en amont.
// Les fonctions publiques (buzzerBip, buzzerDoubleBip, buzzerAlerte)
// font chacune leur propre vérification avant d'appeler celle-ci.
static void _buzzerOnOff(int dureeMs) {
  digitalWrite(BUZZER_PIN, HIGH);
  delay(dureeMs);
  digitalWrite(BUZZER_PIN, LOW);
}

void buzzerBip(int dureeMs) {
  if (!phaseApprentissageTerminee) return;
  _buzzerOnOff(dureeMs);
}

void buzzerDoubleBip() {
  if (!phaseApprentissageTerminee) return;
  _buzzerOnOff(100);
  delay(50);
  _buzzerOnOff(100);
}

void buzzerAlerte() {
  if (!phaseApprentissageTerminee) return;
  _buzzerOnOff(500);
}

void gererApprentissageBuzzer() {
  if (phaseApprentissageTerminee) return;

  // Simulation : 1 jour = 10s | Production : 86400000UL
  unsigned long intervalleJour = 10000UL;

  if (millis() - dernierJourMs >= intervalleJour) {
    dernierJourMs = millis();
    nombreJoursObserves++;

    Serial.print("[BUZZER] Jour ");
    Serial.print(nombreJoursObserves);
    Serial.print("/");
    Serial.println(LEARNING_DAYS);

    if (nombreJoursObserves >= LEARNING_DAYS) {
      phaseApprentissageTerminee = true;
      Serial.println("[BUZZER] Apprentissage terminé — buzzer actif !");
    }
  }
}

// ============================================================
// 11. MODE REMPLISSAGE
// ============================================================

/*
 * COMPORTEMENT DU PREMIER APPUI :
 *   Le premier appui sur un bouton (en mode normal, couvercle ouvert)
 *   ACTIVE le mode remplissage SANS faire bouger le plateau.
 *   C'est une action d'activation, pas de mouvement.
 *   À partir du 2e appui, chaque appui fait avancer ou reculer
 *   d'une position.
 *
 * POURQUOI : éviter un mouvement non intentionnel. Le premier
 *   appui est une confirmation que l'utilisateur veut remplir.
 *
 * CONDITIONS pour que les boutons fassent bouger le plateau :
 *   1. Mode remplissage déjà ACTIVÉ
 *   2. Couvercle OUVERT
 * Si le couvercle est fermé → boutons totalement IGNORÉS,
 * même si le mode remplissage est actif.
 *
 * SORTIE du mode remplissage :
 *   Fermeture du couvercle → retour position 0 → mode normal.
 *   (géré dans loop())
 */

void gererModeRemplissage() {
  // CONDITION OBLIGATOIRE : couvercle ouvert
  if (!couvercleOuvert) {
    return;
  }

  afficherRemplissageCourant();

  if (btnNextPresse()) {
    ledPositionOff();
    avancerUnePosition();
    ledPositionOn();
    afficherRemplissageCourant();
    Serial.print("[REMPLISSAGE] Avancer -> position ");
    Serial.println(positionActuelle);
  }

  if (btnPrevPresse()) {
    ledPositionOff();
    reculerUnePosition();
    ledPositionOn();
    afficherRemplissageCourant();
    Serial.print("[REMPLISSAGE] Reculer -> position ");
    Serial.println(positionActuelle);
  }
}

// ============================================================
// 12. MODE NORMAL
// ============================================================

/*
 * SÉQUENCE RÉELLE (ne pas modifier l'ordre) :
 *
 *   CYCLE_ATTENTE
 *     Boîte fermée. Plateau sur position 0 (compartiment vide).
 *     On attend que le patient OUVRE le couvercle.
 *     │
 *   CYCLE_DEPLACEMENT (déclenché par ouverture)
 *     Le moteur amène le compartiment médicament programmé (1-21)
 *     devant l'ouverture. LED position ON.
 *     Poids AVANT prise mesuré ici :
 *       → Le compartiment vient d'être aligné.
 *       → Le patient n'a pas encore mis la main dedans.
 *       → C'est le moment le plus fiable pour le poids "avant".
 *     │
 *   CYCLE_PRISE_EN_COURS
 *     Le patient prend (ou pas) le médicament.
 *     On attend la FERMETURE du couvercle.
 *     │
 *   CYCLE_RETOUR (déclenché par fermeture)
 *     Poids APRÈS prise mesuré.
 *     Comparaison avant/après → prise détectée ou non.
 *     Plateau REVIENT à position 0 (compartiment vide).
 *     LED position OFF.
 *     Prochain compartiment médicament préparé.
 *     │
 *   CYCLE_ATTENTE (boucle)
 */

void gererModeNormal() {

  switch (cycleNormal) {

    case CYCLE_ATTENTE:
      // Après le homing, on DOIT d'abord rester immobile et
      // attendre un vrai événement d'ouverture du couvercle.
      // On arme donc le cycle seulement après avoir vu le
      // couvercle fermé de manière stable.
      if (!ouvertureCycleArmee) {
        afficherAttenteOuverture();
        if (!couvercleOuvert) {
          ouvertureCycleArmee = true;
        }
        break;
      }

      afficherAttenteOuverture();

      // Transition fermé -> ouvert : SEUL déclencheur autorisé
      if (couvercleOuvert && !couvercleStablePrecedent) {
        Serial.println("[NORMAL] Patient a ouvert la boîte");
        afficherOLED("Couvercle ouvert", nomCompartiment(compartimentProgramme));
        cycleNormal = CYCLE_DEPLACEMENT;
      }
      break;

    case CYCLE_DEPLACEMENT:
      Serial.print("[NORMAL] Déplacement vers compartiment ");
      Serial.println(compartimentProgramme);

      afficherOLED("Couvercle ouvert", nomCompartiment(compartimentProgramme));
      ledPositionOff();
      allerAPosition(compartimentProgramme);
      ledPositionOn();

      // Pour la toute première prise après un remplissage,
      // on réutilise le poids total mémorisé en fin de session.
      delay(200);  // Stabilisation mécanique après déplacement
      if (poidsReferencePostRemplissageValide) {
        poidsAvantPrise = poidsReferencePostRemplissage;
        poidsReferencePostRemplissageValide = false;
        Serial.print("[NORMAL] Poids de reference post-remplissage : ");
      } else {
        poidsAvantPrise = lireCapteurPoids();
        Serial.print("[NORMAL] Poids avant prise : ");
      }
      Serial.println(poidsAvantPrise);

      Serial.println("[NORMAL] Compartiment prêt. Attente fermeture...");
      afficherOLED(nomCompartiment(compartimentProgramme), "Attente fermeture");
      cycleNormal = CYCLE_PRISE_EN_COURS;
      break;

    case CYCLE_PRISE_EN_COURS:
      afficherOLED(nomCompartiment(compartimentProgramme), "Attente fermeture");
      // Transition ouvert → fermé
      if (!couvercleOuvert && couvercleStablePrecedent) {
        Serial.println("[NORMAL] Patient a fermé la boîte");
        cycleNormal = CYCLE_RETOUR;
      }
      break;

    case CYCLE_RETOUR:
      {
        // Mesure du poids APRÈS prise
        int poidsApres = lireCapteurPoids();
        int difference = poidsAvantPrise - poidsApres;

        Serial.print("[NORMAL] Poids après : ");
        Serial.print(poidsApres);
        Serial.print(" | Diff : ");
        Serial.println(difference);

        if (difference > WEIGHT_DIFF_MIN) {
          Serial.println("[NORMAL] ✓ Prise détectée !");
          afficherOLED("PRISE", "Retour pos0");
          ledStatusBlink(2, 200);
          ledStatusOn();
          buzzerBip(100);
        } else {
          Serial.println("[NORMAL] ✗ Pas de prise détectée");
          afficherOLED("NON PRISE", "Retour pos0");
          buzzerAlerte();
        }

        // Retour à position 0 (compartiment vide = repos)
        Serial.println("[NORMAL] Retour position 0 (repos)...");
        ledPositionOff();
        allerAPosition(POSITION_REPOS);
        Serial.println("[NORMAL] Position 0 atteinte.");

        // Préparer le prochain compartiment médicament (1 à 21)
        compartimentProgramme++;
        if (compartimentProgramme > COMPARTIMENT_MAX) {
          compartimentProgramme = COMPARTIMENT_MIN;  // Boucler 1→21→1
        }
        // En production : compartimentProgramme reçu via MQTT

        Serial.print("[NORMAL] Prochain : compartiment ");
        Serial.println(compartimentProgramme);
        Serial.println("---------------------------------");

        ouvertureCycleArmee = true;
        afficherAttenteOuverture();
        cycleNormal = CYCLE_ATTENTE;
      }
      break;
  }
}

// ============================================================
// 13. SETUP
// ============================================================

void setup() {
  Serial.begin(9600);
  Serial.println("=================================");
  Serial.println("  MEDICINE BOX v4.1 (finale)");
  Serial.println("  Prototype Proteus");
  Serial.println("=================================");
  Serial.println("  22 positions : 0=vide, 1-21=médicaments");
  Serial.println("=================================");

  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDRESS)) {
    Serial.println("[INIT] OLED SSD1306 absente");
  } else {
    afficherOLED("Demarrage", "systeme");
  }

  // --- Pins moteur ---
  pinMode(MOTOR_PIN1, OUTPUT);
  pinMode(MOTOR_PIN2, OUTPUT);
  pinMode(MOTOR_PIN3, OUTPUT);
  pinMode(MOTOR_PIN4, OUTPUT);
  stopMoteur();

  // --- Pins entrées ---
  pinMode(REED_PIN, INPUT_PULLUP);
  pinMode(COVER_SWITCH_PIN, INPUT_PULLUP);
  pinMode(BTN_NEXT_PIN, INPUT_PULLUP);
  pinMode(BTN_PREV_PIN, INPUT_PULLUP);

  // --- Pins sorties ---
  pinMode(LED_STATUS_PIN, OUTPUT);
  pinMode(LED_POSITION_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  ledStatusOff();
  ledPositionOff();
  digitalWrite(BUZZER_PIN, LOW);
  // PAS de bip au démarrage

  // --- Précalculer la table cumulative ---
  // stepsCumulatifs[i] = pas totaux du centre de pos 0 au centre de pos i
  stepsCumulatifs[0] = 0;  // Position 0 = point de départ = 0 pas
  for (int i = 1; i < NB_POSITIONS; i++) {
    stepsCumulatifs[i] = stepsCumulatifs[i - 1] + stepsParPosition[i - 1];
  }

  Serial.print("[INIT] Table cumulative : pos 21 = ");
  Serial.print(stepsCumulatifs[21]);
  Serial.println(" pas depuis pos 0");
  // Vérification : stepsCumulatifs[21] + stepsParPosition[21] doit = 2048
  Serial.print("[INIT] Vérification tour complet : ");
  Serial.print(stepsCumulatifs[21] + stepsParPosition[21]);
  Serial.println(" (doit être 2048)");

  // --- Recalage initial (UNIQUE utilisation du reed switch) ---
  Serial.println("[INIT] Recalage initial...");
  if (trouverPositionZero()) {
    etatSysteme = STATE_OK;
    ledStatusOn();
    Serial.println("[INIT] Système recalé. Prêt.");
  } else {
    etatSysteme = STATE_ERROR;
    Serial.println("[INIT] ERREUR CRITIQUE — système bloqué !");
    Serial.println("[INIT] Vérifier reed switch (D2) et moteur.");
  }

  // --- Apprentissage buzzer ---
  nombreJoursObserves = 0;
  phaseApprentissageTerminee = false;
  dernierJourMs = millis();

  // --- État initial ---
  modeRemplissage = false;
  cycleNormal = CYCLE_ATTENTE;
  compartimentProgramme = 10;  // Simulation test : Jeudi matin

  // Initialiser l'état du couvercle
  coverLastRawState = (digitalRead(COVER_SWITCH_PIN) == LOW);
  couvercleStable = coverLastRawState;
  couvercleOuvert = couvercleStable;
  couvercleStablePrecedent = couvercleStable;
  coverLastChangeMs = millis();

  // Important : ne pas partir vers le compartiment cible juste
  // après le homing. Le mouvement n'est autorisé qu'après un
  // vrai front d'ouverture. Si le couvercle est déjà ouvert au
  // démarrage, on exige d'abord une fermeture stable.
  ouvertureCycleArmee = !couvercleOuvert;
  afficherAttenteOuverture();

  if (etatSysteme == STATE_OK) {
    Serial.println("---------------------------------");
    Serial.println("MODE NORMAL actif.");
    Serial.println("  Ouvrir couvercle (D3) -> moteur amène compartiment");
    Serial.println("  Refermer -> retour position 0");
    Serial.println("  Bouton (D5/D6) + couvercle ouvert -> mode remplissage");
    Serial.println("  1er appui = activation, 2e+ = mouvement");
    Serial.println("---------------------------------");
  }
}

// ============================================================
// 14. LOOP
// ============================================================

void loop() {

  // --- ÉTAT D'ERREUR : système bloqué ---
  if (etatSysteme == STATE_ERROR) {
    afficherOLED("ERREUR", "Systeme bloque");
    ledStatusBlink(1, 300);
    ledPositionOn(); delay(300); ledPositionOff(); delay(300);
    return;  // Ne fait RIEN d'autre
  }

  // --- 1. Mettre à jour le couvercle (avec debounce) ---
  mettreAJourCouvercle();

  // --- 2. Gestion des modes ---
  if (modeRemplissage) {

    gererModeRemplissage();

    // Sortie du mode remplissage à la fermeture du couvercle
    if (!couvercleOuvert && couvercleStablePrecedent) {
      delay(200);  // Stabilisation avant prise de la référence totale
      poidsReferencePostRemplissage = lireCapteurPoids();
      poidsReferencePostRemplissageValide = true;
      Serial.print("[SYS] Fin remplissage -> poids total de reference : ");
      Serial.println(poidsReferencePostRemplissage);

      modeRemplissage = false;
      ledPositionOff();
      afficherOLED("Retour pos0", "Fin remplissage");
      allerAPosition(POSITION_REPOS);
      Serial.println("[SYS] Fin remplissage -> retour pos 0 -> mode normal");
      ouvertureCycleArmee = true;
      cycleNormal = CYCLE_ATTENTE;
    }

  } else {

    // Vérifier entrée en mode remplissage :
    //   couvercle ouvert + bouton pressé + pas de cycle en cours
    //
    // COMPORTEMENT : le premier appui ACTIVE le mode seulement.
    //   Le plateau ne bouge PAS encore. Le mouvement commence
    //   au 2e appui (dans gererModeRemplissage).
    if (couvercleOuvert && cycleNormal == CYCLE_ATTENTE) {
      bool nextPress = btnNextPresse();
      bool prevPress = btnPrevPresse();

      if (nextPress || prevPress) {
        modeRemplissage = true;
        Serial.println("[SYS] Mode REMPLISSAGE activé (1er appui = activation)");
        afficherRemplissageCourant();
        ledPositionOn();
        couvercleStablePrecedent = couvercleOuvert;
        return;  // Le front est consommé, pas de mouvement
      }
    }

    gererModeNormal();
  }

  // --- 3. Apprentissage buzzer ---
  gererApprentissageBuzzer();

  // --- 4. Sauvegarder l'état du couvercle pour les transitions ---
  couvercleStablePrecedent = couvercleOuvert;
}


/*
 * ============================================================
 *  RÉSUMÉ ENTRÉES / SORTIES
 * ============================================================
 *
 *  ENTRÉES :
 *    D2  — Reed switch       → position 0 (recalage démarrage UNIQUEMENT)
 *    D3  — Inter. couvercle  → ouverture/fermeture (debounce 80ms)
 *    D5  — BTN Next          → avancer (remplissage + couvercle ouvert)
 *    D6  — BTN Prev          → reculer (remplissage + couvercle ouvert)
 *    A0  — Potentiomètre     → simulation capteur poids
 *
 *  SORTIES :
 *    D8-D11 — Moteur (via ULN2003A)
 *    D4     — LED status     → prêt (ON) / erreur (clignotant)
 *    D12    — LED position   → compartiment aligné (ON)
 *    D7     — Buzzer         → rappel (après 7 jours)
 *
 * ============================================================
 *  POSITIONS DU PLATEAU
 * ============================================================
 *
 *    Position 0     = compartiment VIDE (repos / sécurité / référence)
 *    Positions 1-21 = compartiments MÉDICAMENTS (7j × 3 prises)
 *    Total          = 22 positions
 *
 * ============================================================
 *  LIMITES RESTANTES
 * ============================================================
 *
 *  1. Pas perdus en réel (frottement/inertie) non compensés.
 *  2. Potentiomètre ≠ vrai capteur poids (recalibrer avec HX711).
 *  3. compartimentProgramme séquentiel (en prod : MQTT).
 *  4. Apprentissage compté par Arduino (en prod : backend).
 *  5. Pas de powerbank / mode veille (migration ESP32).
 *  6. Faux positifs poids possibles (ML corrigera).
 * ============================================================
 */
