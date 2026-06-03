// ************************************************************
// TEST 3 : COUVERCLE (D3) — debounce + transitions
// ************************************************************

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64

#define COVER_PIN 3
#define COVER_DB 80

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

bool rawS = false, stableS = false;
unsigned long lastC = 0;
String transitionMsg = "";

void setup() {
  Serial.begin(9600);

  pinMode(COVER_PIN, INPUT_PULLUP);

  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("Erreur OLED");
    for (;;);
  }

  rawS = (digitalRead(COVER_PIN) == LOW);
  stableS = rawS;
  lastC = millis();

  display.clearDisplay();
  display.setTextColor(WHITE);
}

void loop() {
  bool r = (digitalRead(COVER_PIN) == LOW);

  // Détection de changement brut
  if (r != rawS) {
    lastC = millis();
    rawS = r;
  }

  // Debounce
  if ((millis() - lastC) > COVER_DB) {
    bool ancien = stableS;
    stableS = rawS;

    if (stableS != ancien) {
      transitionMsg = stableS ? "Transition: FERME" : "Transition: OUVERT";
      Serial.println(transitionMsg);
    }
  }

  // Affichage OLED
  display.clearDisplay();

  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println("TEST COUVERCLE");

  display.setTextSize(2);
  display.setCursor(0, 18);
  if (stableS) {
    display.println("FERME");
  } else {
    display.println("OUVERT");
  }

  display.setTextSize(1);
  display.setCursor(0, 50);
  display.println(transitionMsg);

  display.display();
  delay(50);
}

