from db.database import get_connection

def create_tables():
    conn = get_connection()
    cursor = conn.cursor()

    # ──────────────────────────────────────────────────────────
    # TABLE : patients
    # Stocke les informations de base du patient
    # Chaque patient est suivi par un médecin référent
    # ──────────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS patients (
            id          SERIAL PRIMARY KEY,
            nom         VARCHAR(100),
            prenom      VARCHAR(100),
            medecin     VARCHAR(100)
        );
    """)

    # ──────────────────────────────────────────────────────────
    # TABLE : medicaments
    # Catalogue des médicaments disponibles
    # Chaque médicament a un nom et un dosage (ex: Doliprane 500mg)
    # ──────────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS medicaments (
            id      SERIAL PRIMARY KEY,
            nom     VARCHAR(100),
            dosage  VARCHAR(50)
        );
    """)

    # ──────────────────────────────────────────────────────────
    # TABLE : prescriptions
    # Lie un patient à un médicament sur une période donnée
    # prescrit_par = le médecin qui a fait l'ordonnance
    # date_debut/date_fin = durée du traitement
    # ──────────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prescriptions (
            id              SERIAL PRIMARY KEY,
            patient_id      INT REFERENCES patients(id),
            medicament_id   INT REFERENCES medicaments(id),
            prescrit_par    VARCHAR(100),
            date_debut      DATE,
            date_fin        DATE,
            created_at      TIMESTAMP DEFAULT NOW()
        );
    """)

    # ──────────────────────────────────────────────────────────
    # TABLE : prescription_doses
    # Détail des doses prescrites pour chaque moment de la journée
    # - moment : 'matin', 'midi', 'soir'
    # - heure_debut/heure_fin : intervalle prescrit par le médecin
    #   (ex: matin = 06:00 → 11:00)
    # - nb_comprimes : nombre de comprimés à prendre
    # - heure_optimisee : NULL au départ, rempli par le modèle ML n°3
    #   (RF Classifier) après apprentissage des habitudes du patient
    # ──────────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prescription_doses (
            id                  SERIAL PRIMARY KEY,
            prescription_id     INT REFERENCES prescriptions(id),
            moment              VARCHAR(20),
            heure_debut         TIME,
            heure_fin           TIME,
            nb_comprimes        INT,
            heure_optimisee     TIME DEFAULT NULL
        );
    """)

    # ──────────────────────────────────────────────────────────
    # TABLE : prises
    # Historique de chaque prise (ou dose manquée)
    # - heure_prevue : milieu de l'intervalle prescrit
    # - heure_reelle : quand le patient a réellement ouvert la boîte
    #   (NULL si dose manquée)
    # - statut : 'pris' ou 'manque'
    # - poids_avant/poids_apres : mesurés par la cellule de charge
    #   Δpoids > 0 → dose prise, Δpoids = 0 → anomalie
    # → Utilisé par ML n°1 (Isolation Forest) et ML n°2 (RF Regressor)
    # ──────────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prises (
            id              SERIAL PRIMARY KEY,
            patient_id      INT REFERENCES patients(id),
            prescription_id INT REFERENCES prescriptions(id),
            moment          VARCHAR(20),
            heure_prevue    TIME,
            heure_reelle    TIMESTAMP,
            statut          VARCHAR(20),
            poids_avant     FLOAT,
            poids_apres     FLOAT,
            created_at      TIMESTAMP DEFAULT NOW()
        );
    """)

    # ──────────────────────────────────────────────────────────
    # TABLE : stock
    # Suivi du stock de médicaments en temps réel
    # - quantite : nombre de comprimés restants
    # - seuil_alerte : quand quantite ≤ seuil → alerte stock
    #   (règle métier simple, pas de ML)
    # ──────────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock (
            id              SERIAL PRIMARY KEY,
            patient_id      INT REFERENCES patients(id),
            medicament_id   INT REFERENCES medicaments(id),
            quantite        INT,
            seuil_alerte    INT,
            date_maj        TIMESTAMP DEFAULT NOW()
        );
    """)

    # ──────────────────────────────────────────────────────────
    # TABLE : alertes
    # Alertes générales du système (doses manquées, stock bas, anomalies)
    # - type : 'dose_manquee', 'stock_bas', 'anomalie'
    # - lu : si le patient/médecin a vu l'alerte
    # ──────────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alertes (
            id          SERIAL PRIMARY KEY,
            patient_id  INT REFERENCES patients(id),
            type        VARCHAR(50),
            message     TEXT,
            created_at  TIMESTAMP DEFAULT NOW(),
            lu          BOOLEAN DEFAULT FALSE
        );
    """)

    # ──────────────────────────────────────────────────────────
    # TABLE : alertes_optimisation
    # Données d'entraînement pour le ML n°3 (RF Classifier)
    #
    # But : apprendre la meilleure heure pour envoyer une alerte
    # au patient, en se basant sur ses habitudes réelles.
    #
    # Colonnes ajoutées par rapport à la version initiale :
    # - moment : 'matin', 'midi', 'soir'
    #   → INDISPENSABLE pour que le modèle apprenne séparément
    #     les habitudes de chaque moment (le patient peut être
    #     ponctuel le matin mais en retard le soir)
    # - phase : 'decouverte' ou 'adapte'
    #   → Phase découverte (jours 1-15) : alertes variées sur
    #     tout l'intervalle médecin pour explorer les habitudes
    #   → Phase adaptée (jours 16+) : alertes resserrées autour
    #     de la moyenne réelle du patient
    #
    # Logique alerte_efficace :
    # - delai < 0     → prise AVANT l'alerte → False (inutile)
    # - delai = 0     → prise à l'heure      → True
    # - 0 < delai<120 → prise dans les 2h    → True (efficace)
    # - delai ≥ 120   → trop tard            → False
    # - dose manquée  → False
    # ──────────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alertes_optimisation (
            id                          SERIAL PRIMARY KEY,
            patient_id                  INT REFERENCES patients(id),
            moment                      VARCHAR(20),
            heure_alerte                TIME,
            jour_semaine                INT,
            heure_prise_apres_alerte    TIMESTAMP,
            delai_minutes               INT,
            alerte_efficace             BOOLEAN,
            phase                       VARCHAR(15) DEFAULT 'decouverte',
            created_at                  TIMESTAMP DEFAULT NOW()
        );
    """)

    conn.commit()
    cursor.close()
    conn.close()
    print("Tables creees avec succes !")

if __name__ == "__main__":
    create_tables()