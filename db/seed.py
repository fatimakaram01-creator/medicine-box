import random
from datetime import datetime, timedelta
from db.database import get_connection

# ══════════════════════════════════════════════════════════════
# CONFIGURATION GLOBALE
# ══════════════════════════════════════════════════════════════

# Phase découverte : durée en jours
# Pendant cette phase, les alertes sont envoyées au milieu de l'intervalle
# (±15 min) pour explorer les habitudes du patient sans a priori
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
# LECTURE DU PROFIL HORAIRE ACTIF
# ══════════════════════════════════════════════════════════════

def lire_intervalle_medecin():
    """
    Lit les plages horaires du profil actif depuis la table
    intervalles_profils (Supabase).

    Retourne un dict : {moment: (debut_minutes, fin_minutes)}
    Les plages désactivées (00:00→00:00) sont ignorées.

    Fallback sur les valeurs par défaut si aucun profil actif :
      matin  06:00→11:00
      midi   11:00→16:00
      soir   19:00→22:00
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT matin_debut, matin_fin,
                   midi_debut,  midi_fin,
                   soir_debut,  soir_fin
            FROM intervalles_profils
            WHERE actif = TRUE;
        """)
        row = cursor.fetchone()
        cursor.close()

        if not row:
            print("⚠️ Aucun profil actif → intervalles par défaut")
            return {
                'matin': (6 * 60, 11 * 60),
                'midi':  (11 * 60, 16 * 60),
                'soir':  (19 * 60, 22 * 60),
            }

        def to_minutes(t):
            """Convertit un objet time en minutes depuis minuit"""
            if not t or (t.hour == 0 and t.minute == 0):
                return None  # plage désactivée
            return t.hour * 60 + t.minute

        result = {}
        noms   = ['matin', 'midi', 'soir']
        # row = (matin_debut, matin_fin, midi_debut, midi_fin, soir_debut, soir_fin)
        for i, nom in enumerate(noms):
            debut = to_minutes(row[i * 2])
            fin   = to_minutes(row[i * 2 + 1])
            if debut is not None and fin is not None:
                result[nom] = (debut, fin)
            # sinon plage désactivée → on l'ignore

        if not result:
            print("⚠️ Toutes les plages sont désactivées → fallback")
            return {
                'matin': (6 * 60, 11 * 60),
                'midi':  (11 * 60, 16 * 60),
                'soir':  (19 * 60, 22 * 60),
            }

        print(f"✅ Profil actif chargé : {list(result.keys())}")
        return result

    except Exception as e:
        print(f"❌ Erreur lecture profil actif : {e} → fallback")
        return {
            'matin': (6 * 60, 11 * 60),
            'midi':  (11 * 60, 16 * 60),
            'soir':  (19 * 60, 22 * 60),
        }
    finally:
        conn.close()


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


def generer_heure_alerte(jour_index, moment, historique_prises, intervalle_medecin):
    """
    Génère l'heure d'alerte selon la phase du système.

    Reçoit intervalle_medecin en paramètre (lu depuis le profil actif)
    pour s'adapter dynamiquement si le médecin change les plages.

    PHASE 1 — Découverte (jours 0 à 6) :
        Le système ne connaît pas encore le patient.
        → Alerte au milieu de l'intervalle ±15 min
        → But : explorer quand le patient prend réellement ses doses

    PHASE 2 — Adaptée (jours 7+) :
        Le système a collecté assez de données.
        → Calcule la moyenne des N derniers jours (fenêtre glissante)
        → Alerte resserrée : moyenne ± 30 min
        → Ex : patient prend toujours matin vers 07:15
               → alertes à 06:50, 07:30, 07:05, 07:40...

    RÉADAPTATION AUTOMATIQUE :
        La fenêtre glissante (7 jours) recalcule automatiquement.
        Si le profil change (Ramadan, Night Shift...), les nouvelles
        plages sont prises en compte immédiatement.

    Retourne : (heure_en_minutes, phase_str)
    """
    debut, fin = intervalle_medecin[moment]
    milieu = (debut + fin) // 2

    # ── Phase 1 : Découverte ──
    if jour_index < JOURS_DECOUVERTE:
        # Alerte au MILIEU de l'intervalle ±15 min
        # Légère variation pour avoir des données variées
        heure = milieu + random.randint(-15, 15)
        heure = max(debut, min(fin, heure))
        return heure, 'decouverte'

    # ── Phase 2 : Adaptée ──
    # Fenêtre glissante : on ne prend que les N derniers jours
    prises_recentes = historique_prises[-FENETRE_ADAPTATION:]
    moyenne = calculer_moyenne_patient(prises_recentes)

    if moyenne is None:
        # Pas assez de données (ex: toutes les doses manquées)
        # → on reste en exploration autour du milieu
        heure = milieu + random.randint(-15, 15)
        heure = max(debut, min(fin, heure))
        return heure, 'decouverte'

    # Alerte resserrée autour de la moyenne patient ±30 min
    # Clampée dans les bornes de l'intervalle médecin
    heure = moyenne + random.randint(-MARGE_ADAPTE, MARGE_ADAPTE)
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

    # ── Lire le profil horaire actif depuis Supabase ──
    # C'est la source de vérité pour les intervalles.
    # Si le médecin a créé un profil "Ramadan" ou "Night Shift",
    # le seed génère les données en cohérence avec ces plages.
    INTERVALLE_MEDECIN = lire_intervalle_medecin()
    moments_actifs = list(INTERVALLE_MEDECIN.keys())
    print(f"📋 Moments actifs pour ce seed : {moments_actifs}")

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
            patient_id, medecin,
            date_debut, date_fin, active
        ) VALUES (%s, %s, %s, %s, true)
        RETURNING id;
    """, (
        patient_id, 'Dr. Saidi',
        date_debut.date(), date_fin.date()
    ))
    prescription_id = cursor.fetchone()[0]

    # ──────────────────────────────────────────────────────
    # 4. DOSES PRESCRITES
    # Générées selon les plages du profil actif.
    # Si une plage est désactivée → pas de dose pour ce moment.
    # heure_prevue = milieu de la plage (référence prescription)
    # ──────────────────────────────────────────────────────
    for moment, (debut_min, fin_min) in INTERVALLE_MEDECIN.items():
        milieu_min = (debut_min + fin_min) // 2
        heure_debut_str = f"{debut_min // 60:02d}:{debut_min % 60:02d}"
        heure_fin_str   = f"{fin_min   // 60:02d}:{fin_min   % 60:02d}"
        heure_milieu_str = f"{milieu_min // 60:02d}:{milieu_min % 60:02d}"
        nb = NB_COMPRIMES.get(moment, 1)

        cursor.execute("""
            INSERT INTO prescription_doses (
                prescription_id, medicament_id, moment,
                heure_prevue, quantite
            ) VALUES (%s, %s, %s, %s, %s);
        """, (prescription_id, medicament_id, moment, heure_milieu_str, nb))

    # ──────────────────────────────────────────────────────
    # 5. GÉNÉRATION DES 90 JOURS DE DONNÉES
    #
    # Pour chaque jour × chaque moment actif :
    #   a) Générer une prise (réussie ou manquée)
    #   b) Stocker l'heure réelle pour l'apprentissage
    #   c) Générer une alerte adaptative (découverte ou adaptée)
    #   d) Calculer l'efficacité de l'alerte
    #   e) Insérer dans les tables prises + alertes_optimisation
    #
    # Historique par moment : stocke les heures réelles de prise
    # pour que generer_heure_alerte() puisse calculer la moyenne
    # ──────────────────────────────────────────────────────
    historique = {m: [] for m in moments_actifs}

    # Compteurs pour le résumé final
    total_prises = 0
    total_alertes_optim = 0

    for jour in range(90):
        date_jour    = date_debut + timedelta(days=jour)
        jour_semaine = date_jour.weekday()  # 0=lundi ... 6=dimanche

        for moment in moments_actifs:
            debut_min, fin_min = INTERVALLE_MEDECIN[moment]

            # ── a) Heure prévue = milieu de l'intervalle ──
            milieu = (debut_min + fin_min) // 2
            heure_prevue = minutes_to_datetime(date_jour, milieu)

            # ── b) Simuler si le patient prend sa dose ou non ──
            prise = random.random() < PROBA_PRISE.get(moment, 0.80)

            # Poids mesuré par la cellule de charge (capteur IoT)
            poids_avant = round(random.uniform(11.5, 13.0), 2)

            if prise:
                # Le patient ouvre la boîte et prend ses comprimés
                # Heure réelle = aléatoire dans la plage prescrite
                minutes_reelles = random.randint(debut_min, fin_min)
                heure_reelle = datetime(
                    date_jour.year, date_jour.month, date_jour.day,
                    minutes_reelles // 60,
                    minutes_reelles % 60,
                    random.randint(0, 59)
                )
                # Δpoids > 0 → dose prise (détecté par ML n°1)
                poids_apres = round(poids_avant - random.uniform(2.0, 2.5), 2)
                statut = 'pris'
            else:
                # Dose manquée → pas d'ouverture détectée
                heure_reelle = None
                poids_apres  = poids_avant  # Δpoids = 0
                statut       = 'manque'

            # ── c) Insérer la prise dans la table prises ──
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
            if not prise:
                cursor.execute("""
                    INSERT INTO alertes (patient_id, type, message)
                    VALUES (%s, %s, %s);
                """, (
                    patient_id,
                    'dose_manquee',
                    f"Dose {moment} manquee - {date_jour.strftime('%d/%m/%Y')}"
                ))

            # ── e) Stocker l'heure réelle pour l'apprentissage ──
            if prise and heure_reelle:
                historique[moment].append(heure_reelle)

            # ── f) Générer l'heure d'alerte adaptative ──
            #
            # PHASE DÉCOUVERTE (jours 0-6) :
            #   Alerte au milieu de la plage ±15 min
            #   But : explorer les habitudes réelles du patient
            #
            # PHASE ADAPTÉE (jours 7+) :
            #   Moyenne des 7 derniers jours ± 30 min
            #   Ex : patient prend matin vers 07:15
            #        → alertes à 06:50, 07:30, 07:05...
            #
            # RÉADAPTATION (changement de profil) :
            #   La fenêtre glissante de 7 jours recalcule automatiquement.
            #   Si le médecin active un nouveau profil, les nouvelles
            #   plages sont utilisées pour les prochains jours.
            alerte_minutes, phase = generer_heure_alerte(
                jour, moment, historique[moment], INTERVALLE_MEDECIN
            )
            heure_alerte_str = minutes_to_time_str(alerte_minutes)
            heure_alerte_dt  = minutes_to_datetime(date_jour, alerte_minutes)

            # ── g) Calculer l'efficacité de l'alerte ──
            delai, alerte_efficace = calculer_efficacite_alerte(
                prise, heure_reelle, heure_alerte_dt
            )

            # ── h) Insérer dans alertes_optimisation ──
            # → Données d'entraînement pour ML n°3 (RF Classifier)
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

    nb_comprimes_par_jour = sum(NB_COMPRIMES.get(m, 1) for m in moments_actifs)

    print("=" * 50)
    print("  SEED TERMINÉ AVEC SUCCÈS")
    print("=" * 50)
    print(f"  Patient ID         : {patient_id}")
    print(f"  Prescription ID    : {prescription_id}")
    print(f"  Moments actifs     : {moments_actifs}")
    print(f"  Prises/jour        : {nb_comprimes_par_jour}")
    print(f"  Prises générées    : {total_prises} lignes (90j × {len(moments_actifs)} moments)")
    print(f"  Alertes optim      : {total_alertes_optim} lignes")
    print(f"  Phase découverte   : jours 1-{JOURS_DECOUVERTE}")
    print(f"  Phase adaptée      : jours {JOURS_DECOUVERTE + 1}-90")
    print(f"  Fenêtre adaptation : {FENETRE_ADAPTATION} jours glissants")
    print("=" * 50)


if __name__ == "__main__":
    seed_data()