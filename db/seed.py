import random
from datetime import datetime, timedelta
from db.database import get_connection

# ══════════════════════════════════════════════════════════════
# CONFIGURATION GLOBALE
# ══════════════════════════════════════════════════════════════

# Intervalles prescrits par le médecin (en minutes depuis minuit)
# Ex: matin = 06:00 (360 min) → 11:00 (660 min)
INTERVALLE_MEDECIN = {
    'matin': (6 * 60, 11 * 60),     # 06:00 → 11:00
    'midi':  (11 * 60, 16 * 60),    # 11:00 → 16:00
    'soir':  (19 * 60, 22 * 60)     # 19:00 → 22:00
}

# Phase découverte : durée en jours
# Pendant cette phase, les alertes sont envoyées aléatoirement
# sur TOUT l'intervalle médecin pour explorer les habitudes du patient
JOURS_DECOUVERTE = 7

# Fenêtre glissante pour calculer la moyenne du patient
# Si les habitudes changent (Ramadan, vacances, nouveau travail...),
# le système se réadapte en 7 jours automatiquement
FENETRE_ADAPTATION = 7

# Marge autour de la moyenne patient en phase adaptée (±30 min)
# Les alertes ne sont plus sur tout l'intervalle mais resserrées
MARGE_ADAPTE = 30

# Probabilité qu'une dose soit prise (réaliste : soir plus risqué)
PROBA_PRISE = {
    'matin': 0.85,
    'midi':  0.85,
    'soir':  0.75
}

# Nombre de comprimés par moment
NB_COMPRIMES = {
    'matin': 2,
    'midi':  1,
    'soir':  1
}


# ══════════════════════════════════════════════════════════════
# FONCTIONS UTILITAIRES
# ══════════════════════════════════════════════════════════════

def minutes_to_time_str(m):
    """
    Convertit des minutes depuis minuit → chaîne 'HH:MM:00'
    Ex: 510 → '08:30:00'
    """
    return f"{m // 60:02d}:{m % 60:02d}:00"


def minutes_to_datetime(date_jour, m):
    """
    Convertit des minutes depuis minuit → objet datetime complet
    Ex: (2025-01-15, 510) → datetime(2025, 1, 15, 8, 30, 0)
    """
    return datetime(
        date_jour.year, date_jour.month, date_jour.day,
        m // 60, m % 60, 0
    )


def calculer_moyenne_patient(historique_heures):
    """
    Calcule la moyenne des heures de prise réelles (en minutes depuis minuit)

    Entrée : liste de datetime (les heures réelles de prise)
    Sortie : int (moyenne en minutes depuis minuit) ou None si liste vide

    Ex: si le patient prend toujours son matin entre 07:00 et 07:30
        → moyenne ≈ 435 minutes (= 07:15)
    """
    if not historique_heures:
        return None
    total = sum(h.hour * 60 + h.minute for h in historique_heures)
    return total // len(historique_heures)


def generer_heure_alerte(jour_index, moment, historique_prises):
    """
    Génère l'heure d'alerte selon la phase du système :

    PHASE 1 — Découverte (jours 0 à 6) :
        Le système ne connaît pas encore le patient.
        → Alerte au tour de la moyenne d'intervalle médecin
        → But : explorer quand le patient prend réellement ses doses
        → Ex matin : alerte à 06:23, 09:45, 07:12, 10:30...

    PHASE 2 — Adaptée (jours 7+) :
        Le système a collecté assez de données.
        → Calcule la moyenne des N derniers jours (fenêtre glissante)
        → Alerte resserrée : moyenne ± 30 min
        → Ex : patient prend toujours matin vers 07:15
               → alertes à 06:50, 07:30, 07:05, 07:40...

    RÉADAPTATION AUTOMATIQUE :
        La fenêtre glissante (7 jours) fait que si les habitudes
        changent (Ramadan, vacances...), le système recalcule la
        moyenne sur la dernière semaine et se réadapte.

    Retourne : (heure_en_minutes, phase_str)
    """
    debut, fin = INTERVALLE_MEDECIN[moment]
    milieu = (debut + fin) // 2
    # ── Phase 1 : Découverte ──
    if jour_index < JOURS_DECOUVERTE:
        # Phase découverte : alerte au MILIEU de l'intervalle
        # Légère variation ±15 min pour avoir des données variées
        # mais centrée sur le milieu (pas random total)
        heure = milieu + random.randint(-15, 15)
        heure = max(debut, min(fin, heure))
        return heure, 'decouverte'

    # ── Phase 2 : Adaptée ──
    # Fenêtre glissante : on ne prend que les N derniers jours
    # pour capter les changements d'habitudes récents
    # Phase adaptée : moyenne patient ± 30 min
    prises_recentes = historique_prises[-FENETRE_ADAPTATION:]
    moyenne = calculer_moyenne_patient(prises_recentes)

    if moyenne is None:
        # Pas assez de données (ex: toutes les doses manquées)
        # → on reste en exploration autour de la moyenne d'intervalle du medecin
        heure = milieu + random.randint(-15, 15)
        heure = max(debut, min(fin, heure))
        return heure, 'decouverte'

    # Alerte resserrée autour de la moyenne patient
    # Mais toujours autour de la moyenne d'intervalle du médecin (clamp)
    heure = moyenne + random.randint(-30, 30)
    heure = max(debut, min(fin, heure))
    return heure, 'adapte'


def calculer_efficacite_alerte(prise, heure_reelle, heure_alerte_dt):
    """
    Détermine si une alerte a été efficace.

    Logique métier :
    - Patient n'a pas pris sa dose       → False (alerte n'a pas marché)
    - delai < 0  → prise AVANT l'alerte  → False (alerte inutile, il avait déjà pris)
    - delai = 0  → prise à l'heure       → True  (parfait)
    - 0 < delai < 120 → prise dans 2h    → True  (alerte efficace)
    - delai ≥ 120 → prise trop tardive   → False (alerte n'a pas suffi)

    Retourne : (delai_minutes ou None, alerte_efficace bool)
    """
    if not prise or heure_reelle is None:
        return None, False

    delai = int((heure_reelle - heure_alerte_dt).total_seconds() / 60)

    if delai < 0:
        # Prise avant l'alerte → l'alerte était inutile
        return None, False
    elif delai == 0:
        return 0, True
    else:
        # Efficace si prise dans les 2 heures après l'alerte
        return delai, delai < 120


# ══════════════════════════════════════════════════════════════
# FONCTION PRINCIPALE : SEED
# ══════════════════════════════════════════════════════════════

def seed_data():
    conn = get_connection()
    cursor = conn.cursor()

    # ── Vérification : éviter les doublons ──
    cursor.execute("SELECT COUNT(*) FROM patients;")
    if cursor.fetchone()[0] > 0:
        print("Donnees deja presentes — seed annule !")
        cursor.close()
        conn.close()
        return

    # ──────────────────────────────────────────────────────
    # 1. PATIENT
    # Un seul patient pour le prototype
    # ──────────────────────────────────────────────────────
    cursor.execute("""
        INSERT INTO patients (nom, prenom, medecin)
        VALUES ('Benmoussa', 'Fatima', 'Dr. Saidi')
        RETURNING id;
    """)
    patient_id = cursor.fetchone()[0]

    # ──────────────────────────────────────────────────────
    # 2. MÉDICAMENT
    # Doliprane 500mg — médicament courant pour le prototype
    # ──────────────────────────────────────────────────────
    cursor.execute("""
        INSERT INTO medicaments (nom, dosage)
        VALUES ('Doliprane', '500mg')
        RETURNING id;
    """)
    medicament_id = cursor.fetchone()[0]

    # ──────────────────────────────────────────────────────
    # 3. PRESCRIPTION
    # 90 jours passés (données historiques pour entraîner le ML)
    # + 30 jours futurs (pour les prédictions)
    # ──────────────────────────────────────────────────────
    date_debut = datetime.now() - timedelta(days=90)
    date_fin   = datetime.now() + timedelta(days=30)

    cursor.execute("""
        INSERT INTO prescriptions (
            patient_id, medicament_id, prescrit_par,
            date_debut, date_fin
        ) VALUES (%s, %s, %s, %s, %s)
        RETURNING id;
    """, (
        patient_id, medicament_id, 'Dr. Saidi',
        date_debut.date(), date_fin.date()
    ))
    prescription_id = cursor.fetchone()[0]

    # ──────────────────────────────────────────────────────
    # 4. DOSES PRESCRITES
    # 3 moments/jour avec intervalles définis par le médecin
    # heure_optimisee = NULL → sera rempli par le ML n°3
    # après apprentissage des habitudes du patient
    # ──────────────────────────────────────────────────────
    doses_config = [
        ('matin', '06:00', '11:00', 2),
        ('midi',  '11:00', '16:00', 1),
        ('soir',  '19:00', '22:00', 1)
    ]

    for moment, h_debut, h_fin, nb_comprimes in doses_config:
        cursor.execute("""
            INSERT INTO prescription_doses (
                prescription_id, moment,
                heure_debut, heure_fin,
                nb_comprimes, heure_optimisee
            ) VALUES (%s, %s, %s, %s, %s, NULL);
        """, (prescription_id, moment, h_debut, h_fin, nb_comprimes))

    # ──────────────────────────────────────────────────────
    # 5. STOCK INITIAL
    # Calculé automatiquement : nb_comprimes/jour × 90 jours
    # Seuil d'alerte = 5 jours de réserve (règle métier simple)
    # ──────────────────────────────────────────────────────
    nb_comprimes_par_jour = sum(NB_COMPRIMES.values())  # 2+1+1 = 4
    stock_initial = nb_comprimes_par_jour * 90           # 360
    seuil_alerte  = nb_comprimes_par_jour * 5            # 20

    cursor.execute("""
        INSERT INTO stock (
            patient_id, medicament_id,
            quantite, seuil_alerte
        ) VALUES (%s, %s, %s, %s);
    """, (patient_id, medicament_id, stock_initial, seuil_alerte))

    # ──────────────────────────────────────────────────────
    # 6. GÉNÉRATION DES 90 JOURS DE DONNÉES
    #
    # Pour chaque jour × chaque moment :
    #   a) Générer une prise (réussie ou manquée)
    #   b) Stocker l'heure réelle pour l'apprentissage
    #   c) Générer une alerte adaptative (découverte ou adaptée)
    #   d) Calculer l'efficacité de l'alerte
    #   e) Insérer dans les tables prises + alertes_optimisation
    #
    # Historique par moment : stocke les heures réelles de prise
    # pour que generer_heure_alerte() puisse calculer la moyenne
    # ──────────────────────────────────────────────────────
    historique = {
        'matin': [],
        'midi':  [],
        'soir':  []
    }

    # Compteurs pour le résumé final
    total_prises = 0
    total_alertes_optim = 0

    for jour in range(90):
        date_jour    = date_debut + timedelta(days=jour)
        jour_semaine = date_jour.weekday()  # 0=lundi ... 6=dimanche

        for moment in ['matin', 'midi', 'soir']:
            debut_min, fin_min = INTERVALLE_MEDECIN[moment]

            # ── a) Heure prévue = milieu de l'intervalle médecin ──
            # C'est la référence "officielle" de la prescription
            milieu = (debut_min + fin_min) // 2
            heure_prevue = minutes_to_datetime(date_jour, milieu)

            # ── b) Simuler si le patient prend sa dose ou non ──
            # Le soir est plus risqué (75% vs 85% matin/midi)
            prise = random.random() < PROBA_PRISE[moment]

            # Poids mesuré par la cellule de charge (capteur IoT)
            poids_avant = round(random.uniform(11.5, 13.0), 2)

            if prise:
                # Le patient ouvre la boîte et prend ses comprimés
                # Heure réelle = aléatoire dans l'intervalle prescrit
                minutes_reelles = random.randint(debut_min, fin_min)
                heure_reelle = datetime(
                    date_jour.year, date_jour.month, date_jour.day,
                    minutes_reelles // 60,
                    minutes_reelles % 60,
                    random.randint(0, 59)
                )
                # Δpoids > 0 → dose prise (détecté par ML n°1)
                poids_apres = round(
                    poids_avant - random.uniform(2.0, 2.5), 2
                )
                statut = 'pris'
            else:
                # Dose manquée → pas d'ouverture détectée
                heure_reelle = None
                poids_apres  = poids_avant  # Δpoids = 0
                statut       = 'manque'

            # ── c) Insérer la prise dans la table prises ──
            # → Utilisé par ML n°1 (Isolation Forest) pour anomalies
            # → Utilisé par ML n°2 (RF Regressor) pour risque dose manquée
            cursor.execute("""
                INSERT INTO prises (
                    patient_id, prescription_id, moment,
                    heure_prevue, heure_reelle, statut,
                    poids_avant, poids_apres
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
            """, (
                patient_id, prescription_id, moment,
                heure_prevue, heure_reelle, statut,
                poids_avant, poids_apres
            ))
            total_prises += 1

            # ── d) Alerte dose manquée (table alertes) ──
            # Alerte simple pour le dashboard Streamlit
            if not prise:
                cursor.execute("""
                    INSERT INTO alertes (patient_id, type, message)
                    VALUES (%s, %s, %s);
                """, (
                    patient_id,
                    'dose_manquee',
                    f"Dose {moment} manquee - "
                    f"{date_jour.strftime('%d/%m/%Y')}"
                ))

            # ── e) Stocker l'heure réelle pour l'apprentissage ──
            # L'historique est utilisé par generer_heure_alerte()
            # pour calculer la moyenne du patient en phase adaptée
            if prise and heure_reelle:
                historique[moment].append(heure_reelle)

            # ── f) Générer l'heure d'alerte adaptative ──
            #
            # PHASE DÉCOUVERTE (jours 0-14) :
            #   Alerte aléatoire sur TOUT l'intervalle médecin
            #   Ex matin : 06:23, 09:45, 07:12, 10:30...
            #   But : explorer les habitudes réelles du patient
            #
            # PHASE ADAPTÉE (jours 15+) :
            #   Moyenne des 7 derniers jours ± 30 min
            #   Ex : patient prend matin vers 07:15
            #        → alertes à 06:50, 07:30, 07:05...
            #
            # RÉADAPTATION (changement d'habitudes) :
            #   La fenêtre glissante de 7 jours recalcule
            #   automatiquement. Si Ramadan décale le soir
            #   de 20:30 à 22:00, après 7 jours le système
            #   alerte autour de 22:00.
            #
            alerte_minutes, phase = generer_heure_alerte(
                jour, moment, historique[moment]
            )
            heure_alerte_str = minutes_to_time_str(alerte_minutes)
            heure_alerte_dt  = minutes_to_datetime(date_jour, alerte_minutes)

            # ── g) Calculer l'efficacité de l'alerte ──
            # Logique : delai = heure_prise - heure_alerte
            # Efficace si 0 ≤ delai < 120 minutes
            delai, alerte_efficace = calculer_efficacite_alerte(
                prise, heure_reelle, heure_alerte_dt
            )

            # ── h) Insérer dans alertes_optimisation ──
            # → Données d'entraînement pour ML n°3 (RF Classifier)
            # → Le modèle apprend : pour ce moment + ce jour_semaine,
            #   quelle heure d'alerte donne le meilleur résultat ?
            # → Après entraînement, il écrit le résultat dans
            #   prescription_doses.heure_optimisee
            cursor.execute("""
                INSERT INTO alertes_optimisation (
                    patient_id, moment, heure_alerte,
                    jour_semaine, heure_prise_apres_alerte,
                    delai_minutes, alerte_efficace, phase
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
            """, (
                patient_id,
                moment,
                heure_alerte_str,
                jour_semaine,
                heure_reelle,
                delai,
                alerte_efficace,
                phase
            ))
            total_alertes_optim += 1

    # ──────────────────────────────────────────────────────
    # COMMIT ET RÉSUMÉ
    # ──────────────────────────────────────────────────────
    conn.commit()
    cursor.close()
    conn.close()

    print("=" * 50)
    print("  SEED TERMINÉ AVEC SUCCÈS")
    print("=" * 50)
    print(f"  Patient ID         : {patient_id}")
    print(f"  Prescription ID    : {prescription_id}")
    print(f"  Stock initial      : {stock_initial} comprimés")
    print(f"  Seuil alerte       : {seuil_alerte} comprimés")
    print(f"  Prises générées    : {total_prises} lignes (90j × 3 moments)")
    print(f"  Alertes optim      : {total_alertes_optim} lignes (90j × 3 moments)")
    print(f"  Phase découverte   : jours 1-{JOURS_DECOUVERTE}")
    print(f"  Phase adaptée      : jours {JOURS_DECOUVERTE + 1}-90")
    print(f"  Fenêtre adaptation : {FENETRE_ADAPTATION} jours glissants")
    print("=" * 50)


if __name__ == "__main__":
    seed_data()