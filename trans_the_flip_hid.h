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
 * Note : la table de mapping est QWERTY US. Si le PC cible est
 * configuré en AZERTY, certains caractères seront erronés — voir README.
 *
 * @param text   Chaîne null-terminée à envoyer.
 * @param len    Longueur de la chaîne (sans le '\0').
 * @return true si envoyé sans erreur.
 */
bool ttf_hid_send_string(const char* text, size_t len);
