// =====================================================
// TEST POWERBANK + ESP32 + ULN2003 + 28BYJ-48
// Version modifiée : moteur tourne en continu
// =====================================================

#define IN1 18
#define IN2 19
#define IN3 23
#define IN4 25

const int stepSeq[8][4] = {
  {1, 0, 0, 0},
  {1, 1, 0, 0},
  {0, 1, 0, 0},
  {0, 1, 1, 0},
  {0, 0, 1, 0},
  {0, 0, 1, 1},
  {0, 0, 0, 1},
  {1, 0, 0, 1}
};

int stepIndex = 0;
const int STEP_DELAY_MS = 4;

void ecrireBobines(int a, int b, int c, int d) {
  digitalWrite(IN1, a);
  digitalWrite(IN2, b);
  digitalWrite(IN3, c);
  digitalWrite(IN4, d);
}

void stopMoteur() {
  ecrireBobines(0, 0, 0, 0);
}

void faireUnPas(int direction) {
  stepIndex += direction;

  if (stepIndex > 7) stepIndex = 0;
  if (stepIndex < 0) stepIndex = 7;

  ecrireBobines(
    stepSeq[stepIndex][0],
    stepSeq[stepIndex][1],
    stepSeq[stepIndex][2],
    stepSeq[stepIndex][3]
  );

  delay(STEP_DELAY_MS);
}

void setup() {
  Serial.begin(115200);

  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);

  stopMoteur();

  Serial.println("=== TEST MOTEUR NON STOP ===");
  Serial.println("Le moteur tourne en continu.");
}

void loop() {
  faireUnPas(1);
}