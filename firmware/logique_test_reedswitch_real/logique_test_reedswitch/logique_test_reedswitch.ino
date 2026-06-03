#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ================= OLED =================
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 32
#define OLED_RESET -1
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// ================= PIN =================
#define REED_PIN 4   // change si besoin

// ---------- lecture stable du reed ----------
int lireStable() {
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

void setup() {
  Serial.begin(115200);

  pinMode(REED_PIN, INPUT_PULLUP);

  Wire.begin(21, 22); // SDA, SCL ESP32

  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("OLED NON DETECTE");
    while (true);
  }

  display.clearDisplay();
  display.setTextColor(WHITE);

  Serial.println("=== TEST REED SWITCH AVEC FILTRE ===");
}

void loop() {
  int etat = lireStable();

  display.clearDisplay();
  display.setTextSize(1);

  display.setCursor(0, 0);
  display.println("TEST REED");

  display.setCursor(0, 16);

  if (etat == LOW) {
    display.println("AIMANT DETECTE");
    Serial.println("AIMANT DETECTE");
  } else {
    display.println("PAS D AIMANT");
    Serial.println("PAS D AIMANT");
  }

  display.display();

  delay(200);
}