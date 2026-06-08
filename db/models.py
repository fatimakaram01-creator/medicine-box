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
            id              SERIAL PRIMARY KEY,
            nom             VARCHAR(100),
            prenom          VARCHAR(100),
            medecin         VARCHAR(100),
            code_activation VARCHAR(20) UNIQUE,
            active          BOOLEAN DEFAULT TRUE
        );
    """)
    # Ajouter colonnes si absentes (migration safe)
    cursor.execute("""
        ALTER TABLE patients ADD COLUMN IF NOT EXISTS code_activation VARCHAR(20) UNIQUE;
    """)
    cursor.execute("""
        ALTER TABLE patients ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE;
    """)

    # Migrations safe pour nouvelles colonnes
    cursor.execute("""
        ALTER TABLE prises ADD COLUMN IF NOT EXISTS cause_manque VARCHAR(20) DEFAULT NULL;
    """)
    cursor.execute("""
        ALTER TABLE alertes_optimisation ADD COLUMN IF NOT EXISTS profil_id INT REFERENCES intervalles_profils(id) DEFAULT NULL;
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
    # Lie un patient à son médecin sur une période donnée
    # NOTE : medicament_id a été retiré → un patient peut avoir
    # plusieurs médicaments liés via prescription_doses
    # prescrit_par → médecin qui a fait l'ordonnance (legacy)
    # medecin      → médecin référent (utilisé par l'interface)
    # ──────────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prescriptions (
            id          SERIAL PRIMARY KEY,
            patient_id  INT REFERENCES patients(id),
            medecin     VARCHAR(100),
            date_debut  DATE,
            date_fin    DATE,
            active      BOOLEAN DEFAULT TRUE,
            created_at  TIMESTAMP DEFAULT NOW()
        );
    """)

    # ──────────────────────────────────────────────────────────
    # TABLE : prescription_doses
    # Détail des doses prescrites pour chaque moment de la journée
    # - moment    : 'matin', 'midi', 'soir'
    # - heure_prevue : heure de référence (milieu de la plage active)
    #   Calculée dynamiquement depuis intervalles_profils
    # - quantite  : nombre de comprimés à prendre
    # - heure_optimisee : NULL au départ, rempli par le modèle ML n°3
    #   (RF Classifier) après apprentissage des habitudes du patient
    #
    # NOTE : heure_debut/heure_fin ont été retirées → les plages
    # horaires sont maintenant gérées dans intervalles_profils.
    # Cela permet au médecin de changer les plages sans modifier
    # la prescription elle-même.
    # ──────────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prescription_doses (
            id              SERIAL PRIMARY KEY,
            prescription_id INT REFERENCES prescriptions(id),
            medicament_id   INT REFERENCES medicaments(id),
            moment          VARCHAR(20),
            heure_prevue    TIME,
            quantite        INT DEFAULT 1,
            heure_optimisee TIME DEFAULT NULL
        );
    """)

    # ──────────────────────────────────────────────────────────
    # TABLE : intervalles_profils
    # Profils horaires définis par le médecin.
    # Chaque profil définit les plages de prise pour chaque moment.
    #
    # - label     : nom du profil (ex: "Standard", "Ramadan",
    #               "Night Shift", "Voyage Paris")
    # - matin_debut / matin_fin : plage du matin (ex: 06:00 → 11:00)
    # - midi_debut  / midi_fin  : plage du midi  (ex: 11:00 → 16:00)
    # - soir_debut  / soir_fin  : plage du soir  (ex: 16:00 → 22:00)
    # - actif     : TRUE = profil actuellement utilisé (1 seul à la fois)
    # - date_debut / date_fin   : période d'utilisation du profil
    # - nb_prises : compteur de prises effectuées sur ce profil
    #   (utilisé par le ML pour savoir si un profil a assez de données)
    #
    # PLAGE DÉSACTIVÉE :
    #   Si debut = fin = 00:00 → moment désactivé pour ce profil
    #   Ex : profil "2 prises/jour" → midi_debut=00:00, midi_fin=00:00
    #   Le backend (_get_moments_config) ignore ces plages
    #   → pas de prise générée pour ce moment
    #
    # HISTORIQUE :
    #   Quand le médecin crée un nouveau profil, l'ancien passe
    #   actif=FALSE avec date_fin=CURRENT_DATE.
    #   Le patient peut réactiver un ancien profil depuis son interface.
    #   Le ML regroupe les données des profils similaires (plages
    #   décalées de moins de 60 min) pour l'entraînement.
    # ──────────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS intervalles_profils (
            id          SERIAL PRIMARY KEY,
            label       TEXT NOT NULL,
            matin_debut TIME NOT NULL,
            matin_fin   TIME NOT NULL,
            midi_debut  TIME NOT NULL,
            midi_fin    TIME NOT NULL,
            soir_debut  TIME NOT NULL,
            soir_fin    TIME NOT NULL,
            date_debut  DATE NOT NULL DEFAULT CURRENT_DATE,
            date_fin    DATE,
            actif       BOOLEAN DEFAULT FALSE,
            nb_prises   INTEGER DEFAULT 0
        );
    """)

    # ──────────────────────────────────────────────────────────
    # TABLE : prises
    # Historique de chaque prise (ou dose manquée)
    # - heure_prevue : milieu de la plage du profil actif au moment
    #   de la création de la prise (référence de la prescription)
    # - heure_reelle : quand le patient a réellement ouvert la boîte
    #   (NULL si dose manquée)
    # - statut : 'en_attente', 'pris' ou 'manque'
    # - poids_avant/poids_apres : mesurés par la cellule de charge
    #   Δpoids > 0 → dose prise, Δpoids = 0 → anomalie
    # → Utilisé par ML n°1 (Isolation Forest) pour anomalies
    # → Utilisé par ML n°2 (RF Regressor) pour risque dose manquée
    # ──────────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prises (
            id              SERIAL PRIMARY KEY,
            patient_id      INT REFERENCES patients(id),
            prescription_id INT REFERENCES prescriptions(id),
            moment          VARCHAR(20),
            heure_prevue    TIMESTAMP,
            heure_reelle    TIMESTAMP,
            statut          VARCHAR(20),
            poids_avant     FLOAT,
            poids_apres     FLOAT,
            cause_manque    VARCHAR(20) DEFAULT NULL,
            created_at      TIMESTAMP DEFAULT NOW()
        );
    """)

    # ──────────────────────────────────────────────────────────
    # TABLE : alertes
    # Alertes générales du système (doses manquées, anomalies,
    # changements de contexte, événements système)
    # - type : 'dose_manquee', 'anomalie', 'contexte', 'systeme'
    # - lu   : si le patient/médecin a vu l'alerte
    #
    # NOTE : la table stock a été supprimée → plus d'alerte 'stock_bas'
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
    # - moment      : 'matin', 'midi', 'soir'
    #   → INDISPENSABLE pour que le modèle apprenne séparément
    #     les habitudes de chaque moment (le patient peut être
    #     ponctuel le matin mais en retard le soir)
    # - heure_alerte : l'heure à laquelle l'alerte a été envoyée
    # - jour_semaine : 0=lundi ... 6=dimanche
    #   → Le patient peut avoir des habitudes différentes selon
    #     le jour (ex: lever tardif le week-end)
    # - heure_prise_apres_alerte : quand le patient a pris sa dose
    #   après avoir reçu l'alerte (NULL si dose manquée)
    # - delai_minutes : temps entre l'alerte et la prise
    #   NULL si dose manquée ou prise avant l'alerte
    # - alerte_efficace : TRUE si la dose a été prise dans les 2h
    # - phase : 'decouverte' (jours 0-6) ou 'adapte' (jours 7+)
    #   → Permet au ML de pondérer les données récentes davantage
    #
    # Logique alerte_efficace :
    # - delai < 0      → prise AVANT l'alerte → False (inutile)
    # - delai = 0      → prise à l'heure      → True
    # - 0 < delai < 120 → prise dans les 2h   → True (efficace)
    # - delai ≥ 120    → trop tard            → False
    # - dose manquée   → False
    # ──────────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alertes_optimisation (
            id                          SERIAL PRIMARY KEY,
            patient_id                  INT REFERENCES patients(id),
            profil_id                   INT REFERENCES intervalles_profils(id) DEFAULT NULL,
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

    # ──────────────────────────────────────────────────────────
    # TABLE : config
    # Paramètres système persistants (survivent aux redémarrages)
    # - system_on              : TRUE/FALSE — système actif ou en pause
    # - intervalles_profil_actif_id : ID du profil horaire actif
    # - intervalles_modifies_le : date du dernier changement de profil
    #   → utilisé par le ML pour isoler les données des différentes
    #     périodes (ne pas mélanger les données avant/après changement
    #     de rythme de vie si le décalage est > 60 min)
    # ──────────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS config (
            id          SERIAL PRIMARY KEY,
            cle         VARCHAR(100) UNIQUE NOT NULL,
            valeur      TEXT,
            updated_at  TIMESTAMP DEFAULT NOW()
        );
    """)

    # ── Valeurs par défaut dans config ──
    # system_on est maintenant par patient : system_on_1, system_on_2, etc.
    # Pas de valeur globale system_on
    cursor.execute("""
        INSERT INTO config (cle, valeur)
        VALUES ('intervalles_profil_actif_id', '1')
        ON CONFLICT (cle) DO NOTHING;
    """)
    cursor.execute("""
        INSERT INTO config (cle, valeur)
        VALUES ('intervalles_modifies_le', CURRENT_DATE::TEXT)
        ON CONFLICT (cle) DO NOTHING;
    """)
    # mode_sans_wifi par patient : mode_sans_wifi_{id} → 'true'/'false'
    # Géré dynamiquement comme system_on_{id}, pas de valeur par défaut globale

    # ── Profil horaire par défaut ──
    # Inséré seulement si intervalles_profils est vide
    # Évite de créer un doublon si create_tables() est relancé
    cursor.execute("SELECT COUNT(*) FROM intervalles_profils;")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO intervalles_profils
                (label, matin_debut, matin_fin, midi_debut, midi_fin,
                 soir_debut, soir_fin, actif, date_debut)
            VALUES
                ('Standard', '06:00', '11:00', '11:00', '16:00',
                 '16:00', '22:00', TRUE, CURRENT_DATE);
        """)
        print("📋 Profil horaire Standard créé par défaut")

    conn.commit()
    cursor.close()
    conn.close()
    print("✅ Tables créées avec succès !")

if __name__ == "__main__":
    create_tables()