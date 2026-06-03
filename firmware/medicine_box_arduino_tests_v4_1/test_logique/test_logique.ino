#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
// ======================= OLED =======================
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 32
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
bool oledOK = false;
// ======================= PINS =======================
#define MOTOR_PIN1 8
#define MOTOR_PIN2 9
#define MOTOR_PIN3 10
#define MOTOR_PIN4 11
#define REED_PIN 2
#define COVER_PIN 3
#define LED_S 4
#define LED_P 12
#define W_PIN A0
// ======================= CONFIG =======================
#define STEP_DELAY 3
#define DROP_THRESHOLD 50
const int stepsPC[22] = {
  93,93,93,93,93,93,93,93,93,93,94,
  93,93,93,93,93,93,93,93,93,93,94
};
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
// ======================= VARIABLES =======================
int sIdx = 0;
int positionCourante = -1;
int positionCible = 1;   // Lun matin, tres proche de POS0
bool homingFait = false;
bool compartimentPresente = false;
bool priseFaite = false;
bool etatCouvercle = false;
bool ancienEtatCouvercle = false;
bool pretPourOuverture = false;
int poidsReference = 0;
const char* compartiments[22] = {
  "VIDE",
  "Lun matin",
  "Lun midi",
  "Lun soir",
  "Mar matin",
  "Mar midi",
  "Mar soir",
  "Mer matin",
  "Mer midi",
  "Mer soir",
  "Jeu matin",
  "Jeu midi",
  "Jeu soir",
  "Ven matin",
  "Ven midi",
  "Ven soir",
  "Sam matin",
  "Sam midi",
  "Sam soir",
  "Dim matin",
  "Dim midi",
  "Dim soir"
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
bool couvercleEstOuvert() {
  // Logique du couvercle par rapport a l'etat actuel :
// HIGH = couvercle ouvert
// LOW  = couvercle ferme
return (digitalRead(COVER_PIN) == HIGH);
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
int lirePoidsMoyen(int n) {
  long sum = 0;
  for (int i = 0; i < n; i++) {
    sum += analogRead(W_PIN);
    delay(2);
  }
  return sum / n;
}
int calculerPasEntrePositions(int fromPos, int toPos) {
  int total = 0;
  if (toPos > fromPos) {
    for (int p = fromPos; p < toPos; p++) {
      total += stepsPC[p];
    }
  } else if (toPos < fromPos) {
    for (int p = toPos; p < fromPos; p++) {
      total += stepsPC[p];
    }
  }
  return total;
}
void allerACompartiment(int cible) {
  int totalPas = calculerPasEntrePositions(0, cible);
  char ligne2[20];
  afficherOLED("Couvercle ouvert", compartiments[cible]);
  Serial.print("Aller vers : ");
  Serial.println(compartiments[cible]);
  for (int i = 0; i < totalPas; i++) {
    pas(1);
    if ((i % 30) == 0 || i == totalPas - 1) {
      int restePas = totalPas - i - 1;
      int resteDeg = (restePas * 360L) / 2048;
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
  char ligne2[20];
  afficherOLED("Retour", "POS0");
  Serial.println("Retour position 0");
  for (int i = 0; i < totalPas; i++) {
    pas(-1);
    if ((i % 30) == 0 || i == totalPas - 1) {
      int restePas = totalPas - i - 1;
      int resteDeg = (restePas * 360L) / 2048;
      snprintf(ligne2, sizeof(ligne2), "Reste %d deg", resteDeg);
      afficherOLED("Retour POS0", ligne2);
    }
  }
  stopM();
  positionCourante = 0;
  afficherOLED("Repos", "POS0");
  Serial.println("Retour POS0 termine");
}
void faireHoming() {
  afficherOLED("HOMING", "Recherche POS0");
  Serial.println("Homing...");
  bool ok = false;
  for (int i = 0; i < 2300; i++) {
    if (digitalRead(REED_PIN) == LOW) {
      ok = true;
      break;
    }
    pas(1);
  }
  stopM();
  if (ok) {
    homingFait = true;
    positionCourante = 0;
    digitalWrite(LED_S, HIGH);
    afficherOLED("POS0 detectee", "OK");
    Serial.println("POS0 detectee");
  } else {
    afficherOLED("ERREUR", "Reed absent");
    Serial.println("ERREUR HOMING");
  }
  delay(1000);
}
void setup() {
  Serial.begin(9600);
  pinMode(MOTOR_PIN1, OUTPUT);
  pinMode(MOTOR_PIN2, OUTPUT);
  pinMode(MOTOR_PIN3, OUTPUT);
  pinMode(MOTOR_PIN4, OUTPUT);
  pinMode(REED_PIN, INPUT_PULLUP);
  pinMode(COVER_PIN, INPUT_PULLUP);
  pinMode(LED_S, OUTPUT);
  pinMode(LED_P, OUTPUT);
  digitalWrite(LED_S, LOW);
  digitalWrite(LED_P, LOW);
  oledOK = display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  if (oledOK) {
    afficherOLED("Medicine Box", "Init...");
  }
  delay(800);
  faireHoming();
  if (!homingFait) return;
  // On lit l'état réel une fois, puis on attend un vrai changement
  etatCouvercle = couvercleEstOuvert();
  ancienEtatCouvercle = etatCouvercle;
  if (etatCouvercle == false) {
  pretPourOuverture = true;
  } else {
    pretPourOuverture = false;
  }
afficherOLED("Attente", "ouverture");
}
void loop() {
  if (!homingFait) return;
  etatCouvercle = couvercleEstOuvert();
  if (!compartimentPresente && etatCouvercle == false) {
    afficherOLED("Attente", "ouverture");
  }
  // -------- vrai événement d'ouverture --------
  if (pretPourOuverture &&
      etatCouvercle == true &&
      ancienEtatCouvercle == false &&
      !compartimentPresente) {
    Serial.println("Evenement ouverture valide");
    priseFaite = false;
    digitalWrite(LED_P, LOW);
    allerACompartiment(positionCible);
    compartimentPresente = true;
    pretPourOuverture = false;
    poidsReference = lirePoidsMoyen(10);
    Serial.print("Poids ref = ");
    Serial.println(poidsReference);
  }
  // -------- surveillance pendant ouverture --------
  if (etatCouvercle && compartimentPresente) {
    int valeurActuelle = lirePoidsMoyen(5);
    int baisse = poidsReference - valeurActuelle;
    if (baisse >= DROP_THRESHOLD) {
      priseFaite = true;
      digitalWrite(LED_P, HIGH);
      afficherOLED(compartiments[positionCible], "PRISE");
    } else {
      afficherOLED(compartiments[positionCible], "Non prise");
    }
    delay(250);
  }
  // -------- vrai événement de fermeture --------
  if (etatCouvercle == false &&
      ancienEtatCouvercle == true &&
      compartimentPresente) {
    Serial.println("Evenement fermeture valide");
    if (priseFaite) {
      poidsReference = lirePoidsMoyen(10);
      afficherOLED("Prise OK", "Retour POS0");
    } else {
      afficherOLED("Aucune prise", "Retour POS0");
    }
    delay(700);
    retourAZero();
    compartimentPresente = false;
    pretPourOuverture = true;
    afficherOLED("Attente", "ouverture");
  }
  ancienEtatCouvercle = etatCouvercle;
  delay(80);
}
