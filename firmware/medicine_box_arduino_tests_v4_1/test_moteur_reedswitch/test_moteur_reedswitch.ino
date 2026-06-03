#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64

#define REED_PIN 2

#define IN1 8
#define IN2 9
#define IN3 10
#define IN4 11

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

// Séquence moteur simple qui marchait dans ton test
int stepSeq[4][4] = {
  {1,0,0,0},
  {0,1,0,0},
  {0,0,1,0},
  {0,0,0,1}
};

int s = 0;

void stopMotor() {
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, LOW);
}

void setup() {
  pinMode(REED_PIN, INPUT_PULLUP);

  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);

  Serial.begin(9600);

  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("Erreur OLED");
    for (;;);
  }

  display.clearDisplay();
  display.setTextColor(WHITE);
  stopMotor();
}

void loop() {
  int reedState = digitalRead(REED_PIN);

  display.clearDisplay();
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println("HOMING SYSTEM");

  if (reedState == HIGH) {
    s = (s + 1) % 4;

    digitalWrite(IN1, stepSeq[s][0]);
    digitalWrite(IN2, stepSeq[s][1]);
    digitalWrite(IN3, stepSeq[s][2]);
    digitalWrite(IN4, stepSeq[s][3]);

    display.setTextSize(2);
    display.setCursor(0, 20);
    display.println("RUN");

    display.setTextSize(1);
    display.setCursor(0, 50);
    display.println("Recherche P0");

    Serial.println("RUN");
    delay(5);
  } else {
    stopMotor();

    display.setTextSize(2);
    display.setCursor(0, 20);
    display.println("STOP");

    display.setTextSize(1);
    display.setCursor(0, 50);
    display.println("Position 0 OK");

    Serial.println("STOP - Position 0");
  }

  display.display();
}