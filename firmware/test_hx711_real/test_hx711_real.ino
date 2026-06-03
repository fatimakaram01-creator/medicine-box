#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "HX711.h"

// ================= OLED =================
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 32
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

// ================= HX711 =================
#define HX711_DT 32
#define HX711_SCK 33
HX711 scale;

// ================= VARIABLES =================
bool oledOK = false;

// ================= LECTURE STABLE =================
long lirePoidsMoyen(int n) {
  long somme = 0;
  int valides = 0;

  for (int i = 0; i < n; i++) {
    if (scale.is_ready()) {
      somme += scale.read();
      valides++;
    }
    delay(10);
  }

  if (valides == 0) return 0;
  return somme / valides;
}

// ================= SETUP =================
void setup() {
  Serial.begin(115200);

  // OLED
  Wire.begin(21, 22);
  oledOK = display.begin(SSD1306_SWITCHCAPVCC, 0x3C);

  if (oledOK) {
    display.clearDisplay();
    display.setTextColor(WHITE);
    display.setCursor(0, 0);
    display.println("Init HX711...");
    display.display();
  }

  // HX711
  scale.begin(HX711_DT, HX711_SCK);

  delay(1000);
}

// ================= LOOP =================
void loop() {

  long val = lirePoidsMoyen(10);

  // ===== SERIAL =====
  Serial.print("Poids = ");
  Serial.println(val);

  // ===== OLED =====
  if (oledOK) {
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(WHITE);

    display.setCursor(0, 0);
    display.println("Poids brut:");

    display.setCursor(0, 16);
    display.println(val);

    display.display();
  }

  delay(300);
}
