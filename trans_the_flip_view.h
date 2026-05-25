#pragma once

#include "trans_the_flip.h"
#include <gui/gui.h>
#include <gui/canvas.h>

/**
 * @brief Callback de rendu GUI.
 *        Appelé depuis le thread GUI — doit être rapide et ne PAS
 *        modifier l'état de l'app (utilise le mutex en lecture seule).
 */
void ttf_view_draw_callback(Canvas* canvas, void* context);

/**
 * @brief Callback de saisie clavier (boutons du Flipper).
 *        Envoie l'InputEvent dans l'event_queue du main thread.
 */
void ttf_view_input_callback(InputEvent* event, void* context);
