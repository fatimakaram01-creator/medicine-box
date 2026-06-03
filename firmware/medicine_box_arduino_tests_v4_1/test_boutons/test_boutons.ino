#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ************************************************************
// TEST 4 : BOUTONS — anti-rebond + couvercle obligatoire
// ************************************************************

// Dimensions de l'écran OLED
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64

// Définition des pins
#define BTN_NEXT 5     // bouton pour aller à la position suivante
#define BTN_PREV 6     // bouton pour revenir à la position précédente
#define COVER_PIN 3    // capteur du couvercle
#define DB_MS 50       // temps de debounce (anti-rebond)

// Initialisation de l'écran OLED
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

// Structure représentant un bouton avec anti-rebond
struct Btn {
  bool stab;              // état stable du bouton (HIGH ou LOW)
  bool prevR;             // dernière lecture brute (raw)
  unsigned long lc;       // instant du dernier changement détecté
  bool front;             // indique si un appui (front) a été détecté
};

// Initialisation des deux boutons (NEXT et PREV)
// HIGH = repos (car INPUT_PULLUP)
Btn bN = {HIGH, HIGH, 0, false};
Btn bP = {HIGH, HIGH, 0, false};

// Variable de position (0 à 21)
int pos = 0;

// Message affiché sur l’OLED
String msg = "ATTENTE";

// ************************************************************
// Fonction de mise à jour d’un bouton avec anti-rebond
// ************************************************************
void updBtn(Btn &b, int pin) {

  // Lecture brute de la pin
  bool r = digitalRead(pin);

  // Si changement détecté → on redémarre le timer
  if (r != b.prevR) {
    b.lc = millis();   // mémorise le temps du changement
    b.prevR = r;       // met à jour la lecture précédente
  }

  // Si le signal reste stable assez longtemps (debounce OK)
  if ((millis() - b.lc) > DB_MS) {
    bool nv = r; // nouvel état stable

    // Détection d’un appui (front descendant : HIGH → LOW)
    if (b.stab == HIGH && nv == LOW) {
      b.front = true; // appui détecté
    }

    // Mise à jour de l’état stable
    b.stab = nv;
  }
}

// ************************************************************
// Fonction qui retourne TRUE une seule fois par appui
// ************************************************************
bool pressed(Btn &b) {

  // Si un front a été détecté
  if (b.front) {
    b.front = false;  // on consomme l’événement
    return true;      // retourne TRUE une seule fois
  }

  return false;       // sinon rien
}

// ************************************************************
// Fonction d’affichage sur l’écran OLED
// ************************************************************
void showOLED(bool couvOuvert) {

  // Effacer l'écran
  display.clearDisplay();

  // Titre
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println("TEST 4 BOUTONS");

  // Etat du couvercle
  display.setCursor(0, 12);
  display.print("Couv: ");
  display.println(couvOuvert ? "OUVERT" : "FERME");

  // Position actuelle
  display.setCursor(0, 24);
  display.print("Pos: ");
  display.println(pos);

  // Message principal (plus grand)
  display.setTextSize(2);
  display.setCursor(0, 42);
  display.println(msg);

  // Actualiser l’écran
  display.display();
}

// ************************************************************
// Setup : exécuté une seule fois au démarrage
// ************************************************************
void setup() {

  Serial.begin(9600);

  // Configuration des boutons et du couvercle en INPUT_PULLUP
  pinMode(BTN_NEXT, INPUT_PULLUP);
  pinMode(BTN_PREV, INPUT_PULLUP);
  pinMode(COVER_PIN, INPUT_PULLUP);

  // Initialisation de l’écran OLED
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("Erreur OLED");
    for (;;); // bloquer si erreur
  }

  display.clearDisplay();
  display.setTextColor(WHITE);

  Serial.println("TEST 4 : BOUTONS + OLED");
}

// ************************************************************
// Loop : exécutée en boucle infinie
// ************************************************************
void loop() {

  // Lecture du couvercle
  // HIGH = ouvert / LOW = fermé (INPUT_PULLUP)
  bool couvOuvert = (digitalRead(COVER_PIN) == HIGH);

  // Mise à jour des deux boutons (anti-rebond)
  updBtn(bN, BTN_NEXT);
  updBtn(bP, BTN_PREV);

  // Si le couvercle est fermé → on bloque les boutons
  if (!couvOuvert) {
    bN.front = false;   // annule les appuis
    bP.front = false;
    msg = "OUVRIR";     // message affiché
    showOLED(couvOuvert);
    delay(20);
    return;             // sortir de loop ici
  }

  // ************************************************************
  // Gestion bouton NEXT
  // ************************************************************
  if (pressed(bN)) {
    pos = (pos + 1) % 22;   // avancer (boucle circulaire)
    msg = "NEXT";

    Serial.print(">> NEXT -> pos ");
    Serial.println(pos);
  }

  // ************************************************************
  // Gestion bouton PREV
  // ************************************************************
  if (pressed(bP)) {
    pos = (pos - 1 + 22) % 22; // reculer (évite négatif)
    msg = "PREV";

    Serial.print(">> PREV -> pos ");
    Serial.println(pos);
  }

  // Affichage OLED
  showOLED(couvOuvert);

  delay(20); // petite pause
}
