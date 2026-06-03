#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 32
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

#define W_PIN A0
#define DROP_THRESHOLD 50

int valeurInitiale = 0;
bool priseFaite = false;

void setup() {
  Serial.begin(9600);

  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    while (true);
  }

  display.clearDisplay();
  display.setTextColor(WHITE);

  long sum = 0;
  for (int i = 0; i < 10; i++) {
    sum += analogRead(W_PIN);
    delay(2);
  }
  valeurInitiale = sum / 10;

  Serial.print("Valeur initiale = ");
  Serial.println(valeurInitiale);
}

void loop() {
  long sum = 0;
  for (int i = 0; i < 5; i++) {
    sum += analogRead(W_PIN);
    delay(2);
  }
  int valeurActuelle = sum / 5;

  int baisse = valeurInitiale - valeurActuelle;
  priseFaite = (baisse >= DROP_THRESHOLD);

  Serial.print("Init=");
  Serial.print(valeurInitiale);
  Serial.print(" | Actuel=");
  Serial.print(valeurActuelle);
  Serial.print(" | Baisse=");
  Serial.print(baisse);
  Serial.print(" | Prise=");
  Serial.println(priseFaite ? "OUI" : "NON");

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(WHITE);

  // Ligne 1
  display.setCursor(0, 0);
  display.print("I:");
  display.print(valeurInitiale);
  display.print(" A:");
  display.print(valeurActuelle);

  // Ligne 2
  display.setCursor(0, 16);
  display.print(priseFaite ? "PRISE" : "OK");

  display.display();

  delay(300);
}