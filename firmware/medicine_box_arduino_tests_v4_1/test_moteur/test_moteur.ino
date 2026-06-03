// ************************************************************
// TEST 1 : MOTEUR — tour complet avec compensation dérive
// ************************************************************

// Définition des pins Arduino connectées au ULN2003 (4 bobines moteur)
#define MOTOR_PIN1  8
#define MOTOR_PIN2  9
#define MOTOR_PIN3  10
#define MOTOR_PIN4  11

// Délai entre deux pas (en millisecondes)
// Plus petit = moteur plus rapide
#define STEP_DELAY  3

// Nombre total de positions (compartiments)
#define NB_POS      22

// Séquence de commande du moteur en demi-pas (8 étapes)
// Chaque ligne = état des 4 bobines
const int stepSeq[8][4] = {
  {1,0,0,0},  // Étape 0
  {1,1,0,0},  // Étape 1
  {0,1,0,0},  // Étape 2
  {0,1,1,0},  // Étape 3
  {0,0,1,0},  // Étape 4
  {0,0,1,1},  // Étape 5
  {0,0,0,1},  // Étape 6
  {1,0,0,1}   // Étape 7
};

// Index de l’étape actuelle dans la séquence
int sIdx = 0;

// Nombre de pas pour chaque transition entre positions
// On alterne 93 et 94 pour atteindre exactement 2048 pas (1 tour complet)
const int stepsPC[22] = {
  93,93,93,93,93, 93,93,93,93,93, 94,
  93,93,93,93,93, 93,93,93,93,93, 94
};

void setup() {

  // Initialisation de la communication série (affichage console)
  Serial.begin(9600);

  // Configuration des pins moteur en sortie
  pinMode(MOTOR_PIN1, OUTPUT);
  pinMode(MOTOR_PIN2, OUTPUT);
  pinMode(MOTOR_PIN3, OUTPUT);
  pinMode(MOTOR_PIN4, OUTPUT);

  // Message de début
  Serial.println("TEST 1 : MOTEUR tour complet (22 positions)");

  // Variable pour compter le total des pas effectués
  int total = 0;

  // Boucle principale : on passe par toutes les positions
  for (int p = 0; p < NB_POS; p++) {

    // Affichage des informations de déplacement
    Serial.print(">> pos ");
    Serial.print(p);
    Serial.print(" -> pos ");
    Serial.print((p+1)%22);  // boucle circulaire
    Serial.print(" : ");
    Serial.print(stepsPC[p]); // nombre de pas pour cette position
    Serial.println(" pas");

    // Boucle pour effectuer les pas nécessaires
    for (int i = 0; i < stepsPC[p]; i++) {

      // Passage à l’étape suivante (cycle sur 8 étapes)
      sIdx = (sIdx + 1) % 8;

      // Envoi des signaux aux 4 bobines du moteur
      digitalWrite(MOTOR_PIN1, stepSeq[sIdx][0]);
      digitalWrite(MOTOR_PIN2, stepSeq[sIdx][1]);
      digitalWrite(MOTOR_PIN3, stepSeq[sIdx][2]);
      digitalWrite(MOTOR_PIN4, stepSeq[sIdx][3]);

      // Petite pause pour laisser le moteur tourner correctement
      delay(STEP_DELAY);
    }

    // Ajout au total des pas effectués
    total += stepsPC[p];

    // Désactivation des bobines pour éviter chauffe inutile
    digitalWrite(MOTOR_PIN1,LOW);
    digitalWrite(MOTOR_PIN2,LOW);
    digitalWrite(MOTOR_PIN3,LOW);
    digitalWrite(MOTOR_PIN4,LOW);

    // Pause entre deux positions
    delay(200);
  }

  // Vérification du nombre total de pas
  Serial.print(">> Total : ");
  Serial.print(total);
  Serial.println(" pas (doit être 2048)");
}

// Boucle vide (le test se fait une seule fois dans setup)
void loop() {
  delay(5000);
}