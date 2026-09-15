

# Medicine Box

Pilulier connecté qui suit la prise réelle des médicaments et prédit le meilleur moment de rappel pour chaque patient grâce à des modèles de machine learning individualisés.

[🇬🇧 English version](README.md)

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Supabase-4169E1?logo=postgresql&logoColor=white)
![MQTT](https://img.shields.io/badge/MQTT-HiveMQ_Cloud-660066?logo=eclipsemosquitto&logoColor=white)
![ESP32](https://img.shields.io/badge/Microcontroller-ESP32-E7352C?logo=espressif&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-lightgrey)

## Démo

[![Démo](https://img.youtube.com/vi/bXz71IvDXHM/0.jpg)](https://youtu.be/bXz71IvDXHM)

<p align="center">
  <img src="assets/box-closed.jpg" width="45%" alt="Medicine Box assemblée, fermée" />
</p>

## Présentation

L'oubli de prise de médicaments est fréquent, en particulier pour les traitements à plusieurs prises quotidiennes et chez les patients âgés vivant seuls. Une enquête menée auprès de 40 répondants pendant la phase de conception a montré que la majorité des gens oublient une prise "souvent" ou "parfois", et que 85% jugent importante une confirmation automatique de la prise réelle, pas seulement un rappel sonore.

Medicine Box est un pilulier à plateau rotatif qui :
- Répartit les doses dans un plateau de 7 jours x 3 prises
- Utilise une cellule de charge pour confirmer qu'une dose a été physiquement retirée, pas seulement qu'une alarme a sonné
- Apprend le rythme réel de prise de chaque patient et ajuste les horaires de rappel en conséquence
- Fournit un tableau de bord web pour les patients et les médecins/aidants

## Fonctionnalités

- Plateau rotatif à 22 compartiments (7 jours x 3 prises + compartiment de référence), entraîné par un moteur pas-à-pas
- Détection de prise par cellule de charge avec filtrage des pics d'amplitude pour éviter les faux positifs liés au contact de la main
- Pipeline ML par patient (Isolation Forest + Random Forest) qui prédit les horaires de rappel optimaux après une phase d'apprentissage de 7 jours
- Tableau de bord patient : prises du jour, historique d'observance, alertes de doses manquées, score de risque d'oubli
- Tableau de bord médecin (protégé par PIN) : prescriptions, gestion multi-patients, déclenchement manuel d'alerte
- Profils horaires configurables, par patient ou globaux (ex. voyage, Ramadan)
- Mode hors-ligne : l'appareil continue d'enregistrer les prises sans Wi-Fi et se synchronise à la reconnexion

## Architecture

```
ESP32 (firmware) <--MQTT/TLS--> Backend FastAPI (Render) <--SQL--> PostgreSQL (Supabase)
                                        |
                                        v
                              Tableau de bord (Patient/Médecin)
                                        |
                                        v
                    Pipeline ML (Isolation Forest + Random Forest, par patient)
```

<p align="center">
  <img src="assets/schematic.png" width="70%" alt="Schéma du circuit" />
</p>

## Stack technique

| Couche | Technologie |
|---|---|
| Microcontrôleur | ESP32 (framework Arduino) |
| Contrôle moteur | Moteur pas-à-pas 28BYJ-48 + driver ULN2003A |
| Capteurs | Amplificateur de cellule de charge HX711, 2x reed switches |
| Affichage | OLED I2C 0.96" (128x32) |
| Messagerie | MQTT via TLS (HiveMQ Cloud) |
| Backend | FastAPI (Python) |
| Base de données | PostgreSQL (Supabase) |
| Hébergement | Render |
| ML | scikit-learn (Isolation Forest, Random Forest classifieur/régresseur) |
| Frontend | Tableau de bord HTML/JS single-page |

## Machine Learning

Chaque patient a son propre profil de modèle (`ml/models/patient_{id}/`) plutôt qu'un modèle global unique.

1. Phase de découverte (7 premiers jours) : planning fixe, en enregistrant chaque prise réelle.
2. Isolation Forest repère les horaires de prise inhabituels.
3. Le classifieur Random Forest prédit si un patient a tendance à être en avance, à l'heure, ou en retard (62,96% de précision sur données de test).
4. Le régresseur Random Forest prédit le délai en minutes entre le créneau prévu et la prise réelle (MAE : 49,4 minutes).
5. Après la phase de découverte, les rappels passent du planning fixe à la prédiction du modèle.

Remarque : des données réelles de patients sur le long terme n'étaient pas disponibles à ce stade. Les modèles ont été entraînés et validés sur un jeu de données synthétique construit pour refléter des profils d'observance réalistes (à l'heure, en avance, en retard, doses manquées).

## Matériel

| Composant | Rôle |
|---|---|
| ESP32 | Microcontrôleur principal |
| Moteur pas-à-pas 28BYJ-48 + ULN2003A | Fait tourner le plateau à 22 compartiments |
| HX711 + cellule de charge | Détecte le retrait d'une dose |
| 2x reed switches | État du couvercle et position de référence du plateau |
| OLED 0.96" (I2C) | Affichage d'état sur l'appareil |
| Buzzer piézo | Rappel sonore |
| 2x boutons poussoirs | Navigation manuelle du plateau |

Boîtier : rond, 150mm de diamètre x 85mm de hauteur, imprimé en 3D.

<p align="center">
  <img src="assets/prototype-breadboard.jpg" width="45%" alt="Premier prototype sur breadboard" />
  <img src="assets/internals-final.jpg" width="45%" alt="Câblage final à l'intérieur du boîtier" />
</p>

## Captures d'écran

<p align="center">
  <img src="assets/screenshot-role-select.png" width="30%" alt="Écran de sélection du rôle" />
  <img src="assets/screenshot-patient-dashboard.png" width="30%" alt="Tableau de bord patient" />
  <img src="assets/screenshot-doctor-dashboard.png" width="30%" alt="Tableau de bord médecin" />
</p>

## Structure du dépôt

```
medicine-box/
├── api/            # Routes FastAPI et logique métier
├── db/             # Schéma / migrations de la base de données
├── firmware/       # Firmware Arduino pour l'ESP32
├── ml/             # Entraînement, prédiction, modèles par patient
├── mqtt/           # Client/config MQTT
├── app.py          # Point d'entrée de l'application
├── index.html      # Tableau de bord web
├── requirements.txt
├── pyproject.toml
└── render.yaml     # Configuration de déploiement Render
```

## Démarrage

```bash
git clone https://github.com/fatimakaram01-creator/medicine-box.git
cd medicine-box
pip install -r requirements.txt
```

Configurer les variables d'environnement pour la connexion Supabase (PostgreSQL) et les identifiants MQTT HiveMQ Cloud, puis lancer :

```bash
python app.py
```

Le firmware pour l'ESP32 se trouve dans `/firmware` et peut être flashé via l'Arduino IDE ou PlatformIO.

## Contexte

Développé comme un capstone project encadré pendant mes études d'ingénieure. J'ai conçu et réalisé seule l'ensemble du système : conception du circuit, firmware, backend, base de données, intégration MQTT, pipeline ML et tableau de bord web.

## Pistes d'amélioration

- Logique de rappel unifiée pour les 7 premiers jours avant qu'un planning soit renseigné
- Notifications push pour les aidants
- Jeu de données élargi pour améliorer la précision du ML

## Licence

MIT — voir [LICENSE](LICENSE).
