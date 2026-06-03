#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ---------------- OLED ----------------
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

// ---------------- PINS ----------------
#define LED_ALERT 4
#define BUZZER    7

// ---------------- PARAMETRES ----------------
#define ON_TIME  200     // durée ON (ms)
#define OFF_TIME 800     // durée OFF (ms)

// ---------------- VARIABLES ----------------
unsigned long lastChange = 0;
bool alertState = false;

// ************************************************************
// Fonction affichage OLED
// ************************************************************
void showOLED(bool state) {
  display.clearDisplay();

  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println("ALERTE MEDICAMENT");

  display.setTextSize(2);
  display.setCursor(0, 20);

  if (state) {
    display.println("ALERTE !");
  } else {
    display.println("...");
  }

  display.display();
}

// ************************************************************
// SETUP
// ************************************************************
void setup() {
  pinMode(LED_ALERT, OUTPUT);
  pinMode(BUZZER, OUTPUT);

  digitalWrite(LED_ALERT, LOW);
  noTone(BUZZER);

  Serial.begin(9600);

  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("Erreur OLED");
    while (true);
  }

  display.setTextColor(WHITE);
}

// ************************************************************
// LOOP
// ************************************************************
void loop() {

  unsigned long now = millis();

  // Gestion ON/OFF non bloquante
  if (!alertState && (now - lastChange >= OFF_TIME)) {

    // Passage OFF -> ON
    lastChange = now;
    alertState = true;

    digitalWrite(LED_ALERT, HIGH);
    tone(BUZZER, 1000); // fréquence audible

    Serial.println("ALERTE ON");
    showOLED(true);
  }

  else if (alertState && (now - lastChange >= ON_TIME)) {

    // Passage ON -> OFF
    lastChange = now;
    alertState = false;

    digitalWrite(LED_ALERT, LOW);
    noTone(BUZZER);

    Serial.println("ALERTE OFF");
    showOLED(false);
  }
}