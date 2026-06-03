/*
 * ============================================================
 *  MEDICINE BOX v4.1 — Tests individuels
 *  Décommenter UN SEUL test à la fois
 * ============================================================
 *
 *  11 tests alignés avec la logique v4.1 :
 *    1.  Moteur (tour complet avec compensation)
 *    2.  Reed switch
 *    3.  Couvercle (debounce + transitions)
 *    4.  Boutons remplissage (anti-rebond + couvercle obligatoire)
 *    5.  2 LEDs
 *    6.  Buzzer (apprentissage)
 *    7.  Capteur poids
 *    8.  Recalage initial + comptage
 *    9.  Cycle normal complet
 *   10.  Recalage échoué (état d'erreur)
 *   11.  Mode remplissage complet (NOUVEAU)
 *
 *  Constantes communes :
 *    Position 0 = compartiment VIDE (repos)
 *    Positions 1-21 = compartiments médicaments
 * ============================================================
// ************************************************************
// TEST 8 : RECALAGE INITIAL + comptage
// ************************************************************
/*
#define MOTOR_PIN1 8
#define MOTOR_PIN2 9
#define MOTOR_PIN3 10
#define MOTOR_PIN4 11
#define REED_PIN 2
#define LED_S 4
#define LED_P 12
#define STEP_DELAY 3

const int stepSeq[8][4] = {
  {1,0,0,0},{1,1,0,0},{0,1,0,0},{0,1,1,0},
  {0,0,1,0},{0,0,1,1},{0,0,0,1},{1,0,0,1}
};
int sIdx=0;
const int stepsPC[22]={93,93,93,93,93,93,93,93,93,93,94,93,93,93,93,93,93,93,93,93,93,94};

void pas(int d){sIdx=(sIdx+d+8)%8;
  digitalWrite(MOTOR_PIN1,stepSeq[sIdx][0]);digitalWrite(MOTOR_PIN2,stepSeq[sIdx][1]);
  digitalWrite(MOTOR_PIN3,stepSeq[sIdx][2]);digitalWrite(MOTOR_PIN4,stepSeq[sIdx][3]);delay(STEP_DELAY);}
void stopM(){digitalWrite(MOTOR_PIN1,LOW);digitalWrite(MOTOR_PIN2,LOW);
  digitalWrite(MOTOR_PIN3,LOW);digitalWrite(MOTOR_PIN4,LOW);}

void setup() {
  Serial.begin(9600);
  pinMode(MOTOR_PIN1,OUTPUT);pinMode(MOTOR_PIN2,OUTPUT);
  pinMode(MOTOR_PIN3,OUTPUT);pinMode(MOTOR_PIN4,OUTPUT);
  pinMode(REED_PIN,INPUT_PULLUP);
  pinMode(LED_S,OUTPUT);pinMode(LED_P,OUTPUT);

  Serial.println("TEST 8 : RECALAGE + COMPTAGE");

  // Phase 1 : Recalage (une seule fois)
  bool ok=false;
  for(int i=0;i<2300;i++){if(digitalRead(REED_PIN)==LOW){ok=true;break;}pas(1);}
  stopM();

  if(!ok){Serial.println(">> ERREUR recalage");
    for(int i=0;i<10;i++){digitalWrite(LED_S,HIGH);delay(200);digitalWrite(LED_S,LOW);delay(200);}
    return;}

  digitalWrite(LED_S,HIGH);
  Serial.println(">> Position 0 (compartiment vide) trouvée !");
  delay(1000);

  // Phase 2 : Comptage (reed PLUS utilisé)
  for(int c=0;c<5;c++){
    Serial.print(">> -> pos ");Serial.print(c+1);
    Serial.print(" (");Serial.print(stepsPC[c]);Serial.println(" pas)");
    digitalWrite(LED_P,LOW);
    for(int i=0;i<stepsPC[c];i++)pas(1);
    stopM(); digitalWrite(LED_P,HIGH); delay(1000);
  }

  // Phase 3 : Retour par comptage
  int ret=0; for(int c=0;c<5;c++)ret+=stepsPC[c];
  Serial.print(">> Retour pos 0 : ");Serial.print(ret);Serial.println(" pas");
  digitalWrite(LED_P,LOW);
  for(int i=0;i<ret;i++)pas(-1);
  stopM();
  Serial.println(">> Position 0 (repos). Test terminé.");
}

void loop(){}
*/


// ************************************************************
// TEST 9 : CYCLE NORMAL COMPLET
// ************************************************************
/*
#define MOTOR_PIN1 8
#define MOTOR_PIN2 9
#define MOTOR_PIN3 10
#define MOTOR_PIN4 11
#define COVER_PIN 3
#define LED_S 4
#define LED_P 12
#define STEP_DELAY 3
#define COVER_DB 80

const int stepSeq[8][4]={
  {1,0,0,0},{1,1,0,0},{0,1,0,0},{0,1,1,0},
  {0,0,1,0},{0,0,1,1},{0,0,0,1},{1,0,0,1}};
int sIdx=0;
const int stepsPC[22]={93,93,93,93,93,93,93,93,93,93,94,93,93,93,93,93,93,93,93,93,93,94};
int cumul[22];

void pas(int d){sIdx=(sIdx+d+8)%8;
  digitalWrite(MOTOR_PIN1,stepSeq[sIdx][0]);digitalWrite(MOTOR_PIN2,stepSeq[sIdx][1]);
  digitalWrite(MOTOR_PIN3,stepSeq[sIdx][2]);digitalWrite(MOTOR_PIN4,stepSeq[sIdx][3]);delay(STEP_DELAY);}
void stopM(){digitalWrite(MOTOR_PIN1,LOW);digitalWrite(MOTOR_PIN2,LOW);
  digitalWrite(MOTOR_PIN3,LOW);digitalWrite(MOTOR_PIN4,LOW);}

bool covR=false, covS=false, covP=false; unsigned long covC=0;
void updCov(){bool r=(digitalRead(COVER_PIN)==LOW);
  if(r!=covR){covC=millis();covR=r;}if((millis()-covC)>COVER_DB)covS=covR;}

int pos=0, cible=3;
enum E{ATT,DEP,PRISE,RET}; E etat=ATT;

void setup(){
  Serial.begin(9600);
  pinMode(MOTOR_PIN1,OUTPUT);pinMode(MOTOR_PIN2,OUTPUT);
  pinMode(MOTOR_PIN3,OUTPUT);pinMode(MOTOR_PIN4,OUTPUT);
  pinMode(COVER_PIN,INPUT_PULLUP);
  pinMode(LED_S,OUTPUT);pinMode(LED_P,OUTPUT);
  digitalWrite(LED_S,HIGH);

  cumul[0]=0; for(int i=1;i<22;i++)cumul[i]=cumul[i-1]+stepsPC[i-1];

  covR=(digitalRead(COVER_PIN)==LOW); covS=covR; covP=covS;
  Serial.println("TEST 9 : CYCLE NORMAL");
  Serial.println("  Pos 0 = compartiment VIDE (repos)");
  Serial.print("  Cible : compartiment "); Serial.println(cible);
  Serial.println("  OUVRIR couvercle (D3) pour démarrer");
}

void loop(){
  updCov();
  switch(etat){
    case ATT:
      if(covS && !covP){Serial.println(">> OUVERT -> déplacement"); etat=DEP;}
      break;
    case DEP:{
      int steps=cumul[cible];
      Serial.print(">> Moteur : ");Serial.print(steps);Serial.println(" pas");
      digitalWrite(LED_P,LOW);
      for(int i=0;i<steps;i++)pas(1);
      stopM(); pos=cible; digitalWrite(LED_P,HIGH);
      Serial.println(">> Compartiment aligné (centre). REFERMER.");
      etat=PRISE; break;}
    case PRISE:
      if(!covS && covP){Serial.println(">> FERMÉ -> retour pos 0"); etat=RET;}
      break;
    case RET:{
      digitalWrite(LED_P,LOW);
      int steps=cumul[pos];
      Serial.print(">> Retour : ");Serial.print(steps);Serial.println(" pas");
      for(int i=0;i<steps;i++)pas(-1);
      stopM(); pos=0;
      Serial.println(">> Position 0 (repos). Cycle terminé.");
      cible++; if(cible>21)cible=1;
      Serial.print(">> Prochain : compartiment ");Serial.println(cible);
      etat=ATT; break;}
  }
  covP=covS;
}
*/


// ************************************************************
// TEST 10 : RECALAGE ÉCHOUÉ — état d'erreur
// ************************************************************
/*
#define MOTOR_PIN1 8
#define MOTOR_PIN2 9
#define MOTOR_PIN3 10
#define MOTOR_PIN4 11
#define REED_PIN 2
#define LED_S 4
#define LED_P 12
#define STEP_DELAY 3

const int stepSeq[8][4]={
  {1,0,0,0},{1,1,0,0},{0,1,0,0},{0,1,1,0},
  {0,0,1,0},{0,0,1,1},{0,0,0,1},{1,0,0,1}};
int sIdx=0;
bool erreur=false;

void setup(){
  Serial.begin(9600);
  pinMode(MOTOR_PIN1,OUTPUT);pinMode(MOTOR_PIN2,OUTPUT);
  pinMode(MOTOR_PIN3,OUTPUT);pinMode(MOTOR_PIN4,OUTPUT);
  pinMode(REED_PIN,INPUT_PULLUP);
  pinMode(LED_S,OUTPUT);pinMode(LED_P,OUTPUT);

  Serial.println("TEST 10 : RECALAGE ÉCHOUÉ");
  Serial.println("NE PAS activer le reed → le système doit se BLOQUER");

  bool ok=false;
  for(int i=0;i<50;i++){
    if(digitalRead(REED_PIN)==LOW){ok=true;break;}
    sIdx=(sIdx+1)%8;
    digitalWrite(MOTOR_PIN1,stepSeq[sIdx][0]);digitalWrite(MOTOR_PIN2,stepSeq[sIdx][1]);
    digitalWrite(MOTOR_PIN3,stepSeq[sIdx][2]);digitalWrite(MOTOR_PIN4,stepSeq[sIdx][3]);
    delay(STEP_DELAY);}
  digitalWrite(MOTOR_PIN1,LOW);digitalWrite(MOTOR_PIN2,LOW);
  digitalWrite(MOTOR_PIN3,LOW);digitalWrite(MOTOR_PIN4,LOW);

  if(ok){Serial.println(">> Position 0 trouvée (pour tester l'erreur, ne pas activer reed)");
    digitalWrite(LED_S,HIGH); erreur=false;}
  else{Serial.println(">> ERREUR ! Système BLOQUÉ."); erreur=true;}
}

void loop(){
  if(erreur){
    digitalWrite(LED_S,HIGH);digitalWrite(LED_P,HIGH);delay(300);
    digitalWrite(LED_S,LOW);digitalWrite(LED_P,LOW);delay(300);
  }
}
*/


// ************************************************************
// TEST 11 : MODE REMPLISSAGE COMPLET (NOUVEAU)
// Séquence complète :
//   1. Couvercle fermé → boutons ignorés
//   2. Ouvrir couvercle
//   3. Appuyer BTN NEXT → activation mode remplissage (pas de mouvement)
//   4. Appuyer BTN NEXT/PREV → rotation manuelle
//   5. Fermer couvercle → retour pos 0 + sortie mode remplissage
// ************************************************************
/*
#define MOTOR_PIN1 8
#define MOTOR_PIN2 9
#define MOTOR_PIN3 10
#define MOTOR_PIN4 11
#define COVER_PIN 3
#define BTN_NEXT 5
#define BTN_PREV 6
#define LED_S 4
#define LED_P 12
#define STEP_DELAY 3
#define COVER_DB 80
#define BTN_DB 50

const int stepSeq[8][4]={
  {1,0,0,0},{1,1,0,0},{0,1,0,0},{0,1,1,0},
  {0,0,1,0},{0,0,1,1},{0,0,0,1},{1,0,0,1}};
int sIdx=0;
const int stepsPC[22]={93,93,93,93,93,93,93,93,93,93,94,93,93,93,93,93,93,93,93,93,93,94};
int cumul[22];

void pas(int d){sIdx=(sIdx+d+8)%8;
  digitalWrite(MOTOR_PIN1,stepSeq[sIdx][0]);digitalWrite(MOTOR_PIN2,stepSeq[sIdx][1]);
  digitalWrite(MOTOR_PIN3,stepSeq[sIdx][2]);digitalWrite(MOTOR_PIN4,stepSeq[sIdx][3]);delay(STEP_DELAY);}
void stopM(){digitalWrite(MOTOR_PIN1,LOW);digitalWrite(MOTOR_PIN2,LOW);
  digitalWrite(MOTOR_PIN3,LOW);digitalWrite(MOTOR_PIN4,LOW);}

// Couvercle debounce
bool covR=false,covS=false,covP=false; unsigned long covC=0;
void updCov(){bool r=(digitalRead(COVER_PIN)==LOW);
  if(r!=covR){covC=millis();covR=r;}if((millis()-covC)>COVER_DB)covS=covR;}

// Boutons anti-rebond
struct Btn{bool stab;bool prevR;unsigned long lc;bool front;};
Btn bN={HIGH,HIGH,0,false},bP={HIGH,HIGH,0,false};
void updBtn(Btn&b,int pin){bool r=digitalRead(pin);
  if(r!=b.prevR){b.lc=millis();b.prevR=r;}
  if((millis()-b.lc)>BTN_DB){bool nv=r;if(b.stab==HIGH&&nv==LOW)b.front=true;b.stab=nv;}}
bool pressed(Btn&b){if(b.front){b.front=false;return true;}return false;}

int pos=0;
bool modeRemp=false;

void setup(){
  Serial.begin(9600);
  pinMode(MOTOR_PIN1,OUTPUT);pinMode(MOTOR_PIN2,OUTPUT);
  pinMode(MOTOR_PIN3,OUTPUT);pinMode(MOTOR_PIN4,OUTPUT);
  pinMode(COVER_PIN,INPUT_PULLUP);
  pinMode(BTN_NEXT,INPUT_PULLUP);pinMode(BTN_PREV,INPUT_PULLUP);
  pinMode(LED_S,OUTPUT);pinMode(LED_P,OUTPUT);
  digitalWrite(LED_S,HIGH);

  cumul[0]=0; for(int i=1;i<22;i++)cumul[i]=cumul[i-1]+stepsPC[i-1];

  covR=(digitalRead(COVER_PIN)==LOW); covS=covR; covP=covS;

  Serial.println("=============================================");
  Serial.println("TEST 11 : MODE REMPLISSAGE COMPLET");
  Serial.println("=============================================");
  Serial.println("Séquence à suivre :");
  Serial.println("  1. Vérifier que le couvercle est FERMÉ");
  Serial.println("     -> appuyer BTN -> rien ne se passe");
  Serial.println("  2. OUVRIR le couvercle (D3)");
  Serial.println("  3. Appuyer BTN NEXT (D5)");
  Serial.println("     -> mode remplissage ACTIVÉ (pas de mouvement)");
  Serial.println("  4. Appuyer BTN NEXT/PREV");
  Serial.println("     -> le plateau tourne");
  Serial.println("  5. FERMER le couvercle");
  Serial.println("     -> retour pos 0 + sortie mode remplissage");
  Serial.println("=============================================");
  Serial.print("Position actuelle : "); Serial.println(pos);
}

void loop(){
  updCov();
  updBtn(bN,BTN_NEXT);
  updBtn(bP,BTN_PREV);

  // --- Mode remplissage actif ---
  if(modeRemp){

    // Boutons actifs SEULEMENT si couvercle ouvert
    if(covS){
      if(pressed(bN)){
        digitalWrite(LED_P,LOW);
        for(int i=0;i<stepsPC[pos];i++)pas(1);
        stopM();
        pos=(pos+1)%22;
        digitalWrite(LED_P,HIGH);
        Serial.print("[REMP] NEXT -> pos "); Serial.println(pos);
      }
      if(pressed(bP)){
        digitalWrite(LED_P,LOW);
        int pp=(pos-1+22)%22;
        for(int i=0;i<stepsPC[pp];i++)pas(-1);
        stopM();
        pos=pp;
        digitalWrite(LED_P,HIGH);
        Serial.print("[REMP] PREV -> pos "); Serial.println(pos);
      }
    } else {
      // Couvercle fermé : consommer les fronts
      bN.front=false; bP.front=false;
    }

    // Sortie du mode remplissage à la fermeture
    if(!covS && covP){
      modeRemp=false;
      Serial.println("[REMP] Couvercle fermé -> retour pos 0");
      digitalWrite(LED_P,LOW);
      // Retour à pos 0 par le CHEMIN LE PLUS COURT
      // (aligné avec allerAPosition() du code principal)
      if(pos > 0){
        int distHoraire = (2048 - cumul[pos]) % 2048;  // pas en avant pour revenir à 0
        int distAntiHoraire = cumul[pos];                // pas en arrière
        int stepsRetour;
        int dir;
        if(distHoraire <= distAntiHoraire){
          stepsRetour = distHoraire; dir = 1;
        } else {
          stepsRetour = distAntiHoraire; dir = -1;
        }
        Serial.print("[REMP] Retour : "); Serial.print(stepsRetour);
        Serial.print(" pas ("); Serial.print(dir==1 ? "horaire" : "anti-horaire");
        Serial.println(")");
        for(int i=0;i<stepsRetour;i++)pas(dir);
        stopM();
      }
      pos=0;
      Serial.println("[REMP] Position 0 (repos). Mode remplissage TERMINÉ.");
      Serial.println(">> Ouvrir + appuyer bouton pour recommencer");
    }

  // --- Pas en mode remplissage ---
  } else {

    // Entrée en mode remplissage : couvercle ouvert + bouton pressé
    if(covS){
      if(pressed(bN) || pressed(bP)){
        modeRemp=true;
        digitalWrite(LED_P,HIGH);
        Serial.println("[SYS] Mode REMPLISSAGE activé (1er appui = activation seule)");
        Serial.print("[SYS] Position actuelle : "); Serial.println(pos);
      }
    } else {
      // Couvercle fermé : consommer les fronts
      bN.front=false; bP.front=false;
    }
  }

  covP=covS;
}
*/
