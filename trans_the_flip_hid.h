/*
 * TransTheFlip — BLE Remote Bad USB
 * Copyright (C) 2026 Romain Champliau
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */
#pragma once

#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>

/**
 * @brief Initialise le mode clavier USB HID.
 *        Sauvegarde la config USB courante et bascule en mode HID keyboard.
 * @return true si réussi, false sinon.
 */
bool ttf_hid_init(void);

/**
 * @brief Désactive le mode HID et restaure la config USB précédente.
 */
void ttf_hid_deinit(void);

/**
 * @brief Envoie une chaîne de caractères via USB HID keyboard.
 *
 * Syntaxe des touches spéciales (inline dans le texte) :
 *   [ENTER]         Touche Entrée
 *   [TAB]           Tabulation
 *   [ESC]           Échap
 *   [BACKSPACE]     Retour arrière
 *   [DEL]           Supprimer
 *   [UP/DOWN/LEFT/RIGHT]  Flèches
 *   [HOME] [END] [PGUP] [PGDN]
 *   [F1]..[F12]     Touches de fonction
 *   [CTRL+c]        Combinaisons (modificateurs + touche)
 *   [ALT+F4]
 *   [WIN+r]
 *   [CTRL+SHIFT+ESC]
 *   [DELAY:500]     Pause de 500 ms
 *
 * @param text   Chaîne null-terminée à envoyer.
 * @param len    Longueur de la chaîne (sans le '\0').
 * @return true si envoyé sans erreur.
 */
bool ttf_hid_send_string(const char* text, size_t len);

/**
 * @brief Charge un fichier de layout BadUSB (.kl) et l'active.
 *        Format : 256 octets = 128 × uint16_t (modifier<<8 | hid_keycode),
 *        indexé par code ASCII 0–127. Compatible avec les layouts de
 *        /ext/badusb/assets/layouts/ (en-US.kl, fr-FR.kl, etc.).
 * @param path  Chemin absolu vers le fichier .kl sur la SD.
 * @return true si chargé avec succès, false sinon (layout inchangé).
 */
bool ttf_hid_load_layout(const char* path);

/**
 * @brief Réinitialise le layout au QWERTY US intégré (par défaut).
 */
void ttf_hid_reset_layout(void);
